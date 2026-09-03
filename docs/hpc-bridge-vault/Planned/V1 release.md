# V1 release

> [!abstract] In one line
> The sprint plan for publishing hpc-bridge V1 — **scoped to what's built and tested**: the SSH-bootstrap path + zero-SSH MEP for consent-free facilities + BYO discovery, shipped to a **Claude Code plugin marketplace**. Decided 2026-08-21 (maintainer + Ryan, after Ryan's live MEP testing). This note is the plan of record — if a task runs long, come back here to reorient.

## The three decisions (2026-08-21)

1. **Scope — the "honest V1":** V1 supports the **SSH-bootstrap path** (curated + BYO-discovered facilities) and **zero-SSH facility-MEP dispatch for consent-free facilities** (the [[Endpoint reuse and MEP integration|M1 path]], validated live on globus1). Shipped examples: Anvil (SSH) + globus1 (MEP). **Explicitly deferred to V1.x:** M2 (the Globus browser-consent flow — required for consent-gated facility MEPs), MFA-bootstrap facilities ([#3](https://github.com/ryanchard/hpc-bridge/issues/3): NERSC/ALCF/OLCF/TACC), the ACCESS catalog channel ([#7](https://github.com/ryanchard/hpc-bridge/issues/7)), customizable resources ([#2](https://github.com/ryanchard/hpc-bridge/issues/2)). *This supersedes the earlier "V1 = M1 + M2" framing in [[Endpoint reuse and MEP integration]] — M2 was never exercisable on globus1 (consent-free), and gating V1 on it gates V1 on a facility we don't have.*
2. **The long-task block-thrashing bug gets FIXED before V1** (not documented-around). Symptom (seen 2026-08-19 under cluster contention): a compute block is CANCELLED at 24–142 s repeatedly before a 180 s task completes, orphaning the `poll_task` handle — the agent polls forever. Pre-existing, SSH path, the [[Cost control]]/#21 area (block keep-alive vs the Parsl scaling strategy). Fix on its own branch after M1 merges.
3. **Distribution = a Claude Code plugin marketplace** — installable from the terminal (`/plugin` flow), not just `--plugin-dir`. Defines the Tier-3 work.

## The tiers (work backwards from "published")

**Tier 1 — merge PR #41 (`feat/mep-m1`)** *(in progress)*
- [x] General code review of the branch diff — done 2026-08-21 (independent reviewer on `src/` + maintainer pass on harness/docs): no merge-blockers; 4 should-fix (MEP account dropped on the startup-pin path; `$USER`-remainder scratch roots broke the session-shell env fingerprint; an offline MEP read as "allocating nodes…"; `stop_mep` drained a *running* task's handle) + 4 nits, **all fixed with tests** (commit `fix(review)`)
- [x] Clean green regression re-run on a quiet cluster — **all green**: wave 1 3/3 (`mep_compute_only`, `happy_path`, `endpoint_reuse_chain`, 2026-08-19); `endpoint_reuse`, `facility_cache`, `session_persistence` (08-19); `spend_gate_enforced`, `gated_provision`, `long_task_via_handle` (2026-09-01, once a node freed). The only red left in wave 2 was the `spend_refusal` grader gap (Tier 2).
- [ ] Address review findings → un-draft → squash-merge #41

**Tier 2 — V1 quality**
- [ ] **Fix the long-task block-thrashing bug** (own branch; root-cause first; `long_task_via_handle` + `long_job_30m` are the validation). **Evidence so far (2026-09-01):** the 08-19 blocks were `CANCELLED by <the pool user's own uid>` — the endpoint cancelled its own blocks (24 s, 28 s, 142 s; two at the same instant) — and it did **not** reproduce on a quiet node (the same scenario passed: one block, task ran to completion, stop released it). Ruled out: Parsl idle scale-in (needs `active_tasks == 0` *and* > `max_idletime` 600 s), the HTEX `MISSING` rewrite (a post-mortem label on already-ended jobs, not a cause), and our manager config (no `idle_heartbeats` override — UEP idle is the Globus default). The cancel is issued when the **manager signals a UEP shutdown** (`endpoint_manager … Signaling shutdown of user endpoint` → SIGTERM → the interchange's Parsl provider `scancel`s its blocks). **Next:** (1) make the harness capture the UEP + manager `endpoint.log`s into the provenance bundle at teardown (the 08-19 logs were deleted before anyone read them — the harness key *can* read the pool user's `~/.globus_compute`); (2) reproduce under load (saturated cluster / slow `worker_init`) and read *why* the manager shut the UEP down.
- [ ] **New-user story:** verify BYO discovery end-to-end as a stranger (no curated entry, own Globus login from scratch — the [[Credential seeding]] flow) and document it
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
