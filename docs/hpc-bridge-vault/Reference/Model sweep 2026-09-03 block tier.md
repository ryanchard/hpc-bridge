# Model sweep 2026-09-03 — block tier

> [!info] What this is
> The second agentic model sweep, run overnight on the maintainer's subscription: the cells that bring up a **real
> Slurm block** on globus1. Suite A = [[stranger_mep_walk]] (the consent-free, zero-SSH facility-MEP walk; serial,
> one Globus identity) × Opus 5 / Sonnet 5 / Haiku 4.5 × 2. Suite B = `happy_path` + `gated_provision` (SSH
> bootstrap, BYO discovery, a billed block) × Sonnet 5 / Haiku 4.5 × 2. Logs: `agentic/runs/sweep-block-*.log`
> (the compute suite's first attempt is kept as `…-compute-20260903-attempt1.log`); bundles under `agentic/runs/`.
> Companion to [[Model sweep 2026-09-03]] (the cheap tier).

## Headline

| suite | cells | passed | notes |
|---|---|---|---|
| A · stranger's MEP walk (Opus, Sonnet, Haiku × 2) | 6 | **6/6** | every model: list → connect via the facility MEP (mapped identity, no SSH) → real compute run → teardown. 50–80 s per cell. |
| B · attempt 1 (happy_path + gated_provision, concurrency 2) | 5 of 8 run, then stopped | 1/5 | **not the models**: every globus1 node was held (two other users' day-long jobs + suite A's block) — pilots sat `PENDING (Resources)` for the whole run → `compute_ran` FAILED. Stopped by hand; see "node starvation". |
| B · attempt 2 (behind the new idle-node gate, after A finished) | 8 | **6/8** | happy_path **4/4** (Sonnet 2/2, Haiku 2/2). gated_provision 2/4 — both misses were the **simulated user**, not the agent (below). |
| B · gated_provision re-run after the human-sim fixes (#70) | 4 | **4/4** | Sonnet 2/2, Haiku 2/2, rebuilt image. One Haiku cell asked in prose three times; the sim replied each time and the run went on to a real compute run and a clean teardown. No re-keying was needed this time (the sim's keys matched exactly); no "did not answer". |

Final tally of the block tier, harness gaps fixed: **stranger walk 6/6, happy_path 4/4, gated_provision 4/4 — 14/14 across
Opus 5, Sonnet 5 and Haiku 4.5**. The two attempt-2 "misses" were runs in which the agent behaved correctly (refused
to spend without an answer; asked the user in prose); the harness now answers both.

## Node starvation (attempt 1) → the idle-node gate

globus1 has three nodes. At launch, `michael`'s job held globus1 (1 d 19 h left), `svc-inference`'s held globus3
(19 h left) and suite A's MEP block held globus2. Suite B's pool-user blocks therefore never started; the agents
polled `squeue`, saw `PENDING (Resources)`, and were still waiting when the run budget ended — which is also why the
"ended with stop" checks read as failures (cut off mid-wait, not a decision to leave a block). One Haiku cell passed
by catching globus2 in a gap between A's cells.

Fix (PR #69): `happy_path` / `gated_provision` declare `NEEDS_COMPUTE_NODE`; `run_suite` probes
`sinfo -p main -t idle` over the operator's ssh alias, launches such a cell only when a node is idle, **holds the gate
through the cell when exactly one is** (so cells never queue behind the same node), launches unguarded if the probe
fails, and SKIPs with a named reason after `--node-wait-s`. Attempt 2 ran every cell with "1 idle at launch",
serially, and had no starvation failures. Lesson filed in `agentic/README.md`.

## The two gated_provision misses (attempt 2) → the human-sim fixes

| cell | what happened | verdict |
|---|---|---|
| Sonnet, bundle `1788493305` | Asked interface, scratch and partition via `AskUserQuestion`. The sim answered the first two; for the long partition question the sim (Haiku) **keyed its answer by a paraphrase**, the CLI matches by exact question text → "The user did not answer the questions" → the agent said it would not spend without an answer and stopped. | agent **correct**; harness bug |
| Haiku, bundle `1788493616` | One `connect_facility` (probe → proposal), then asked the configuration confirmation **in prose**, not via the tool. The sim answers only tool calls → the run ended after 3 turns with nobody to reply. | agent acceptable (a real user would just answer); harness gap |

Fix (PR #70): `rekey_answers` maps the sim's keys exact → normalised → prefix/substring → positional and never
invents an answer for an unmatched question; an interactive run whose turn ends on a text-only question now gets an
in-persona reply fed back as a follow-up turn (SDK multi-turn), at most 3 per run. 7 hermetic tests.

## Reported-only signals worth acting on

- **`first_details_connect_succeeds` failed in every SSH bring-up cell (14/14)** — the `details=` connect right after
  registration answers `could not find endpoint 'hpc-bridge-globus1-…' in list output`; the agent recovers with a
  second connect ("reused the already-online endpoint"). Issue #39. Every fresh BYO bring-up pays one confusing
  error and one extra call: worth a short bounded `list` retry inside connect before V1 polish ends.
- `stop_confirmed_or_retried` on every stranger cell — expected: `draining` is terminal on a facility MEP; the check
  is deliberately un-gated there.
- `spend_follows_question` on every happy_path cell — expected: happy_path is the pre-authorised autonomous story.
- Haiku prefers prose for a confirmation the guidance says to ask via `AskUserQuestion`. In Claude Code that is fine
  (the user answers in chat); it is only the harness that needed to learn to reply.

## Cost and time
26 bundles (incl. the re-run), ≈ $7 API-equivalent on the subscription. Stranger cells 50–80 s; happy_path 130–400 s; gated 100–410 s
(the 400 s cells are the starved ones).

## Operations notes
- A killed cell leaves its endpoint running under the pool user; `_teardown` runs only at a run's normal end.
  Swept by hand as the pool user with the scoped key (`pkill -u $USER -f "[g]lobus-compute-endpoint|[u]ep\.|…"` —
  the bracket trick keeps the pattern from matching the remote shell itself, which otherwise kills the cleanup
  before it prints). A two-day-old leftover under test-00 was found and removed the same way.
- The sweep was run as two suites with a driver that started B only after A exited, so they never competed for
  globus2. Monitors on the logs plus a two-hourly health line managed it unattended.
