# Configuration

> [!abstract] Role
> The environment variables `make_facility` and `lifespan` ([[server]]) read at startup. Read **once** when the MCP server launches — change one ⇒ restart the session.

## Facility selection

| Var | Effect |
|---|---|
| `HPC_BRIDGE_MACHINE` | A catalog machine id/subject (e.g. `anvil`) → resolve its profile from the [[Facility catalog|catalog]] at startup; unset → local dev (the agent can bind one at runtime via `connect_facility`). Machines are catalog *data*, never hardcoded. |
| `HPC_BRIDGE_SEARCH_INDEX` | **Required for catalog discovery** — the Globus Search index UUID (run `hpc-bridge-catalog` once for the search scope). Unset → no catalog: a machine can't be resolved (a hard failure until agent-discovery lands). |
| `HPC_BRIDGE_SSH_USER` · `HPC_BRIDGE_SSH_KEY` | **Optional overrides** — SSH login name + key. Unset ⇒ read live from your `~/.ssh/config` (`ssh -G` for the user, the config's `IdentityFile` for the key), so they needn't be exported into the already-running server's env. |
| `HPC_BRIDGE_ACCOUNT` | Slurm charge account — **required only on the `HPC_BRIDGE_MACHINE` startup-pin path**; the agentic flow takes it from `connect_facility`'s allocations or you pass it to `ensure_endpoint_up`. |
| `HPC_BRIDGE_SSH_HOST` | Override the SSH host — **startup-pin path only** (`HPC_BRIDGE_MACHINE`): reach the catalog's canonical machine via your own `~/.ssh/config` alias / a specific login node / an FQDN (the container needs the FQDN — no ssh config). The agentic `connect_facility` path **ignores it** — the *bound* facility's own `ssh_host` is authoritative, so a stray/global env can't silently redirect an agent-chosen facility ([#35](https://github.com/ryanchard/hpc-bridge/issues/35)). It is also the discovery-probe host when you `connect_facility` without an `ssh_host`. |
| `HPC_BRIDGE_SSH_CONTROL_PERSIST` | Seconds to keep the per-facility SSH **ControlMaster** alive (default `60`; `0` disables multiplexing) — one auth serves the whole bootstrap + discovery. |
| `HPC_BRIDGE_RELEASE_ATTEMPTS` · `HPC_BRIDGE_RELEASE_BACKOFF_S` | `stop_endpoint`'s bounded retry to **confirm** the block cancel when the login release channel is cold (default `3` × `6`s). Exhausted → honest `status="draining"` (never a false `"down"`); see [[Cost control]] / #24. |
| `HPC_BRIDGE_REMOTE_VENV` | Override the remote `globus-compute-endpoint` venv path (else the `/home/{user}/hpc-bridge/gce-venv` convention). |
| `HPC_BRIDGE_PARTITION` | Default partition — the [[Resource shapes & the spend floor|gate]] overrides it per run. |

## Session & cost

| Var | Effect |
|---|---|
| `HPC_BRIDGE_PROFILE` | `interactive` \| `batch` (default `batch`) — see [[profile]]. |
| `HPC_BRIDGE_SCRATCH` | Override the [[Session continuity\|session-shell root]] (else the facility's `$SCRATCH`, else a local default). |
| `HPC_BRIDGE_STATE_DIR` | Base dir for hpc-bridge's **local state** — login-node pins (`endpoints.json`), the local-discovery facility cache (`facilities.json`), and the SSH ControlMaster sockets. Default `~/.hpc-bridge`; relocating it isolates all state (the test suite points it at a tmp dir so tests never touch the real one). |
| `HPC_BRIDGE_CHARGE_FACTOR` | The QOS SU multiplier for the [[Cost control\|spend clock]] (default `0.0` = free). |
| `HPC_BRIDGE_SYNC_WAIT_S` | How long `run_shell` blocks for a result before handing back a poll handle (default `120`). A command still running past it comes back `running` + `task_id` (**not** cut); retrieve it with `poll_task`. Clamped strictly below the task ceiling. |
| `HPC_BRIDGE_MAX_TASK_S` | Optional cap (seconds) on a single task before the worker kills it (exit 124). **Unset ⇒ the ceiling is the block walltime** — the deterministic default. Set it to bound the blast radius of a hung task on a long-walltime facility ([[Cost control]], [#21](https://github.com/ryanchard/hpc-bridge/issues/21)). |
| `HPC_BRIDGE_USER_DIR` | Local `globus_compute` dir (set by `.mcp.json`). |

## BYO endpoint

| Var | Effect |
|---|---|
| `HPC_BRIDGE_ENDPOINT_ID` | A UUID to dispatch to directly, skipping local provisioning. **Required on macOS/Windows**, where the local daemon can't run ([[endpoint]]). |

## See also
[[server]] · [[facility-remote]] · [[Plugin packaging]]
