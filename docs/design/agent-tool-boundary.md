# The Agent–Tool Boundary

**Status:** living design principle · **Applies to:** anyone adding a tool, profile field, or automation to hpc-bridge.

## Why this exists

hpc-bridge stakes a claim: an *agent* standing up HPC compute is worth more than a *library* that does the same and hands the model a UUID. That is only true if the boundary between **deterministic machinery (tools)** and **agent judgment** sits in the right place.

- Put too little on the tool side → the agent re-derives solved, expensive-to-rediscover procedure every session: slow, non-deterministic, occasionally wrong.
- Put too much → we encode *judgment* as rules, and the agent becomes a button-presser you could replace with `cron`.

This doc defines the boundary, scores where we are, and gives a check to apply in review.

## The test (the whole thing in one line)

> **The agent is justified iff what remains on its side is _decision-shaped_ — not procedure we were too lazy to encode.**

Everything below is how to apply that.

## Procedure vs Decision

**PROCEDURE → tool-side (encode it).** All hold:

- one known-correct answer (or a small fixed state machine)
- independent of user intent *and* of surprise runtime state
- expensive or error-prone to rediscover (SU, time, expertise)
- same every invocation; silent or confusing when wrong

*Examples:* the Globus YAML, SSH flags, the idle-release config, the worker canary, session cwd/env persistence, accounting math.

**DECISION → agent-side (give it primitives + visibility, not a rule).** Any holds:

- the answer depends on user intent or context the tool can't see
- it requires interpreting novel or messy runtime state
- it's recovery from an *unanticipated* failure
- it's a trade-off the user might weigh (cost↔speed, batch↔interactive, which machine)
- it generalizes past what we pre-modeled

*Examples:* which partition for this job, how to recover a wedged queue, whether to spend the last of an allocation, adapting to a facility we've never seen.

## The deterministic floor (the one carve-out)

Irreversible or high-blast-radius actions — **spending allocation, destroying state** — get a deterministic hard-stop or confirmation gate *even when the surrounding decision is the agent's*. You tolerate non-determinism for reversible choices; never for irreversible cost. (The auto-idle-release net is this pattern; a redesigned cost gate should be too.)

## The design rule

**Every tool exposes state + primitives, never a sealed decision.**

Litmus, per tool: *On an unanticipated failure, can the agent (a) **see** enough to diagnose and (b) **reach a primitive** to act?*

- Yes → the structure **amplifies** the agent.
- No → it **cages** it.

`run_shell` is the universal "act." Structured notices + the canary are the "see." Never ship a verb that makes an irreversible *choice* the agent can't inspect or override.

## Discovery: how a fact crosses the boundary

This is how we *earn* the agent rather than just asserting it.

A hardcoded facility profile is library-shaped: it is a snapshot of a discovery *we* did by hand. Each baked-in assumption is a place the system breaks opaquely when reality drifts (a renamed module, a moved venv, a different fabric). Moving a fact from **static code → live discovery** is what converts "we wrote a class per machine" (library) into "the agent probes the machine" (agent) — i.e. it operationalizes the *generalization* capability.

Discovery itself splits on the same boundary:

- **Gathering** the facts (`module spider`, `sinfo`, `ip addr`, "does the venv exist") is **procedure → a deterministic probe (tool).**
- **Interpreting** them ("module's gone, pick the nearest Python 3.11"; "venv missing → bootstrap it"; "gce skews with my SDK → rebuild or warn"; "an orphan is running → reuse or kill") is **judgment → the agent.**

The pattern is always: **tool discovers → returns legible structured state → agent interprets and adapts.** Crucially, we do **not cache** discovery to begin with — every cycle re-derives from the live machine, so the discovery/judgment capability is actually exercised rather than short-circuited. Caching is a deliberate later phase, designed by watching the real pipeline. Full treatment: [`facility-discovery.md`](./facility-discovery.md).

## Current scorecard

| Component | Class | Target state |
|---|---|---|
| `run_shell` (+ `session_shell`) | **Primitive ★** — the escape hatch | keep; it's the load-bearing "act" primitive |
| `config_template`, gce drivers, `ssh_exec`, `lifecycle`, `estimate_spend` | **Primitive** — mechanism | keep |
| `GlobusRunner.canary` | **Primitive + legibility** | keep; model for "structure that makes state visible" |
| `MachineProfile` / `anvil_profile` | **Primitive-as-data ⚑** (hand-authored) | derive by discovery; static = fallback/seed |
| `ensure_endpoint_up` | **Hybrid — black box ⚠** | add preflight/discovery so provisioning state is visible |
| `dispatch` recovery notices | **Hybrid — editorializing ⚠** | keep **advisory only**; never auto-act |
| `make_facility` | **Hybrid** — static env switch | fine now; agentic = agent-probed facility |
| `cost.gate_profile` | **Judgment encoded ✕** (and inert) | split: deterministic budget floor + agent owns the response |
| profile authoring, self-heal, intent→resources, budget stop | **Missing (Decision)** | the agent's distinctive value — build on the procedure substrate |

## The PR check

Before adding a tool, profile field, or automation, ask:

1. **Decision or procedure?** Procedure → encode it. Decision → expose state + a primitive; don't encode a rule.
2. **Does it spend or destroy?** Yes → add a deterministic floor/confirmation *under* the agent's choice.
3. **On an unforeseen failure, can the agent see in and act?** No → you're building a cage; add observability or a primitive.
4. **Am I hardcoding a fact the machine could tell us?** Yes → prefer a discovery probe; at minimum leave the agent a way to override.
5. **Could the agent already do this with `run_shell` + good state?** Yes → maybe don't build the tool at all.

## Known debt / line-crossings

- `cost.gate_profile` — judgment (interactive→batch) encoded as a rule, *and* inert (`allocation_remaining → None`). Redesign: deterministic budget floor + surface `allocation_remaining`/`session_spend`, leave the response to the agent. (Coupled to allocation discovery.)
- `dispatch` recovery notices — keep **advisory**; the day they trigger automatic retry/downgrade they've crossed the line.
- `ensure_endpoint_up` — black box bundling provision→…→canary with low sub-step visibility; move toward a discovery/preflight that surfaces provisioning state.
- UUID read two ways (`endpoint.json` locally, `gce list` scrape remotely) — reconcile, ideally via discovery (read + verify).
- Static `anvil_profile` — the generalization seam: target is derive-by-discovery, not hand-authored-per-machine.
