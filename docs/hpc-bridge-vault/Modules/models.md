# models.py

> [!abstract] Role
> The Pydantic result types the MCP tools return. Failures come back as **structured outcomes**, never raw crashes.

## What it does

- **`ShellOutcome`** (`models.py:10`) — result of `run_shell`/`reset_session`: `phase` (`complete` | `cold_start` | `failed` | `needs_confirmation`), `exit_code`, `stdout`, `stderr_snippet`, `cwd`, `block_state`, `session_spend`, `notice`.
- **`EndpointStatus`** (`:25`) — result of `ensure_endpoint_up`/`stop_endpoint`: `status` (`up` | `provisioning` | `down` | `needs_confirmation`), `block_state`, `endpoint_id`, `session_spend`, `partition`, `notice`.
- **`LoginShellResult`** — result of `login_shell`: `exit_code`, `stdout`, `stderr_snippet`, `notice` — a separate channel from compute (no block, no spend).
- **`ConnectFacilityResult`** — result of `connect_facility`: `phase` (`needs_account` | `provisioning` | `needs_facility_details` | `unsupported` | `failed`), `facility`, `allocations` (`AllocationOption`s — account + balance), `notice`. Drives the [[Facility catalog|catalog]] selection loop.
- **`FacilityDetails`** — *input* to `connect_facility(details=…)` for a machine **not** in the catalog (the [[Globus index discovery channel|Socratic fallback]]). Its per-field `Field(description=…)` IS the elicitation template the agent reads to question the user (`ssh_host`, `interface`, `env_setup`, `scratch_root`, `partition`, optional allocation command). Facility config only — credentials come from env.

The `needs_confirmation` phase/status is the [[Resource shapes & the spend floor|spend floor]] signal; `cold_start` is the no-hang cold path; `needs_facility_details` is the un-indexed-facility signal.

## See also
[[The MCP tools]] · [[server]] · [[dispatch]]
