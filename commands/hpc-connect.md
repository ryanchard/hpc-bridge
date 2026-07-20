---
description: Bring up an HPC endpoint to run on — connect to the facility you name (or list options), then warm its login node and report status.
---

Bring up an HPC endpoint the user can run on, then report its status.

**Reaching a facility starts with `connect_facility`, never a bare `ensure_endpoint_up`.** With no facility bound, `ensure_endpoint_up` targets a *local* endpoint — which only exists on a Linux host and errors on macOS ("runs only on Linux"). Don't lead a user there. Instead:

1. **The user named a facility, or gave a login host** → `connect_facility(facility="<id-or-name>"[, ssh_host="<login-host>"])`. This binds the facility and stands up — or **zero-SSH reuses** — the endpoint, then returns your allocations. `provisioning` ⇒ call again shortly; `needs_facility_details`/`needs_preauth` ⇒ follow the `driving-hpc` guidance.
2. **No facility yet** → `list_facilities()` and ask which one (or ask for the SSH login host of an un-catalogued cluster).
3. **Only** if a facility is pinned by env (`HPC_BRIDGE_MACHINE`), or you are running on the target Linux login node itself, is `ensure_endpoint_up(shape="login")` the direct path.

Then report: the facility, its login node + worker state, and the allocations/partitions available. For the full **select → discover → gate → provision → wait** flow (and how reuse/pre-auth/discovery are decided), follow the `driving-hpc` skill.
