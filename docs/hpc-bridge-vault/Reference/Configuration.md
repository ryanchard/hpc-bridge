# Configuration

> [!abstract] Role
> The environment variables `make_facility` and `lifespan` ([[server]]) read at startup. Read **once** when the MCP server launches — change one ⇒ restart the session.

## Facility selection

| Var | Effect |
|---|---|
| `HPC_BRIDGE_MACHINE` | A catalog machine id/subject (e.g. `anvil`) → resolve its profile from the [[Facility catalog|catalog]] at startup; unset → local dev (the agent can bind one at runtime via `connect_facility`). Machines are catalog *data*, never hardcoded. |
| `HPC_BRIDGE_SEARCH_INDEX` | Globus Search index UUID → the live catalog; unset → the bundled seed. |
| `HPC_BRIDGE_SSH_USER` · `HPC_BRIDGE_SSH_KEY` · `HPC_BRIDGE_ACCOUNT` | **Required** for a remote Slurm machine — SSH identity + Slurm charge account. (`ACCOUNT` is the env shortcut; the agentic flow picks it from `connect_facility`'s allocations.) |
| `HPC_BRIDGE_SSH_HOST` | Override the SSH alias (else the catalog entry's `ssh_host`). |
| `HPC_BRIDGE_PARTITION` | Default partition — the [[Resource shapes & the spend floor|gate]] overrides it per run. |

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
