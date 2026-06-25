# server.py

> [!abstract] Role
> The FastMCP server — the agent-facing entry point. Declares the seven MCP tools, holds session state (`AppCtx`), selects the facility, and runs the provision → canary → dispatch → spend flow under a lock.

## What it does

`server.py` is the runtime heart. It exposes seven tools ([[The MCP tools]]), each a thin `@mcp.tool()` wrapper over a private `_`-helper that takes the `AppCtx`:

| Tool | Helper | Does |
|---|---|---|
| `ensure_endpoint_up` (`:414`) | `_ensure_endpoint_up` (`:350`) | provision/probe; report warm via the canary |
| `run_shell` (`:592`) | `_run_shell` (`:542`) | dispatch a command to the warm block |
| `reset_session` (`:610`) | `_reset_session` (`:562`) | clear a session's cwd/env |
| `stop_endpoint` (`:467`) | `_stop_endpoint` (`:432`) | teardown + release the block |
| `login_shell` (`:494`) | `_login_shell` (`:473`) | read-only login-node command over SSH |

## How it works

- **State.** `AppCtx` (`:50`) holds the facility, profile, endpoint state, the per-shape `ShapeRuntime` (`:31` — its Executor, canary result, and spend clock), and an `asyncio.Lock`. `lifespan` (`:133`) builds it from `make_facility` + env.
- **Facility selection.** `make_facility` returns a `SlurmFacility` resolved from the [[Facility catalog|catalog]] (`HPC_BRIDGE_MACHINE`, or `connect_facility` at runtime) or a `LocalFacility`, reading the login-node pin from [[state]] and rebinding the CLI to it.
- **The provision choke point.** `_provision` (`:297`): bootstrap if there's no endpoint → `ensure_warm` ([[lifecycle]]) → on `"warm"`, confirm a *live worker* via `_confirm_worker` (`:201`, the canary) → `_settle_billing`. Both `ensure_endpoint_up` and `run_shell` (via `_ensure_warm_runner`) reach it.
- **The lock.** Serialises provision / runner-swap / teardown so concurrent tool calls can't race `AppCtx`. Dispatch happens *outside* the lock, so a long command doesn't serialise everything else.
- **The spend floor.** `_provision` returns `"needs_confirmation"` for a billed (slurm) shape until `confirm_spend=True` — see [[Resource shapes & the spend floor]]. Partition selection is threaded in via `_apply_partition` (`:332`).

> [!warning] "warm" means a *worker* answered — not "manager online"
> `manager_online` (a cheap web query) only reflects the login-node manager. In the MEP model the first task forks the UEP and submits the block, so the manager reads online while the next command would cold-start. `_confirm_worker` submits a **canary** through the real Executor; only a returned result ⇒ warm. `CANARY_TTL_S` (`:158`) then trusts that for 45 s so an interactive burst doesn't pay the round-trip each call. See [[Warmth, the canary & cold-start]].

## See also
[[Two-channel architecture]] · [[Warmth, the canary & cold-start]] · [[Resource shapes & the spend floor]] · [[The MCP tools]] · [[runner]] · [[lifecycle]] · [[facility-remote]]
