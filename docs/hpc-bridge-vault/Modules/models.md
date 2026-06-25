# models.py

> [!abstract] Role
> The Pydantic result types the MCP tools return. Failures come back as **structured outcomes**, never raw crashes.

## What it does

- **`ShellOutcome`** (`models.py:10`) — result of `run_shell`/`reset_session`: `phase` (`complete` | `cold_start` | `failed` | `needs_confirmation`), `exit_code`, `stdout`, `stderr_snippet`, `cwd`, `block_state`, `session_spend`, `notice`.
- **`EndpointStatus`** (`:25`) — result of `ensure_endpoint_up`/`stop_endpoint`: `status` (`up` | `provisioning` | `down` | `needs_confirmation`), `block_state`, `endpoint_id`, `session_spend`, `partition`, `notice`.
- **`LoginShellResult`** (`:39`) — result of `login_shell`: `exit_code`, `stdout`, `stderr_snippet`, `notice` — a separate channel from compute (no block, no spend).

The `needs_confirmation` phase/status is the [[Resource shapes & the spend floor|spend floor]] signal; `cold_start` is the no-hang cold path.

## See also
[[The MCP tools]] · [[server]] · [[dispatch]]
