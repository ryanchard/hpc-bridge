# models.py

> [!abstract] Role
> The Pydantic result types the MCP tools return. Failures come back as **structured outcomes**, never raw crashes.

## What it does

- **`ShellOutcome`** (`models.py:10`) — result of `run_shell`/`reset_session`: `phase` (`complete` | `cold_start` | `failed` | `needs_confirmation`), `exit_code`, `stdout`, `stderr_snippet`, `block_state`, `session_spend`, `notice`.
- **`EndpointStatus`** (`:23`) — result of `ensure_endpoint_up`/`stop_endpoint`: `status` (`up` | `provisioning` | `down` | `needs_confirmation`), `block_state`, `endpoint_id`, `session_spend`, `partition`, `account`, `notice`.
- **`LoginShellResult`** — result of `login_shell`: `exit_code`, `stdout`, `stderr_snippet`, `notice` — a separate channel from compute (no block, no spend).
- **`ConnectFacilityResult`** (`:115`) — result of `connect_facility`: `phase` (`needs_account` | `provisioning` | `needs_facility_details` | `proposed_facility_details` | `unsupported` | `failed`), `facility`, `allocations` (`AllocationOption`s — account + balance), `proposed_details` (the discovered `FacilityDetails` draft, set on the `proposed_facility_details` phase), `notice`. Drives the [[Facility catalog|catalog]] selection loop.
- **`FacilityDetails`** (`:58`) — *input* to `connect_facility(details=…)` for a machine **not** in the catalog, **and** the discovery probe's *output* (a proposed draft). Its per-field `Field(description=…)` doubles as the question template (`ssh_host`, `interface`, `env_setup`, `scratch_root`, `partition`, optional allocation command). Facility config only — SSH credentials come from `~/.ssh/config` (env overrides optional), not here. See [[Globus index discovery channel]].

The `needs_confirmation` phase/status is the [[Resource shapes & the spend floor|spend floor]] signal; `cold_start` is the no-hang cold path; `proposed_facility_details` (a probed draft to confirm) and `needs_facility_details` (no SSH host yet) are the un-indexed-facility signals.

## See also
[[The MCP tools]] · [[server]] · [[dispatch]]
