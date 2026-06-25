# state.py

> [!abstract] Role
> Durable local record of which login node each endpoint landed on — the [[Standing up the endpoint|login-node pin]].

## What it does

- **`EndpointRecord`** (`state.py:18`) — `endpoint_id`, `login_host` (resolved FQDN), `alias` (the round-robin SSH alias), `user`, `key_path`, `name`, `provisioned_at`.
- **`LoginNodeStore`** (`:33`) — JSON at `~/.hpc-bridge/endpoints.json`, keyed by `(alias, name)`. `put`/`get`/`remove`/`all`.

> [!warning] Written `0600` from creation
> The file references a credentialed host, so `_save` opens with `0o600` and `chmod`s — it never exists world-readable, even briefly.

Used by [[facility-remote]]: `bootstrap` records the pin after `start`; `make_facility` reads it and `rebind`s the CLI to that node; `teardown` removes it only when the daemon is actually gone (a failed teardown keeps the pin rather than orphaning it).

## See also
[[Standing up the endpoint]] · [[facility-remote]] · [[Two-channel architecture]]
