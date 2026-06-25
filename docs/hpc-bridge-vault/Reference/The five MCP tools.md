# The five MCP tools

> [!abstract] Role
> The agent-facing surface. All five are declared in [[server]] and return structured [[models|Pydantic results]] — failures come back as outcomes, never raw crashes.

| Tool | Returns | What it does |
|---|---|---|
| `ensure_endpoint_up(shape="slurm", partition=None, confirm_spend=False)` | `EndpointStatus` | Provision/probe the endpoint; reports `up` only once a **worker answers a canary** ([[Warmth, the canary & cold-start]]), else `provisioning`. A billed `slurm` block won't start without `confirm_spend=True` → `needs_confirmation`; `partition` selects the target and persists for the session. |
| `run_shell(command, session_id="default", shape="slurm")` | `ShellOutcome` | Run a command on the warm block (`shape="slurm"`) or the login node (`shape="login"`, free — the no-SSH discovery channel). Cold endpoint → `cold_start` (no hang). cwd/env persist per session ([[Session continuity]]). |
| `reset_session(session_id="default")` | `ShellOutcome` | Clear a session's persisted cwd + environment. |
| `stop_endpoint()` | `EndpointStatus` | Tear down the endpoint, `scancel` its block, drop the login-node pin, reset session state ([[Cost control]]). |
| `login_shell(command)` | `LoginShellResult` | Read-only command on the login node over a **fresh SSH** connection — the cold-start discovery escape hatch. Prefer `run_shell(shape="login")` once an endpoint is up ([[Discovery today]]). SSH facility only. |

> [!note] Two execution channels
> `run_shell`/`reset_session` ride [[Two-channel architecture|AMQP]] (the warm block or the login shape). `login_shell` is the only tool that opens a fresh SSH — reserved for cold-start discovery.

## See also
[[server]] · [[models]] · [[Resource shapes & the spend floor]] · [[Discovery today]]
