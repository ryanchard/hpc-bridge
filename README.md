# hpc-bridge

**Extend AI coding agents into HPC.** A Claude Code plugin (FastMCP server) that gives an
agent interactive, low-latency access to real supercomputer compute — by standing up a
*personal* Globus Compute endpoint on an HPC login node over SSH, then dispatching shell
commands to a warm compute block over Globus Compute's credential-free AMQP path.

Globus Compute is the engine; `hpc-bridge` is the agent-facing packaging, the bootstrap, and
the runtime that makes a batch supercomputer feel like a REPL.

---

## Status

Actively developed. **Proven end-to-end on a real facility (Purdue Anvil / Slurm)** and on a
local dev endpoint. The agent stands up an Anvil endpoint over key-based SSH, runs commands on
a compute node, and tears it down — releasing the allocation. Suite: **90 passed, 2 skipped**,
ruff clean.

Current direction is **discovery-first** (the agent probes a facility and derives its config,
rather than us hand-writing a class per machine) — see the design docs below.

---

## The five MCP tools

All return structured Pydantic results; failures come back as structured outcomes, never raw
crashes.

| Tool | What it does |
|---|---|
| `ensure_endpoint_up()` | Provision/probe the personal endpoint. Reports `up` only once a **worker answers a canary** (not merely that the manager is online); otherwise `provisioning` (and the probe kicks a cold block). |
| `run_shell(command, session_id="default")` | Run a shell command on the warm compute block → `{phase, exit_code, stdout, stderr_snippet, block_state, session_spend}`. Cold endpoint → `cold_start` (no hang). |
| `reset_session(session_id="default")` | Clear a session's persisted working directory + environment. |
| `stop_endpoint()` | Tear down the endpoint, release its Slurm block, reset session state. |
| `login_shell(command)` | Run a **read-only** command on the login node over SSH for *discovery* (`sinfo`, `sacctmgr`, `module avail`) — no block, no allocation, no cost. SSH facility only. |

---

## How it works

Two channels, by design: **SSH is control-plane only** (bootstrap/teardown); the **work hot
path is Globus Compute over AMQP** (a scoped Globus Auth token, never SSH material).

```
Claude Code (laptop)
  │  MCP tools (stdio): ensure_endpoint_up · run_shell · reset_session · stop_endpoint · login_shell
  ▼
hpc-bridge MCP server          ── SSH (key-only, bootstrap/teardown ONLY) ──┐
  • provision + worker canary                                              ▼
  • session-shell shim · spend clock                          Endpoint manager (login node)
  • asyncio.Lock serializes provision/swap/teardown            │ submits a Slurm block on first task
  │  ShellFunction over AMQP (credential-free hot path)        ▼
  ▼                                                          Warm block on a compute node
Globus web service ── AMQP ──► Endpoint  ◄───────────────────  │ runs the command
                                                               ▼  state persists via shared FS
                                                            result ── AMQP ──► back to the agent
```

**The warmth lifecycle (and why the canary matters).** `ensure_endpoint_up` provisions the
endpoint if needed and checks `manager_online` (a cheap Globus web query) — but that only
reflects the login-node *manager*, not a worker. In the v4 MEP model the first task forks a
User Endpoint Process and submits a Slurm block, so the manager reads "online" while the next
command would cold-start. The fix is a **worker-registration canary**: a trivial `ShellFunction`
submitted through the same long-lived Executor real work uses, with a short budget. A returned
result ⇒ truly warm; a timeout ⇒ still `provisioning` (and the submit *kicks* the block). A 45 s
TTL keeps the hot path from paying the round-trip every call. This is what makes "is it warm?"
honest and keeps `run_shell` from dispatching into a 124 timeout.

**Cost control.** The load-bearing net is **idle block release**: the Slurm provider runs with
`min_blocks=0` + `max_idletime` (default 600 s), so the compute node — the thing that costs
allocation — self-releases after the last task (validated live on Anvil). A spend clock
(`session_spend`, driven by true worker presence and accrued across warm intervals) is surfaced
on every result; `stop_endpoint` is the explicit exit. `charge_factor` defaults to `0.0` (free
local dev).

**Session continuity.** `ShellFunction` runs each command in a fresh subprocess, so the
server wraps commands to rehydrate+persist `cwd`/env in `<scratch>/sessions/<id>/{.cwd,.env}` on
the shared filesystem — a bare `cd build` then `make` just works. `session_id` is
allowlist-validated and the command is base64-carried so it can't break out of the wrapper.

### Remote bootstrap

**Credential seeding.** On the first connect to a remote facility, hpc-bridge builds a
minimally-scoped `storage.db` locally — only the Globus Compute and Auth tokens an endpoint
needs, refresh tokens included — from your existing `globus-compute-endpoint login`, and ships
it to the remote `~/.globus_compute/storage.db` (directory `0700`, file `0600`). The local
trimmed copy is written to a temp directory and wiped after transfer. Subsequent sessions reuse
the remote credential; seeding is skipped if `whoami` succeeds.

**Login-node pinning.** The endpoint manager daemon is a detached process that lives on ONE
login node, but HPC SSH aliases typically round-robin across many nodes. hpc-bridge records the
resolved FQDN (captured in the same SSH connection that starts the daemon, before the alias can
send you elsewhere) in `~/.hpc-bridge/endpoints.json` (`0600`) and reconnects straight to that
node next session. To reset a stale pin — e.g. after a node goes down — delete that file.

**Resource shapes.** One templatable endpoint serves both `shape="login"` (a `LocalProvider`
running on the login node — lightweight, no allocation, not billed) and `shape="slurm"` (a
`SlurmProvider` block — heavy compute, billed, idle-released) via per-task
`user_endpoint_config`. `run_shell(command, shape=...)` selects the target; sessions (cwd/env)
persist independently per shape. The rendered provider is parameterized — `max_workers_per_node`,
`nodes_per_block`, `max_blocks`, and `available_accelerators` (GPU count or device IDs) default
from the `MachineProfile` and can be overridden per task via `user_endpoint_config`.

**Teardown.** `stop_endpoint` stops the endpoint, releases the Slurm block, and removes the
login-node pin from `endpoints.json` so a stale FQDN is not reused next session. The seeded
remote credential (`storage.db`) is kept by default so a later session can reconnect without
re-seeding; the facility's `teardown` method accepts an opt-in `wipe_credentials=True` to also
remove it from the remote host.

### The `Facility` seam

Everything machine-specific sits behind one protocol (`provision` / `manager_online` /
`config_template`); the runtime is facility-agnostic.

- **`LocalFacility`** — `LocalProvider`, no SSH, for local dev (Linux only).
- **`SlurmFacility`** — provisions a Globus Compute endpoint on a remote Slurm login node over
  key-based SSH. Per-facility data lives in a `MachineProfile` (`anvil_profile` is the first).

### Module map (`src/hpc_bridge/`)

`server.py` (FastMCP tools, lifespan, the provision→canary→dispatch→spend flow + the lock) ·
`runner.py` (`GlobusRunner`: `ShellFunction` dispatch + the `canary`) · `dispatch.py` (translate
timeouts/oversized-results/task-failures into structured outcomes) · `lifecycle.py`
(provision + manager-level probe) · `session_shell.py` (cwd/env shim) · `cost.py`
(`estimate_spend`, `cap_output`) · `profile.py` · `endpoint.py` (local `globus-compute-endpoint`
CLI wrapper) · `facility/{base,local,remote}.py` · `models.py`.

---

## Run / develop

Requires Python ≥3.11 and [`uv`](https://docs.astral.sh/uv/). Globus Compute is an optional
`integration` extra (unit tests are hermetic and don't need it).

```bash
uv sync --extra dev
uv run pytest -q                 # 90 passed, 2 skipped
uv run hpc-bridge                # run the MCP server standalone (stdio)
claude --plugin-dir .            # install into Claude Code for local testing
```

**Config (env vars):**

- `HPC_BRIDGE_FACILITY` — `anvil` for the remote Slurm facility; unset = local dev.
- Anvil requires `HPC_BRIDGE_SSH_USER`, `HPC_BRIDGE_SSH_KEY`, `HPC_BRIDGE_ACCOUNT`
  (optional `HPC_BRIDGE_SSH_HOST`, `HPC_BRIDGE_PARTITION`).
- `HPC_BRIDGE_PROFILE` (`interactive`|`batch`), `HPC_BRIDGE_SCRATCH`, `HPC_BRIDGE_USER_DIR`,
  `HPC_BRIDGE_CHARGE_FACTOR`.
- `HPC_BRIDGE_ENDPOINT_ID=<uuid>` — **BYO endpoint**: dispatch to an existing endpoint and skip
  local provisioning. Required on macOS/Windows, where `globus-compute-endpoint` (the local
  daemon) can't run; the SDK dispatch path still reaches a remote/Linux endpoint by UUID.

> **globus-compute-endpoint 4.x invariants** (learned by live debugging, encoded in the code):
> `start` runs an EndpointManager — `config.yaml` must be **engine-free**, the engine lives in
> `user_config_template.yaml.j2`; `configure` forces `--multi-user false` (personal, no
> identity-mapping); `start` needs `--detach`; `get_endpoint_status` returns only
> `{"status":"online"}` (manager, not worker); `ShellFunction` runs `cmd.format()`.

---

## Security posture (current)

The hot path carries a scoped Globus Auth token, **never SSH material**; SSH is key-only
(`BatchMode`, `IdentitiesOnly`) and used only to bootstrap/teardown. `session_id` is
allowlist-validated (no traversal); commands are base64-carried into the shim; session files are
`0600`. A `PreToolUse` credential-guard hook is a documented **non-load-bearing backstop**.

Honestly deferred (see [`docs/analysis/02-risk-register.md`](docs/analysis/02-risk-register.md)
and [`03-ssh-mfa-interactive-access.md`](docs/analysis/03-ssh-mfa-interactive-access.md)): a
separate-UID credential broker for OTP facilities, and the irreducible "the agent runs as you and
can read your own credential files" prompt-injection surface — to be addressed with hard controls
before any production facility deployment.

---

## Documentation map

- **This README** — current state: what's built, how it works, how to run it.
- **Workflow & gaps** — [`docs/design/workflow.md`](docs/design/workflow.md): the end-to-end flow phase by phase, marked built / partial / not-yet, with the prioritized next steps.
- **Direction** — [`docs/design/agent-tool-boundary.md`](docs/design/agent-tool-boundary.md)
  (what belongs to a tool vs the agent's judgment) and
  [`docs/design/facility-discovery.md`](docs/design/facility-discovery.md) (discovery-first,
  uncached facility profiles — where this is going).
- **Core mechanism** — [`docs/design/pilot-job-repl-lifecycle.md`](docs/design/pilot-job-repl-lifecycle.md)
  (why a warm pilot block = an interactive REPL).
- **Background analysis** (the *why* behind decisions, not current state) —
  [`docs/analysis/`](docs/analysis/README.md): viability, risk register, the credential/MFA
  security model, and the architecture + capability-probing reasoning the discovery angle builds on.
- **Vision** — [`docs/vision.md`](docs/vision.md): the north-star pitch (personal endpoints as the
  bottom-up path that pulls MEPs into facilities).

---

## What's deferred / not built

Worker-pinned live-process state (a true in-memory kernel); a separate-UID credential broker +
MFA elicitation (for OTP facilities like NERSC/ALCF/OLCF); durable task handles that survive an
MCP restart; facilities beyond Anvil + local; and the **discovery pipeline** itself — the next
concrete step (a thin Stage-2 slice: gce-version preflight, login-node pinning, `$SCRATCH`
discovery), per [`facility-discovery.md`](docs/design/facility-discovery.md).
