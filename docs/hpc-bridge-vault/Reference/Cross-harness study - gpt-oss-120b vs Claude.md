# Cross-harness study — gpt-oss-120b vs Claude on interactive HPC (2026-09-06)

First quantified comparison of a **weaker open model** against Claude driving hpc-bridge through the
*interactive* (human-in-the-loop) flows, using the cross-harness operator support ([[Using hpc-bridge with hermes-agent]],
PRs #133–#135). The plugin and its guidance are identical for both; only the operator changes.

## Setup

- **Operators:** `claude-opus-5` (Claude Code / Agent SDK) vs `openai/gpt-oss-120b` (hermes-agent driving the
  ALCF FIRST inference service). The simulated **user** (human-sim) is Claude for both — the variable under test is
  the *operator*, not the user.
- **Scenarios (interactive spend/discovery gates):** `gated_provision` (cooperative), `rich_gate` (budget_hawk),
  `spend_refusal` (declines_spend), `partition_choice` (cooperative).
- **Cluster:** the fake cluster's `site` profile (3 nodes, 3 partitions, enforced accounting, `mybalance`).
- **Matrix:** repeat 2, concurrency 2 — 16 cells (4 scenarios × 2 operators × 2). One `run_suite.py`
  invocation per operator (`HPCB_OPERATOR`), same profile, fresh cluster each.

## Result

| scenario | Claude (opus-5) | gpt-oss-120b |
|---|---|---|
| gated_provision | **2/2** | 0/2 |
| rich_gate | **2/2** | 0/2 |
| spend_refusal | **2/2** | 0/2 |
| partition_choice | **2/2** | 0/2 |
| **total** | **8/8** | **0/8** |

Claude passes every interactive cell; gpt-oss-120b passes none. (For context, gpt-oss *does* complete the simpler
**autonomous** `happy_path` end-to-end — see [[hermes-agent]] — so this is specifically the interactive, multi-step,
gated flows where it breaks down.)

## Failure taxonomy (all genuine — Claude 8/8 on the identical harness rules out harness artifacts)

The interaction diagnostics (`harness:interaction` kinds + the `harness:prose_followups` cap) separate these:

1. **Stalls after the gate** — `gated_provision` ×2 (`compute_ran`). The agent asks, the user approves
   (`answer×1`, no corrections), and then it never provisions or runs — it connects repeatedly and stops.
2. **Skips discovery** — `rich_gate` ×2, `partition_choice` (`allocations_parsed`, `partitions_offered`).
   It provisions (or tries to) without first parsing the allocations / offering the partitions the gate requires.
3. **No spend gate at all** — `spend_refusal` ×1, `partition_choice` ×1 (`refusal_exercised`,
   `spend_follows_question`). It never poses a spend question (`none classified`, 0 exchanges), so the refusal
   path can't run / the spend isn't gated.
4. **Prose-question loop** — `spend_refusal` ×1 (`harness:prose_followups` capped). It keeps asking in prose
   (`answer×3`) without resolving, and the run ends at the follow-up cap.
5. **Raw SSH after the endpoint is up** — `rich_gate` ×1 (`no_raw_ssh_after_endpoint_up`). After the block is
   warm it goes back to `login_shell` (raw SSH) for `mybalance` instead of `run_shell` over AMQP — the exact
   guidance-violation the check exists for (verified in the trace: a real `login_shell` after endpoint-up, not a
   mis-grade).

Note the **two distinct `spend_refusal` failures across the two repeats** — one "no gate at all", one "prose loop"
— which the diagnostics tell apart. That legibility (a run that *passed after corrections* vs *looped* vs *made a
wrong call*) was the point of the interaction instrumentation, and it held up.

## Reading

- gpt-oss-120b's ceiling is **tool-calling discipline over a long, gated, multi-step chain**, not anything about
  hpc-bridge: it loses the plot after the discovery/ask phase (stalls, loops, or skips the gate). The hermes
  deferred-tool overhead (a `tool_search`/`tool_describe` round per tool) lengthens every chain and likely
  compounds this.
- The plugin + guidance-over-MCP are **operator-neutral**: the same server, tools and guidance that give Claude
  8/8 are what gpt-oss runs against. The failures are the model's, and now legible per-mode.

## Caveats / next

- **n=2 per cell** — enough to surface modes and the stark gap, not to estimate rates precisely; a higher repeat
  would sharpen the variance (gpt-oss is non-deterministic — cf. the 3 `happy_path` autonomous runs).
- One profile (`site`), one weak model. Worth extending: other ALCF models, the ablation (`--no-skill`) to test
  whether guidance helps or overwhelms a weak model, and a "recovered-after-correction" metric (none of these runs
  reached that state — gpt-oss failed before a correction could land).

Bundles: `agentic/runs/*-{gated_provision,rich_gate,spend_refusal,partition_choice}` from this sweep
(operator + kinds in each `record.json`). Sweep logs: `agentic/runs/xharness-*-{claude,hermes}.log`.
