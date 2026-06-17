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
| Discover **budget** | ✅ | `login_shell("mybalance")` / `xdusage` is the gather (live-proven), now **wired into the gate**: the `driving-hpc` skill surfaces the live balance + block cost in the `AskUserQuestion` before provisioning. Balance stays a per-facility **recipe**, not a server API. |

## Phase 1 — Policy gates (decide *how* to provision)

| Step | Status | Notes / gap |
|---|---|---|
| **Partition gate** | ✅ **live** | Discover → present partitions (node size, **live idle count**, caveats) via `AskUserQuestion`; user picks. 🔶 Currently **stops at selection** — does not yet feed provisioning (deliberate, for the dry-run test). |
| **Budget gate** | ✅ | The skill surfaces the balance + block cost and the human decides; **and** the deterministic floor is back as an *enforced confirmation*: a billed Slurm block returns `needs_confirmation` and starts nothing until `ensure_endpoint_up(confirm_spend=True)` — covering `run_shell` too (its canary would otherwise kick a block). The *response* (confirm / downgrade to `login` / stop) is the agent's. Not a re-added inert `allocation_remaining`: load-bearing regardless of `charge_factor`. |
| Other selectors (account, walltime, nodes) | ⬜ | Mostly should be **sensible-defaulted, not gated** ("gate, not interrogation"). |
| The human↔agent **gradient** | 🔶 | Rung 1 (human picks) built for partitions; rungs 2–4 (agent proposes → confirms → decides clear cases → always-gate-irreversible) not built. |

## Phase 2 — Provision

| Step | Status | Notes / gap |
|---|---|---|
| **Feed the gated selection → provision** | ✅ **live-proven on Anvil** | **The loop is closed.** `ensure_endpoint_up(partition=…)` overrides the shape's `user_endpoint_config` partition (validated at the boundary, runner rebuilt on change so the Executor doesn't carry a stale partition); the choice **persists for the session**. The `driving-hpc` skill now sequences *discover → gate → provision-onto-selection* (was "stop at the gate"). Live-proven: agent gated to `shared`, block provisioned + ran on `shared` (job 18223506, node a110), not the `debug` default. Unit-covered (`test_server.py`). |
| Provision the endpoint + submit the Slurm block | ✅ **live** | `SlurmFacility.provision` over SSH; idempotent (reuse running / configure-if-absent). **SSH-once:** `bootstrap` first checks the Globus **web** service (`find_online_endpoint`) and reuses an already-online endpoint over AMQP with **zero SSH** — only bootstraps over SSH when none is online (the MFA-minimizing keystone; relates #3, #8). |
| Confirm **warm** | ✅ **live** | The worker **canary** — warmth = a worker answered a trivial task, not merely manager-online. |
| Version-skew **preflight** | ⬜ | Skew is caught at *dispatch* (the canary parses worker py/dill); the cheaper *provision-time* preflight (compare local SDK vs remote `gce --version` first) is designed, not built. |
| Login-node **pinning** | ✅ **live** | The manager's FQDN is captured at `start`, recorded, and the CLI rebinds to it so later control-plane ops reach the right node. Fixes the round-robin teardown bug (manager was orphaned when the alias hit the wrong node). |

## Phase 3 — Work

| Step | Status | Notes / gap |
|---|---|---|
| Dispatch to the warm block | ✅ **live** | `run_shell` (Globus AMQP), session shell (cwd/env persist), structured outcomes, concurrency lock. |
| Cost tracking | ✅ | `session_spend` on every result (idle-aware billing clock). Budget **enforcement** is back: a billed block won't start without `confirm_spend=True` (the deterministic floor). |

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

1. ~~**Close the partition loop**~~ ✅ **done, live-proven on Anvil** — `ensure_endpoint_up(partition=…)` + the skill sequencing *discover → gate → provision*. Turned the dry-run gate into a real, still-gated stand-up (gated to `shared`, block ran there, not the `debug` default).
2. ~~**Budget as a second gate**~~ ✅ **done** (unit-covered; live Anvil proof pending) — `mybalance`/`xdusage` recipe in the skill → present balance + cost in the gate, and the deterministic **floor** re-added as an enforced `confirm_spend` (a billed block won't start without it). Response (confirm / downgrade to `login` / stop) left to the agent.
3. **The Stage-2 robustness slice** — version-skew preflight and `$SCRATCH` discovery (login-node pinning is now done). Exercises the discovery pattern against the current structure (which then *shows* what the `Facility` seam should become).
4. **`FacilityProbe` + the source map** — a structured discovery record (provenance) and the multi-source selection heuristic; then the ACCESS catalog as a discovery seed.
5. **(Later, earned)** discovery-*derived* profiles (generalization to unseen facilities), self-heal, multi-facility selection, and the credential broker.

**SSH-once thread (in progress).** Parallel to the above, a principle is being enforced: SSH is a *one-time bootstrap*, not a channel — every new SSH risks an MFA re-auth, so all sensing/behavior must ride the endpoint (AMQP + Globus web), not a fresh SSH. **Landed:** (1) `bootstrap` reuses an already-online endpoint via the web (`find_online_endpoint`) with zero SSH; (2) **discovery + the wait moved onto the login-shape AMQP channel** — the `driving-hpc` skill now sequences *establish endpoint (`shape="login"`) → discover via `run_shell(shape="login")` → gate → provision slurm → wait by polling `squeue` via the login shape*, with `login_shell` (SSH) demoted to a cold-start escape hatch. So a reconnect session is SSH-free end to end (relates #3 MFA, #8 gce-list→SDK). Follow-ups: **stale-reuse reconciliation** (re-bootstrap a reused endpoint that the web calls `online` but never warms); an optional server-side `wait_for_block` tool (move the poll loop into one call); full #8 (drop the `gce list` parse inside `provision()`).

The throughline: the **core runtime is built and live-proven** (provision → canary → dispatch → idle-release → teardown), and the **first policy gate** now runs end to end — discover → present partitions → **provision onto the selection** (item 1, closed and live-proven on Anvil). The second gate (budget) is now enforced too — discover balance → present cost → `confirm_spend` before a billed block starts. Everything past the gates — toward an automated, multi-source, multi-facility, self-healing agent — is designed and on the record, not yet built. Item 3 (the Stage-2 robustness slice: version-skew preflight + `$SCRATCH` discovery) is next.
