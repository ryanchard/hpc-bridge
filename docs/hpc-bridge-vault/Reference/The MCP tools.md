# The MCP tools

> [!abstract] Role
> The agent-facing surface — **nine** tools, all declared in [[server]], all returning structured [[models|Pydantic results]] (failures come back as outcomes, never raw crashes).

## Stand up & run

| Tool | Returns | What it does |
|---|---|---|
| `ensure_endpoint_up(shape="compute", partition=None, confirm_spend=False, account=None)` | `EndpointStatus` | Provision/probe the endpoint; reports `up` only once a **worker answers a canary** ([[Warmth, the canary & cold-start]]), else `provisioning`. A billed `compute` block won't start without `confirm_spend=True` → `needs_confirmation`. `partition` and `account` (the chosen allocation) select the **scheduler target** — a Slurm partition or PBS queue — and persist for the session. |
| `run_shell(command, session_id="default", shape="compute")` | `ShellOutcome` | Run a command on the warm block (`shape="compute"`) or the login node (`shape="login"`, free — the no-SSH discovery channel). Cold endpoint → `cold_start` (no hang). A command that outlives the sync-wait comes back `running` + a `task_id` (**not** cut — it runs up to the block walltime) → retrieve it with `poll_task`. cwd/env persist per session ([[Session continuity]]). |
| `poll_task(task_id, wait=0.0)` | `ShellOutcome` | Retrieve a long task's result: `complete` once it finishes, else `running` (poll again); `wait` optionally blocks up to N seconds. This is what lets long work be a *foreground task* instead of a detached process — a running task keeps the block busy, so it's never idle-released ([[Cost control]], [#21](https://github.com/ryanchard/hpc-bridge/issues/21)). |
| `reset_session(session_id="default", shape="compute")` | `ShellOutcome` | Clear a session's persisted cwd + environment (per shape). A session with a task still running is busy — reset (like a second `run_shell`) is refused until it's polled. |
| `stop_endpoint()` | `EndpointStatus` | Release the billed block over the login endpoint (AMQP, no SSH) via the scheduler's own cancel (`scancel` on Slurm, `qdel` on PBS); **leave the login-node endpoint online** for a zero-SSH reconnect. "Stop" = stop spending, not tear down ([[Cost control]]). Retries a cold release channel to confirm; `status="down"` = cancel confirmed, `status="draining"` = dispatched but **unconfirmed** (re-call to confirm; [#24](https://github.com/ryanchard/hpc-bridge/issues/24)). |
| `teardown_endpoint()` | `EndpointStatus` | The explicit "destroy it entirely": `gce stop` + `delete` over SSH and clear all state — for when the user insists on removing the login endpoint (normally it's left online for reuse). After it, don't `run_shell` (that re-provisions a fresh one). |
| `login_shell(command)` | `LoginShellResult` | Read-only command on the login node over a **fresh SSH** connection — the cold-start discovery escape hatch. Prefer `run_shell(shape="login")` once an endpoint is up ([[Discovery today]]). Requires a connected SSH facility. |

## Catalog selection (the agentic discovery front)

| Tool | Returns | What it does |
|---|---|---|
| `list_facilities(query="")` | `list[CatalogSummary]` | Browse the [[Facility catalog]] (the Globus Search index). Agent-safe summaries — identity + provenance, **no** executable config or raw UUIDs. No SSH, no spend. |
| `connect_facility(facility, ssh_host=None, details=None)` | `ConnectFacilityResult` | Bind a machine and bring up its **free login shape** (SSH cold-bootstrap once, or reuse an online endpoint — no scheduler account needed), run the facility's allocation command over Compute, return `needs_account`. `provisioning` ⇒ still warming. A facility used before resolves from the **local cache** (`facilities.json`, keyed by `ssh_host` — [[state]]) with **no SSH probe**, then reuses the online endpoint (zero-SSH reconnect). **Not in the catalog ⇒ discover, don't interrogate:** pass `ssh_host` and the tool **probes the login node**, returning `proposed_facility_details` with a draft [[models\|FacilityDetails]] to confirm; call again with `details=…` to register a **session-local** entry (never indexed) and proceed (the login-shape canary validates it). A host needing interactive login (password/Duo) returns `needs_preauth` + a `preauth_command` for the user to run in **their own terminal** — the secret never reaches the agent ([[MFA and interactive SSH auth]]). With no host ⇒ `needs_facility_details`. See [[Globus index discovery channel]]. |

> [!note] Two execution channels
> `run_shell`/`reset_session` ride [[Two-channel architecture|AMQP]] (the warm block or the login shape). `login_shell` is the only tool that opens a fresh SSH — reserved for cold-start discovery.

> [!note] The selection flow
> `list_facilities` → `connect_facility(facility)` → pick an allocation → `ensure_endpoint_up(account=…, partition=…, confirm_spend=True)`. Machine + allocation are **agent-chosen at runtime** ([[Facility catalog]]); a machine can also be pinned at startup with `HPC_BRIDGE_MACHINE`.

## See also
[[server]] · [[models]] · [[Facility catalog]] · [[Resource shapes & the spend floor]] · [[Discovery today]]
