# hpc-bridge Plugin v1 — Design Spec

Status: design spec · 2026-06-02 · branch `design/agentic-hpc-plugin`
Builds on: [`../vision.md`](../vision.md) · [`./pilot-job-repl-lifecycle.md`](./pilot-job-repl-lifecycle.md) · [`../analysis/`](../analysis/README.md)

**Scope.** The full hpc-bridge Claude Code plugin (MCP server + skill + `/hpc-connect` + hooks), **designed whole but built local-dev-endpoint-first** so the entire runtime (pilot lifecycle, durable-handle dispatch, session shell, cost plane) is verifiable with no facility access; the SSH/credential/facility complexity is built last (M5) behind clean seams. The product is the pilot's lifecycle managed as a REPL — provision, warm, idle-down, self-heal, credential-isolated — behind one tool call.

**Non-goals (deferred).** Rung-4 live-process/in-memory-kernel state (worker-pinning); multi-session concurrency and batch fan-out (`concurrency > 1`); autonomous self-heal at ALCF/OLCF/TACC (human-in-the-loop MFA per repair). See §12.

---

## 1. Architecture

Three processes plus the Compute endpoint. The trust boundary from [`../analysis/03-ssh-mfa-interactive-access.md`](../analysis/03-ssh-mfa-interactive-access.md) falls out of the process split: the hot path carries only a scoped Globus Auth token; SSH secrets never leave the broker.

```
Claude Code (laptop)
  │  MCP tools (stdio): ensure_endpoint_up · run_shell · read_file · write_file · reset_session
  ▼
hpc-bridge MCP server  ──UDS RPC──►  Credential broker  (separate process/UID; NO-OP in local dev)
  • pilot lifecycle state machine        • the ONLY thing that touches SSH / MFA / cert
  • durable-handle store (sqlite)        • hosts the url-mode OTP page (secret never reaches MCP proc)
  • session-shell shim · cost plane      • drives sshproxy / ssh / NERSC SFAPI
  • holds ONLY a scoped Globus token
  │  submit ShellFunction + reload_tasks  (Globus Compute over AMQP — credential-free hot path)
  ▼
Globus Compute endpoint
  • DEV:   LocalProvider on this machine (no scheduler)
  • NERSC: SlurmProvider on a Perlmutter login node
```

### 1.1 The `Facility` seam (what makes local-first work)

All facility-specific behavior sits behind one Protocol; the lifecycle/dispatch/cost code is facility-agnostic.

```python
class Facility(Protocol):
    name: str
    async def provision(self, profile: Profile) -> EndpointHandle: ...      # write config.yaml, start endpoint
    async def restart(self, endpoint_id: str) -> None: ...                  # reconcile (local: direct; NERSC: via broker)
    async def allocation_remaining(self) -> NodeHours | None: ...           # local: None/mock; NERSC: Iris/sacctmgr
    def config_template(self, profile: Profile) -> dict: ...               # LocalProvider vs SlurmProvider config.yaml
    async def canary(self, endpoint_id: str) -> CanaryResult: ...          # round-trip + version-parity check
```

- `LocalFacility` — `LocalProvider` config, no SSH, mock accounting. Drives M0–M4.
- `NerscFacility` — `SlurmProvider` config + `worker_init`/`address_by_interface`; `provision`/`restart` delegate SSH to the broker; `allocation_remaining` hits Iris. Built at M5.

---

## 2. The MCP tool contract

The model only ever sees abstract tools and structured results — never a block id, a credential, or a raw stderr dump. Built with `mcp.server.fastmcp.FastMCP` (pin `mcp>=1.23,<2`); tools are `async`; results are Pydantic models (auto structured-output).

| Tool | Args | Warm result | Cold result |
|---|---|---|---|
| `ensure_endpoint_up` | `profile?` | `{status:"up", block_state:"warm", session_spend, allocation_remaining, cert_expires_in?}` | `{status:"provisioning", est_wait_s, block_state:"cold"}` |
| `run_shell` | `command, session_id?` | `{phase:"complete", exit_code, stdout, stderr_snippet, cwd, block_state, session_spend}` | `{phase:"cold_start", task_handle, est_wait_s, block_state:"cold"}` |
| `read_file` / `write_file` | `path, content?` | structured result (cold-aware) | as above |
| `reset_session` | `session_id?` | `{ok:true, cwd}` | — |

**Result models** (`models.py`, Pydantic):

```python
class ShellOutcome(BaseModel):
    phase: Literal["complete", "cold_start"]
    exit_code: int | None = None
    stdout: str = ""                 # snippet, capped (see §5, §9)
    stderr_snippet: str = ""
    cwd: str | None = None
    block_state: Literal["warm", "cold", "provisioning"]
    session_spend: NodeHours
    allocation_remaining: NodeHours | None = None
    task_handle: str | None = None   # cold path: poll handle
    est_wait_s: int | None = None
    notice: str | None = None        # honest cold-start / re-MFA messaging
```

Every result carries `session_spend`/`allocation_remaining` and `block_state` so cost and warm/cold are visible without a separate tool the agent must remember.

`read_file`/`write_file` are **thin siblings of `run_shell`** (a `ShellFunction` doing a bounded `cat`/write through the same dispatch + session-shell + cold/warm machinery), not a separate subsystem — so file I/O inherits durable handles, cwd-awareness, and result-size caps for free.

---

## 3. Pilot lifecycle state machine (`lifecycle.py`)

```
                 ensure_endpoint_up / first run_shell
   ┌────────┐  ───────────────────────────────────►  ┌──────────────┐
   │  COLD  │                                         │ PROVISIONING │
   │ (b=0)  │ ◄──── idle TTL elapsed (cost.py) ───┐   │ sbatch/local │
   └────────┘                                     │   │ block sent   │
        ▲ scancel on no-activity                  │   └──────┬───────┘
        │                                         │          │ worker REGISTERS at interchange
   ┌────┴─────┐   no MCP tool activity N min   ┌──┴──────────▼──────┐
   │   IDLE   │ ◄──────────────────────────────│       WARM         │
   │  (warm,  │ ── next run_shell ───────────► │ dispatch loop      │
   │  unused) │                                └─────────┬──────────┘
   └──────────┘                                          │ endpoint dies / block lost
                                                         ▼
                                                  ┌──────────────┐
                                                  │     DEAD     │ ── reconcile (facility.restart,
                                                  │              │      same UUID) ──► PROVISIONING
                                                  └──────────────┘
```

**Two correctness rules (easy to get wrong):**

1. **Probe WARM vs COLD by worker/manager registration at the interchange, NOT by endpoint `Running` state.** The endpoint reports `Running` the instant the coordinator starts — before any worker block registers — so a state-keyed probe misroutes the first call into the fast path and then blocks unbounded. Implementation: treat the endpoint as WARM only once `≥1` worker is registered (query endpoint status / manager count); otherwise COLD/PROVISIONING.
2. **Reconcile is `Facility.restart` keyed on a stable UUID.** Locally: stop/start the `LocalProvider` endpoint directly. NERSC: the broker re-SSHes (or SFAPI) — human MFA may be required (mark non-autonomous off-NERSC).

`ensure_warm()` returns once a worker is registered; the warm-block lifetime is bound to the session and bounded by the idle TTL (§5).

---

## 4. Durable-handle dispatch (`dispatch.py`, `store.py`)

`run_shell` is a durable-handle task, **not** a blocking RPC — so a result survives MCP/session restart within the 30-min result TTL.

```python
async def dispatch(cmd: str, session: Session) -> ShellOutcome:
    wrapped = session_shell.wrap(cmd, session)          # §6
    block = await lifecycle.probe(session.endpoint_id)
    if block is COLD:
        fut = executor.submit(ShellFunction(wrapped))   # kicks provisioning
        store.save_task(fut.task_id, executor.task_group_id, session, cmd)  # PERSIST BEFORE RETURN
        return ShellOutcome(phase="cold_start", task_handle=fut.task_id, est_wait_s=..., block_state="cold")
    fut = executor.submit(ShellFunction(wrapped))
    store.save_task(fut.task_id, executor.task_group_id, session, cmd)      # PERSIST BEFORE RETURN
    res: ShellResult = await asyncio.wrap_future(fut, ...)  # bounded timeout (§9)
    return ShellOutcome(phase="complete", exit_code=res.returncode,
                        stdout=res.stdout, stderr_snippet=res.stderr, cwd=read_cwd(session), ...)
```

- One long-lived `globus_compute_sdk.Executor` per endpoint, held in the server lifespan (§8), so the AMQP subscription outlives individual tool calls.
- On startup/reconnect: `executor.task_group_id = stored_tgid; for f in executor.reload_tasks(): ...` re-attaches orphaned futures and fetches completed-but-unfetched results inside the 30-min window.
- **sqlite schema** (`store.py`, file at `${CLAUDE_PLUGIN_DATA}/hpc-bridge.db`):
  - `tasks(task_id PK, task_group_id, endpoint_id, session_id, cmd, submit_ts, status, result_fetched)`
  - `sessions(session_id PK, endpoint_id, cwd_path, env_path, created_ts)`
  - `blocks(block_id PK, endpoint_id, nodes, start_ts, end_ts, charge_factor)`  # cost ledger (§5)
- Per-session **`concurrency = 1`** at the dispatch layer (serialize `run_shell` per session) — prevents `.cwd`/`.env` races (§6) and doubles as the action-rate governor.

---

## 5. Cost control plane (`cost.py`) — the MCP server is the accounting authority

The warm profile disables Parsl idle scale-in (`min_blocks ≥ 1`), and `max_idletime` (120 s default) is shorter than agent think-time — so the server, which knows true agent-idle state, owns cost:

1. **Pre-flight allocation gate** — `ensure_endpoint_up`/any block-provision queries `Facility.allocation_remaining()` and **refuses the warm profile below a configurable floor** (default: force `batch` under 1000 node-hours). Local: gate is a no-op/mock.
2. **Session ledger** — debit `live_block_walltime × nodes × charge_factor` from the `blocks` table; surface `session_spend`/`allocation_remaining` on **every** tool result. Hard-stop + re-auth on budget exceed.
3. **Wall-clock idle TTL** — a lifespan asyncio task `scancel`s the block after N minutes of no MCP tool activity (default 5 min), independent of Parsl `max_idletime`.
4. **Per-block node cap** — `max_nodes_per_burst` (default 1–2) in the provision path, so an injected "benchmark the cluster 1000×" payload can't expand a block.
5. **Scale-to-zero / `batch` is the only no-questions default**; warm is a session-bound, budget-gated, logged opt-in.

---

## 6. Session-shell shim (`session_shell.py`) — filesystem-as-state continuity (rung 3)

`ShellFunction` runs each command in a fresh subprocess and has **no per-submit cwd argument** (working dir is engine-level), so cwd/env do not persist between calls. The shim makes them persist without model discipline:

- On `ensure_endpoint_up`, mint a sticky per-session dir (`${SCRATCH or local tmp}/.hpc-bridge/sessions/<session_id>/`), set endpoint `run_in_sandbox=False` for the interactive profile (isolation = per-session dir, not per-task sandbox).
- `wrap(cmd, session)` rehydrates then persists cwd+env around every call:
  ```sh
  cd "$(cat <s>/.cwd 2>/dev/null || echo <root>)" && source <s>/.env 2>/dev/null
  { <cmd>; }; rc=$?; pwd > <s>/.cwd; export -p > <s>/.env; exit $rc
  ```
- `reset_session` clears `.cwd`/`.env`. The `driving-hpc` skill teaches **one** rule: "you have a persistent session shell; relative paths work; call `reset_session` for a clean slate."
- **Cannot fix (defer to rung 4, state in skill):** background processes / `nohup &`, live `python -i`/debugger/tmux; node-local `/tmp` across nodes; `conda activate` hook *functions* (PATH survives via `export -p`, functions do not — fold `module load`/`conda activate` into `worker_init`).

---

## 7. Credential broker (`broker/`) — built at M5, no-op locally

Separate process (separate UID where a home exists — NERSC collaboration account; see analysis §6.3). The MCP server talks to it over a `0700` Unix-domain socket; secrets live only in the broker's address space.

- **Broker RPC** (JSON-lines over UDS): `ensure_login(facility) -> {ok, cert_expires_in}`, `restart_endpoint(endpoint_id) -> {ok}`. Never a passthrough `ssh`.
- **MFA via url-mode elicitation** (verified: `mcp>=1.23` `ctx.elicit_url`, Claude Code ≥ v2.1.76):
  1. MCP server asks broker for a one-time OTP URL (`127.0.0.1:rand`, nonce, ~120 s TTL, one-POST) → the **broker** hosts the page.
  2. MCP server `await ctx.elicit_url(message, url, elicitation_id)` — Claude Code opens it; user types password+OTP into the **broker's** page; the secret never touches the MCP/model process.
  3. Broker drives `sshproxy` (NERSC 24h cert) / `ssh`, signals completion over UDS; MCP server `ctx.session.send_elicit_complete(elicitation_id)` and returns `{status:"up"}`.
  - Guard with `ctx.session.check_client_capability(ElicitationCapability())`; **url-mode is not separately advertised** in capabilities, so fall back (PTY + `SSH_ASKPASS`) on a runtime error/older client.
- **NERSC preferred path:** SFAPI "Red" client (OAuth2 client-credentials, no runtime MFA, IP-pinned) for bootstrap/restart where the deployment satisfies the IP whitelist — removes the OTP prompt entirely.
- Harden: `ForwardAgent=no` (CVE-2023-38408), `IdentitiesOnly=yes`, broker-private ssh config, sockets `0600`/`0700`, ssh verbosity off.
- **`LocalBroker`** (M0–M4): a no-op implementing the same client interface (LocalProvider needs no SSH).

---

## 8. MCP server wiring (`server.py`)

```python
mcp = FastMCP("hpc-bridge", lifespan=lifespan)

@asynccontextmanager
async def lifespan(server) -> AsyncIterator[AppCtx]:
    facility = make_facility(env)                 # Local | Nersc
    store = Store(db_path); store.reload()        # re-attach orphaned tasks
    executor = Executor(endpoint_id=..., task_group_id=store.last_tgid)
    broker = make_broker(env)                     # LocalBroker | UDS client
    idle = asyncio.create_task(cost.idle_timer(...))
    try:
        yield AppCtx(facility, store, executor, broker, cost_ledger)
    finally:
        idle.cancel(); executor.shutdown(); store.close()

@mcp.tool()
async def run_shell(command: str, ctx: Context, session_id: str | None = None) -> ShellOutcome:
    app = ctx.request_context.lifespan_context
    return await dispatch(command, app.session(session_id))
```

- Tools reach long-lived state via `ctx.request_context.lifespan_context`.
- Launched by the plugin as `uvx hpc-bridge` (stdio).

---

## 9. Error handling

| Failure | Behavior |
|---|---|
| `.result()` would hang (endpoint unreachable) | bounded `timeout` → `{status:"endpoint_unreachable", action:"run ensure_endpoint_up", notice:...}` — never a silent hang to the tool-timeout |
| Cold start | honest `{phase:"cold_start", est_wait_s, notice:"allocating nodes…"}`; `ctx.report_progress` while waiting |
| `MaxResultSizeExceeded` (10 MB) | `run_shell` caps `snippet_lines`; teach (skill) "redirect verbose output to a file, read back in bounded chunks" as a hard wrapper |
| `MaxRequestsExceeded` (20/10 s) | `concurrency=1` makes this rare; throttle if a fan-out appears |
| Worker/client SDK version skew (issue #1197) | `Facility.canary` asserts python/dill/SDK parity at provision; gate `run_shell` on the compatibility check, not just "endpoint up" |
| Endpoint death mid-session | `DEAD → reconcile`; in-flight running task `.result()` raises (at-least-once) — dedup non-idempotent commands via a filesystem sentinel |
| Result lost (cold task completes while MCP dead > 30 min) | filesystem side-effects are the source of truth; report honestly |

---

## 10. Plugin packaging

```
hpc-bridge/                          # this repo IS the plugin
├── .claude-plugin/plugin.json       # { name, description, version }
├── .mcp.json                        # mcpServers.hpc-bridge = { command:"uvx", args:["hpc-bridge"],
│                                     #   env:{ HPC_BRIDGE_DB:"${CLAUDE_PLUGIN_DATA}/hpc-bridge.db" } }
├── skills/driving-hpc/SKILL.md      # cwd discipline (one rule), cold-start, result-size, safety norms
├── commands/hpc-connect.md          # /hpc-connect bootstrapper (asks account, queue; calls ensure_endpoint_up)
├── hooks/
│   ├── hooks.json                   # PreToolUse: credential-guard (Bash + mcp__hpc-bridge__*); audit (async)
│   └── credential-guard.sh          # deny inbound credential-looking strings (non-load-bearing backstop)
├── src/hpc_bridge/                  # server package (§ modules)
│   ├── server.py models.py lifecycle.py dispatch.py session_shell.py
│   ├── cost.py store.py endpoint.py
│   ├── facility/{__init__,local,nersc}.py
│   └── broker/{__init__,client,process,mfa_url}.py
├── tests/
└── pyproject.toml                   # mcp>=1.23,<2 ; globus-compute-sdk>=4 ; [project.scripts] hpc-bridge=hpc_bridge.server:main
```

- **Hook contract** (verified): PreToolUse reads `{tool_name, tool_input}` on stdin; denies via `{hookSpecificOutput:{hookEventName:"PreToolUse", permissionDecision:"deny", permissionDecisionReason:...}}`. The credential guard is a documented **non-load-bearing backstop** — the real boundary is the credential-free hot path (§1, §7).
- Local test install: `claude --plugin-dir ./hpc-bridge`.

---

## 11. Build milestones (local-dev-first)

| M | Deliverable | Verify | Real / mocked |
|---|---|---|---|
| **M0** | Skeleton: FastMCP over stdio; lifespan starts a `LocalProvider` endpoint via `globus-compute-endpoint`; `ensure_endpoint_up`→warm; plugin installs | `claude --plugin-dir`; tool returns `{status:up}` | all real, no facility |
| **M1** | `run_shell` via `ShellFunction` → `ShellOutcome` on warm local endpoint | integration test: `echo` round-trips | real |
| **M2** | Durable handles (persist-before-return, `reload_tasks` on restart); warm/cold probe by worker registration | kill MCP mid-flight; result recovers in TTL | real |
| **M3** | Session-shell shim + **acceptance test**: a bare relative path turn + a turn after >120 s think-time (forces scale-to-zero) | both succeed w/ honest messaging | real (this is the REPL gate) |
| **M4** | Cost plane (`session_spend` every result, idle-TTL scancel, node cap) + `driving-hpc` skill + credential-guard hook | unit: ledger/TTL; e2e: spend visible | facility accounting mocked |
| **M5** | `NerscFacility` (Slurm template + canary) + UDS credential broker + url-mode MFA + `/hpc-connect` over SSH/SFAPI — **gated by E1** | on Perlmutter: measured warm RTT; one-OTP-then-silent | real facility |

M0–M4 require **zero facility access**. M5 adds SSH/credential/facility behind the §1.1 `Facility` and §7 broker seams.

**M5 gate — experiment E1:** before building M5, measure the warm `submit → .result()` median + p99 for a no-op `ShellFunction` on a real Perlmutter warm block. If median ≳ 10 s, the REPL thesis weakens → revisit before investing in the facility layer. (E1–E6: [`../analysis/04-architecture-and-roadmap.md`](../analysis/04-architecture-and-roadmap.md) §7.)

---

## 12. Testing

- **TDD** throughout: write the failing test, then the module.
- **Unit:** lifecycle transitions (mock `Facility`), dispatch persist/reload (mock `Executor`), `session_shell.wrap`, cost ledger math + idle-timer, broker UDS protocol, hook deny logic.
- **Integration:** a pytest fixture that `configure`s + `start`s a real `LocalProvider` endpoint under a temp `GLOBUS_COMPUTE_USER_DIR`, runs real `ShellFunction`s, tears down. Covers M1–M4 end-to-end with no facility.
- **Acceptance (M3):** the REPL gate test above — the single pass/fail for "REPL vs flaky remote shell."

---

## 13. Dependencies & open questions

**Pinned:** Python ≥ 3.11; `mcp>=1.23,<2` (FastMCP, `elicit_url`; avoid the unreleased `MCPServer` rename); `globus-compute-sdk>=4`; `globus-compute-endpoint` (dev); `uv`/`uvx` launcher.

**Open / verify at implementation (do not treat as settled):**
- Globus token-cache filename (`storage.db` widely cited but unconfirmed) — confirm against installed SDK before the credential-guard references it.
- url-mode elicitation is not separately advertised in client capabilities — detect via runtime error + fall back (PTY+`SSH_ASKPASS`).
- `ShellFunction` subprocess-reuse semantics (fresh subprocess strongly implied, not doc-stated) — verify the shim assumptions on the local endpoint at M1.
- `PBSProProvider` field names (`queue`/`scheduler_options`) — confirm vs current Parsl when ALCF/OLCF come into scope.
- NERSC SFAPI "Red" client review latency, extended-cert lifetime, broker IP-whitelist on a login node — all open (analysis §9); M5 planning input.

**Deferred to later versions:** rung-4 live-process/kernel state (worker-pinning); multi-session concurrency & batch fan-out (`concurrency>1` — interactive and batch profiles need different dispatch semantics); autonomous self-heal off NERSC.
