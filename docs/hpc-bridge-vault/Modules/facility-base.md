# facility-base.py — `facility/base.py`

> [!abstract] Role
> The **Facility seam**: one Protocol behind which all machine-specific behaviour lives, so the runtime is facility-agnostic.

## What it does

- **`Facility`** Protocol (`facility/base.py:17`) — the contract the runtime depends on: `provision(profile)`, `manager_online(endpoint_id)`, `config_template(profile)`. (Concrete facilities also add `bootstrap`/`teardown`/`login_exec`/etc., accessed via `getattr` so they're optional.)
- **`EndpointHandle`** (`:10`) — `provision`/`bootstrap` return value: `endpoint_id`, `name`, `login_host` (the pinned FQDN), `reused` (True if reused over the web, no SSH).

Implementations: [[facility-local]] (LocalProvider, no SSH) and [[facility-remote]] (Slurm over SSH). [[server]]'s `make_facility` picks one from env.

> [!note] Why a seam
> Everything that differs between machines sits behind this Protocol; the dispatch/session/cost runtime never imports a specific facility. The discovery work ([[Discovery today]]) is about *generating* these instead of hand-writing them.

## See also
[[facility-local]] · [[facility-remote]] · [[server]] · [[Discovery today]]
