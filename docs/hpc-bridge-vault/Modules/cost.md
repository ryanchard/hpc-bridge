# cost.py

> [!abstract] Role
> Two small pure helpers: estimate node-hours of spend, and bound output to the result limit.

## What it does

- **`estimate_spend(elapsed_s, nodes, charge_factor)`** (`cost.py:4`) → node-hours = `elapsed/3600 × nodes × charge_factor`. `charge_factor` is the facility's QOS multiplier (`0.0` = free local dev). Used by the [[Cost control|spend clock]] in [[server]].
- **`cap_output(text, max_chars)`** (`:12`) — truncates with a hint to redirect verbose output to a file (Globus Compute has a hard 10 MB result limit). Used by [[dispatch]].

Pure and dependency-free — no SDK, easy to unit-test.

## See also
[[Cost control]] · [[dispatch]] · [[server]]

> [!note] Split step 4 (2026-09-03) — the session spend clock lives here now
> `_bank_warm_interval`, `_billable`, `_settle_billing`, `_session_spend`, `_total_session_spend` and `_with_spend` moved in from `server.py` (the vault always documented cost as their home). `server` re-exports them.
