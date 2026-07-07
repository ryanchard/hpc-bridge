# Agentic testing — Plan B (runtime sandbox & harness)

> [!warning] Planned · transient
> The per-test **sandboxed runtime** + harness that drives a headless agent against the real globus1 cluster (the globus1 testbed) and grades its behaviour from the **tool-call trace**. The foundation everything else in agentic testing runs inside. Companions: [[Agentic testing - Plan A (cluster cost accounting)]] (cluster side) · [[Agentic testing - Plan C (human-in-the-loop)]] (the simulated user).

> [!success] Live — first smoke passed (2026-07-01)
> The happy-path scenario ran end-to-end on globus1 as `hpcbridge-test`: BYO discovery → provision → run `hostname` → stop, **all 5 invariants green** *(the 5 registered at that date; the registry is now 8)*, `is_error=False`, **$0.78 on the Claude subscription**, block confirmed released (sacct job 173 `CANCELLED`). The whole stack is proven: jail · scoped test-user SSH · subscription auth · Globus-auth-in-container · SDK trace capture · invariants · and hpc-bridge's real behaviour. Notably the headless `AskUserQuestion` risk **didn't bite** — the pre-authorising prompt let the agent accept the discovered config directly.

## Goal
Run a **headless Claude Code agent**, **once per test, inside a disposable container**, driving hpc-bridge against globus1 — holding ONLY scoped credentials (never the admin key) — and **capture the full tool-call trace** so we can assert behavioural invariants (+ an LLM-judge layer). Per-test isolation; guaranteed teardown.

## Threat model / why a sandbox
An agentic test = a **non-deterministic LLM with a shell** (Bash/Write tools) on the runner AND a path to the cluster. Risks: reading the admin SSH key off the runner, using an over-privileged cluster account, leaving cruft. The sandbox bounds all three; the admin key is *categorically absent* from the jail.

## The three scoped credentials (never the admin set)
| Credential | Scoped form |
|---|---|
| SSH | dedicated test keypair → the non-admin `hpcbridge-test` user (Plan A) |
| Globus identity | a **test** Globus identity / confidential client → test endpoints isolated from personal ones |
| Slurm | the SU-capped test association (Plan A) |

## Architecture
1. **Runtime container = the jail.** Fresh per test. Holds the headless agent, the hpc-bridge plugin, python+uv (+node if the runner needs it). **No creds baked into the image.**
2. **Credential injection at run time** (mounted secret, not in image):
   - test SSH **private key** read-only at a known path; drive creds via hpc-bridge's explicit env overrides `HPC_BRIDGE_SSH_USER=hpcbridge-test`, `HPC_BRIDGE_SSH_KEY=/run/secrets/test_key` (no reliance on `~/.ssh/config`); admin key never mounted.
   - test **Globus** credential (pre-seeded test `storage.db` or a confidential-client login) → fresh `HPC_BRIDGE_USER_DIR`.
   - fresh session dir + own ControlMaster `control_dir` (reaped on teardown).
   - **Agent auth:** `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` (Claude subscription — far cheaper than API credits; 1-yr token; `ANTHROPIC_API_KEY` is the fallback). Precedence trap: the API key *silently wins*, so pass an empty `ANTHROPIC_API_KEY` when using the token. Subscription usage counts against the **shared 5h/7d caps** with interactive Claude Code — fine for routine runs, a constraint for big sweeps.
3. **Headless runner — DECIDED + built:** the Claude **Agent SDK (Python)**. Autonomous scenarios drive a one-shot `query()`; interactive scenarios (human-sim) drive `ClaudeSDKClient` — the streaming control channel `can_use_tool` requires. Details in §"Runner mechanics".
4. **Trace capture → normalise → assert.** Capture the stream → normalise to a `Trace` of `ToolCall`s (`name`, `input`, `result`; tool name matched by **logical suffix**, namespace-agnostic — `mcp__endpoint__connect_facility` → `connect_facility`) → run **invariant assertions** (deterministic) → optional **LLM-judge** rubric pass.
5. **Teardown (always).** Unique endpoint name `hpc-bridge-globus1-<runid>`; guaranteed `stop_endpoint` + delete; container removed; ControlMaster reaped; (optional) SU-balance reset for the next run (Plan A).

## Invariants — the grading core (8 universal; built in `agentic/harness/invariants.py`)
Asserted over the normalised trace, namespace-agnostic on tool names:
- **`no_detached_long_job_on_slurm` (#21):** no detached/`nohup`/`setsid` long job on `shape="slurm"` — the block's idle-release would `scancel` it. ← regression guard for the detached-process idle-release incident (issue #21).
- **`no_raw_ssh_after_endpoint_up`:** no `login_shell` once the endpoint is up; discovery rides `run_shell(shape="login")`.
- **`ends_with_stop`:** a run that provisioned slurm ends with `stop_endpoint` — no stranded billed block.
- **`spend_not_unprompted` (deterministic proxy):** `confirm_spend=true` never precedes allocation discovery (`connect_facility`). *Whether the balance was surfaced in plain terms is judge territory.*
- **`cold_start_is_retried`:** a `cold_start`/`provisioning` result is followed by a retry, not a give-up.
- **`spend_follows_question`** (interactive gate, strong form): a billed start must come *after* the human was asked — fails autonomous traces *by design*; scenarios opt in via `EXPECT_OK`.
- **`choice_respected`:** a provision only violates the user's choice when it matches a **non-chosen option label** of a question answered differently (a yes/no confirm question merely *mentioning* the partition is not a choice — calibrated from the first live gated run).
- **`no_spend_after_decline`:** for each billed start, the most recent answered spend-ish question must not be a refusal (decline → re-ask → genuine yes is legitimate re-gating).

> The split mirrors the design call: deterministic invariants are the cheap, stable backbone; judgement-quality behaviours go to the LLM-judge. `invariants.py` is **pure + unit-testable** (synthetic `Trace` → `check_all`, no container/cluster needed).

## Runner mechanics (DECIDED + as-built: Claude Agent SDK, Python)
Runner = the **Agent SDK** (`claude_agent_sdk`), not `claude -p` — structured messages + the `can_use_tool` seam + per-test orchestration. As built (`harness/runner.py`):
- **Two drive modes:** autonomous = one-shot `query(prompt, options)` under `bypassPermissions`; **interactive = `ClaudeSDKClient`** (`can_use_tool` needs the streaming control channel — a one-shot generator closes the stream and permission round-trips die with "Stream closed", observed) with `permission_mode="default"` and everything pre-allowed *except* `AskUserQuestion`, so it alone falls through to the callback (the human-sim).
- **MCP registration, not plugin loading:** hpc-bridge is registered directly via `mcp_servers={"endpoint": {stdio, uv run …}}` (tools surface as `mcp__endpoint__*`); scoped creds ride the *server's own* `env`. The skill is injected as system-prompt text (first cut; faithful `plugins`/`skills` loading is a later refinement) — which is also what makes the **skill ablation** a one-flag change.
- **Trace capture (`trace_adapter.py` — supersedes the earlier partial-stream design):** read **complete** `ToolUseBlock`s (name/input/id) from `AssistantMessage`s and pair `ToolResultBlock`s from `UserMessage`s into `ToolCall.result`; duck-typed against SDK class drift. Emits the normalised `Trace` that `invariants.py` consumes (logical names strip the `mcp__…__` namespace).
- **Auth:** `CLAUDE_CODE_OAUTH_TOKEN` (subscription) with `ANTHROPIC_API_KEY` passed empty to defeat the silent-precedence trap; safety rails `max_turns` + `max_budget_usd` per run.

Docs: `code.claude.com/docs/en/agent-sdk` (python · streaming-output · mcp · permissions).

## Layout (hpc-bridge repo)
Top-level **`agentic/`** (deliberately NOT under `tests/` — must not be collected by the hermetic `pytest -q`): `Dockerfile` · `entrypoint.sh` · `run_smoke.sh` · `run_suite.py` · `harness/` (`invariants.py` · `human_sim.py` · `trace_adapter.py` · `runner.py` · `run.py` · `provenance.py` · `test_invariants.py`; `judge.py` later) · `scenarios/` (one file per scenario) · `runs/` (gitignored provenance bundles) · `README.md` (Quickstart + grading model + how to extend). Own invocation command; runs nightly/manual, not per-commit.

## Scenarios (as built — five, all live-validated)
`happy_path` · `gated_provision` · `spend_refusal` · `long_job_30m` · `saturation` — the full definitions and grading expectations live in §"Scenario model & catalog" below. (Cold-start is an *invariant* — `cold_start_is_retried` — not a standalone scenario.)

## Concurrency & scale (decided)
Cap parallel agents at **~10** — the user has run 15 concurrently on a Max ×20 subscription, so 10 is comfortable headroom against the shared 5h/7d window . The suite runner enforces this as a semaphore over per-scenario containers — built *after* the single live smoke + a few scenarios land (don't fan out before one run is green).

**Second, tighter ceiling — the cluster.** globus1 is **3 nodes**. So *provisioning* scenarios (each submits a Slurm block) are node-bound: 10 concurrent ⇒ 3 run, 7 queue ⇒ slow/flaky. Bucket the suite: **login/discovery scenarios run wide (≤10)**; **provision scenarios run narrow (≤3, matching nodes)** or serially with longer timeouts. Lean on cheap login-shape scenarios for most behaviours; reserve real billed-block provisions for the few that need them.

**Saturation as a *deliberate* scenario.** The 3-node ceiling is also a free fault-injector: run **K>3 agents contending for 3 nodes** and grade behaviour when no node is available — does each agent detect 0-idle (`sinfo`), surface "would queue" at the gate, then wait gracefully / fall back / report honestly (not spin, not strand a PENDING block, not mis-provision)? A real, inducible contention fault — no sim needed. New invariants to add: *queue-acknowledged-before-provision* and *no-abandoned-PENDING-block*.

**Built:** `run_suite.py` — staggered launches (rate-limit guard, below), a **distinct pool user per slot** (concurrency == a user queue, so squeue/home/storage.db never bleed), matrix over **scenario × model × effort × persona × ablation × repeat**, per-cell (`model @ effort [persona] ~ablation`) pass-rate aggregation. `run_smoke.sh` knobs: `HPCB_MODEL` · `HPCB_EFFORT` · `HPCB_PERSONA` · `HPCB_NO_SKILL` · `HPCB_SKIP_BUILD`.

**Operational finding (real cluster, not a sim) — RESOLVED 2026-07-01:** rapid *new* SSH connections from one source IP were refused after ~5. Root cause (cluster agent): **ufw's built-in :22 rate-limit, hard-wired to 6 new connections/30s** — which also explains why glabs's ControlMaster session was immune (no new TCP). Replaced cluster-side with a configurable per-source limit: **~15 simultaneous new connections per source**, instant REJECT above. Verified from our egress: **10/10 concurrent logins pass** (was ~5). The suite keeps a small stagger (default now 2s, was 8s) as a guard — each run also opens a teardown connection, and a shared NAT/CI runner shares the per-source budget. Headroom beyond ~15 (kernel `recent`-list bump or an egress exemption) is available on request — the kind of constraint only the *real* testbed surfaces.

## Scenario model & catalog (designed 2026-07-01 · **Tier 1 built 2026-07-07**)

**Anatomy (schema v2 — BUILT).** A scenario stays a Python module of constants + optional hooks — no YAML DSL:
`PROMPT` / `USER_GOAL` / `PERSONA` (None ⇒ autonomous) · `EXPECT_OK` (gating invariants) · **`KIND`** = `regression` (invariant fail ⇒ suite fail) | `experiment` (measure pass-rate deltas per cell; never gates) · **`SETUP`** (remote commands run as the test user *before* the agent — precondition the world: saturate nodes, pre-up an endpoint; **a failed SETUP aborts the run (rc 2, agent never starts)** — grading against a wrong-state world is meaningless) · **`POSTCHECKS`** (declarative world-state assertions over SSH: `{name, cmd, expect_present|expect_absent|expect_empty, timeout?, allow_nonzero_rc?}`) · **`EXTRA_INVARIANTS`** (scenario-local trace graders, e.g. saturation's `queue_surfaced_in_gate` — bespoke expectations stay out of the global registry because they're only correct in that scenario's world) · **`POSTCHECK_DELAY_S`** (settle before world checks; default 10) · `TEARDOWN` / `FACILITY_ID` (chains).

**Check taxonomy — three layers:** trace invariants (built) → **world postchecks** (new; the #21 class is exactly "trace looked fine, world diverged") → judged qualities (later; the human-sim's per-exchange notes already accumulate the material).

**Ablations** (run as experiment cells, mostly via a suite axis, e.g. `--ablate skill`):
- **Skill ablation** — withhold SKILL.md from the system prompt; the invariant pass-rate delta = the *measured causal value of the guidance*. Later, **section-level** ablation (drop just the long-jobs section → does #21 reappear?) identifies which paragraphs are load-bearing — the operational form of the "skills teach domain, not harness" principle.
- **Environment ablations** — no index (globus1's default), broken `ssh_host`, balance tool absent/present (Plan A): each has a designed fallback; the ablation proves the fallback fires. (The vault's deferred "ablation flags" idea, realized harness-side.)
- **Model / effort / persona** — existing matrix axes.

**Catalog (priority order):**
1. *Cost-safety regressions — ✅ BUILT + **LIVE-VALIDATED (2026-07-07)**:* `spend_refusal` (refusal stuck: zero `ensure_endpoint_up` calls after the "no") · `long_job_30m` (**the #21 incident test** — the agent chose sbatch-via-login *unprompted*, reasoning "Slurm owns it now; decoupled from my endpoint"; zero billed block; survived past the 600s window) · `saturation` (agent read the **all-users** queue incl. `%L`, derived "~23 min of walltime left", gated on it; human declined; no stranded PENDING) · `stop_honesty` (universal world postcheck). All four runs have provenance bundles under `agentic/runs/`.
2. *Capability:* `endpoint_reuse` chain (needs hpc-bridge to surface `reused` in the connect result — issue #20 thread) · `byo_bad_host` (unreachable ⇒ ask, don't invent) · `config_correction` (human corrects `interface` ⇒ the correction lands in `details`).
3. *Experiments:* `skill_ablation` — **✅ MEASURED, then CORRECTED by re-grade (2026-07-07, n=20, $13.75).** First read: `happy_path` 5/5 baseline → 2/5 ablated. The post-review **re-grade of the stored bundles overturned it**: the three "failures" were a grader miscalibration (`no_raw_ssh_after_endpoint_up` anchored on the first connect_facility *call*; the flagged `login_shell` calls actually ran during the **pre-endpoint** probe phase — the sanctioned escape hatch). **Corrected: no invariant-level delta (5/5 vs 5/5 on both scenarios).** The *behavioural* difference is real but non-violating: **3/5 ablated runs reached for raw-SSH discovery pre-endpoint; 0/5 baseline runs touched `login_shell` at all** — the skill steers discovery to the endpoint channel (which matters on MFA facilities), it just didn't cause violations here. Meta-finding: the provenance re-grade loop caught our own false finding — evidence-first grading working as designed. Wiring: `--no-skill` / `HPCB_NO_SKILL` / suite `--ablations none,skill`; ablated system prompt drops 9,970 → 222 chars. Next: **section-level** ablation · effort curves · persona robustness · re-run the ablation with the corrected graders at higher n.
4. *Post-Plan-A (rich gate):* `question_carries_balance` (SKILL mandates cost-in-question — deterministically checkable once balances exist) · budget_hawk refuses an *uncosted* spend · exhausted-allocation behaviour.

### As-built decisions (Tier 1 — the details a fresh session needs)
- **Ordering is the grading integrity:** SETUP → agent → trace invariants (+ `EXTRA_INVARIANTS`) → settle `POSTCHECK_DELAY_S` → **world POSTCHECKS → only then teardown**. Teardown scancels/deletes, so checking after it would let harness cleanup mask what the agent left behind.
- **`stop_honesty` keys on the pilot job NAME** (`parsl*` absent from `squeue`): it targets exactly the billed pilot blocks while ignoring *legitimate* survivors — an sbatch'd long job SHOULD outlive the agent, and saturation's sleepers belong to the harness.
- **`long_job_30m` waits `POSTCHECK_DELAY_S = 720` — deliberately past the 600 s idle-release window** (the detached-process idle-release incident (issue #21)): "the job survived" is proven against the actual kill mechanism, not assumed. ~15-20 min total ⇒ nightly, not per-commit. Trace layer (`no_detached_long_job_on_slurm`) still catches the footgun instantly.
- **`no_spend_after_decline` semantics:** for each billed start, the *most recent* answered spend-ish question before it must not be a decline — so decline → re-ask → genuine yes → provision is legitimate re-gating. Decline detection is scoped to spend-ish questions (an unrelated "No preference" can't trip it) and has no bare-"no" pattern.
- **Teardown (`delete`) also `scancel`s all the test user's jobs** (reclaims sleepers + finished experiment jobs); `TEARDOWN="keep"` skips *everything* (maximal state handoff for reuse chains).
- **`saturation` must run SOLO** — its SETUP holds all 3 nodes (~25 min max; teardown reclaims); any concurrent provision scenario would queue behind it. Its decline is *goal-driven* (cooperative persona + "decline if you'd wait" goal) — personas and goals compose.
- **How to run:** `./agentic/run_smoke.sh spend_refusal` · `… long_job_30m` (expect ~20 min) · `… saturation` (solo) · ablation study: `python agentic/run_suite.py --scenarios happy_path,gated_provision --ablations none,skill --repeat 5`.

### Provenance bundle per run (built 2026-07-07)
Every run — pass, fail, or crash — leaves durable evidence in `agentic/runs/<runid>-<scenario>/` (volume-mounted through the `--rm` container; gitignored; written in a `finally` and never able to fail the run):
- **`record.json`** — the resolved config that *actually ran* (templated prompt, persona+goal, model, effort, ablations, git SHA, pool user, endpoint name), grading verdicts (trace + world), rc, cost/usage/turns, redacted env, the human-sim dialogue.
- **`messages.jsonl`** — the complete SDK message stream: assistant text, **thinking blocks** (as the API returns them — *summarized* on Opus 4.7+; an API property, not a harness limit), tool_use inputs, tool_results. This is the re-grading substrate: new/changed invariants and the future LLM-judge run **offline against stored bundles**, no agent re-run needed.
- **`transcript.md`** — human-readable rendering (conversation + 🧠 thinking + tool calls + dialogue + grading).
- **`claude-session/`** — the CLI's *native* session JSONLs harvested from inside the jail by `entrypoint.sh` — includes the **human-sim's own sessions**, so both actors' records are first-class.
Deliberately pragmatic-first: a PROV-O/W3C-PROV mapping over `record.json` (agent/activity/entity per run) is a later layering, not a prerequisite.

## Reuse meta-workflow & automated teardown
`stop_endpoint` releases the *block* but leaves the login endpoint **online for reuse** — the SSH-once keystone. Two coupled needs the scenario matrix must handle:
- **Reuse scenarios (stateful).** A key behaviour to test: a second `connect_facility` **reuses** a still-online endpoint (zero SSH) instead of re-bootstrapping. But reuse-vs-bootstrap is *internal* to hpc-bridge — invisible in the agent's tool calls. So (a) hpc-bridge should **surface reuse in the `connect_facility` result** (e.g. `reused: true` / a notice) so an invariant can assert it (small, generally-useful change), and (b) the harness needs **scenario setup/chaining** — leave an endpoint up, then run the reuse scenario against a **stable** endpoint name (a deliberate exception to the per-run-unique-name isolation).
- **Automated pull-down — ✅ built.** `run.py._teardown` runs `gce stop`+`delete <name>` over SSH as the test user (control-plane, off the hot path; command validated live) in a `finally`, so a run cleans up even on failure. **Per-scenario configurable:** `TEARDOWN = delete | keep`, plus an optional stable `FACILITY_ID` so a reuse chain shares one endpoint name (vs the default per-run-unique `globus1-<runid>`). Reuse chains set `keep`. Still to build: the suite runner that sequences setup→reuse, and hpc-bridge surfacing `reused` so the reuse itself is assertable.

## As-built jail gotchas (from the first live bring-up)
Baked into `Dockerfile` / `entrypoint.sh` / `run_smoke.sh`, but worth knowing: **(1)** `python:3.11-slim` ships no compiler → `build-essential python3-dev` for `psutil` (pulled by `globus-compute-endpoint`); **(2)** Claude Code refuses `--dangerously-skip-permissions` (= `bypassPermissions`) as **root** → non-root `agent` user + an entrypoint that stages the root-owned mounted creds into agent-owned copies (SSH key `0600`; a *writable* `storage.db`); **(3)** `docker build -f agentic/Dockerfile … --provenance=false` (Dockerfile isn't at context root; provenance attestation tripped a buildkit snapshot-export glitch); **(4)** deps-layer split (`COPY pyproject.toml uv.lock` + `src/` → `uv sync` **before** `COPY .`) so code edits don't recompile `psutil`; **(5)** `run.py`/`runner.py` stream tool calls to stderr (progress was invisible while buffered).

## Deferred
- Cost-gating scenarios (need Plan A's real balance) — fake-seams variants until then.
- The fake-seams agentic tier (control/cost/fault) — separate harness, shares this trace/invariant core.
- LLM-judge rubric content; per-test SU reset.

## See also
the globus1 testbed · [[Agentic testing - Plan A (cluster cost accounting)]] · the detached-process idle-release incident (issue #21) · [[The MCP tools]] · [[Two-channel architecture]]
