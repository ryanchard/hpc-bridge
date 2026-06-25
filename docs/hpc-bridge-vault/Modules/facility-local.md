# facility-local.py — `facility/local.py`

> [!abstract] Role
> `LocalFacility` — a Globus Compute endpoint via `LocalProvider`, **no SSH**, for local dev (Linux only). The simplest [[facility-base|Facility]] implementation.

## What it does

- **`config_template(profile)`** (`facility/local.py:18`) — the UEP template (engine inline). The `interactive` profile holds a warm block (`min_blocks=1`); `batch` scales to zero. A `LocalProvider` block costs no allocation, so unlike [[facility-remote]] it can stay warm for snappy dev.
- **`provision(profile)`** (`:39`) — `configure` (forced `--multi-user false`), write the engine into the UEP template, `start`. Drives the local [[endpoint|`EndpointCLI`]].
- **`manager_online(endpoint_id)`** (`:49`) — the Globus web status query.

> [!note] Linux only
> The local `globus-compute-endpoint` daemon runs only on Linux; on macOS/Windows use `HPC_BRIDGE_ENDPOINT_ID` (BYO) instead — see [[endpoint]] and [[Configuration]].

## See also
[[facility-base]] · [[endpoint]] · [[MEP & templated endpoints]]
