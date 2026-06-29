# Credential seeding

> [!abstract] In one line
> On first connect we build a **least-privilege `storage.db`** locally — only the two tokens an endpoint needs to `start` — and ship it to the remote `~/.globus_compute/storage.db` (`0700`/`0600`), so the daemon authenticates non-interactively without us copying our whole credential.

## What & why

A started endpoint needs tokens for exactly two resource servers:

- **Globus Compute** (`funcx_service`) — to register and receive tasks.
- **Globus Auth** (`auth.globus.org`) — carrying **`openid` + `manage_projects`**.

`build_minimal_storage_db()` ([[credentials]], `credentials.py:78`) copies *only* those two records (with their **refresh tokens**) from the user's `~/.globus_compute/storage.db` into a fresh db. That trimmed db is the least credential that lets `globus-compute-endpoint start` run in a detached daemon. It's shipped base64-over-SSH by `seed_storage_db` ([[facility-remote]], `remote.py:286`) into a `0700` dir as a `0600` file; the local temp copy is wiped after transfer. Seeding is **skipped if `whoami` already succeeds** on the remote.

> [!warning] Validate `manage_projects` *before* shipping
> A plain SDK `Client` login only gets `openid` on `auth.globus.org`, **not** `manage_projects`. A started manager registers `manage_projects` as a hard requirement; without it `login_required()` is True, the detached daemon tries an interactive login, and **dies silently**. `build_minimal_storage_db` checks scope adequacy locally and raises `MissingCredentials` with a clear remediation (run `globus-compute-endpoint login`) — learned live on Anvil.

> [!warning] Refresh tokens are mandatory
> Without a refresh token the endpoint stops working when the access token expires. Missing-refresh is a hard `MissingCredentials` failure, not a warning.

The required resource-server names and scopes are resolved from the SDK at runtime, not hardcoded, so they track upstream renames.

## See also
[[Standing up the endpoint]] · [[credentials]] · [[facility-remote]] · [[Two-channel architecture]]
