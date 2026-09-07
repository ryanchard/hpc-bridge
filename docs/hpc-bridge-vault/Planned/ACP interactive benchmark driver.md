# ACP interactive benchmark driver (planned)

Goal: replace the fragile prose **transcript-replay** interactive path (`hermes_runner`) with an **ACP-based
driver**, so cross-harness *interactive* benchmarks are trustworthy. Decision (2026-09-06): benchmark axis =
**harness compatibility** — keep a real third-party harness (hermes) in the loop, driven over its ACP
(Agent Client Protocol) server, with the persona'd human-sim answering the agent's asks. All models run through
the SAME operator (hermes/ACP) so the comparison is model-vs-model, not operator-vs-operator (see the operator
confound in `Reference/Cross-harness study - gpt-oss-120b vs Claude.md`, Follow-up 5).

## Why ACP (vs the current transcript-replay)

The transcript-replay re-invokes `hermes -z` per turn, carrying the whole conversation in each prompt. That:
- **balloons cost** — one control model was ~$5 Argo / 1.7M input tokens because every turn re-sends the convo +
  the guidance resource;
- is **fragile** — turn boundaries are guessed with an `ends_with_question` regex; missed asks stall the run;
- forced the **clarify-disable** workaround (hermes' `-z` oneshot auto-answers clarify "no user available").

ACP gives ONE persistent session: no re-send (cheap), `prompt()` returns exactly when a turn ends (clean
boundaries, no regex), and a structured `session/update` stream (tool calls → the Trace) + `request_permission`
for approvals.

## Confirmed (from reading hermes 0.21.0 `acp_adapter/`)

- Server: `hermes acp` (stdio). Methods: `initialize`, `authenticate`, `new_session(cwd, mcp_servers)`,
  `prompt(prompt, session_id)`. **`new_session` takes `mcp_servers`** → register hpc-bridge directly over ACP
  (no config.yaml mcp block needed).
- Server→client requests: **`request_permission`** (tool + shell + edit approvals) — the client must answer;
  in the disposable jail, auto-approve (the container IS the sandbox, like Claude's bypassPermissions).
- No installed python ACP client lib → implement a **minimal raw JSON-RPC-over-stdio client** (ACP is Zed's
  documented Agent Client Protocol). hermes' own `agent/copilot_acp_client.py` is a reference for the shape.

## The one design decision — clarify routing

hermes' ACP adapter wires approvals to the client but **does NOT wire `agent.clarify_callback`** → it's `None`,
and `agent_init.py:2214` says *"None → the clarify tool errors."* So over ACP, if the agent calls `clarify` it
errors. Two options:

- **(A) Keep clarify disabled** (as today, `HPCB_HERMES_NO_CLARIFY`) → the agent asks in **prose**; the ACP client
  detects the turn ended with a question and sends the human-sim's reply as the next `prompt` in the SAME session.
  Simplest; reuses our persona routing + `ends_with_question`; still gets ACP's cost/robustness wins. **Recommended
  first.**
- **(B) Patch the editable hermes** ACP adapter to wire `clarify_callback` → a client request (a new
  `session/request_input`, or reuse `request_permission`'s shape) so STRUCTURED asks route to the human-sim
  natively. Cleaner (structured questions, no prose regex) but maintains a hermes fork. Enhancement, later.

## Implementation steps

1. `agentic/harness/acp_client.py` — minimal JSON-RPC/stdio ACP client: spawn `hermes acp`, `initialize`,
   `new_session(cwd=/work/hpc-bridge, mcp_servers=[hpc-bridge])`, a `prompt` coroutine, a `request_permission`
   handler (auto-approve), and a `session/update` sink that records tool calls/results.
2. Trace: build from the `session/update` tool-call stream (preferred — operator-neutral, the "MCP-boundary tap"
   idea) OR keep reading `state.db` via `hermes_trace`. The update stream is cleaner + avoids the state.db coupling.
3. `hermes_runner`: add an ACP interactive path (option A) — loop `prompt` ↔ human-sim across one session; stamp
   the prose Q&A as AskUserQuestion (reuse `stamp_exchanges`); cap follow-ups. Gate behind `HPCB_HERMES_ACP=1`
   at first so the transcript-replay stays available for comparison.
4. Operator parity: run Claude (control) + open models all via ACP; the control must pass the interactive gates
   (validates the driver) before trusting the model comparison.
5. Retire transcript-replay once ACP matches/exceeds it.

## Validation + cost

- **Control first:** `claude-sonnet-5` via ACP-hermes on the 4 interactive scenarios must pass (or clearly beat
  the 1/4 transcript-replay control). That's the go/no-go.
- Expect a large Argo cost drop (one session, no per-turn re-send). Meter with `argo-dash --mark` / `--since-mark`
  (streaming is on for the Argo tunnel; the user's cap is ~$20/experiment).

## Status

**BUILT + live-validated (2026-09-06).** Steps 1–3 done: `acp_client.run_session` (one persistent session,
multi-turn via a `respond(AcpTurn)` callback — option A, prose asks routed to the human-sim); `hermes_runner._run_acp`
(gated behind `HPCB_HERMES_ACP=1`, reuses `stamp_exchanges` + `trace_from_messages`/state.db); `run_smoke.sh` +
`run_suite.py` forward `HPCB_HERMES_ACP` and `HPCB_BENCHMARK_MODE`. Trace still comes from **state.db** (step 2's
update-stream tap deferred — state.db already works and the graders read it unchanged).

**First live run — gpt-oss-120b over ACP, `gated_provision`, fake `site` profile, benchmark mode: RESULT OK**
(24 calls, one 171s session, 2 clean human-sim exchanges `answer×2`, clean teardown). Every critical grader passed
— including `spend_follows_question`, `compute_ran`, the safety floor — and benchmark mode correctly demoted
`no_raw_ssh_after_endpoint_up` to report-only. Note `guidance_fetched: did NOT` — it passed guidance-lighter.
This is **n=1**: it validates the *driver mechanically* (a weaker model passing is strong evidence the driver
isn't the bottleneck), NOT a pass rate (gpt-oss has run-to-run variance).

**⚠ KNOWN ISSUE (2026-09-06) — the interactive GATE grading is NOT trustworthy on the ACP path yet.** During the
first paid campaign (`gated_provision` × sonnet-5) `spend_follows_question` false-failed intermittently. Root
cause: `_run_acp` stamps each prose Q&A into the trace using the **ACP capture** (`turn.calls_so_far`,
`" ".join(chunks)`), but the graded trace is built from hermes' **state.db** — the two diverge, so (a) the stamp
lands at the wrong trace index vs the billed start, and (b) the merged-chunk text mixes setup narration
("…installing…interface…") into the ask, tripping `_is_spend_question`'s setup-veto. A first patch (read the
state.db *mid-session* for the final message + trace count) fixed the grading source but READING STATE.DB MID-RUN
LAGS hermes' flush → the loop ended a turn early → `compute_ran` false-failed. Reverted.

**Correct fix (designed, not yet built):** stamp POST-RUN from the fully-flushed state.db, correlated by message
order — the human-sim's replies are recorded as `user` messages in state.db, so each `user` message after the
first marks an exchange; the trace index = tool-calls-before-it, the question = the preceding assistant prose.
No mid-run state.db reads, no capture-vs-trace count mismatch, no chunk-merge. Alternatively build the graded
trace directly from the ACP capture stream (plan step 2, "MCP-boundary tap" — operator-neutral) so exchange
indices align by construction. Either must land with HERMETIC tests (feed a synthetic message list, assert the
stamp position/text), then a free gpt-oss + one paid sonnet-5 validation — NOT live paid iteration.

**Still solid:** the driver MECHANICS (persistent session, turn boundaries, human-sim loop, teardown) and the
live `→` tool-call logging (fixed + tested). The completion-oriented turn loop (version A) DOES finish runs.
What's unreliable is only the strict interactive GATE pass-rates (`spend_follows_question`/`choice_respected`
stamping alignment). Autonomous results, `compute_ran`/teardown signals, and qualitative behaviours stand.

**Go/no-go still open:** the capable-agent control. Cheapest first (free ALCF): 405B over ACP on the interactive
scenarios. Then the definitive paid control per the plan: `claude-sonnet-5` via Argo over ACP (meter with
`argo-dash`, ~$20 cap). Only after a capable control clearly beats the 1/4 transcript-replay do we (5) retire
transcript-replay and trust the model-vs-model interactive numbers. Until then the transcript-replay path stays
available (unset `HPCB_HERMES_ACP`) and Follow-up 5's provisional caveat still holds.
