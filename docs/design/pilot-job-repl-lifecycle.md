# Pilot-Job REPL Lifecycle

Status: design note · 2026-06-02 · companion to [`../vision.md`](../vision.md) and [`../analysis/`](../analysis/README.md)

**Purpose.** Name the core mechanism hpc-bridge actually sells — *a pilot job's lifecycle, managed as an interactive REPL* — and define the lifecycle state machine the plugin must implement. This is the bridge from the analysis to the v1 plugin design: §1–§5 are the concept; §6–§9 are the build.

---

## 1. The unlock: pay the queue tax once

The thing that kills interactivity on HPC is the **queue tax** — every `sbatch`/`qsub` pays minutes-to-hours of wait before a single line runs. A *pilot job* is the late-binding trick that pays that tax **once**: submit one placeholder job that grabs an allocation, hold it warm, and dispatch many small tasks *inside* that allocation at dispatch-overhead latency.

| | Batch lifecycle | Pilot lifecycle |
|---|---|---|
| Shape | `submit → queue → run once → exit` | `submit once → queue once → hold warm → dispatch N → release` |
| Latency per task | full queue wait (minutes–hours) | ~round-trip (target: low single-digit seconds) |
| Feel | fire-and-forget | **REPL** |

That amortization *is* the REPL. In Globus Compute / Parsl-HTEX terms, an endpoint's **block is a pilot job**; the warm block is the interactive surface. This is the heart of the product — more central than "we use Globus Compute" (Compute is *how* we implement it; the pilot lifecycle is *what* we sell — see [`../analysis/01-viability-and-opportunity.md`](../analysis/01-viability-and-opportunity.md) §3).

---

## 2. Bind the pilot lifecycle to the session lifecycle

The organizing principle of the whole plugin: **the pilot's lifecycle should be bound to the agent's session lifecycle.** A REPL has a lifecycle — start, interactive loop, idle, exit — and we map it straight onto the block.

| REPL / session phase | Pilot-job action | Cost |
|---|---|---|
| Session start | provision a block, warm it (the one cold-start we pay) | begins |
| Active turns | dispatch onto the warm block — seconds each | accruing |
| Think-time / idle | **scale-to-zero on an idle TTL** | paused |
| Session end / crash | release the block; reconcile re-warms on next turn | stops |

This binding is also the answer to the cost objection. We do **not** hold the pilot forever; we hold it *for the session*, bounded by an idle timer. **Cost is just the lifecycle's shadow** (§5) — the lifecycle framing and the cost-governance fix are the same idea.

---

## 3. How much REPL? The continuity ladder

"REPL-like" is a ladder, not a binary. The pilot job gives the substrate; how *much* REPL depends on how much state survives between turns. There is a rung the vision quietly defers — name which rung v1 targets so "REPL" doesn't over-promise a Jupyter kernel.

| Rung | What's warm | What persists turn-to-turn | Gives you | v1? |
|---|---|---|---|---|
| 1 | **Allocation** (the pilot block) | nodes reserved, no re-queue | kills queue latency | ✅ |
| 2 | **Workers** (HTEX) | worker processes ready | kills worker-spawn latency | ✅ |
| 3 | **Filesystem-as-state** | cwd, files, build artifacts (shared FS) | **edit→run→debug REPL** | ✅ **← v1 line** |
| 4 | **Live-process state** | in-memory vars, live `python -i`, debugger, env | **true kernel-style REPL** | ⏸️ deferred |

**The critical nuance at 3→4:** a Globus Compute `ShellFunction` runs each command in a **fresh subprocess** on the warm worker. The *allocation and workers* are warm, but the *process* is not — `x = 5` in one turn is gone the next, and `cd build` does not carry. So at rung 3 the **shared filesystem is the continuity layer**, and cwd/env must be engineered to survive (§4) or the REPL feels like "a remote shell that forgets where it is." Rung 4 (variables and a live interpreter persisting like a notebook) needs **worker-pinning + a long-lived interactor process** — a genuinely different, later product. v1 ships rung 3 honestly.

---

## 4. State continuity at rung 3: filesystem + a session-shell shim

Relying on the model to prepend `cd /abs/path &&` to 100% of commands across dozens of turns is not reliable; the one turn it emits `python train.py` produces a `FileNotFoundError` that sends the agent down a phantom debugging path. The fix is a **session-shell shim owned by the MCP server, invisible to the model**:

1. Mint a **per-session sticky working directory** at a stable absolute path the server controls (e.g. `${SCRATCH}/.hpc-bridge/sessions/<session_id>/cwd`), created on `ensure_endpoint_up`. Run the interactive profile with `run_in_sandbox=False` (isolation comes from the per-session dir, not per-task sandboxing).
2. `run_shell` does not send the bare command; the server **wraps it to rehydrate then persist cwd+env around every call**:
   ```sh
   cd "$(cat <session>/.cwd 2>/dev/null || echo <root>)" && source <session>/.env 2>/dev/null
   { <command>; }; rc=$?; pwd > <session>/.cwd; export -p > <session>/.env; exit $rc
   ```
   so a bare `cd build` then `make` in the next turn just works — no model discipline required.
3. **Serialize `run_shell` per session (concurrency = 1)** at the dispatch layer so two commands cannot race the `.cwd`/`.env` files. This doubles as the action-rate governor the safety analysis demands.
4. The skill then teaches **one enforced rule** — "you have a persistent session shell; relative paths work; call `reset_session` for a clean slate" — instead of an unenforceable "always absolute-path everything."

**What the shim cannot fix (state plainly, defer to rung 4):** background processes / `nohup foo &`, an open `python -i` or debugger, tmux; node-local `/tmp` if a different node takes the next call; `conda activate`'s shell *functions* (PATH survives via `export -p`, hook functions do not). Fold `module load` / `conda activate` into `worker_init` so the fresh-subprocess model doesn't re-pay heavy warmup each turn.

> Filesystem-as-state is also the cross-session prompt-injection persistence channel (a poisoned file written in turn N is read in N+1). Functional sufficiency ≠ security sufficiency — pair with provenance on externally-sourced content. See [`../analysis/02-risk-register.md`](../analysis/02-risk-register.md) R1.

---

## 5. Cost is the lifecycle's shadow

Facilities charge for **all reserved walltime regardless of utilization**. The lifecycle binding (§2) is what makes warm affordable, but two traps must be handled in code, not docs:

- The `interactive`/warm profile needs `min_blocks ≥ 1`, which **structurally disables Parsl idle scale-in** — flipping the knob to warm turns *off* the only built-in cost protection.
- Parsl's `max_idletime` default (120 s) is **shorter than agent think-time between turns**, so a human reading the transcript can trigger teardown, and the next turn silently re-queues a cold block — a possible *double*-charge if the agent misreads the stall and retries.

**Therefore the MCP server is the cost authority, not the scheduler** (the server knows true agent-idle state; Parsl only sees worker queue depth):

- **Scale-to-zero / `batch` is the only no-questions-asked default;** warm is a **session-bound, budget-gated, logged opt-in**.
- **Wall-clock idle TTL** in the server: `scancel`/`qdel` the block after N minutes of no MCP tool activity.
- **Pre-flight allocation gate:** refuse a warm block below a configurable floor; surface `session_spend` / `allocation_remaining` on **every** tool result.
- **Per-block node cap** so an injected "benchmark the cluster 1000×" payload can't expand a block.

Full cost design: [`../analysis/04-architecture-and-roadmap.md`](../analysis/04-architecture-and-roadmap.md) §3.

---

## 6. What the plugin manages: the lifecycle state machine

Everything above reduces to one thing the MCP server owns — **a state machine over the pilot block**, driven by tool calls and an idle timer:

```
                 ensure_endpoint_up / first run_shell
   ┌────────┐   ───────────────────────────────────►   ┌──────────────┐
   │  COLD  │                                           │ PROVISIONING │
   │ (b=0)  │ ◄──────── idle TTL elapsed ───────────┐   │ (sbatch sent,│
   └────────┘                                       │   │  queue wait) │
        ▲                                            │   └──────┬───────┘
        │ scancel on no-activity                     │          │ worker REGISTERS
        │                                            │          ▼  at interchange
   ┌────┴─────┐    no tool activity for N min    ┌───┴────────────────┐
   │   IDLE   │ ◄──────────────────────────────  │       WARM         │
   │ (warm    │                                  │ run_shell → seconds│
   │  but     │   ── next run_shell ──────────►  │ (dispatch loop)    │
   │  unused) │                                  └────────┬───────────┘
   └──────────┘                                           │ block lost / endpoint dies
                                                          ▼
                                                   ┌──────────────┐
                                                   │     DEAD     │ ── reconcile ──►  (re-provision,
                                                   │ (reconcile)  │     same UUID/config   back to
                                                   └──────────────┘                   PROVISIONING)
```

Two correctness rules that are easy to get wrong:

- **Probe WARM-vs-COLD by worker registration at the interchange, NOT by endpoint `Running` state.** The endpoint reports `Running` the instant the coordinator starts — before any worker block registers — so a state-keyed probe misroutes the first call into the fast path and then blocks unbounded. This is the single most important correctness detail.
- **`run_shell` is a durable-handle task, not a blocking RPC.** Persist `{task_id, task_group_id, endpoint_id, cwd, submit_ts}` to a small sqlite store **before** returning; on reconnect call `reload_tasks(task_group_id)` to re-attach orphaned futures within the 30-min result TTL. (Why this is "real v1 core, not a small delta": [`../analysis/04-architecture-and-roadmap.md`](../analysis/04-architecture-and-roadmap.md) §1.2.)

---

## 7. The tool contract this implies

The model only ever sees abstract tools and structured results — never a block id, a credential, or a raw stderr dump.

| Tool | Warm path | Cold path |
|---|---|---|
| `ensure_endpoint_up` | `{status:"up", block_state:"warm", cert_expires_in, session_spend}` | provisions; `{status:"provisioning", est_wait_s}` |
| `run_shell(cmd)` | bounded blocking wait → `{exit_code, stdout_snippet, cwd, session_spend}` | `{phase:"cold_start", task_handle, est_wait_s}` the agent polls |
| `read_file` / `write_file(path)` | dispatch onto warm worker; structured result | same, cold-aware |
| `reset_session` | clears the session cwd/env shim | — |

Every result carries `session_spend` / `allocation_remaining` and `block_state`, so cost and warm/cold are visible to the agent without a separate tool it must remember to call.

---

## 8. Defensibility: the managed primitive, not the pattern

The pilot-job-as-REPL is an *insight*, and it is a **pattern** — anyone can `salloc` a block and `srun` into it. What hpc-bridge sells is **the lifecycle managed for you, automatically and safely**: auto-provision the pilot, auto-warm, auto-idle-down, auto-reconcile, credential-isolated, behind one tool call. A scientist hand-driving `salloc` + a tmux babysitter reaches rungs 1–3 too — they just have to *manage the lifecycle themselves and keep the allocation from leaking*.

> **Pitch:** *"Pilot jobs turn a batch queue into a warm interactive surface; hpc-bridge manages that pilot's lifecycle — provision, warm, idle-down, self-heal — so an agent gets a REPL on a supercomputer without you babysitting an allocation or leaking a credential."*

---

## 9. v1 scope line

**Ships (rung 3, single session):**
- Pilot lifecycle state machine (§6) with worker-registration warm/cold probe.
- Durable-handle `run_shell` + `read_file`/`write_file`; session-shell shim (§4); `concurrency = 1` per session.
- Scale-to-zero default; warm as session-bound, idle-TTL'd, budget-gated opt-in (§5); `session_spend` on every result.
- `ensure_endpoint_up` reconcile (same UUID/config).
- **Develop against a local dev endpoint first**, then a single real facility (NERSC — the only site where the auth, persistence, and broker story all hold today; see [`../analysis/03-ssh-mfa-interactive-access.md`](../analysis/03-ssh-mfa-interactive-access.md)).

**Defers:**
- Rung 4 (worker-pinned live process / kernel; persistent in-memory state).
- Multi-session concurrency and batch fan-out (`concurrency > 1` — the interactive and batch profiles need genuinely different dispatch semantics).
- Autonomous self-heal off NERSC (ALCF/OLCF/TACC are human-in-the-loop restart — facility MFA per repair).

**Gating experiment (do before packaging):** **E1 — measure the warm `submit → .result()` round-trip on a real Perlmutter node.** No such number exists publicly; if the median is ≳10 s the REPL thesis weakens and we learn it for the price of one day. (E1–E6: [`../analysis/04-architecture-and-roadmap.md`](../analysis/04-architecture-and-roadmap.md) §7.)

---

*Next: a focused v1 plugin design spec (MCP server + skill + `/hpc-connect` + hooks) built on this lifecycle. This note defines the runtime behavior that spec must realize.*
