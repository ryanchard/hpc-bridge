# V1 release

> [!abstract] In one line
> The sprint plan for publishing hpc-bridge V1 — **scoped to what's built and tested**: the SSH-bootstrap path + zero-SSH MEP for consent-free facilities + BYO discovery, shipped to a **Claude Code plugin marketplace**. Decided 2026-08-21 (maintainer + Ryan, after Ryan's live MEP testing). This note is the plan of record — if a task runs long, come back here to reorient.

## The three decisions (2026-08-21)

1. **Scope — the "honest V1":** V1 supports the **SSH-bootstrap path** (curated + BYO-discovered facilities) and **zero-SSH facility-MEP dispatch for consent-free facilities** (the [[Endpoint reuse and MEP integration|M1 path]], validated live on globus1). Shipped examples: Anvil (SSH) + globus1 (MEP). **Explicitly deferred to V1.x:** M2 (the Globus browser-consent flow — required for consent-gated facility MEPs), MFA-bootstrap facilities ([#3](https://github.com/ryanchard/hpc-bridge/issues/3): NERSC/ALCF/OLCF/TACC), the ACCESS catalog channel ([#7](https://github.com/ryanchard/hpc-bridge/issues/7)), customizable resources ([#2](https://github.com/ryanchard/hpc-bridge/issues/2)). *This supersedes the earlier "V1 = M1 + M2" framing in [[Endpoint reuse and MEP integration]] — M2 was never exercisable on globus1 (consent-free), and gating V1 on it gates V1 on a facility we don't have.*
2. **The long-task block-thrashing bug gets FIXED before V1** (not documented-around). Symptom (seen 2026-08-19 under cluster contention): a compute block is CANCELLED at 24–142 s repeatedly before a 180 s task completes, orphaning the `poll_task` handle — the agent polls forever. Pre-existing, SSH path, the [[Cost control]]/#21 area (block keep-alive vs the Parsl scaling strategy). Fix on its own branch after M1 merges.
3. **Distribution = a Claude Code plugin marketplace** — installable from the terminal (`/plugin` flow), not just `--plugin-dir`. Defines the Tier-3 work.

## The tiers (work backwards from "published")

**Tier 1 — merge PR #41 (`feat/mep-m1`)** ✅ *(done 2026-09-03)*
- [x] General code review of the branch diff — done 2026-08-21 (independent reviewer on `src/` + maintainer pass on harness/docs): no merge-blockers; 4 should-fix (MEP account dropped on the startup-pin path; `$USER`-remainder scratch roots broke the session-shell env fingerprint; an offline MEP read as "allocating nodes…"; `stop_mep` drained a *running* task's handle) + 4 nits, **all fixed with tests** (commit `fix(review)`)
- [x] Clean green regression re-run on a quiet cluster — **all green**: wave 1 3/3 (`mep_compute_only`, `happy_path`, `endpoint_reuse_chain`, 2026-08-19); `endpoint_reuse`, `facility_cache`, `session_persistence` (08-19); `spend_gate_enforced`, `gated_provision`, `long_task_via_handle` (2026-09-01, once a node freed). The only red left in wave 2 was the `spend_refusal` grader gap (Tier 2).
- [x] Address review findings → un-draft → squash-merge #41 — **merged 2026-09-03** (`main` @ `45547b6`, branch deleted). **Tier 1 complete.**

**Tier 2 — V1 quality**
- [ ] ~~Fix the long-task block-thrashing bug~~ → **re-scoped 2026-09-03 after root-cause: it was a HARNESS artifact, not a product bug.** Two concurrent `run_suite` invocations both allocated pool user `test-00` (per-process allocator, no cross-process claim) and one run's user-wide `scancel -u` teardown killed the other's live blocks (timestamps match to the second; the exact `sacct` signature — `CANCELLED by <pool uid>`, `None assigned` — was reproduced live with a pending dummy job + the verbatim teardown). Two items replace it:
  - [x] **Harness:** cross-process pool claims (`harness/pool.py`, flock), **run-scoped teardown** (delete only this run's endpoint; cancel only its `uep.<eid>` blocks — never `-u`), endpoint-log capture into every bundle, a manual `sweep_pool_user.sh` for stranded leftovers — PR `fix/harness-pool-isolation`.
  - [x] **Product (the surviving observation):** after the other run deleted its *endpoint*, `poll_task` hung for 20+ min. Now a pending task whose endpoint is offline/gone is reported as a terminal `failed` (ORPHANED) and its handle dropped (`_endpoint_gone` / `_orphaned_outcome`, PR `fix/poll-task-lost-endpoint`); a killed block under a live endpoint still reads `running` (Parsl relaunches it — polling is correct). **Live-checked 2026-09-03** on a real endpoint (globus1, pool user, no agent): long task → external `gce stop` → poll → `failed` ORPHANED. ✅
- [ ] **New-user story** — re-scoped 2026-09-03 into two product features (the index is meant as a PUBLIC registry; the Globus login should be in-terminal like the Cloudflare plugin's):
  - [ ] **B. In-terminal Globus login** — plan: [[In-terminal Globus login]] (a `needs_login` phase + `authenticate`/`complete_login` tools, riding the Compute SDK's own UserApp so seeding keeps working; loopback browser flow with paste-back fallback). Build order L1–L4; L4 is a live browser check with the maintainer.
  - [ ] **A. Public registry** — ship the index id as the plugin default; `SearchCatalog` reads **anonymously** (Search needs auth only for non-public entries — verified in the docs); `list_facilities` works out of the box; "add your facility" = seed YAML + PR. Open: create a purpose-named production index (today's is `hpc-bridge-test`) and name the curator of record.
  - [ ] **Then the stranger's walk** — fresh state → `needs_login` → browser → `list_facilities` → connect → run; fix what's rough; this shapes the docs.
- [ ] **End-user docs:** install + quickstart, the supported-facility matrix (Anvil · globus1-MEP · BYO), platform notes (local provisioning Linux-only; BYO/MEP cross-platform)
- [ ] `spend_refusal` grader accepts proactive refusal (small, agentic-only)

**Tier 3 — release engineering**
- [ ] Marketplace packaging + publish (`.claude-plugin/plugin.json` versioning, the marketplace entry, install-from-terminal verified)
- [ ] `0.1.0 → 1.0.0`, tag, changelog
- [ ] Security review (SSH + credential handling — run the security-review pass over the repo)
- [ ] Docs hygiene: absorb/retire the 5 leftover `docs/design/*.md` into the vault; final vault reconcile

## Standing decisions that shape the work
- MEP entries are **compute-only** (no login shape) with **draining-only stop** — settled in M1, don't relitigate.
- MEP config comes from the **Globus index, never the SSH probe** ([[Discovery channel model]]).
- Open question parked with the cluster admin: drop `interface` from the MEP seed so the facility template owns the NIC (safer; not V1-blocking).

## Where things stand
Live state, gotchas, and how-to-run: `HANDOFF.md` (repo root). M1 design: [[Endpoint reuse and MEP integration]]. The regression harness: `agentic/README.md`.
