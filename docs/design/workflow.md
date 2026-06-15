# The Agentic HPC Workflow — State & Gaps

**Status:** living roadmap · 2026-06 · the end-to-end "ask the agent to do HPC work" flow, phase by phase, marking what's built so the dev gaps are explicit.

**Legend:** ✅ built (and where noted, **live-proven on Anvil**) · 🔶 partial · ⬜ designed, not built.

Companion docs: [`agent-tool-boundary.md`](./agent-tool-boundary.md) (tool vs agent judgment) · [`facility-discovery.md`](./facility-discovery.md) (discovery, recipes, policy gates, multi-source, ACCESS) · the [README](../../README.md) (what's built + how to run).

---

## Phase 0 — Reach & discover the facility

| Step | Status | Notes / gap |
|---|---|---|
| Reach the login node (SSH control channel) | ✅ **live** | `login_shell` — read-only, credential-isolated (key stays in the MCP server). |
| Discover facility **shape** (partitions, accounts, scheduler config) | 🔶 | ✅ the agent gathers via `login_shell` + the `sinfo`/`sacctmgr` recipe (live-proven). ⬜ Not built: the multi-source **source map** + selection heuristic (catalog vs live probe), the **ACCESS Operations API** catalog seed, a structured **`FacilityProbe`** record (gather is currently freeform, unrecorded), and a discovery-**derived** profile (`anvil_profile` is still hand-authored). |
| Discover **budget** | 🔶 | ✅ `login_shell("mybalance")` works as a gather (live-proven). ⬜ Not wired as a *gate* (Phase 1). |

## Phase 1 — Policy gates (decide *how* to provision)

| Step | Status | Notes / gap |
|---|---|---|
| **Partition gate** | ✅ **live** | Discover → present partitions (node size, **live idle count**, caveats) via `AskUserQuestion`; user picks. 🔶 Currently **stops at selection** — does not yet feed provisioning (deliberate, for the dry-run test). |
| **Budget gate** | ⬜ | Budget is discoverable but no gate presents/decides on it before provisioning, and the deterministic cost **floor** was removed in the subtraction pass — to be re-added per the boundary doc (hard floor + agent-owned response). |
| Other selectors (account, walltime, nodes) | ⬜ | Mostly should be **sensible-defaulted, not gated** ("gate, not interrogation"). |
| The human↔agent **gradient** | 🔶 | Rung 1 (human picks) built for partitions; rungs 2–4 (agent proposes → confirms → decides clear cases → always-gate-irreversible) not built. |

## Phase 2 — Provision

| Step | Status | Notes / gap |
|---|---|---|
| **Feed the gated selection → provision** | ⬜ | **The gap that closes the loop.** `config_template` accepts a partition (via the profile/env), but there is **no path to pass the gate's selection into provisioning** — `ensure_endpoint_up` provisions with the *fixed* profile partition. Need: a selection→provision wiring (e.g. `ensure_endpoint_up(partition=…)` or a per-call profile override) **and** the skill sequencing *select → provision* instead of stopping. |
| Provision the endpoint + submit the Slurm block | ✅ **live** | `SlurmFacility.provision` over SSH; idempotent (reuse running / configure-if-absent). |
| Confirm **warm** | ✅ **live** | The worker **canary** — warmth = a worker answered a trivial task, not merely manager-online. |
| Version-skew **preflight** | ⬜ | Skew is caught at *dispatch* (the canary parses worker py/dill); the cheaper *provision-time* preflight (compare local SDK vs remote `gce --version` first) is designed, not built. |
| Login-node **pinning** | ✅ **live** | The manager's FQDN is captured at `start`, recorded, and the CLI rebinds to it so later control-plane ops reach the right node. Fixes the round-robin teardown bug (manager was orphaned when the alias hit the wrong node). |

## Phase 3 — Work

| Step | Status | Notes / gap |
|---|---|---|
| Dispatch to the warm block | ✅ **live** | `run_shell` (Globus AMQP), session shell (cwd/env persist), structured outcomes, concurrency lock. |
| Cost tracking | ✅ | `session_spend` on every result (idle-aware billing clock). 🔶 No budget *enforcement* (the floor was removed). |

## Phase 4 — Release

| Step | Status | Notes / gap |
|---|---|---|
| Idle **auto-release** | ✅ **live** | `min_blocks=0` + `max_idletime` — the block (the SU charge) self-releases when idle. The load-bearing cost net. |
| Explicit teardown | ✅ **live** | `stop_endpoint` — stop the manager on the pinned node, **scancel the endpoint's Slurm block** (backstop so an ungraceful stop can't orphan held compute), reset session state. |

## Cross-cutting (not yet built)

| Capability | Status | Notes |
|---|---|---|
| **Self-heal / reconcile** | ⬜ | `restart` was removed; recovering *novel* failures is unbuilt (the agent *could* via `run_shell`/`login_shell`, but nothing scaffolds it). |
| **Multi-facility** ("which machine?") | ⬜ | Single-facility only (Anvil/local). Cross-facility selection + the per-facility credential matrix are unbuilt. |
| **Credential broker** (OTP facilities) | ⬜ | NERSC/ALCF/OLCF MFA + separate-UID broker unbuilt; Anvil works because its CLI SSH is key-only. |
| **Durable handles** | ⬜ | Results don't survive an MCP restart (no sqlite task store). |
| **ACCESS federation** (catalog seed, XDMoD budget, CILogon identity) | ⬜ | Researched; designed as **decoupled sources/recipes** (see `facility-discovery.md`). Catalog = a discovery seed; budget = a per-facility recipe; identity = CILogon behind the broker. |

---

## What to build next (prioritized)

1. **Close the partition loop** — wire the gate's *selection* → provisioning (`ensure_endpoint_up(partition=…)` + the skill sequencing *select → provision*). Smallest change with the biggest payoff: turns the dry-run gate into a real, still-gated, end-to-end stand-up.
2. **Budget as a second gate** — a `mybalance` recipe → present/confirm before provisioning, and re-add the deterministic **floor** (hard stop) with the *response* left to the agent. Cheap, no new credentials, reuses what's already tested.
3. **The Stage-2 robustness slice** — version-skew preflight and `$SCRATCH` discovery (login-node pinning is now done). Exercises the discovery pattern against the current structure (which then *shows* what the `Facility` seam should become).
4. **`FacilityProbe` + the source map** — a structured discovery record (provenance) and the multi-source selection heuristic; then the ACCESS catalog as a discovery seed.
5. **(Later, earned)** discovery-*derived* profiles (generalization to unseen facilities), self-heal, multi-facility selection, and the credential broker.

The throughline: the **core runtime is built and live-proven** (provision → canary → dispatch → idle-release → teardown), and the **first policy gate** (discover → present partitions) is built and live-proven but **stops before provisioning**. Everything past "present the gate" — toward an automated, multi-source, multi-facility, self-healing agent — is designed and on the record, not yet built. Item 1 is the seam between the two.
