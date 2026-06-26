# Cost control

> [!abstract] In one line
> The load-bearing net is **idle block release** (`min_blocks=0` + `max_idletime`): the compute node self-releases after the last task, so a forgotten session can't bleed allocation. A spend clock and the [[Resource shapes & the spend floor|spend floor]] guard the rest.

## The three nets

1. **Idle-release (back end).** The `SlurmProvider` runs `min_blocks=0` with `max_idletime` (default 600 s), so the compute block — the thing that costs allocation — self-releases after the last task (validated live on Anvil). This is the safety net even if nothing else fires.
2. **The spend clock.** `session_spend` ([[cost]] `estimate_spend` = elapsed × nodes × `charge_factor`) is driven by **true worker presence** (the canary, [[Warmth, the canary & cold-start]]) and **accrued across warm intervals** — banked on each warm→cold transition so it survives idle-release without over-counting the idle gap. It rides every result. `charge_factor` defaults to `0.0` (free local dev).
3. **The spend floor (front end).** A billed block won't *start* without `confirm_spend=True` — see [[Resource shapes & the spend floor]].

`stop_endpoint` is the explicit exit: `teardown` **`scancel`s the block and `gce stop`s the manager — both over SSH** (each bounded by `_TEARDOWN_SSH_S`=30 s; the endpoint is being destroyed, so SSH — needed for `gce stop` anyway — is the reliable tool), then resets session state. (The old multi-minute "stop hang" was `runner.close()` blocking on `Executor.shutdown(wait=True)`, not the cancel — fixed in [[runner]].)

> [!note] session_spend ≠ real SU charge (today)
> Because `charge_factor` defaults to 0 and isn't set on Anvil, `session_spend` reads 0 even while Slurm charges the allocation. The authoritative number is the live `mybalance` delta; wiring a real per-facility rate is [[Discovery today|future discovery]] work, not current behaviour.

## See also
[[Resource shapes & the spend floor]] · [[Warmth, the canary & cold-start]] · [[cost]] · [[server]]
