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

## Follow-up 1 — more repeats + the guidance (`--no-skill`) ablation for gpt-oss

Repeat 3, guidance ON (`none`) vs OFF (`~skill`), same 4 scenarios (3 partition_choice/OFF cells were
node-skipped, so n<3 there):

| scenario | guidance ON | guidance OFF |
|---|---|---|
| gated_provision | 0/3 | 0/3 |
| rich_gate | 0/3 | 0/3 |
| spend_refusal | 1/3 | 2/3 |
| partition_choice | 0/3 | (skipped) |
| **total** | **1/12** | **2/9** |

Two findings:

- **Guidance doesn't raise the ceiling.** `gated_provision` and `rich_gate` are 0/3 *with and without* guidance —
  the hard multi-step gates fail either way. `spend_refusal` (the shortest flow) is the only one that ever passes,
  and the on-vs-off difference (1 vs 2) is within noise at this n.
- **Guidance raises the *floor* on engagement.** The agent made **zero tool calls** ("did nothing",
  `agent_engaged` FAIL) in **3/12 cells with guidance vs 4/9 without** (~25% → ~44%). The guidance pointer/nudge
  helps a weak model *engage the tools at all*, even when it can't complete the flow — a floor effect on
  engagement, not a ceiling effect on completion.

## Follow-up 2 — a "smarter" / agentic model: Devstral-2-123B

Hypothesis: if the ceiling is tool-use capability, a larger *agentic-tuned* model should do better. Tested
`mistralai/Devstral-2-123B-Instruct-2512` (Mistral's coding/agent model) on the same 4 scenarios, guidance on,
repeat 3 — **0/12**. It fails the same result-based gates as gpt-oss (`compute_ran`, `allocations_parsed`,
`partitions_offered`) and adds its own signature mode: a **tool-DESCRIBE loop** — it repeatedly `tool_search`/
`tool_describe`s the hpc-bridge tools without ever *invoking* one (`agent_engaged` FAIL on 4/12), i.e. it inspects
the tools instead of using them.

So **"smarter/agentic" did not clear the interactive gates** — the naive "bigger model → better" reading does not
hold here. The differentiator is Claude's tool-use *reliability* (right calls, right order, follow-through), which
neither a mid open model nor a 123B agentic model matched via hermes+ALCF.

**Validity note (input-encoding fix).** Devstral invokes tools through hermes' `tool_call` dispatcher nesting args
under `"parameters"` (gpt-oss uses `"arguments"`); 54 hpc-bridge calls across the Devstral bundles used that form.
`hermes_trace._unwrap` now reads both keys (with a test). **Re-grading all 12 Devstral bundles with the fixed
adapter leaves the verdict unchanged: 12/12 still fail ≥1 result-based critical** the input-fix can't affect, so
0/12 is valid. (gpt-oss uses `"arguments"` throughout, so its numbers are unaffected.)

## Reading

- The ceiling is **reliable multi-step tool USE over a long gated chain**, not hpc-bridge and not merely model
  size: gpt-oss stalls after the gate, Devstral loops in tool-discovery — both short of Claude's follow-through.
  The hermes deferred-tool overhead (a `tool_search`/`tool_describe` round per tool) lengthens every chain and is
  a plausible common confound worth isolating.
- The plugin + guidance-over-MCP are **operator-neutral**: the same server, tools and guidance that give Claude
  8/8 are what the open models run against. The failures are the models', now legible per-mode. Guidance helps a
  weak model *engage*, but does not make it *competent* at the gated flow.

## Caveats / next

- **n=2–3 per cell** — enough for the stark gap + the modes, not precise rates. Node-skips thinned a few cells.
- Two open models, one profile (`site`). Worth extending: a warm larger dense model (405B) when the cluster
  allows (ALCF auto-scales big models down; several were cold-blocked during this study), the deferred-tool
  overhead isolated (does exposing hpc-bridge tools directly help?), and a "recovered-after-correction" metric —
  no open-model run reached that state (they failed before a correction could land).

Bundles: `agentic/runs/*-{gated_provision,rich_gate,spend_refusal,partition_choice}` (each `record.json` carries
`operator`, `model`, `ablate_skill`, `failed`, and the interaction `kind`s). Sweep logs:
`agentic/runs/{xharness,ablation,smart}-*-*.log`.
