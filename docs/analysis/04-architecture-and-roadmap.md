# Architecture Refinements, Open Questions & De-risking Roadmap

Status: analysis draft · 2026-06-02 · companion to `docs/vision.md`
Mandate: steelman + de-risk. This document assumes hpc-bridge *should* exist and concentrates on making its architecture survive contact with the Globus Compute internals, the four flagship facilities, and a CISO. It is constructive but refuses to restate any claim the adversarial verification marked CONTESTED or REFUTED. Claim IDs (C1–C10) are cited inline with their verification verdict.

This is the *technical-refinements + roadmap* document. The trust-boundary rewrite (C1), GTM rewrite (C5), and the full threat-model are owned by their respective companion documents; here they appear only where they force an architectural change.

---

## 0. Orientation: what is actually being built

Two facts from the research reframe the whole effort and should anchor every decision below.

1. **The core MCP-over-Compute layer already exists and is shipped by Globus Labs.** `github.com/globus/globus-mcp` already exposes `register_shell_command`, `submit_task`, `get_task_status`; `globus-labs/science-mcps` (arXiv 2508.18489) already ran MCP-driven agents over Globus Compute on HPC with it. hpc-bridge's differentiated surface is **five things that do not exist yet**: the SSH bootstrapper, the out-of-band credential broker, the reconcile loop, facility config templates + probing, and the safety/cost control plane. The vision's framing of `run_shell` as "a small delta to the existing globus-mcp server" (vision.md line 91) is the single most optimistic line in the plan — see C10/§1, where the Globus Compute internals engineer's review shows the durable-handle store, `reload_tasks()` reconnection, and version-probing are the *actual core* of v1.

2. **NERSC is the only facility where the v1 architecture works end-to-end today.** Every contested claim (C2, C3, C8, C9, C10) collapses to "true at NERSC, degraded-to-impossible elsewhere." This is not a footnote; it is the organizing constraint of the roadmap. The honest scope for Plugin v1 is **NERSC-first, single facility**, not "1–2 flagship facilities (Polaris/PBS, Perlmutter/Slurm)" as written in vision.md line 104 — Polaris/ALCF is materially harder on auth (C3) and policy (C8) and should move to a later, human-in-the-loop phase.

The refinements below are organized by the architectural surface they touch, then collapsed into a sequenced roadmap (§7) that front-loads the cheapest experiment able to kill or confirm each contested assumption.

---

## 1. The blocking `run_shell` / AMQP path and its real latency (C10: STANDS-WITH-CAVEATS)

### 1.1 What survives and what does not

The mechanism is real: a warm HTEX pilot block dispatches over ZeroMQ sub-second internally, and results stream over AMQPS immediately rather than by polling. The headline warm round-trip figure reported in the funcX work (low tens of ms) **is not a usable number for this project** — it was measured with the endpoint on a cloud VM co-located with the service (~1 ms service-to-endpoint hop), not on a leadership-class compute node behind a facility firewall (and the exact figure is unconfirmed for this purpose). There is **no published end-to-end warm `ShellFunction` latency for Perlmutter or Polaris.** The full agent loop also carries dill serialize/deserialize + base64, the command's own runtime, and the agent's LLM round-trips, none of which the micro-benchmark captures.

The realistic expectation, stated honestly: a trivial warm call is **plausibly low-single-digit seconds** round-trip (one wide-area hop each way through `compute.amqps.globus.org` + the internal interchange hop + serialization). That is genuinely interactive and genuinely better than the sbatch-poll latency of the SSH+Slurm MCP substitutes — but it is *asserted, not measured*, and the only real agent-on-HPC demo (science-mcps on Polaris) used a **1-minute status-poll loop**, not a seconds-scale REPL.

### 1.2 The blocking-call contract is wrong as specified

This is the load-bearing refinement of the whole document. The vision specifies `run_shell` as a single synchronous blocking RPC (vision.md line 91). Per the Globus Compute internals review, that contract **silently has two regimes and the cold one collides with MCP RPC lifetime and the 30-minute result TTL on the exact demo money-path (the first interactive turn).**

| Property | Warm regime | Cold regime (default `init_blocks=0`, `min_blocks=0`) |
|---|---|---|
| First-call latency | ~seconds | Full Slurm/PBS queue wait: **minutes to hours** (production-dataset mean per-task queue ≈ 415 s) |
| `.result()` behavior | blocks briefly, returns value | blocks **indefinitely** by default (no timeout unless passed) |
| Failure mode if MCP/session restarts mid-wait | in-memory future lost, but warm re-submit is cheap | in-memory future **and AMQP subscription gone**; queued result orphaned; **30-min result TTL** can silently discard it |

**Refinement — durable-handle pattern (this is core v1, not a delta):**

1. Run **one long-lived `Executor` per endpoint inside the MCP process**, so the AMQP subscription outlives individual tool calls.
2. On submit, **persist `{task_id, task_group_id, endpoint_id, cwd, submit_ts}` to a small sqlite store next to `storage.db` BEFORE returning** from the tool call.
3. **Probe warm-vs-cold by manager/worker registration at the interchange, NOT by endpoint `Running` state.** The interchange reports `Running` the instant the coordinator starts — before any worker block registers — so a probe keyed on endpoint state misroutes the first call into the fast path and then blocks unbounded anyway. This is the single most important correctness detail in §1.
4. **Branch the return shape on the probe:** warm path does a short bounded blocking wait and returns the value; cold path returns a structured `{phase: "cold_start_provisioning", task_handle, est_wait_s, block_state}` that the agent polls.
5. On MCP/reconcile-loop startup, call **`reload_tasks(task_group_id)`** to re-attach orphaned futures and fetch completed-but-unfetched results inside the 30-min window.
6. Put a **hard default timeout on `.result()`** so an indefinite hang becomes a structured `{status: "endpoint_unreachable", action: "run ensure_endpoint_up"}` message. The worst failure mode for interactive feel is not an error — it is a hang to Claude Code's tool timeout with no diagnostic.

### 1.3 The version-coupling trap (capability probing, not just config)

`ShellFunction` sidesteps serializing the *command string*, but the function object and its kwargs are still dill-pickled and deserialized under the **worker-side Python + `globus-compute-sdk` versions.** The MCP server runs the SDK on the laptop; the worker env is installed on the login node via `worker_init`. These drift independently when a facility bumps a default conda/python module or the plugin auto-updates the SDK (cf. issue #1197: dill/Python skew → total serialization failure with cryptic errors). Failure mode: works in the demo, then weeks later `run_shell` returns a deserialization error that looks nothing like "the command failed," and the agent tries to fix it by editing the user's code. **Refinement: gate `run_shell` on a worker-side SDK-version compatibility check at the capability-probing layer (§5), not just on "endpoint is up."**

### 1.4 Hard ceilings to enforce in the tool, not the skill

- **10 MB result payload, unrecoverable** (`MaxResultSizeExceeded`). After base64-of-dill overhead, raw stdout+stderr must stay under ~7 MB. The MCP server must cap `snippet_lines` (default 1000) and teach the "redirect verbose output to a file, read back in bounded chunks" pattern as a hard wrapper, not a soft instruction.
- **30-min result TTL** — see §1.2.
- **20 submissions / 10 s** rate limit — fine at one command/turn, but a parallel-fan-out agent trips it; the dispatch layer must serialize or throttle.

---

## 2. The reconcile / SSH-restart loop and UUID/config reuse (C2: REFUTED, "survives transparently" does not hold)

### 2.1 What is true and what is not

| Sub-claim | Verdict |
|---|---|
| Same UUID/config reused on restart (on-disk in `~/.globus_compute/<name>/`) | **TRUE, mechanical** |
| Transient network drop self-heals without SSH | **TRUE** — endpoint auto-retries AMQP reconnect for 1–20 h (7,200 attempts; v2.17.0). The SSH reconcile loop is *redundant* for the common case. |
| Queued (not-yet-running) task survives a graceful restart | **TRUE** — v3.3.0 does not ACK on shutdown, so AMQP retains the task for the next instance (at-least-once). |
| In-flight *running* task survives endpoint death | **FALSE** — `.result()` raises; the task does not resume. At-least-once, not exactly-once. |
| Restart is non-interactive | **TRUE only at NERSC** (sshproxy cert + ControlMaster). Human MFA required per restart at ALCF/OLCF/TACC (C3). |
| "Survives **transparently**" | **REFUTED** — warm block is lost on restart, in-flight work is lost, and the recovery latency can itself exceed the 30-min result TTL. |

### 2.2 Refinement: invert "cattle not pets" off the client, onto a facility-side self-heal primitive

The sysadmin persona's hardest blocker is decisive and architectural: **an uncoordinated client-side reconcile loop is a thundering herd.** When Arbiter2/cgroup reaps a coordinator under login-node contention, every MCP server independently SSHes back in and re-submits a pilot block at once; the restart re-spikes RSS and is reaped again. At 40 endpoints at 2am this is indistinguishable from a fork-bomb, and the deeper killer is **`slurmctld` RPC saturation** — N coordinators + N watchdogs each dripping `squeue`/`sacct`/`sbatch` against a single-threaded controller, made *worse* by scale-to-zero block churn. The cost-friendly default is scheduler-hostile.

**Concrete changes:**

1. **Move self-heal off the client onto a facility job.** At NERSC use **`scrontab`** (Slurm-managed cron, resilient across login-node failure, 30-day walltime) or the **workflow QOS** to host the watchdog; it restarts the endpoint in place with **no inbound SSH and no MFA**. The client does **read-only status checks only**. At OLCF the equivalent is a **looped chained PBS job** (the facility-sanctioned daemon pattern). This is the same redesign C8/§4 demands for policy reasons — it is the one change that satisfies both the sysadmin and the CISO.
2. Make the coordinator **reap-survivable**: `systemd --user` with lingering + a memory ceiling + pre-imported modules.
3. **Full-jitter backoff** (base 30 s, cap 30 min) plus a **restart budget** (back off above K restarts in N minutes) so any residual client retries cannot correlate into a storm.
4. Treat **aggregate scheduler-RPC load as a first-class constraint** alongside the cost knob. Add it to the open-risks list (vision.md §"Open risks").
5. Persist `task_group_id` (§1.2) so a reconnect can `reload_tasks()` rather than orphaning the in-flight result.

### 2.3 Honest restatement of C2

> *In scale-to-zero mode at NERSC, endpoint death costs only a re-queue the agent would have paid anyway; the watchdog re-submits via a scrontab/workflow-QOS job with no human MFA. The warm block and any in-flight task are NOT preserved across restart, and the first post-restart call incurs a cold start. At ALCF/OLCF/TACC, restart requires a human MFA event, so the endpoint is a pet: keep the coordinator alive (looped job / long walltime) rather than relying on disposable restart.*

---

## 3. Scale-to-zero & cost guardrails

### 3.1 The cost model is the binding constraint for the land-phase user

Facilities charge for **all reserved walltime regardless of utilization** (NERSC: walltime × nodes × QOS factor; 2-h minimum charge on preempt QOS; no idle grace). A single warm GPU node held 8 h/day ≈ **2,880 node-hours/year ≈ 14% of a 20K Director's Discretionary allocation**, and a NERSC Exploratory award (250 GPU node-hours) is gone from the **warm coordinator alone in ~31 days** before any science runs. The warm-block latency win (the only thing distinguishing hpc-bridge from the free SSH+Slurm substitute) is **economically disqualifying for exactly the small-allocation early adopters the GTM needs.** This is a real tension the vision's "one labeled knob" framing (vision.md line 63) understates.

Two mechanical traps the PI persona surfaced:

- **The `interactive`/warm profile requires `min_blocks ≥ 1`, which structurally disables idle scale-in** — Parsl never scales below `min_blocks`. Flipping the knob to warm **turns OFF the only cost protection**; it is not "scale-to-zero with a knob."
- At floored-charge facilities, the default `max_idletime=120s` is **shorter than agent think-time between turns**, so a human reading the transcript triggers teardown, and the next turn pays a fresh minimum charge. Scale-to-zero can cost *more* than staying warm when turn cadence straddles the 120 s window — and a silent mid-session scale-to-zero then makes the next call re-queue a cold block with no signal, so the agent may mis-plan (assume a hang, retry, provision a *second* block → double burn).

### 3.2 Refinement: the MCP server is the accounting authority, not the scheduler

Cost governance must be a **runtime control plane in the MCP server**, enforced in tool-dispatch logic outside the LLM — not a documented tradeoff or a YAML default.

1. **Pre-flight allocation gate.** `ensure_endpoint_up` and every block-provisioning call queries the facility accounting API (NERSC Iris REST / `sacctmgr`; SchedMD `sshare`/`sacctmgr show assoc`) and **refuses the warm profile if remaining < floor** (default: force `batch` under 1000 node-hours remaining).
2. **Session node-hour budget**, debited in real time (`live_block_walltime × nodes × charge_factor`), surfaced as an **`allocation_remaining` / `session_spend` field on EVERY tool result** (not a separate tool the agent must remember). Hard-stop + human re-auth on overage. (Closes the `allocation_remaining` open risk in vision.md line 96.)
3. **Wall-clock warm-block TTL independent of Parsl idle logic** — the server `scancel`/`qdel`s its own blocks after N minutes of no MCP tool activity (default 5 min). The server knows true agent-idle state; Parsl only sees worker queue depth. (Tune against turn cadence — too-aggressive TTL re-pays cold-start + floored charges.)
4. **Per-block size cap** in the provision path (default `max_nodes_per_burst = 1–2`) so an injected "benchmark the cluster 1000×" payload cannot expand a block (this is also the Threat-10 allocation-DoS control).
5. **`batch`/scale-to-zero is the only no-questions-asked default; warm is a budget-gated, logged, per-session opt-in.**

### 3.3 Residual the project must state plainly

The server's debit ledger is an **estimate** — facility accounting lags (post-job charging; Iris reconciliation trails minutes-to-a-day), and the agent and the user's own batch jobs draw the same allocation with no shared lock. More important is **attribution collapse in the ledger**: because the endpoint runs as the user, every agent-burned node-hour is indistinguishable in Iris/sacct from the PI's deliberate science, and idle warm-block burn reads in an annual review as "low GPU utilization / poor allocation stewardship" — the metric that gets renewals cut. *Mitigation:* emit a facility-ingestible signed usage tag (Slurm `--comment` / Globus telemetry field) so a future reconciliation can separate agent-driven from human-driven node-hours.

---

## 4. Filesystem-as-state sufficiency (C6: STANDS-WITH-CAVEATS)

C6 holds **for the file-centric edit→run→debug loop that is the literal v1 target**, and the discipline it depends on is mechanically reinforced (each `ShellFunction` is a fresh subprocess on a separate-process HTEX worker, so `cd`/env genuinely do not persist — the agent *must* anchor to a path). The existence proof (science-mcps on Polaris) ran real workloads this way. But the DX designer's review shows the vision's mitigation — "the skill teaches working-directory discipline" — is **necessary but not sufficient**, because the failure is silent, intermittent, and looks exactly like a code bug.

### 4.1 The two collision modes

- **Sandbox ON** (`run_in_sandbox=True`): every task runs in a fresh `tasks_working_dir/<TASK_UUID>`, so `cd build` in turn N is gone in turn N+1 and a relative `./a.out` vanishes → spurious `FileNotFoundError`.
- **Sandbox OFF**: all tasks share one `tasks_working_dir`, so two overlapping calls (or a stale warm worker from a prior session) stomp each other's files non-deterministically.

Relying on the model to prepend `cd /abs/path &&` to 100% of commands over dozens of turns is not reliable; the one turn it emits `python train.py` produces an error that sends the agent down a phantom debugging path. **This is the difference between "HPC as a REPL" and "a remote shell that randomly forgets where it is."**

### 4.2 Refinement: a session-shell shim owned by the MCP server, invisible to the model

1. Mint a **per-Claude-Code-session sticky working directory at a stable absolute path** the server controls (e.g. `${SCRATCH}/.hpc-bridge/sessions/<session_id>/cwd`), created on `ensure_endpoint_up`; set endpoint `working_dir` to it and **`run_in_sandbox=False`** for the interactive profile (isolation comes from the per-session dir, not per-task sandboxing).
2. `run_shell` does not send the bare command; it wraps it server-side to **rehydrate then persist cwd+env around every call**:
   ```
   cd "$(cat <session>/.cwd 2>/dev/null || echo <root>)" && source <session>/.env 2>/dev/null
   { <command>; }; rc=$?; pwd > <session>/.cwd; export -p > <session>/.env; exit $rc
   ```
   so a bare `cd build` then `make` in the next turn just works without model discipline.
3. **Serialize `run_shell` per session (concurrency=1) at the dispatch layer**, so two commands cannot race the `.cwd`/`.env` files. This doubles as the action-rate governor the security stream demands.
4. The skill then teaches **one enforced rule** ("you have a persistent session shell; relative paths work; call `reset_session` for a clean slate") instead of an unenforceable "always absolute-path everything."
5. Fold `module load` / `conda activate` into `worker_init` (or a sourced setup prefix) so the fresh-subprocess model does not re-pay heavy warmup interactively (§5).

### 4.3 Residuals the shim cannot fix (state plainly)

- Background processes / `nohup foo &`, an open `python -i`, a debugger, tmux — genuinely cannot be held without worker-pinning (deferred). `conda activate` relies on shell *functions* that `export -p` does not capture, so PATH may say "activated" while hook functions are missing.
- Node-local `/tmp` state is lost if a different node picks up the next call (shim covers only shared-FS state).
- concurrency=1 caps throughput — fine for a REPL, a regression for batch fan-out. **The interactive and batch profiles need genuinely different dispatch semantics, not just different block sizing.**
- Filesystem-as-state is also the cross-session prompt-injection persistence channel (Threat 8): a poisoned file written in session N is read in N+1. Functional sufficiency ≠ security sufficiency; pair with provenance on externally-sourced content. (Owned by the threat-model doc; flagged here because it is the same design property.)

### 4.4 Acceptance test for the "HPC as a REPL" milestone

The demo must include **(a) one turn that uses a bare relative path** and **(b) one turn separated from the prior by >120 s of think-time** (forcing scale-to-zero), and **both must succeed with honest latency messaging.** If that passes, you have a REPL; if not, you have the flaky remote shell the thesis was meant to kill.

---

## 5. Config templates + capability probing (C7: STANDS-WITH-CAVEATS, "probing solves it" is the weak leg)

The config surface decomposes into a **probeable layer** and a **hand-authored layer**; the claim's contrast ("templates + probing **rather than** hand-written per-facility skills") is partly a relabeling, because a hand-authored, version-pinned, actively-maintained per-facility template **is** the per-facility skill in YAML form.

| Field | Discoverable? | Mechanism |
|---|---|---|
| scheduler type (Slurm vs PBS) | **Probeable** | `sbatch` vs `qsub` on PATH |
| account | **Probeable** | `sacctmgr show assoc user=$USER -P` |
| queue/partition, walltime caps, node bounds | **Probeable** | `sinfo`/`scontrol show partition`; `qstat -Q` |
| launcher, block sizing | **Templateable** | facility constants — Globus ships ~14 example configs incl. Perlmutter/Polaris |
| `address_by_interface` (ifname) | **NOT reliably probeable** | Globus docs: "no one answer… educated guesses and trial and error" (hsn0/ib0/bond0/…); wrong value silently breaks worker→interchange routing |
| `worker_init` / reproducible env | **NOT probeable** | depends on the **client's** exact Python+dill+SDK versions, which a login-node probe cannot see |

### 5.1 Refinements

1. **Stop probing the reproducible environment; pin it.** Ship the worker inside an **Apptainer/Shifter/Podman-HPC image** fixing Python + `globus-compute-endpoint` + dill identically on client and worker, OR have the bootstrapper **create and name the venv it will activate** (so `worker_init` activation is *generated, not guessed*). This eliminates the issue-#1197 version-skew class entirely and converts `worker_init` from "unknowable user env" into "a known artifact the plugin created." (Also fixes the §1.3 version-coupling trap.)
2. **Replace "probing" with probe-then-validate (fail-fast canary).** After generating `config.yaml`, submit a trivial round-trip `ShellFunction` (`echo` + `import globus_compute_sdk`; assert a worker connects back within N s; assert version parity) through the warm path and only report `ready` on success. This turns Globus's documented "trial and error" for ifname into **automated trial-and-error**, and catches stale module names at provision time instead of at first real task.
3. **Narrow the claim honestly:** structural fields (scheduler/account/queue/launcher/block-sizing) are template+probe solvable; the environment is solved by pinning/containerization; the template library + canary expectations ARE per-facility content, curated as data rather than prose.
4. **Scope to the ~5 facilities with maintained templates**; "unsupported facility ⇒ manual config" fallback. Refresh templates against facility drift (e.g. Polaris's conda module rename) via a telemetry-driven update channel.

---

## 6. The credential-boundary process design (C9: REFUTED — "effectively closed" via scrubbing does not hold)

### 6.1 The boundary is drawn in the wrong place

The vision isolates the **LLM context** from the **MCP-server process** (vision.md line 71). But every credential lives at the **user's UID**, and `run_shell` executes **as that same UID**. A prompt-injected agent never needs the secret to "reach the LLM" — it issues `run_shell("base64 -w0 ~/.ssh/nersc-cert.pub")` or `run_shell("sqlite3 ~/.globus_compute/storage.db .dump")` and the credential exfiltrates through the **tool's own output channel** (base64-encoded → no key-shaped string to match), or server-side via Globus Transfer with nothing returning to the model at all. **Process separation defeats accidental logging; it is null against the primary tool the agent is given.** Both named controls guard the wrong direction: the `PreToolUse` hook inspects the *inbound* call (a clean `cat`/`base64`/`ssh -S` command with nothing credential-looking to reject), and regex output-scrubbing is the exact pattern-filtering the research shows is readily defeated by base64/hex/split-across-calls evasion (OWASP LLM Top-10; arXiv 2604.03070 finds 73.5% of leaks flow via stdout).

The ControlMaster socket is **worse than a secret**: it is reusable authenticated state, so `ssh -S <ctl-socket> login.facility <cmd>` rides the already-authenticated connection with **zero credential and zero MFA**. The "authenticate once per session" property the vision sells as UX is, cryptographically, the conversion of a per-action MFA gate into a per-8-hour MFA-exempt bearer capability sitting in the filesystem.

### 6.2 Refinement: move the boundary from "separate process" to "separate principal + credential-free hot path"

Three trust domains, not two:

| Domain | Role | Holds credentials? |
|---|---|---|
| LLM / Claude Code client | reasons, calls tools | never |
| MCP server | drives the Globus Compute **hot path** | only a narrowly-scoped Globus Auth bearer token (task-submit to one endpoint UUID) |
| **Credential broker** (separate UID / `systemd --user` with private `$XDG_RUNTIME_DIR`) | the **only** thing that touches SSH/MFA/cert/password | yes, kernel-isolated from the agent UID |

1. **Make the hot path credential-free by construction.** The interactive loop (`run_shell`/`read_file`/`write_file`) rides **Globus Compute over Globus Auth** — a bearer token, never SSH material. SSH is touched **only by the broker, only at bootstrap/repair.** In steady state the LLM-driven channel carries no SSH secret, so `{status: up}` is genuinely empty of secrets rather than scrubbed-clean. This is the single most important property and it is *achievable* because Compute already authenticates via Globus Auth, not SSH.
2. **Run the broker as a distinct principal** so `/proc` and filesystem perms become a kernel-enforced inter-UID boundary. The broker holds the sshproxy cert / agent / ControlMaster socket in a 0700 dir at a path the `run_shell` task env cannot resolve. It exposes **only a narrow restart-only RPC** (`ensure_endpoint_up`, `restart_endpoint`) — never a passthrough `ssh`.
3. **Harden the broker's SSH:** `ForwardAgent=no` (CVE-2023-38408), `IdentitiesOnly=yes`, dedicated agent socket never exported into any task env, short `ControlPersist`, ssh verbosity OFF in production.
4. **Construct tool results from an allow-list of typed fields, never raw child stdout/exception tracebacks.** This is what makes leakage structurally impossible rather than filtered.
5. **Demote the `PreToolUse` hook and output scrubber to documented NON-load-bearing backstops** (exact-match redaction of tokens the broker itself loaded is the one scrubbing variant worth keeping). The vision's claim that these "make the credential-exposure risk effectively closed" must be deleted.

### 6.3 The per-facility broker-home problem is a v1 gate, not an open risk

This is the decisive constraint and it reinforces NERSC-first:

- **NERSC:** the broker has a real private home — **collaboration accounts** (`collabsu`/`sshproxy -c`) give a separate principal. Full-strength boundary.
- **ALCF/OLCF:** **no documented service-account path**, so the broker has nowhere private to run, *and* the shared global filesystem (Lustre/GPFS mounts `$HOME` on the worker) means the worker can read `~/.ssh` and `~/.globus_compute` unless actively mount-namespaced away. OLCF additionally **bans ControlMaster**, so the "secure-ish at NERSC" design is impossible there. **Mark ALCF/OLCF as "human-in-the-loop restart only, no autonomous reconcile" until a broker principal exists** — do not paper over them with the same self-heal claim.

### 6.4 Irreducible residuals (must be stated to facility security, verbatim, not optimized away)

1. **The agent runs as you on a host holding your credentials.** `run_shell` legitimately executes where `~/.ssh/*`, the sshproxy cert, `storage.db` tokens, and kerberos caches sit readable by the user's UID. A prompt-injected agent can `cat` and exfiltrate them via Globus Transfer (unencrypted, unmonitored, tens of Gbps). **No broker isolation removes this** — it is the irreducible consequence of shell-as-you. hpc-bridge does not leak the SSH password into the LLM, *but it does give an injectable agent read access to the user's own credential files.* This is the honest one-sentence statement a CISO needs.
2. The scoped Globus bearer token is necessarily on the hot path; scope it as narrowly as Compute permits.
3. The one-time MFA entry moment is observable to anything co-resident under the same UID; minimize frequency (24-h certs) and use OS secure-input.

### 6.5 How Claude Code drives MFA safely (design is now clean)

As of Claude Code v2.1.76, **MCP URL-mode elicitation** is the primary OTP channel: the broker stands up a single-use loopback page (127.0.0.1, nonce, ~120 s TTL, one-POST-then-dead), emits `elicitation/create mode:"url"`, the user types password+OTP into the broker's own page, and the spec guarantees the secret never passes through the LLM context or MCP client. The model relays only `{status: "auth_required", facility}` and later sees `{status: up, cert_expires_in: "23h"}`. Fallbacks: `SSH_ASKPASS` + `SSH_ASKPASS_REQUIRE=force` (broker-owned PTY, no browser); form-mode elicitation is **last-resort only** (spec forbids it for passwords) with an `ElicitationResult` redaction hook. **The vision's security section (line 71) currently names only `SSH_ASKPASS`; update it to name URL-mode elicitation as primary.** At NERSC, prefer the **Superfacility API "Red" client** (OAuth2 client-credentials, no runtime MFA, IP-whitelisted, can submit jobs + run login-node commands) for the bootstrap/restart path — it removes the OTP prompt entirely and is the cleanest realization of credential-free reconcile.

---

## 7. Sequenced de-risking roadmap

Principle: **front-load the cheapest experiment that would kill or confirm each contested claim before building on it.** Ordered by (risk × cost-to-discover-late) ÷ cost-of-experiment. Each experiment is something a single engineer can run in days against NERSC, before committing to the architecture it gates.

### 7.1 The kill-first experiments (do these before Plugin v1)

| # | Contested claim | Cheapest experiment that kills/confirms it | Kill criterion |
|---|---|---|---|
| E1 | **C10** — warm loop is seconds-scale on real nodes | On a Perlmutter warm pilot block, measure `submit→.result()` median + p95/p99 for a no-op `ShellFunction`, including the AMQP round-trip + dill, sustained across time-of-day. **No code, ~1 day.** | If median ≳ 10 s or p99 in tens of seconds, the core differentiator vs SSH+Slurm is gone → rethink. |
| E2 | **C10/§1.2** — blocking contract durability | Kill the MCP process mid-`.result()` on a cold task; confirm `reload_tasks(task_group_id)` recovers the result within the 30-min TTL. | If results are unrecoverable on restart, the durable-handle store is mandatory before any demo. |
| E3 | **C9/§6.3** — broker home exists | Confirm a NERSC **collaboration account** can host the broker (`sshproxy -c`, separate UID, 0700 socket dir the worker UID cannot read). | If no separate principal is available, the credential boundary is intra-UID only → the "effectively closed" claim must be retired facility-wide. |
| E4 | **C2/§2.2 + C8** — scheduler-safe self-heal | Stand up the watchdog as a **scrontab/workflow-QOS job**; induce a coordinator reap; confirm in-place restart with no inbound SSH/MFA. Then run **25 endpoints on a dev login node and induce a simultaneous reap** to observe `slurmctld` RPC load. | If the herd saturates the scheduler or restart needs SSH, the client reconcile loop must be removed before scale. |
| E5 | **C6/§4.4** — REPL illusion holds | Run the §4.4 acceptance test (bare relative path + >120 s think-time turn) with the session-shell shim. | If cwd/env do not survive, `run_shell` is a flaky remote shell, not a REPL. |
| E6 | **C7/§5** — config is solvable | Provision a Perlmutter endpoint from a template + the fail-fast canary; deliberately use a stale module name and confirm the canary catches it at provision time. | If ifname/worker_init cannot be made reliable via pin+canary, scope shrinks to hand-held configs. |

E1, E3, and E4 are the three that can *kill the project's differentiation* (latency moat, credential boundary, facility acceptance) and are all runnable at NERSC in under a week each. They should precede any plugin packaging work.

### 7.2 Mapping to the vision's phased plan — what to change

| Vision phase | Keep | Change |
|---|---|---|
| **1. Demo ("HPC as a REPL")** | The demo as the thesis-maker | **Build the durable-handle store + warm/cold probe-by-registration + session-shell shim into the demo** — they are core, not a later delta (§1.2, §4.2). Gate the milestone on the §4.4 acceptance test. Run E1/E2/E5 first. |
| **2. Plugin v1 (1–2 flagship facilities)** | One-line bootstrap, `interactive`/`batch` profiles | **Scope to NERSC only.** Drop Polaris/ALCF from v1 — its auth (C3), policy (C8), and broker-home (C9) all fail or are grey. Add the **credential broker as a separate principal** (§6.2) and **URL-mode elicitation** (§6.5) to v1, not later. Make **`batch`/scale-to-zero the only default**; warm is budget-gated opt-in (§3.2). |
| **3. Self-heal + cost guardrails** | Reconcile loop, scale-to-zero, allocation reporting | **Move self-heal off the client onto scrontab/workflow-QOS** (§2.2). Promote cost guardrails from "reporting" to a **runtime control plane**: pre-flight allocation gate, per-tool `session_spend` field, wall-clock warm-block TTL, per-block size cap (§3.2). Add **scheduler-RPC budget** as a first-class constraint. |
| **4. Catalog + telemetry** | Capability descriptors, facility-usage view | **Replace probing-as-discovery with pin + fail-fast canary** (§5). Make telemetry **opt-in and user-visible at install** (forced by the GTM rewrite — owned by the C5 doc; flagged here because the capability-descriptor work touches it). |
| **5. Marketplace + facility engagement** | Plugin publication | **Gate launch on one on-record NERSC reference**, not install count. Lead with the workflow-QOS/Spin sanctioned path. (GTM spine rewrite owned by the C5 companion doc.) |

### 7.3 New open risks to add to vision.md §"Open risks"

The vision's open-risks list (lines 95–99) is good but missing four items the verification surfaced:

1. **Correlated retry storms + `slurmctld` RPC saturation** from N client reconcile loops + watchdogs; scale-to-zero raises RPC load even as it cuts cost (§2.2).
2. **Blocking-call durability** — orphaned results on MCP/session restart vs the 30-min TTL; requires `task_group_id` persistence (§1.2).
3. **Worker/client SDK version skew** silently breaking `run_shell` with deserialization errors that masquerade as code bugs (§1.3).
4. **No private broker home at ALCF/OLCF** — the credential boundary is intra-UID there; autonomous reconcile is not safe until a service-account principal exists (§6.3).

---

## 8. One-paragraph honest summary for the project owner

The personal-endpoint *privilege* model is sound and the warm-pilot latency idea is real and genuinely differentiated — but four architectural surfaces the vision treats as small are actually the core of v1: (1) `run_shell` is not a synchronous RPC, it is a durable-handle task with a registration-keyed warm/cold probe and `reload_tasks()` recovery; (2) self-heal must move off the client onto a scrontab/workflow-QOS job or it becomes a scheduler-saturating thundering herd; (3) the credential boundary must move from "separate process" to "separate principal + credential-free Globus-Auth hot path," which only has a home at NERSC today; and (4) cost governance must be a runtime control plane, because the warm profile that justifies the product bankrupts the small-allocation user the GTM needs. All four are confirmable or killable at NERSC in under a week each (§7.1) — run those experiments before packaging. Scope Plugin v1 to NERSC alone; ALCF/OLCF are a later, human-in-the-loop phase, not a co-launch.
