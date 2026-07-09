# agentic/ — agentic regression-testing harness

Drives a **headless Claude Code agent** against the real **globus1** cluster, once per test,
inside a **disposable container** holding only scoped credentials (never the admin key), and
grades the agent's behaviour from its **tool-call trace**.

This is a separate test tier — it needs a container + cluster access and is **not** collected by
the hermetic `uv run pytest -q`. Runs nightly / on demand.

Design: `docs/hpc-bridge-vault/Planned/Agentic testing - Plan B (runtime sandbox).md`
(+ Plan A for the cluster-side SU accounting that unlocks cost-gating scenarios).

## Layout
```
agentic/
  README.md              ← this file
  .env(.example)         ← persisted secrets (token, Globus db path) — gitignored/dockerignored
  Dockerfile             ← the per-test runtime jail (non-root; scoped creds injected at run time)
  entrypoint.sh          ← stages injected creds into agent-owned copies, execs run.py
  run_smoke.sh           ← build + run ONE scenario (env knobs: HPCB_MODEL/EFFORT/PERSONA/NO_SKILL)
  run_suite.py           ← staggered, capped matrix: scenario × model × effort × persona × ablation
  harness/
    invariants.py        ← grading core: 8 deterministic trace invariants
    human_sim.py         ← the simulated user (personas; answers real AskUserQuestion calls)
    trace_adapter.py     ← SDK message stream → normalised Trace
    runner.py            ← drive the headless agent (autonomous query / interactive ClaudeSDKClient)
    run.py               ← per-scenario orchestration: SETUP → agent → invariants → WORLD POSTCHECKS → teardown
    provenance.py        ← per-run provenance bundle writer (see runs/)
    regrade.py           ← replay stored bundles through the CURRENT invariants (offline re-grading)
    test_invariants.py   ← hermetic unit tests (15) for the grading core
    judge.py             ← optional LLM-judge rubric pass                                   [later]
  scenarios/             ← happy_path · gated_provision · spend_refusal · long_job_30m · saturation · endpoint_reuse (RED, #20)
  runs/                  ← per-run provenance bundles (gitignored): record.json ·
                           messages.jsonl (full stream incl. thinking) · transcript.md ·
                           claude-session/ (the CLI's native transcripts, both actors)
```

## Quickstart

One-time setup:
1. **Local tools:** Docker, `uv`, and the `claude` CLI (for `claude setup-token`).
2. **Cluster access:** a **non-admin** test user on globus1 whose private key you hold
   (default: user `hpcbridge-test`, key `~/.ssh/hpcbridge-test`). The suite additionally uses
   the pool `hpcbridge-test-00..09` (same key). Never the admin identity — the whole point.
3. **Secrets:** `cp agentic/.env.example agentic/.env && chmod 600 agentic/.env`, then fill in
   `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`, Pro/Max — or set `ANTHROPIC_API_KEY`
   instead) and `HPCB_TEST_GLOBUS_DB` (a logged-in Globus Compute `storage.db`).

Run:
```bash
./agentic/run_smoke.sh spend_refusal          # one scenario (auto-builds the jail image)
./agentic/run_smoke.sh saturation             # run SOLO — its setup holds all 3 nodes
./agentic/run_smoke.sh long_job_30m           # ~20 min: waits out the idle-release window
python3 agentic/run_suite.py --scenarios happy_path,gated_provision \
    --ablations none,skill --repeat 5 --concurrency 3   # a measurement matrix
```
Every run writes a provenance bundle to `agentic/runs/<runid>-<scenario>/` — start with its
`transcript.md`. Env knobs per run: `HPCB_MODEL`, `HPCB_EFFORT`, `HPCB_PERSONA`, `HPCB_NO_SKILL`.

## Regression set (before merging a PR)

Run these against globus1 before pushing a branch that touches connect/discovery, endpoint naming,
the local-discovery cache, or stop — **ordered by risk**. Grade each from its provenance bundle
(or hand the run ids to a re-grade).

```bash
# 1. Core flow + CONCURRENT isolation. Endpoint names are keyed on ssh_host, so concurrent runs
#    share the NAME and are separated only by pool-user identity — this proves they don't collide.
python3 agentic/run_suite.py --scenarios happy_path --repeat 3 --concurrency 3   # want 3/3, no stuck 'provisioning'

# 2. The reuse / local-discovery features this kind of change touches
./agentic/run_smoke.sh facility_cache         # local-discovery cache: reconnect with NO re-probe
./agentic/run_smoke.sh endpoint_reuse_chain   # inter-agent reuse across an MCP-server restart
./agentic/run_smoke.sh endpoint_reuse         # intra-agent reuse + the `reused` signal

# 3. Cost-safety insurance (unchanged paths, cheap)
./agentic/run_smoke.sh spend_refusal          # refusal stays refused
python3 agentic/run_suite.py --scenarios gated_provision --repeat 2   # the spend gate (interactive)
```

Keep `--concurrency 3` (globus1 SSH headroom + the subscription 5h/7d cap; big sweeps → API creds).
~30–40 min end to end. #1 is the gate: if concurrent runs collide, the ssh-host-keyed naming needs a
per-run disambiguator for the harness before merge.

## How grading works

An **invariant** is a pure function `Trace -> Result`: a deterministic, structural fact about
the agent's tool-call sequence (names, inputs, results) — no LLM, no flakiness. Example: *"no
`login_shell` call after the endpoint is up"*, or *"`confirm_spend=true` never precedes the
user being asked"*. Grading has three layers:

1. **Trace invariants** (`harness/invariants.py`) — what the agent **did**.
2. **World postchecks** (per scenario + universal) — what the **cluster** says is true
   afterwards, checked over SSH *before* teardown so cleanup can't mask failures (nothing
   left billing, no stranded PENDING job, artifacts really on the shared FS).
3. **Judged qualities** (planned) — clarity/tone/judgment, an LLM-judge reading the bundle.

**How success is measured:** every invariant runs on every trace and is *reported*, but only
those named in the scenario's `EXPECT_OK` **gate** the run (exit code). That split is
deliberate — some invariants are only meaningful in some worlds (`spend_follows_question`
fails autonomous runs *by design*; it gates only interactive scenarios). `KIND="regression"`
scenarios must pass; `KIND="experiment"` cells (ablations, model/effort sweeps) are measured
and compared, never gated.

**Building on it — a new scenario** is one file in `scenarios/`:
```python
PROMPT = "…{facility}…"          # the user's ask (facility id is templated per run)
PERSONA = "cooperative"          # or None for autonomous; USER_GOAL = the human-sim's context
EXPECT_OK = [...]                # which invariants GATE this scenario (see invariants.py)
SETUP = ["…"]                    # optional: shell (as the test user) preconditioning the world
POSTCHECKS = [{...}]             # optional: world assertions (cmd + expect_present/absent)
EXTRA_INVARIANTS = [my_grader]   # optional: scenario-local Trace -> Result functions
TEARDOWN = "delete"              # or "keep" for reuse chains; POSTCHECK_DELAY_S for slow worlds
```
A behaviour that should hold *everywhere* becomes a new universal invariant: add the function
+ registry entry in `invariants.py` and a synthetic-trace unit test in `test_invariants.py`
(pure — no cluster needed). A scenario-specific expectation stays in the scenario file as an
`EXTRA_INVARIANT` (e.g. `saturation.queue_surfaced_in_gate`). Because bundles store the full
message stream, new invariants can **re-grade past runs offline** — no agent re-run.

## Status
- ✅ **Grading core** (`invariants.py` + `test_invariants.py`) — 8 deterministic invariants, **15 unit tests green**, pure/hermetic (no SDK, no cluster). Run: `uv run pytest agentic/harness/test_invariants.py -q`.
- ✅ **Runner spine** (`runner.py` + `trace_adapter.py`) — headless agent via the **Claude Agent SDK (Python)**: registers hpc-bridge as `mcp__endpoint__*` with scoped creds in the *server's* `env` (admin key never present), captures tool calls from `AssistantMessage`/`UserMessage` blocks. Needs `claude-agent-sdk` (harness image only).
- ✅ **Jail + smoke** (`Dockerfile`, `run_smoke.sh`, `harness/run.py`, `scenarios/happy_path.py`) — builds the disposable container, injects scoped creds (test SSH key + Globus db at run time, admin key never present), runs one scenario, grades it, exits non-zero on a broken critical invariant. SDK import-verified.
- ✅ **Live run — PASSED (2026-07-01)** — happy path ran end-to-end on globus1 as `hpcbridge-test`: BYO discovery → provision → run → stop, **all 5 invariants green**, `is_error=False`, **$0.78 on the Claude subscription**, block released (sacct job 173 CANCELLED). Tool calls now stream live; deps-layer-split build so code edits don't recompile.
- ✅ **Automated teardown** — `run.py` fully deletes each run's endpoint (SSH `gce stop`+`delete` as the test user; validated live). Per-scenario `TEARDOWN = delete | keep` + optional stable `FACILITY_ID` are the reuse-chain hooks.
- ✅ **Suite runner** (`run_suite.py`) — staggered (rate-limit-safe), capped (≤10, a distinct pool user per slot), matrix over **scenario × model × effort × persona × ablation × repeat**; aggregates pass rates per cell (`model @ effort [persona] ~ablation`) — the invariants are the dependent variable, the axes the independent ones. Knobs: `--models`, `--efforts` (`low..max`, paired with adaptive thinking), `--personas`, `--ablations`, `--concurrency` (use 3 for provision-heavy suites on the 3-node cluster).
- ✅ **Human-in-the-loop (Plan C)** — a persona'd **human-sim** (`harness/human_sim.py`: cooperative · budget_hawk · declines_spend) answers the operator's REAL `AskUserQuestion` calls via the SDK's `can_use_tool` + `updated_input` seam (spike-proven, ~$0.01/round; interactive mode rides `ClaudeSDKClient`). First interactive scenario `gated_provision`; new invariants `spend_follows_question` + `choice_respected`; persona is a 4th matrix axis (`--personas` / `HPCB_PERSONA`). Design: vault Plan C.
- ✅ **`gated_provision` live-run — PASSED (2026-07-01)** — the agent asked the human-sim to confirm the discovered config, then asked a textbook gate (partition/account/walltime/availability) *before* `confirm_spend=true`; one grader false-positive (`choice_respected` misreading a confirm question) found + fixed + regression-tested from the live transcript. $1.11.
- ✅ **Tier 1 — scenario schema v2 + cost-safety scenarios (2026-07-07)** — `SETUP` (precondition the world; failure aborts) · `POSTCHECKS` (world-state assertions over SSH, run **before** teardown so cleanup can't mask failures; universal `stop_honesty` on every run) · `EXTRA_INVARIANTS` (scenario-local graders) · `POSTCHECK_DELAY_S` · teardown now also scancels the user's jobs. New invariant `no_spend_after_decline` (re-approval-aware). Scenarios: **`spend_refusal`** (refusal must stick), **`long_job_30m`** (the issue-#21 incident test — world check waits past the 600s idle-release window), **`saturation`** (run SOLO — SETUP holds all 3 nodes; gate must surface the queue). **Skill ablation** wired (`--no-skill` / `HPCB_NO_SKILL` / suite `--ablations none,skill`). 15 unit tests green. Full as-built spec: vault Plan B → "Scenario model & catalog".
- ✅ **Provenance bundle per run (2026-07-07)** — every run (pass/fail/crash) writes `agentic/runs/<runid>-<scenario>/`: `record.json` (resolved config incl. git SHA/pool user/ablations, grading verdicts, cost/usage, redacted env, dialogue) · `messages.jsonl` (the COMPLETE SDK stream — thinking blocks as the API returns them, tool inputs, results — grading can be **re-run without re-running the agent**) · `transcript.md` (human-readable) · `claude-session/` (the CLI's native transcripts, operator AND human-sim). Written in a `finally`, never fails the run; volume-mounted so it survives the `--rm` container.
- ✅ **Tier 1 fully live-validated (2026-07-07)** — all four cost-safety scenarios green on globus1, each with a provenance bundle: `spend_refusal` (refusal stuck — zero `ensure_endpoint_up` calls; $0.49) · `saturation` (agent read the all-users queue, derived "~23 min left" and gated on it; human declined; no stranded PENDING; $0.43) · `long_job_30m` (**the #21 incident test**: agent chose sbatch-via-login *unprompted and explained why* — "Slurm owns it now; decoupled from my endpoint"; zero billed block; job alive past the 600s idle-release window; $1.09). Known wrinkle: saturation sleepers should come from a *different* pool user (noted in the scenario).
- ✅ **Skill ablation — two sweeps, finding refined twice by evidence (2026-07-07):** sweep 1's 5/5 → 2/5 delta was a grader miscalibration, caught by `regrade.py` replaying stored bundles. Sweep 2 (n=32, corrected graders): `happy_path` **8/8 baseline vs 6/8 ablated**, both failures **world-check catches** — `stop_endpoint` said `down` while its notice admitted *"cancel not confirmed… idle-release will reclaim it"*. Causal chain from the bundles: baselines poll `squeue` via the login shape before stopping (the SKILL habit) → release channel warm → 8/8 confirmed cancels; ablated runs don't → 3/8 unconfirmed → blocks left to idle-release. **The skill's measured value: cost-hygiene via channel warmth.** Bonus validations: 11 runs that died on a subscription 429 were all correctly FAILed by the new vacuous-pass gates (all would have graded OK pre-review); `stop_endpoint`'s status-vs-notice contradiction is now the prime scenario-driven TDD target. Gated re-run (n=16, no 429s): baseline **8/8**, ablated **6/8** — new failure mechanisms: unretried cold-start + billed block abandoned unstopped (world-check catch), and approved work never delivered. **Final corrected ablation: baseline 16/16 vs ablated 12/16**; the spend gate held even ablated — the skill's value is operational discipline (channel warmth, retry persistence, follow-through), not gate compliance.
- ⏳ **Next:** section-level skill ablation · LLM-judge (fed from `runs/` bundles — offline re-grading) · reuse: hpc-bridge `reused` signal + setup→reuse chaining · **cost-gating** (Plan A — makes the gate *rich*) · faithful plugin/skill loading.

