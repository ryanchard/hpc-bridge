# profile.py

> [!abstract] Role
> The **session-level** profile: how this run behaves (interactive vs batch, block sizing, idle grace). Distinct from a facility's `MachineProfile`.

## What it does

`Profile` (`profile.py:11`) is a frozen dataclass: `mode` (`interactive` | `batch`), `nodes_per_block`, `max_idletime_s` (default 600 — the [[Cost control|idle-release]] grace), `account`, `queue`. It validates `mode` against `MODES` (`:7`) and rejects a sub-1s idle time.

> [!note] Two different "profiles"
> `Profile` (here) is **session policy** and applies to any facility. `MachineProfile` ([[facility-remote]]) is **per-facility data** (host, modules, interface, …). Don't conflate them.

## See also
[[server]] · [[facility-remote]] · [[Cost control]]
