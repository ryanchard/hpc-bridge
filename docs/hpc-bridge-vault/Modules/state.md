# state.py

> [!abstract] Role
> Durable local state under `~/.hpc-bridge/` (relocatable via `HPC_BRIDGE_STATE_DIR`): the [[Standing up the endpoint|login-node pin]] and the **local-discovery cache** of confirmed BYO facility configs.

## What it does

- **`_state_dir()`** (`state.py:18`) — the state root: `HPC_BRIDGE_STATE_DIR` or `~/.hpc-bridge`. The env override lets tests point it at a tmp dir so they never touch real state. It also roots the ControlMaster sockets (`<state>/cm`) — but [[server]]'s `_short_control_dir` swaps in `~/.hpc-bridge/cm` or `/tmp/hpcb-cm-<uid>` when that path would push the expanded `ControlPath` past the Unix socket cap (`ControlPath too long`, found on the stranger's walk with a deep temp dir).
- **`EndpointRecord`** (`:26`) — `endpoint_id`, `login_host` (resolved FQDN), `alias` (the round-robin SSH alias), `user`, `key_path`, `name`, `provisioned_at`.
- **`LoginNodeStore`** (`:40`) — JSON at `~/.hpc-bridge/endpoints.json`, keyed by `(alias, name)`. `put`/`get`/`remove`/`all` — the login-node pin.
- **`FacilityStore`** (`:79`) — JSON at `~/.hpc-bridge/facilities.json`, keyed by **`ssh_host`**. Caches a confirmed BYO `FacilityDetails` dict (`get`/`put`/`remove`, `:106`–`:115`) so a later session **reconnects from the cache with no SSH probe** — the local half of discovery: [[server]]'s `connect_facility` resolves a known `ssh_host` here before ever probing. See [[Discovery today]].

> [!warning] Written `0600` from creation
> Both stores reference a credentialed host, so `_save` opens with `0o600` and `chmod`s — the file never exists world-readable, even briefly.

Used by [[facility-remote]]: `bootstrap` records the login pin after `start`; `_slurm_facility` ([[server]]) reads it — at startup *and* on every `connect_facility` bind — and `rebind`s the CLI to that node when `_routable_pin` accepts it. **Nothing removes a pin today**: `LoginNodeStore.remove` exists but no caller uses it (`teardown` leaves the record), so a routable-but-dead pin fails fast under `BatchMode` and the reset is to delete `~/.hpc-bridge/endpoints.json` by hand. `connect_facility` reads/writes `FacilityStore` — a confirmed session facility is cached, and a known `ssh_host` then resolves from it with zero SSH — but only **after** the registry misses ([[Facility catalog]] precedence). The registry's own fetched-entry cache lives elsewhere (`<CLAUDE_PLUGIN_DATA or ~/.hpc-bridge>/catalog-cache/`, [[Facility catalog]]).

> [!note] Superseded: "teardown removes the pin"
> An earlier version of this note said `teardown` removes the pin once the daemon is gone. It doesn't — see above. (Reported in the 2026-09-03 vault audit as a code gap, not fixed in the docs by pretending otherwise.)

## See also
[[Standing up the endpoint]] · [[facility-remote]] · [[Discovery today]] · [[Facility catalog]] · [[Two-channel architecture]] · [[Configuration]]
