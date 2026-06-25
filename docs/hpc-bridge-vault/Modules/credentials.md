# credentials.py

> [!abstract] Role
> Builds a **least-privilege `storage.db`** from the user's existing Globus login — the trimmed credential shipped to a remote login node so the endpoint daemon can `start` non-interactively.

## What it does

**`build_minimal_storage_db(src, dst, namespace)`** (`credentials.py:78`) copies *only* the records for the two required resource servers — Globus Compute (`funcx_service`) and Globus Auth (`auth.globus.org`) — with their refresh tokens, from the user's `storage.db` into a fresh db. The resource-server names (`_required_resource_servers`, `:19`) and required scopes (`_required_scopes`, `:35`) are resolved from the SDK, not hardcoded.

It raises `MissingCredentials` (`:66`) when a required token is absent, has no refresh token, or is **under-scoped**.

> [!warning] The `manage_projects` check
> A plain SDK `Client` login carries only `openid` on `auth.globus.org`, not `manage_projects`. A remote manager registers `manage_projects` as a hard requirement and dies (interactive login in a daemon) without it. This module verifies scope adequacy **locally**, before shipping, with a clear remediation. The conceptual rationale is in [[Credential seeding]].

## See also
[[Credential seeding]] · [[facility-remote]]
