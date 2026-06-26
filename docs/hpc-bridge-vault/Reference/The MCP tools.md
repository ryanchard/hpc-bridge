# The MCP tools

> [!abstract] Role
> The agent-facing surface — **seven** tools, all declared in [[server]], all returning structured [[models|Pydantic results]] (failures come back as outcomes, never raw crashes).

## Stand up & run

| Tool | Returns | What it does |
|---|---|---|
| `ensure_endpoint_up(shape="slurm", partition=None, confirm_spend=False, account=None)` | `EndpointStatus` | Provision/probe the endpoint; reports `up` only once a **worker answers a canary** ([[Warmth, the canary & cold-start]]), else `provisioning`. A billed `slurm` block won't start without `confirm_spend=True` → `needs_confirmation`. `partition` and `account` (the chosen allocation) select the Slurm target and persist for the session. |
| `run_shell(command, session_id="default", shape="slurm")` | `ShellOutcome` | Run a command on the warm block (`shape="slurm"`) or the login node (`shape="login"`, free — the no-SSH discovery channel). Cold endpoint → `cold_start` (no hang). cwd/env persist per session ([[Session continuity]]). |
| `reset_session(session_id="default")` | `ShellOutcome` | Clear a session's persisted cwd + environment. |
| `stop_endpoint()` | `EndpointStatus` | Release the billed Slurm block over the login endpoint (AMQP, no SSH); **leave the login-node endpoint online** for a zero-SSH reconnect. "Stop" = stop spending, not tear down ([[Cost control]]). |
| `login_shell(command)` | `LoginShellResult` | Read-only command on the login node over a **fresh SSH** connection — the cold-start discovery escape hatch. Prefer `run_shell(shape="login")` once an endpoint is up ([[Discovery today]]). SSH facility only. |

## Catalog selection (the agentic discovery front)

| Tool | Returns | What it does |
|---|---|---|
| `list_facilities(query="")` | `list[CatalogSummary]` | Browse the [[Facility catalog]] (the Globus Search index). Agent-safe summaries — identity + provenance, **no** executable config or raw UUIDs. No SSH, no spend. |
| `connect_facility(facility)` | `ConnectFacilityResult` | Bind a machine and bring up its **free login shape** (SSH cold-bootstrap once, or reuse an online endpoint — no Slurm account needed), run the facility's allocation command over Compute, return `needs_account` with the parsed allocations. `provisioning` ⇒ login node still warming, call again. |

> [!note] Two execution channels
> `run_shell`/`reset_session` ride [[Two-channel architecture|AMQP]] (the warm block or the login shape). `login_shell` is the only tool that opens a fresh SSH — reserved for cold-start discovery.

> [!note] The selection flow
> `list_facilities` → `connect_facility(facility)` → pick an allocation → `ensure_endpoint_up(account=…, partition=…, confirm_spend=True)`. Machine + allocation are **agent-chosen at runtime** ([[Facility catalog]]); a machine can also be pinned at startup with `HPC_BRIDGE_MACHINE`.

## See also
[[server]] · [[models]] · [[Facility catalog]] · [[Resource shapes & the spend floor]] · [[Discovery today]]
