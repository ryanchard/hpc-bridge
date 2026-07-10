# state.py

> [!abstract] Role
> Durable local state under `~/.hpc-bridge/` (relocatable via `HPC_BRIDGE_STATE_DIR`): the [[Standing up the endpoint|login-node pin]] and the **local-discovery cache** of confirmed BYO facility configs.

## What it does

- **`_state_dir()`** (`state.py:18`) — the state root: `HPC_BRIDGE_STATE_DIR` or `~/.hpc-bridge`. The env override lets tests point it at a tmp dir so they never touch real state (it also roots the ControlMaster sockets).
- **`EndpointRecord`** (`:26`) — `endpoint_id`, `login_host` (resolved FQDN), `alias` (the round-robin SSH alias), `user`, `key_path`, `name`, `provisioned_at`.
- **`LoginNodeStore`** (`:40`) — JSON at `~/.hpc-bridge/endpoints.json`, keyed by `(alias, name)`. `put`/`get`/`remove`/`all` — the login-node pin.
- **`FacilityStore`** (`:79`) — JSON at `~/.hpc-bridge/facilities.json`, keyed by **`ssh_host`**. Caches a confirmed BYO `FacilityDetails` dict (`get`/`put`/`remove`, `:106`–`:115`) so a later session **reconnects from the cache with no SSH probe** — the local half of discovery: [[server]]'s `connect_facility` resolves a known `ssh_host` here before ever probing. See [[Discovery today]].

> [!warning] Written `0600` from creation
> Both stores reference a credentialed host, so `_save` opens with `0o600` and `chmod`s — the file never exists world-readable, even briefly.

Used by [[facility-remote]]: `bootstrap` records the login pin after `start`; `make_facility` reads it and `rebind`s the CLI to that node; `teardown` removes it only when the daemon is actually gone (a failed teardown keeps the pin rather than orphaning it). `connect_facility` ([[server]]) reads/writes `FacilityStore` — a confirmed session facility is cached, and a known `ssh_host` then resolves from it with zero SSH.

## See also
[[Standing up the endpoint]] · [[facility-remote]] · [[Discovery today]] · [[Two-channel architecture]]
