# hpc-bridge

**Extend AI coding agents into HPC.** A Claude Code plugin that gives an agent
interactive, low-latency access to real supercomputer compute — by standing up a
*personal* Globus Compute endpoint behind the scenes, no admin and no multi-user
endpoint (MEP) deployment required.

Globus Compute is the engine under the hood; `hpc-bridge` is the agent-facing
packaging and the frictionless on-ramp. The product is a **pilot job's lifecycle
managed as an interactive REPL** — provision → warm → idle-down → self-heal,
credential-isolated, behind one tool call.

---

## Status (branch `claude-effort` · 2026-06-02)

A working **v1 plugin is implemented and proven against a live local Globus Compute
endpoint**, built strictly TDD and local-dev-first. Milestones M0, M1, M3, M4 are
done; M2 (durable handles) and M5 (real facility + credential broker) are next.

| Milestone | Scope | Status |
|---|---|---|
| **M0** | Installable FastMCP plugin skeleton, `ensure_endpoint_up`, `Facility` seam | ✅ live |
| **M1** | `run_shell` dispatch via Globus Compute `ShellFunction` | ✅ live (rc=0) |
| **M3** | Session-shell shim (`reset_session`) — cwd/env persistence | ✅ live REPL acceptance |
| **M4** | Cost-governance primitives (allocation gate, spend, output cap) | ✅ |
| **M2** | Durable task handles (`reload_tasks`, persist-before-return) | ⏸ deferred (robustness) |
| **M5** | NERSC + interactive SSH/MFA + credential broker | ⏭ next (needs facility access) |

**Tests:** 56 unit pass + 2 gated live integration tests. A 7-agent adversarial code
review was run and its findings (incl. 4 HIGH security/robustness bugs) fixed with
regression tests. See [`docs/analysis/`](docs/analysis/README.md) (viability + risks),
[`docs/design/`](docs/design/plugin-v1-design.md) (architecture), and
[`docs/plans/`](docs/plans/) (the TDD milestone plans).

> This branch is committed locally and pushed to `origin/claude-effort` only —
> **never to `main`**.

---

## What works now

Installed into Claude Code, the plugin exposes three MCP tools (all return structured
results; failures are reported as `phase: "failed"`, never raw crashes):

- **`ensure_endpoint_up`** — provisions/warms a personal endpoint, reports `up`/`provisioning`.
- **`run_shell(command, session_id="default")`** — runs a shell command on the warm
  compute block and returns `{exit_code, stdout, stderr_snippet, block_state, session_spend}`.
- **`reset_session(session_id)`** — clears a session's persisted cwd/env.

**Proven live** (against a `LocalProvider` endpoint on this machine):
- An agent dispatches real shell commands to a Globus Compute worker and gets results back.
- **REPL continuity:** `cd` and `export` persist across separate `run_shell` calls in a
  session (bare relative paths work); different `session_id`s are isolated.
- Cost primitives, output capping, and structured error handling are wired in.

Also bundled: a `/hpc-connect` slash command, a `driving-hpc` skill (working-dir
discipline, cold-start, result-size guidance), and a `PreToolUse` credential-guard hook.

---

## Architecture

Three processes plus the Compute endpoint (the credential boundary falls out of the split):

```
Claude Code ──stdio──▶ hpc-bridge MCP server ──UDS──▶ credential broker (M5; no-op locally)
                         • pilot lifecycle state machine        • SSH/MFA only (out-of-band)
                         • session-shell shim, cost plane
                         • holds only a scoped Globus token
                         │ ShellFunction over AMQP (credential-free hot path)
                         ▼
                       Globus Compute endpoint  (DEV: LocalProvider · NERSC: SlurmProvider)
```

The `Facility` protocol (`LocalFacility` / future `NerscFacility`) is the seam that makes
local-first work: lifecycle/dispatch/cost code is facility-agnostic.

`src/hpc_bridge/`: `server.py` (FastMCP tools + lifespan), `lifecycle.py` (provision/probe),
`dispatch.py` (+ failure translation), `runner.py` (`GlobusRunner` + `ShellFunction`),
`session_shell.py` (cwd/env shim), `cost.py`, `endpoint.py` (CLI wrapper), `facility/`,
`models.py`, `profile.py`.

**Note on globus-compute-endpoint 4.x** (learned by live debugging): `start` runs an
EndpointManager — `config.yaml` must be engine-free, the engine lives in
`user_config_template.yaml.j2`, `configure` must force `--multi-user false` (personal,
no identity-mapping), `start` needs `--detach`, the UUID is in `endpoint.json`,
`get_endpoint_status` returns only `{"status":"online"}`, and `ShellFunction` runs
`cmd.format()` so braces are handled. All encoded in the code.

---

## Develop / run it

Requires Python ≥3.11 and [`uv`](https://docs.astral.sh/uv/). Globus Compute is an
optional `integration` extra (M0–M4 unit tests are hermetic and don't need it).

```bash
uv sync --extra dev                 # unit test deps
uv run pytest -q                    # 56 passed, 2 skipped

# Run the MCP server standalone (stdio):
uv run hpc-bridge

# Install into Claude Code for local testing:
claude --plugin-dir .
```

**Drive a real local endpoint** (needs a one-time Globus login):

```bash
uv sync --extra dev --extra integration
uv run globus-compute-endpoint login          # interactive (browser + paste code)
# hpc-bridge provisions/starts the endpoint for you on first ensure_endpoint_up,
# or run the gated integration tests:
HPC_BRIDGE_RUN_INTEGRATION=1 HPC_BRIDGE_LIVE_ENDPOINT=<uuid> uv run pytest tests/integration -q
```

**Config (env vars):** `HPC_BRIDGE_PROFILE` (`interactive`|`batch`),
`HPC_BRIDGE_USER_DIR`, `HPC_BRIDGE_SCRATCH`, `HPC_BRIDGE_ALLOC_FLOOR`,
`HPC_BRIDGE_CHARGE_FACTOR`, `HPC_BRIDGE_ENDPOINT_ID` (dispatch to an existing
endpoint UUID; skips local provisioning).

> **Platform note:** local provisioning — `ensure_endpoint_up` starting an endpoint
> for you — requires **Linux**; `globus-compute-endpoint` does not run on macOS or
> Windows. On those hosts, run the endpoint elsewhere (a Linux box, container, or HPC
> login node) and set `HPC_BRIDGE_ENDPOINT_ID=<uuid>`. The SDK dispatch path
> (`run_shell`) is cross-platform and reaches the endpoint by UUID.

---

## Security posture (current)

Hardened in v1: `session_id` is allowlist-validated (no path traversal); commands are
base64-carried into the shim so they can't break out of the wrapper; session state files
are `0600` (umask 077); the hot path uses a Globus Auth token, never SSH material; the
credential-guard hook covers both `Bash` and `run_shell`.

Honestly deferred (see [`docs/analysis/02-risk-register.md`](docs/analysis/02-risk-register.md)):
the credential broker as a separate UID (M5), and the irreducible "agent runs as you / can
read your own credentials" prompt-injection surface — to be addressed with hard controls
(Transfer allowlist, dedicated-UID broker, audit log) before any facility deployment.

---

## Next steps

1. **M2 — durable handles:** persist `task_group_id` before return; `reload_tasks()` on
   reconnect to recover results within the 30-min TTL; structured cold-path task handle.
2. **M5 — facility:** `NerscFacility` (Slurm template + canary), the credential broker +
   url-mode MFA elicitation, `/hpc-connect` over sshproxy/SFAPI. Gated by **experiment E1**
   (measure real warm round-trip latency on a Perlmutter node).
3. **Polish:** quiet the Globus SDK's stderr logging; wire `ShellOutcome.cwd`; an
   end-to-end `wrap()` integration test in CI.

See [`docs/design/plugin-v1-design.md`](docs/design/plugin-v1-design.md) §11 for the full
milestone plan and [`docs/vision.md`](docs/vision.md) for the original vision.
