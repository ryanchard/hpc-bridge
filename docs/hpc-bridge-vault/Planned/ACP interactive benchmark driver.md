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

Design grounded (this doc); not yet built. Next focused effort. The transcript-replay + clarify-disable +
streaming/metering already in main are the interim; the interactive pass-rate numbers stay provisional until ACP
lands (Follow-up 5 caveat).
