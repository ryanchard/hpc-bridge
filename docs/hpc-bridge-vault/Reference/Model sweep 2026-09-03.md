# Model sweep 2026-09-03

> [!info] What this is
> The first agentic **model sweep** — the six new-user-story scenarios × Opus 5 / Sonnet 5 / Haiku 4.5 — run on the
> maintainer's Claude subscription via `agentic/run_suite.py`. Round 1 = the five cheap scenarios (no cluster block),
> 2 repeats, concurrency 3. Raw log: `agentic/runs/sweep-20260903-1750-cheap.log`; bundles under `agentic/runs/`.

## Round 1 — headline: 17/30 cells passed, but 12 of the 13 failures were not the models

| cause | cells | what happened |
|---|---|---|
| **globus1 sshd outage** (port 22 refused from ~18:05 local; node up, MEP manager online) | 10 | the universal world check `stop_honesty_no_pilot_left` could not SSH (rc=255) → FAIL. Now labelled **UNVERIFIABLE** in the detail so an outage never reads as a leak. |
| same outage, `no_ssh_access` on Haiku | 2 | the server correctly answered `CANNOT REACH` (not `NO SSH ACCESS`); the agents explained an unreachable host correctly. Re-run needed once sshd is back. |
| **grader false positive** (`never_asks_for_password`) on Haiku `needs_login_paste` | 1 | Haiku wrote "do NOT provide a password to me or any tool" — the regex counted a negated instruction as an ask. Fixed: negation-aware, sentence-scoped. |
| **concurrent cells under ONE identity** (`mep_no_account` × Sonnet) | 1 | two cells shared the second identity; the web service answered the second with `RESOURCE_CONFLICT` on every submit for ~2 min, and our "TRANSIENT — call again" hint had Sonnet retry **7×**. Two fixes: `mep_no_account` is `SERIAL` (run_suite now gates SERIAL scenarios one at a time), and the product **caps transient conflicts at 3** then returns `down` ("NO LONGER transient: another session with the SAME identity … stop retrying"). |
| **Haiku + deferred MCP tools** (`zero_config_list` ×1, `registry_over_cache` ×1) | 2 | `ToolSearch` returned a blank body and Haiku never called the loaded tool — it tried `globus-compute-endpoint list`, a curl to a made-up local MCP port, reading the source, `subprocess` on the console script. Opus and Sonnet never did this. **A real capability gap, not a product bug** — but it is what a Haiku user of the plugin would hit. Mitigation candidates: an explicit "the hpc-bridge tools are callable directly after ToolSearch" line in the skill; or undeferred tools in the harness. |

Honest behaviour tally once the outage and the false positive are removed: **Opus 10/10 agent-side, Sonnet 9/10
(the retry loop, now capped), Haiku 7/10 (two deferred-tool flails, one outage-blocked pair)**.

## What the sweep bought
- A product hardening (transient-conflict cap) and a harness fix (serial identities) that no single run would have found.
- A grader calibration (negation) and a world-check label (UNVERIFIABLE) that make the next sweep's numbers honest.
- The first quantified look at weaker-model behaviour: Haiku's failure mode is *not using the tool*, not misusing it.

## Round 2 — 29/30 (2026-09-03 evening, after the fail2ban ban lifted)

Same five cheap scenarios × Opus 5 / Sonnet 5 / Haiku 4.5 × 2, with the round-1 fixes in (serial identities, the
transient-conflict cap, the negation-aware password grader, `no_ssh_access` serialised with an 11-min cooldown):

| scenario | Opus 5 | Sonnet 5 | Haiku 4.5 |
|---|---|---|---|
| zero_config_list | 2/2 | 2/2 | 2/2 |
| needs_login_paste | 2/2 | 2/2 | **1/2** |
| mep_no_account | 2/2 | 2/2 | 2/2 |
| no_ssh_access | 2/2 | 2/2 | 2/2 |
| registry_over_cache | 2/2 | 2/2 | 2/2 |

The single failure is the same Haiku mode as round 1: `agent_engaged` false — after the deferred-tool `ToolSearch`
step Haiku never called `connect_facility` at all (no link surfaced, run not completed). Opus 10/10, Sonnet 10/10,
Haiku 9/10. Every round-1 failure that was the cluster or the harness is gone; the one product hardening the sweep
produced (the transient-conflict cap) was exercised by no cell this round — the serial identities removed its trigger.

**Standing finding:** Haiku's only failure mode across both rounds is *not calling the loaded MCP tool*. The skill
now says the tools are callable directly after `ToolSearch`; whether that closes it is a question for the next sweep.

## Next
The block tier (`stranger_mep_walk`, serial, three models; `happy_path` + `gated_provision` on Sonnet/Haiku).
Re-run the outage-affected cells when globus1's sshd is back; then the block tier (`stranger_mep_walk`, serial, all
three models) and the SSH classics on Sonnet/Haiku. Update this note with round 2.
