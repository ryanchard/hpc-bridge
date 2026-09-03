# hpc-bridge — handoff (state of the repo)

_Snapshot: 2026-09-03, end of day. V1 sprint: **Tier 1 DONE** (#41); the 08-19 "block-thrashing" thread **closed** (#43 harness, #44 product, #45 follow-ups); **fake-cluster spike merged** (#46). **Tier 2 in progress — see "Resume here" just below.**_
_For a week-long handoff to a co-dev. Design rationale lives in `docs/hpc-bridge-vault/`; this file is the live state + how-to-run + gotchas on top._

## TL;DR

**M1 — "consume a facility-run multi-user Globus Compute endpoint (MEP) with zero SSH" — is code-complete and validated live on globus1.** This is the V1-gating objective (Phase 2 in the vault). A catalogued MEP entry now resolves to a compute-only `MEPFacility` that dispatches over AMQP through the facility's identity mapping (no SSH bootstrap, ever). All unit tests green (**334 passed, 2 skipped**; harness **52 passed**). The SSH personal-endpoint path is unchanged and still green.

What's left before merge: a **general code review** of the branch, a **clean agentic regression re-run** on a quiet cluster (a couple of wave-2 scenarios were only blocked by real cluster contention, not by our code), and two small **follow-ups** (below).

## Resume here (paused 2026-09-03 evening — subscription allocation)

**Where we stopped:** Tier 2 of the V1 sprint — `docs/hpc-bridge-vault/Planned/V1 release.md` is the plan of record. Tier-2 item 1 was re-scoped into **B — in-terminal Globus login** and **A — public registry**. Everything is on `main`; there are **no open branches**.

- **Tier-2 B (in-terminal Globus login) is BUILT** on `feat/in-terminal-login` (PR #48): `needs_login` phase gating `connect_facility` first; `authenticate(force, mode)` / `complete_login(code)` tools; loopback browser flow + paste-back fallback (headless-safe); minimum consent (Search reads are anonymous). L1–L4 live-validated, independent review done and its fixes in. Next: merge #48 → Tier-2 A (default registry index, `list_facilities` out of the box, 'add your facility' docs). Design + decisions: `Planned/In-terminal Globus login.md`. **Self-test as a fresh user:** `scripts/fresh_user_session.sh` (scratch token + state dirs, launched outside the repo so no repo-local config applies; `--reset` = brand-new user again; then say "connect me to globus1").
- **A (public registry)** follows B: ship the index id as the plugin default; `SearchCatalog` reads **anonymously** (Search needs auth only for non-public entries — verified against the docs); `list_facilities` works out of the box; "add your facility" = seed YAML + PR. Open: create a purpose-named production index (today's is `hpc-bridge-test`) and name the curator of record.
- **Fake-cluster tier** (~½ day) is scheduled in the plan; the spike works today: `agentic/fakecluster/bin/up.sh` → `prove-sbatch.sh` → `stretch.sh`; `bin/down.sh` stops it.
- **Docker Desktop was fully quit** at the stop (it was misbehaving) — restart it before any live run.
- Merged today, in order: #41 (M1), #42, #43 (harness isolation), #44 (`poll_task` ORPHANED), #45 (harness follow-ups), #46 (fake-cluster spike), and this pause point. Unit suite 345, harness 61.

## Building project context — start with the vault

**If you're coming to this project cold — a new dev, or a fresh AI/agent session building context — read the vault first: `docs/hpc-bridge-vault/`.** It's the maintainer's map of how hpc-bridge actually works, kept in step with the code. It ships in this repo (plain markdown — you don't need Obsidian; wikilinks `[[X]]` just mean the file `X.md` somewhere in the vault).

**Entry point: [`docs/hpc-bridge-vault/Home.md`](docs/hpc-bridge-vault/Home.md)** — it's the index and has its own reading order. The short path:

1. **`Home.md`** — the one-paragraph "what this is" + the map of everything below.
2. **`Happy path.md`** — the end-to-end flow (select facility → stand up/reuse endpoint → gate spend → run on a compute block), the fastest way to see the whole system at once.
3. **Concepts**, in order: `Concepts/Two-channel architecture.md` (SSH control plane vs the AMQP hot path — the central idea) → `Concepts/Standing up the endpoint.md` → `Concepts/MEP & templated endpoints.md`. Then `Concepts/Facility catalog.md` and `Concepts/Resource shapes & the spend floor.md`.
4. **For the current work (M1/MEP):** `Planned/Endpoint reuse and MEP integration.md` is the plan of record.
5. **To understand a specific source file:** `Modules/` has a note per `src/hpc_bridge/` module (`Modules/server.md`, `Modules/facility-remote.md`, …) — read the module note beside the code.

The vault holds the *why* (design rationale, decisions, the reading order); this `HANDOFF.md` holds the *now* (live state, how to run, what's next). Read the vault to understand the system; read on here for where it stands today. (Contributing to the vault itself? `docs/hpc-bridge-vault/Vault style guide.md` first.)

## What M1 added (the branch)

Read the commits in order — each is a self-contained step with a full message:

| Commit | What |
|---|---|
| `e2891be` | Foundations: `ssh_host` optional, `Defaults.init_blocks`, `_reachable` + no-client-templating validators; **`MEPFacility`** (`src/hpc_bridge/facility/mep.py`); + the runner/session-shell/scratch hardening a MEP forced (see below) |
| `d808ae0` | 3a — a `compute_mep_uuid` entry builds a `MEPFacility` (`_facility_from_entry` dispatches on it first; MEP wins; no SSH lookup) |
| `34a73a6` | 3b–3d — the server seams: `_shape_reject`, `_connect_mep` (attach, no block), `_stop_mep` (draining-only), teardown-as-detach |
| `528aea3` | The `globus-cluster.yaml` seed + "compute-only facility" guidance in `skills/driving-hpc/SKILL.md` + `commands/hpc-connect.md` |
| `b4be77a`, `efd3a47` | Vault: MEP plan updated (M4 folded into M1); the Search index UUID + its entries recorded |
| `33912f1` | **Agentic harness** — coverage-audit fixes (see "Testing") |
| `4999026` | `mep_compute_only` scenario (the live MEP path) |
| `4a8fdfd` | `agentic/whoami_globus.py` — which identity + scopes a `storage.db` holds |

**The one design idea to internalize:** a facility MEP has **no login shape** (its schema rejects our `LocalProvider`/`compute:false`). `MEPFacility` declares `supported_shapes = ("compute",)` and the server *derives* everything else from that one fact via `getattr(app.facility, "supported_shapes", …)`: no login shape ⇒ no free channel for the allocation listing / the #32 pilot query / the scancel release ⇒ **stop is draining-only, teardown is a detach, every shape is billed.** `SlurmFacility`/`LocalFacility` are untouched (they get the default = every shape).

Hardening that shipped here because a MEP exposed it (all pre-existing, SSH path benefits too):
- **A non-timeout canary failure** (a web-service-rejected submit) used to leave the SDK Executor shut down → `Executor is shutdown` forever while the caller saw "allocating nodes…". Now: keep the failed `CanaryResult`, mark the runner stale (rebuilt next call), surface the error. (`_confirm_worker` in `server.py`.)
- **`session_shell`** expands a `$HOME/`-relative scratch root on the *worker* (`"$HOME"'/rest'`) instead of quoting it literal — needed for a facility whose local username we can't know client-side.
- **`_resolve_scratch_root`** expands `~` client-side only for `LocalFacility`; a remote root stays verbatim.

## The live infrastructure (facts you'll need)

- **The MEP:** `globus-cluster-mep` on globus1, UUID **`da3df250-4013-4d69-942c-eef1568f860c`**. Deployed + administered by the globus-cluster admin (not by us). Identity mapping: **`gusellerm@uchicago.edu` → local `glabs`**. Pinned to `globus-compute-endpoint==4.15.0` (the seed's `worker_init` pins it **unconditionally** — a version-skewed worker fails cryptically). `AccountingStorageEnforce=none` so no Slurm `--account` is needed (`account_required: false`).
- **The Globus Search index (the runtime catalog):** **`6ff95fb8-1113-42be-a811-3d1cb5a67bd5`** (display name `hpc-bridge-test`, owned by the maintainer's Globus identity). Entries: `purdue:anvil` (SSH) and `globus:globus1` (MEP). **It's set in `.claude/settings.local.json` (gitignored) — NOT in your shell** (that's the #1 gotcha below). The `globus:globus1` entry is already ingested.
- **`agentic/whoami_globus.py`** — read-only check of which identity + scopes a `storage.db` holds (the two facts that decide whether a run can reach the MEP and the catalog). Run it if a live run behaves unexpectedly.

## How to run things

```bash
# Unit tests (fast, hermetic, no cluster) — the default gate.
python -m pytest -q                                  # 334 passed, 2 skipped

# Agentic harness graders (also hermetic — proves the graders, not the product).
python -m pytest agentic/harness/test_invariants.py -q   # 52 passed

# Live agentic scenarios (need globus1 + Docker + agentic/.env; cost money).
#   agentic/.env holds: CLAUDE_CODE_OAUTH_TOKEN (subscription, NOT API key),
#   HPCB_TEST_GLOBUS_DB (a storage.db whose identity the MEP maps), HPCB_TEST_SSH_* .
./agentic/run_smoke.sh happy_path                    # one scenario
python3 agentic/run_suite.py --scenarios happy_path --repeat 3 --concurrency 3

# The MEP scenario needs the catalog index forwarded into the jail:
HPC_BRIDGE_SEARCH_INDEX=6ff95fb8-1113-42be-a811-3d1cb5a67bd5 \
  ./agentic/run_smoke.sh mep_compute_only
```

The pre-merge regression set + costs (~30–40 min, ~$7–10 for a full pass; subscription-billed) are in **`agentic/README.md`** — read it before a live run.

## The agentic testing framework (`agentic/`)

This repo ships a **live-agent regression harness** — its own test tier, separate from the unit tests. It drives a **headless Claude Code agent** against the real **globus1** cluster, once per scenario, inside a **disposable Docker container** holding only scoped (non-admin) credentials, and **grades the agent's behaviour from its tool-call trace** rather than from return values. It's what proves the *product* works end-to-end (an agent can actually drive HPC through hpc-bridge), which unit tests can't. `agentic/README.md` is the authoritative guide; this is the orientation.

**Two things it is not:** it is **not** collected by `python -m pytest -q` (that's the hermetic tier), and it is **not** free — each scenario runs a real agent against a real cluster and bills your Claude subscription. Run it nightly / on demand / before merging anything that touches connect, discovery, endpoint naming, the local-discovery cache, or stop.

**How one run works** (`harness/run.py`): `SETUP` (optional cluster prep) → drive the agent on the scenario's `PROMPT` (a `human_sim.py` persona answers any `AskUserQuestion`) → grade the trace against **invariants** (`harness/invariants.py`, deterministic checks like "no raw SSH after the endpoint is up", "spend was confirmed before a billed block", "stop was honest") → **world postchecks** over SSH (did a block actually get left running?) → teardown. Every run writes a **provenance bundle** to `agentic/runs/<id>/` (`record.json` with the grading, `messages.jsonl`, `transcript.md`) — gitignored, but they're how you debug a failure after the fact, and `harness/regrade.py` can replay a stored bundle through the *current* invariants offline.

**A scenario** is one file in `agentic/scenarios/` declaring a `PROMPT`, a persona, `EXTRA_INVARIANTS`, `EXPECT_OK` (which invariants gate the verdict), and optional `SETUP`/`POSTCHECKS`. To add coverage you add a scenario + (usually) a grader, and a hermetic unit test for the grader in `harness/test_invariants.py` — that last part is the discipline that lets a green run be trusted. `mep_compute_only.py` is the newest and a good template for the MEP path; `happy_path.py` for the SSH path.

**Running it:**
```bash
# Grading core — hermetic, fast, no cluster (run this whenever you touch a grader):
python -m pytest agentic/harness/test_invariants.py -q            # 52 passed

# One live scenario (needs Docker + globus1 + agentic/.env):
./agentic/run_smoke.sh happy_path
HPCB_NO_SKILL=1 ./agentic/run_smoke.sh spend_gate_enforced        # ablation: withhold SKILL.md
HPC_BRIDGE_SEARCH_INDEX=6ff95fb8-1113-42be-a811-3d1cb5a67bd5 \
  ./agentic/run_smoke.sh mep_compute_only                         # the MEP path (needs the catalog index)

# A matrix / repeats, staggered + capped (globus1 SSH + subscription-rate headroom):
python3 agentic/run_suite.py --scenarios happy_path --repeat 3 --concurrency 3
```
**Prerequisites** (one-time, in `agentic/.env` — gitignored): `CLAUDE_CODE_OAUTH_TOKEN` (subscription, from `claude setup-token` — **not** an API key), `HPCB_TEST_GLOBUS_DB` (a Globus `storage.db` whose identity the target facility maps — for the MEP that means `gusellerm@uchicago.edu`; check with `python agentic/whoami_globus.py`), and the scoped SSH test user/key (`HPCB_TEST_SSH_*`, default user `hpcbridge-test`). Full setup + the pre-merge regression set are in `agentic/README.md`; the design rationale is `docs/hpc-bridge-vault/Planned/Agentic testing - Plan B (runtime sandbox).md`.

## Where things are

> The **vault (`docs/hpc-bridge-vault/`) is committed in THIS repo** — not a submodule, not a separate remote. It ships with the code (same `ryanchard/hpc-bridge` remote, already in PR #41). It's an Obsidian vault (has `.obsidian/`), so open that folder in Obsidian for the wikilinks/graph — but every file is plain markdown you can read anywhere.


- `src/hpc_bridge/server.py` — the FastMCP tools + all the orchestration seams (`_connect_facility`, `_connect_mep`, `_ensure_endpoint_up`, `_run_shell`, `_stop_endpoint`/`_stop_mep`, `_shape_reject`).
- `src/hpc_bridge/facility/` — `remote.py` (SSH `SlurmFacility`), `local.py`, **`mep.py`** (new), `base.py` (the `Facility` protocol + `EndpointHandle`).
- `src/hpc_bridge/catalog/` — `entry.py` (the `CatalogEntry` model), `search.py` (the runtime index client), `seed/*.yaml` (curator ingest sources), `ingest.py` (`hpc-bridge-catalog` CLI).
- `docs/hpc-bridge-vault/` — the design record (Obsidian). Start at `Planned/Endpoint reuse and MEP integration.md` (the MEP plan) and `Concepts/Facility catalog.md`.
- `agentic/` — the live-agent regression harness (`harness/`, `scenarios/`, `run_smoke.sh`, `run_suite.py`, `README.md`).

## Live validation so far

- **Wave 1 (2026-08-19): 3/3 green.** `mep_compute_only` passed on its first live run — the full chain: index → `MEPFacility` → identity-mapped compute (`whoami` → `glabs`) → draining-only stop → the facility's idle-release reclaimed the block (world check confirmed). `happy_path` + `endpoint_reuse_chain` confirm the SSH path is intact.
- **Wave 2: partial, but every failure explained and none an M1 regression** — `gated_provision` / `long_task_via_handle` / the `spend_gate_enforced` world-check were blocked by **real cluster contention** (another user held globus2/3 for 1.5 days); the gate logic itself passed. `spend_refusal` tripped a **grader gap** (the agent was *more* cost-safe than the scenario models — it declined proactively without being asked). Re-run the three contention-affected scenarios on a quiet cluster to close them out.

## Next steps (priority order)

> **2026-08-21 — the V1 sprint is ON.** Scope, decisions, and the full tiered punch-list live in **`docs/hpc-bridge-vault/Planned/V1 release.md`** (the plan of record — reorient there if a task runs long). Headlines: V1 = SSH path + consent-free MEP + BYO (M2 deferred); the long-task block-thrashing bug gets **fixed** before V1; distribution = a Claude Code plugin **marketplace**. Items 1–2 below are its Tier 1.

1. **General code review of the branch** (the M1 diff), then merge the draft PR. _(Not yet done — the last planned step before merge.)_
2. **Clean regression re-run** of `gated_provision`, `long_task_via_handle`, `spend_gate_enforced` when globus1 is quiet (`sinfo -N` to check; another user was saturating it 08-19→08-21).
3. **Follow-up A — (was) long-task block-thrashing — RESOLVED as a harness artifact (2026-09-03):** two concurrent `run_suite` invocations shared pool user `test-00` and one run's user-wide teardown `scancel`led the other's blocks. Fixed in the harness (cross-process pool claims, run-scoped teardown, endpoint-log capture — `agentic/harness/pool.py`, `cluster_ops.py`). The surviving *product* item: `poll_task` must detect a block killed externally instead of hanging (Tier 2 in `Planned/V1 release.md`).
4. **Follow-up B — `spend_refusal` grader** (`agentic/scenarios/spend_refusal.py`): `refusal_exercised` should also count a *proactive* refusal ("I won't spend without asking"), not only a declined question. Cheap fix.

## Open decision

- **Should the `globus:globus1` seed drop `interface: enP7s7`?** We forward it, which *overrides* the MEP template's own NIC default; the admin's verified UEC didn't include it. `enP7s7` is correct per the cluster facts, but a wrong NIC on an `init_blocks:1` block is cold-forever-but-billed, so letting the facility's template own the NIC is arguably safer for MEP entries. Confirm with the cluster admin or just drop it from `src/hpc_bridge/catalog/seed/globus-cluster.yaml` and re-ingest.

## Gotchas (things that cost us time)

- **`HPC_BRIDGE_SEARCH_INDEX` is not in your shell.** It lives in `.claude/settings.local.json` (gitignored), so the running MCP server has it but an interactive `hpc-bridge-catalog …` invocation does not — pass the UUID literally. Symptom: `run_suite`/CLI can't resolve a catalogued machine, or `hpc-bridge-catalog` errors "seed_path required" (the empty var was swallowed as the index arg).
- **`#39` registration-lag** is real and now *visible* (the `first_details_connect_succeeds` reported invariant): the first `connect_facility(details=…)` on a fresh bootstrap often returns `failed` ("could not find endpoint … in list output") and the retry succeeds. It's reported, not gating. Don't mistake it for an M1 regression.
- **Cluster contention** silently fails billed agentic scenarios (block never scheduled → `compute_ran`/`ends_with_stop`/world checks break together). Check `sinfo` / `squeue` before trusting a billed-scenario failure; use `sacct -u <pool-user>` to see whether a block ran and got CANCELLED.
- **Seed `aliases` are not indexed** — `ingest.py` drops them; at runtime use the `id` (`globus1`) or subject (`globus:globus1`), as `list_facilities` shows. `globus-cluster-mep` won't resolve as a facility arg.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. PR bodies end with the Claude Code generation line. `agentic/.env` is gitignored — never commit secrets.
