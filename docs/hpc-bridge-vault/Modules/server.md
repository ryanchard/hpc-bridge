# server.py

> [!abstract] Role
> The FastMCP server — the agent-facing entry point. Declares the nine MCP tools, holds session state (`AppCtx`), selects the facility, and runs the provision → canary → dispatch → spend flow under a lock.

## What it does

`server.py` is the runtime heart. It exposes nine tools ([[The MCP tools]]), each a thin `@mcp.tool()` wrapper over a private `_`-helper that takes the `AppCtx`:

| Tool | Helper | Does |
|---|---|---|
| `list_facilities` | `_list_facilities` | browse the [[Facility catalog\|catalog]] (agent-safe summaries) |
| `connect_facility` | `_connect_facility` | bind a facility (or probe + propose an un-indexed one via `ssh_host`), bring up its login shape, list allocations |
| `ensure_endpoint_up` | `_ensure_endpoint_up` | provision/probe; report warm via the canary; thread account/partition |
| `run_shell` | `_run_shell` | dispatch a command to the warm block / login shape |
| `poll_task` | `_poll_task` | retrieve a long task's result (the poll handle `run_shell` returns as `running`) |
| `reset_session` | `_reset_session` | clear a session's cwd/env |
| `stop_endpoint` | `_stop_endpoint` | release the block over AMQP; leave the manager online for reuse |
| `teardown_endpoint` | `_teardown_endpoint` | fully destroy the endpoint (`gce stop` + delete over SSH) — the rare explicit case |
| `login_shell` | `_login_shell` | read-only login-node command over SSH (cold-start escape hatch) |

(Helpers carry the logic; the `@mcp.tool()` wrappers are thin. Exact line numbers drift — grep the symbol.)

## How it works

- **State.** `AppCtx` (`:61`) holds the facility, profile, endpoint state, the per-shape `ShapeRuntime` (`:42` — its Executor, canary result, and spend clock), the session-local facilities dict, and an `asyncio.Lock`. `lifespan` (`:285`) builds it from `make_facility` + env.
- **Facility selection.** `make_facility` returns a `SlurmFacility` resolved from the [[Facility catalog|catalog]] (`HPC_BRIDGE_MACHINE`, or `connect_facility` at runtime — by id *or* subject) or a `LocalFacility`, reading the login-node pin from [[state]] and rebinding the CLI to it. `lifespan` **boots resiliently** — a failed `make_facility` (stale env, no index) warns and starts unbound rather than crashing; `connect_facility` then binds and **moves `scratch_root`** to the facility ([[Session continuity]]). `HPC_BRIDGE_SSH_HOST` overrides the SSH host **only on this startup-pin path** (`_facility_from_entry(pinned_host=…)`); the agentic `connect_facility` path uses the *bound* facility's own `ssh_host`, so a global env can't silently redirect an agent-chosen facility ([#35](https://github.com/ryanchard/hpc-bridge/issues/35)).
- **Un-indexed discovery.** When `_connect_facility` (`:692`) misses the catalog, `_propose_or_ask` (`:796`) builds a bare `SshTarget` (SSH user from `_ssh_config_user` / `ssh -G`, `:87`; key + host from `~/.ssh/config` + env) and runs the [[discovery]] probe → `proposed_facility_details`. On confirm, `_entry_from_details` (`:658`) builds a session-local entry whose endpoint name comes from `_session_endpoint_name` (`:649`) — `hpc-bridge-<facility>`. `_control_settings` (`:105`) configures the shared ControlMaster ([[facility-remote]]).
- **The provision choke point.** `_provision` (`:460`): bootstrap if there's no endpoint → `ensure_warm` ([[lifecycle]]) → on `"warm"`, confirm a *live worker* via `_confirm_worker` (`:364`, the canary) → `_settle_billing`. Both `ensure_endpoint_up` and `run_shell` (via `_ensure_warm_runner`) reach it.
- **The lock.** Serialises provision / runner-swap / stop so concurrent tool calls can't race `AppCtx`. Dispatch happens *outside* the lock, so a long command doesn't serialise everything else. `stop_endpoint` cancels the block over the login shape (AMQP) via `_release_blocks_over_login` (`scancel` on Slurm, `qdel` on PBS), then drops the billed shape under the lock — leaving the manager online for reuse. The runner `close()` is non-blocking ([[runner]]) so a stop returns promptly.
- **Long-task poll handles ([#21](https://github.com/ryanchard/hpc-bridge/issues/21)).** A command that outlives the sync-wait is registered in `AppCtx.tasks` (a `TaskHandle` keyed by `task_id`) and returned as `phase="running"`; `poll_task` reaps it. A live task short-circuits the warmth [[Warmth, the canary & cold-start|canary]], blocks a same-session second dispatch **and** a partition/account change (both would corrupt or cancel it), and every block-close site drains the registry.
- **The spend floor.** `_provision` returns `"needs_confirmation"` for a billed (slurm) shape until `confirm_spend=True` — see [[Resource shapes & the spend floor]]. Partition selection is threaded in via `_apply_partition` (`:496`).
- **Pilot-state observability ([#32](https://github.com/ryanchard/hpc-bridge/issues/32)).** When a billed block stays cold, `_ensure_endpoint_up` enriches the `provisioning` notice with the pilot's ACTUAL scheduler state — read over the login shape (AMQP) by the same `uep.<eid>` marker the release path uses (`_pilot_status_over_login`): `RUNNING`/queued/`HELD`, or, past a ~45 s grace (`PROVISION_GRACE_S`, clocked by `ShapeRuntime.provisioning_since`), *"no pilot → likely REJECTED"*. Otherwise a rejected/held `qsub` (bad account, missing `filesystems` directive) is indistinguishable from a normal queue wait — surfaced live on [[Aurora (PBS + bastion) bring-up|Aurora]].

> [!warning] "warm" means a *worker* answered — not "manager online"
> `manager_online` (a cheap web query) only reflects the login-node manager. In the MEP model the first task forks the UEP and submits the block, so the manager reads online while the next command would cold-start. `_confirm_worker` submits a **canary** through the real Executor; only a returned result ⇒ warm. `CANARY_TTL_S` (`:321`) then trusts that for 45 s so an interactive burst doesn't pay the round-trip each call. See [[Warmth, the canary & cold-start]].

## See also
[[Two-channel architecture]] · [[Warmth, the canary & cold-start]] · [[Resource shapes & the spend floor]] · [[The MCP tools]] · [[runner]] · [[lifecycle]] · [[facility-remote]]
