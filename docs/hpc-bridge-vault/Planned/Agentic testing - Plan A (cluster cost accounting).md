# Agentic testing — Plan A (real-cluster cost accounting)

> [!warning] Planned · transient
> Cluster-side enablement so the **cost-gating** scenarios can run against the REAL globus1 cluster, not only the fake-seams tier. Executed in the **`globus-cluster-docs`** Ansible repo (`globus-admin/`), not in hpc-bridge. Companion: [[Agentic testing - Plan B (runtime sandbox)]]. **Status: step 1 (test user + pool) DONE; steps 2–5 (SU cap · enforcement · `mybalance` shim · facility wiring) remain the plan.** Delete once landed + folded into the globus1 testbed.

## Goal
Give a dedicated, **SU-capped test user** on globus1 a real allocation balance — *without restricting any other user* — and expose it through a `mybalance`-compatible command, so hpc-bridge's allocation discovery + spend gate exercise against **real data**. Today globus1 records usage (sacct works) but `AccountingStorageEnforce=none` and has no balance tool, so cost-gating can only run on fakes (the globus1 testbed).

## Why (the gap this closes)
The most safety-critical agent behaviour — *surface the balance → gate → `confirm_spend`* — needs a real balance to reason over. A free, unmetered cluster can't provide one, so that behaviour otherwise lives only in the fake-seams tier. Closing it lets the **same** behaviour be regression-tested end-to-end on real Slurm.

## Invariants (must hold)
1. **No other user is restricted.** Limits go ONLY on the test association/QOS. Every existing `people:` user already has a `lab` association (the `slurm_accounting` role adds them), so turning on cluster-wide enforcement doesn't surprise-block anyone — enforcement only bites where a limit is set.
2. **Test user is non-admin** (`admin: false` ⇒ `labusers` only, no sudo).
3. **IaC + reversible.** Every change is in `group_vars/all.yml` + roles; reverting the vars and re-applying removes it. DB-password handling unchanged (generated on the controller, never in git).
4. **No hpc-bridge code change** — the shim emits the format the existing `mybalance` parser already reads (`catalog/parsers.py`). Fallback: add a real `sshare` parser if the shim is too lossy.

## Steps (in `globus-cluster-docs/globus-admin`)
1. **Test user — ✅ DONE (2026-07-01):** `hpcbridge-test` (uid 5015, `admin: false`, dedicated test key) exists on globus1 and is the harness' default identity. **Also done (2026-07-07): the pool `hpcbridge-test-00..09` (uids 5016–5025, same key)** — one per suite concurrency slot, so squeue/home/storage.db never bleed across parallel runs. (Related cluster-side enablement, recorded in Plan B: the ufw per-source SSH rate limit was raised to ~15 for concurrent bootstraps.)
2. **SU cap (scoped)** — add vars (`slurm_test_user`, `slurm_test_su_minutes`) and a `slurm_accounting` task that sets `GrpTRESMins=cpu=<N>` on *that user's* association (or a dedicated `test-su` QOS assigned only to it). Idempotent (check-then-set).
3. **Enable enforcement** — set `AccountingStorageEnforce=safe` in the slurm.conf template (`safe` = limits + associations, and blocks a job that can't finish within the remaining balance — the clean "out of allocation" signal). Pre-check: every `people:` user has an association (they do).
4. **`mybalance` shim** — deploy `/usr/local/bin/mybalance` (Ansible `copy`): a few lines wrapping `sshare`/`sacctmgr` to print the test user's remaining `GrpTRESMins` in the exact columns hpc-bridge's `mybalance` parser expects (account · balance · units · type). Validate against the parser fixture.
5. **hpc-bridge facility wiring** — a session/BYO facility for globus1 with `allocation.command = mybalance`, `allocation.parser = mybalance`, so `connect_facility` returns a real balance. Session-local first; curate into the index later.

## Verification
- As `hpcbridge-test`: `mybalance` prints a balance; `sacctmgr show assoc user=hpcbridge-test` shows the `GrpTRESMins`.
- Submit a small job → balance decrements (`sshare`/`sreport`).
- Drive the cost-gate scenario through Plan B's harness: real balance surfaced → gate → `confirm_spend` → block runs; then exhaust the cap → submission blocked (the real allocation-exhausted path).
- Confirm an UNRELATED user can still submit (no regression).

## Deferred / open decisions
- **Per-test SU reset** so each run starts at a known balance: a `sacctmgr modify ... set GrpTRESMins=` reset step (admin-only) in the harness setup, vs a fresh per-suite balance. Decide alongside Plan B's isolation model.
- Curate globus1 into the index vs keep session-local.
- This is **optional + later** — cost-gating runs on the fake tier until this lands; it only *graduates* that behaviour onto real Slurm.

## See also
the globus1 testbed · [[Agentic testing - Plan B (runtime sandbox)]] · [[Resource shapes & the spend floor]] · [[Facility catalog]]
