# Cost control

> [!abstract] In one line
> The load-bearing net is **idle block release** (`min_blocks=0` + `max_idletime`): the compute node self-releases after the last task, so a forgotten session can't bleed allocation. A spend clock and the [[Resource shapes & the spend floor|spend floor]] guard the rest.

## The three nets

1. **Idle-release (back end).** The scheduler provider (`SlurmProvider` **or** `PBSProProvider` — scheduler-neutral) runs `min_blocks=0` with `max_idletime` (default 600 s), so the compute block — the thing that costs allocation — self-releases after the last task (validated live on Anvil). This is the safety net even if nothing else fires.
2. **The spend clock.** `session_spend` ([[cost]] `estimate_spend` = elapsed × nodes × `charge_factor`) is driven by **true worker presence** (the canary, [[Warmth, the canary & cold-start]]) and **accrued across warm intervals** — banked on each warm→cold transition so it survives idle-release without over-counting the idle gap. It rides every result. `charge_factor` defaults to `0.0` (free local dev).
3. **The spend floor (front end).** A billed block won't *start* without `confirm_spend=True` — see [[Resource shapes & the spend floor]].

> [!note] Long work is a *task*, not a detached process ([#21](https://github.com/ryanchard/hpc-bridge/issues/21))
> Idle-release keys off **Compute-task activity**, not node CPU. A long *foreground* task keeps the block busy, so it runs to completion (retrieved via [[The MCP tools|poll_task]] once it outlives the sync-wait) and is never released mid-run — its ceiling is the **block walltime** (the worker kills it at ~that, exit 124), optionally capped by `HPC_BRIDGE_MAX_TASK_S`. A **detached** process (`nohup … &`) is *not* a Compute task, so the block idle-releases out from under it — the classic footgun. `session_spend` accrues across the whole run: a running task keeps the warm clock ticking (the [[Warmth, the canary & cold-start|canary is short-circuited]] while it holds the worker).

`stop_endpoint` means **stop spending, not destroy the endpoint**: it `scancel`s the billed block over the **login endpoint (AMQP, no SSH)** and **leaves the manager online**, so the next session reuses it with zero SSH ([[Standing up the endpoint|SSH-once]]). It drops the billed (slurm) shape (a later run re-provisions a fresh block) and keeps the login shape, the manager, and the [[state|pin]].

**Confirmed-or-honest release (#24).** The cancel rides the login shape, which may be **cold** at stop time (the worker scaled in) — the first `scancel` dispatch then comes back `cold_start`, not `complete`, so the cancel is *unconfirmed*. That first hit wakes the worker, so `_release_blocks_over_login` **retries a bounded few times** (`HPC_BRIDGE_RELEASE_ATTEMPTS`×`_BACKOFF_S`, default 3×6s) to *confirm*. If confirmed → `status="down"`. If still cold after the retries → **`status="draining"`, never `"down"`**: spend is not verifiably stopped, so the status must not say it is (an agent that reads "down" walks away while the block burns). idle-release (`min_blocks=0`+`max_idletime`, ~10 min) is the backstop, and re-calling `stop_endpoint` (channel now warm) confirms. Before the fix, stop returned `status="down"` with a notice admitting *"cancel not confirmed"* — a contradiction measured at ~5% of stops; see [[Agentic testing - Plan B (runtime sandbox)|the harness]] `stop_is_honest` invariant. Fully pulling the endpoint down (`gce stop` — the facility's `teardown()`) is a separate, rarer operation, **not** done here (not yet exposed as a tool). (The old multi-minute "stop hang" was `runner.close()` blocking on `Executor.shutdown(wait=True)`, not the cancel — fixed in [[runner]].)

> [!note] session_spend ≠ real SU charge (today)
> Because `charge_factor` defaults to 0 and isn't set on Anvil, `session_spend` reads 0 even while Slurm charges the allocation. The authoritative number is the live `mybalance` delta; wiring a real per-facility rate is [[Discovery today|future discovery]] work, not current behaviour.

## See also
[[Resource shapes & the spend floor]] · [[Warmth, the canary & cold-start]] · [[cost]] · [[server]]
