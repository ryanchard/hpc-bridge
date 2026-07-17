# New-user testing (clean-session)

> [!abstract] In one line
> `agentic/clean-session.sh` launches a **pristine** Claude Code session — no `~/.claude` priors and an isolated hpc-bridge state sandbox, loading ONLY the hpc-bridge plugin — so interactive testing reflects what a **brand-new user** gets, not the maintainer's accumulated memory/cache.

## Why

A normal `claude --plugin-dir .` inherits the maintainer's [[Home|auto-memory]], `~/.claude/CLAUDE.md`, global rules, other plugins, and — crucially — `~/.hpc-bridge/facilities.json` (a leftover facility cache). Those priors mask real new-user behaviour. One live symptom: with a leftover `globus1` cache entry **and** a forced `HPC_BRIDGE_SSH_HOST=aurora`, a cold agent connected to "globus1" but SSH'd to Aurora and concluded *"globus1 is Aurora"* — a config-from-X / SSH-to-Y confusion ([#35](https://github.com/ryanchard/hpc-bridge/issues/35)).

## What it isolates

| Layer | Mechanism | Effect |
|---|---|---|
| Claude config | throwaway `CLAUDE_CONFIG_DIR` (`mktemp`) | no auto-memory / CLAUDE.md / rules / history / other plugins |
| hpc-bridge state | isolated `HPC_BRIDGE_STATE_DIR` sandbox | no leaked facility cache; caching still works *within* the sandbox |
| shell env | `env -i` + subscription token | no stray `ANTHROPIC_API_KEY` outranks the token |
| **nothing forced** | no `HPC_BRIDGE_SSH_HOST` | the agent picks the facility; discovery/caching drive it ([#35](https://github.com/ryanchard/hpc-bridge/issues/35)) |

The ControlMaster is pre-opened in the sandbox (`~/.hpc-bridge-newuser/cm`) so it reuses passcode-free across runs; `HPCB_CLEAN_FRESH=1` wipes the sandbox cache for a genuine first-connect (discovery) test. Auth uses the Claude **subscription** token (`CLAUDE_CODE_OAUTH_TOKEN`), which authenticates a fresh config dir without touching `~/.claude`.

> [!note] Distinct from the Docker jail
> This is the **interactive, MFA-capable** counterpart to the [[Agentic testing - Plan B (runtime sandbox)|Docker harness]] (`run_smoke.sh`): the jail has no `~/.ssh/config` (so it can't do a bastion/MFA two-hop) and runs the agent headless; clean-session runs on the host so the ControlMaster + `~/.ssh/config` work — the only way to drive a facility like [[Aurora (PBS + bastion) bring-up|Aurora]] by hand.

## See also
[[Aurora (PBS + bastion) bring-up]] · [[MFA and interactive SSH auth]] · [[Configuration]] · [[Agentic testing - Plan B (runtime sandbox)]] · [[Home]]
