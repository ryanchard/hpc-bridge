# Configuration

> [!abstract] Role
> The environment variables `make_facility` and `lifespan` ([[server]]) read at startup. Read **once** when the MCP server launches — change one ⇒ restart the session.

## Facility selection

| Var | Effect |
|---|---|
| `HPC_BRIDGE_FACILITY` | `anvil` → the remote Slurm facility; unset → local dev. |
| `HPC_BRIDGE_SSH_USER` · `HPC_BRIDGE_SSH_KEY` · `HPC_BRIDGE_ACCOUNT` | **Required** for Anvil — SSH identity + Slurm charge account. |
| `HPC_BRIDGE_SSH_HOST` | SSH alias (default `anvil.rcac.purdue.edu`). |
| `HPC_BRIDGE_PARTITION` | Default partition (default `debug`) — the [[Resource shapes & the spend floor|gate]] overrides it per run. |

## Session & cost

| Var | Effect |
|---|---|
| `HPC_BRIDGE_PROFILE` | `interactive` \| `batch` (default `batch`) — see [[profile]]. |
| `HPC_BRIDGE_SCRATCH` | Override the [[Session continuity\|session-shell root]] (else the facility's `$SCRATCH`, else a local default). |
| `HPC_BRIDGE_CHARGE_FACTOR` | The QOS SU multiplier for the [[Cost control\|spend clock]] (default `0.0` = free). |
| `HPC_BRIDGE_USER_DIR` | Local `globus_compute` dir (set by `.mcp.json`). |

## BYO endpoint

| Var | Effect |
|---|---|
| `HPC_BRIDGE_ENDPOINT_ID` | A UUID to dispatch to directly, skipping local provisioning. **Required on macOS/Windows**, where the local daemon can't run ([[endpoint]]). |

## See also
[[server]] · [[facility-remote]] · [[Plugin packaging]]
