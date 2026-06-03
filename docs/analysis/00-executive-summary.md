# Executive Summary & Verdict — hpc-bridge

**Verdict: BUILD WITH CONDITIONS.** The technical core is real and worth building; the *framing* and *go-to-market* are not, and several load-bearing claims in the vision are refuted by the evidence. hpc-bridge should ship — but as a **NERSC-first, scale-to-zero-default, facility-co-designed** tool whose pitch is *"materially lower review burden than an MEP, plus the lowest-friction warm interactive dispatch on HPC,"* **not** *"identical to SSH, zero new attack surface, no review, accumulate-then-reveal."* The single most dangerous sentence in `vision.md` (line 34, "identical to SSH... zero new attack surface") and the GTM spine (lines 75–81) are the parts that get the project banned on first incident. Delete them; keep the engineering.

This document leads with the verdict, then states what must be true, the top risks with verification status, and the highest-leverage next experiment.

---

## What hpc-bridge is

A single Claude Code plugin that gives an AI agent interactive, low-latency access to real HPC compute by transparently provisioning a **personal Globus Compute endpoint**. The endpoint is a lightweight coordinator on a login node that holds a warm pilot-job block of pre-allocated compute nodes and dispatches shell commands to them over Globus' AMQP path in seconds — turning a batch supercomputer into an interactive surface. It ships as an MCP server (`run_shell`/`read_file`/`write_file`/`ensure_endpoint_up`), an SSH bootstrapper that stands up the endpoint on first use, one working-directory-discipline skill, and credential-boundary hooks.

**What it is *not* building from scratch:** Globus Labs already ships `globus/globus-mcp` (the MCP-over-Compute layer with `register_shell_command`/`submit_task`/`get_task_status`) and has demonstrated MCP-driven agents over Globus Compute on HPC (Globus Labs, arXiv 2508.18489). hpc-bridge's *actual differentiated scope* is the narrower, defensible delta: the **SSH bootstrapper/reconcile loop**, **out-of-band credential management**, **safety/audit controls**, and **facility config templates**. The vision's "small delta to globus-mcp" framing understates this — the durable-task-handle store, `reload_tasks()` reconnection, and version-compatibility probing are the real v1 engineering.

---

## The honest viability verdict

The proposition is **viable in a narrowed form and not viable as written.** Three independent things are simultaneously true:

1. **The mechanism is real.** Warm-block HTEX dispatch genuinely eliminates per-call queue wait; the funcX work reported low-tens-of-ms warm round-trips in a cloud-co-located benchmark (not yet measured on a leadership-class node — see C10). The personal-vs-MEP privilege argument is technically sound (personal endpoint runs as one UID, no root, no `getpwnam`/`fork`/`setuid` identity-mapping daemon). Filesystem-as-state works for the edit→run→debug loop. **(C6, C7, C10 STAND-WITH-CAVEATS.)**

2. **The marketing absolutes are false.** "Identical to SSH / zero new attack surface / no new review," "persistence stops mattering / survives transparently," "non-interactive SSH works across leadership facilities," and "Globus Compute is our moat" are each refuted by facility-policy, threat-model, and competitive evidence. **(C1, C2, C3, C4 REFUTED.)**

3. **The GTM is structurally adversarial to the only party who can bless it.** The "accumulate unsanctioned endpoints, then walk in with telemetry" flywheel manufactures an argument for a *crackdown* before it manufactures demand for consolidation, and asks early adopters to install a tool whose business model reports their behavior to their own facility security org. **(C5, C8 REFUTED.)**

The net is not "kill it." The mechanism advantage over the free substitutes (Charlie-Z/`slurm-mcp-server` + sshproxy, working today in <1 hr) is genuine *but only in the warm-block configuration* — which is exactly the configuration that burns allocation and triggers facility scrutiny. Build the thing; sell it honestly; ship it where it's welcome.

---

## The 3–5 things that must be true

| # | Must be true for hpc-bridge to win | Current status |
|---|---|---|
| **MT1** | **One facility (NERSC) sanctions the pattern on the record** — via Workflow QOS / Spin, with a published policy sentinel — so launch is *sanctioned-by-construction*, not discovered-as-shadow-IT. | Achievable but unproven. NERSC has the only purpose-built home (Workflow QOS: 90-day, 25% login node, **request form required**). Not yet secured. |
| **MT2** | **Non-interactive self-heal works at the launch facility.** The reconcile loop needs SSH-without-human-MFA. | True **only at NERSC** (sshproxy 24 h cert + ControlMaster + SFAPI). False at ALCF/OLCF/TACC. (C3) |
| **MT3** | **The warm-block latency win is affordable for the beachhead user.** | Conditional and at risk. Warm block = continuous node-hour burn (~2,880/yr for 1 node 8 h/day = ~14% of a 20 K Director's Discretionary award). Scale-to-zero is *mandatory* default; but scale-to-zero ≈ same latency as the free SSH substitute. (C10) |
| **MT4** | **The autonomous-agent attack surface is neutralized with hard, non-LLM controls** (not regex scrubbing). | Not yet designed. Requires action/rate governors, Globus-Transfer allowlist, tamper-evident audit log, dedicated-UID credential broker, structured allow-listed tool outputs. (C9) |
| **MT5** | **The v1 run_shell is a durable task with session-persistent cwd/env, fast-path + handle-based fallback** — not a naive blocking RPC. | Identified as the real engineering core (by Globus-internals + DX reviewers); currently budgeted as a "small delta," the plan's single most optimistic assumption. |

---

## Top risks with verification status

Claims C1–C10 were adversarially verified across technical, security-policy, and strategic lenses. Seven of ten load-bearing claims are **REFUTED**; the three that stand are the *engineering* claims, narrowly scoped.

| Claim | One-line | Verdict | Why it matters |
|---|---|---|---|
| **C1** trust boundary "identical to SSH / zero new attack surface / no new review" | **REFUTED** | The privilege comparison vs MEP holds; the *absolute* and the *no-review inference* do not. A persistent AMQP-connected coordinator + a Globus refresh-token control plane that bypasses facility MFA + an injectable autonomous agent (indirect prompt injection still succeeds at ~45–52% against the best-defended agents in published benchmarks, and rising; the May 2026 Sysdig incident showed an LLM-agent-driven full-database exfiltration in under 2 minutes) are net-new surface SSH never had. Self-refuted by the vision's own "four pains an MEP resolves." |
| **C2** persistence stops mattering / endpoints survive death transparently | **REFUTED** | Holds only at NERSC, in scale-to-zero, for queued (not running) tasks, within the 30-min result TTL. Warm block is *lost* on restart; in-flight `.result()` raises; restart is human-MFA-gated at 3 of 4 facilities. |
| **C3** non-interactive SSH under MFA works across leadership facilities | **REFUTED** | True at NERSC (sshproxy). **OLCF bans ControlMaster system-wide** + single-use RSA fob; ALCF per-login OTP; TACC per-login TOTP + "AI tools on login nodes → suspension." The two actual DOE *Leadership* facilities (Aurora, Frontier) are where it fails hardest. |
| **C4** Globus Compute is a durable moat no competitor can match | **REFUTED** | The moat is **Globus Auth** (owned by UChicago, available to all), not Compute. Compute is open-source; Globus ships its own MCP; `salloc`+ControlMaster reproduces the warm foothold; the foothold is transitional (personal→MEP one-line change hands value to the facility). Only "cloud sandboxes can't reach leadership scale" survives. |
| **C5** personal-endpoint flywheel manufactures MEP demand | **REFUTED** | Inverts directionality: accumulation reads as a shadow-IT *incident*, not a procurement lead. MEPs already shipped top-down (ALCF Polaris is the reference deployment); the identity-mapping model is security-reviewed across 30,000+ GCS deployments — so the "failed top-down motion" premise is empirically false. |
| **C6** filesystem-as-state sufficient for v1 | **STANDS-WITH-CAVEATS** | Mechanically true (ShellFunction shared cwd; existence proof on Polaris). Caveats: cwd must be a hard tool-parameter not a soft skill; 10 MB result cap + 30-min TTL + single-flight ordering must also be taught; warm-in-memory workflows are out of scope. |
| **C7** per-facility config solvable via templates + probing | **STANDS-WITH-CAVEATS** | Scheduler/account/queue/launcher/block-size are template-able and probeable; **`worker_init`/network-interface/reproducible-env are not** — pin the env (container or plugin-managed venv keyed to the client's SDK/dill/Python versions) and validate with a canary task. Templates *are* the per-facility content, relocated. |
| **C8** persistent login-node coordinator is tolerable | **REFUTED** | True at NERSC *only via the gated Workflow QOS request* — tolerability comes from a sanctioned opt-in, not from being lightweight. **TACC: "AI workloads on compute nodes only," suspension.** OLCF: killed without warning; sanctioned path is Slate/chained-job. ALCF: undefined grey zone. |
| **C9** credentials kept out of LLM via separate process + scrub hook | **REFUTED** | The out-of-band *acquisition* path is sound. But the dominant leak channel is `run_shell` stdout (73.5% of leaks via stdout, arXiv 2604.03070); an injected agent runs `base64 ~/.ssh/nersc-cert.pub`, and regex scrubbing is readily evaded by encoding/splitting. "Effectively closed" overstates; needs a dedicated-UID broker + structured outputs. |
| **C10** warm-pilot delivers seconds-scale interactive loop | **STANDS-WITH-CAVEATS** | Mechanism real; **no published end-to-end warm-RTT benchmark on a real compute node exists** (the 76 ms is AWS-co-located). The only real agent-on-Polaris demo used minute-scale polling. Holds only while a warm block is held at continuous cost; cold-start (the default) is minutes-to-hours. |

---

## Recommendation: BUILD WITH CONDITIONS

Build, with these non-negotiable conditions, in priority order:

1. **Rewrite the two load-bearing claims honestly.** Replace C1's "identical to SSH / zero new attack surface" with *"identical privilege boundary to SSH (your UID, no root, no identity-mapping daemon) — materially lower review burden than an MEP — plus a new agent command channel we make facility-observable, facility-revocable, and periodically re-MFA'd."* This single change converts your weakest sentence into your security pitch.

2. **Invert the GTM (C5/C8).** Replace "land silently → accumulate → reveal telemetry" with **"co-design with NERSC as a named design partner from day zero."** Gate marketplace launch on one on-record facility reference. In HPC, the security-review-passed reference — not install count — is the unit of adoption.

3. **Make scale-to-zero the only default; gate warm-block behind a per-facility, budget-checked opt-in (C8/C10).** Enforce cost governance as a runtime control plane in the MCP server (pre-flight allocation check, live `session_spend` on every tool result, wall-clock warm-block TTL, per-block node cap), not a documented tradeoff.

4. **Ship NERSC-first; mark ALCF/OLCF/TACC "human-in-the-loop restart only" (C2/C3).** Do not paper over them with the "cattle not pets" self-heal claim that the ControlMaster ban (OLCF) and missing service accounts make impossible.

5. **Build the agent-safety subsystem as hard controls (C9/C1):** dedicated-UID credential broker (so `run_shell` cannot read `~/.ssh`/`storage.db`), `ForwardAgent=no` (CVE-2023-38408), structured allow-listed tool outputs, a Globus-Transfer destination allowlist with human confirm above a size threshold, and a tamper-evident per-tool-call audit log. Treat regex scrubbing as a non-load-bearing backstop, documented as such.

6. **Scope the run_shell engineering honestly (C6/C10):** durable task handle persisted before return, `reload_tasks()` on reconnect, warm-vs-cold detection keyed on *worker registration* (not endpoint Running state), session-persistent cwd/env shim, and SDK-version-compatibility probing.

**Why not "rethink":** the mechanism advantage and the personal-vs-MEP privilege argument are genuine and evidence-backed; the failures are in framing and motion, which are fixable without abandoning the architecture. **Why not unconditional "build":** as written, the first prompt-injection incident under a user's UID produces a facility-wide ban and reputational blast radius onto Globus Auth/Compute — the very assets the monetizable endgame depends on.

---

## The single highest-leverage next experiment

**Run and publish the "HPC as a REPL" demo on real Perlmutter compute nodes — instrumented — and use it as the artifact to secure NERSC's on-record sanction.** One experiment resolves the most uncertainty and de-risks the most must-be-trues:

- **Measures the unmeasured core (C10, MT3):** median + p99 end-to-end warm `ShellFunction` round-trip (`submit()`→`.result()`, including the cloud-mediated AMQP hop and dill serialization) for a trivial command on a *warm pilot block on a real compute node*. No such number exists anywhere. If the median lands in low single-digit seconds and p99 stays in seconds, the latency moat is confirmed; if not, the whole thesis weakens and you've learned it for the price of one demo.
- **Forces the real engineering (MT5):** a passing transcript must include **one turn using a bare relative path** (proving the cwd/env shim) and **one turn separated by >120 s of think-time** (forcing scale-to-zero and proving honest cold-start messaging + durable task-handle recovery). These two acceptance criteria flush out the durable-handle/`reload_tasks`/session-shell work the plan currently under-budgets.
- **Produces the GTM artifact (MT1, C5/C8):** done via NERSC's Workflow QOS with a filed request form, the demo *is* the sanctioned-pilot conversation — turning the flywheel from adversarial-bottom-up into facility-co-designed, and yielding the on-record reference that gates marketplace launch.

Concretely: stand up a personal endpoint on a Perlmutter login node under Workflow QOS, hold a 1-node warm block, drive a Claude Code edit→run→debug session through the MCP server, log per-turn latency and block state, and bring the transcript + cost data to NERSC as the basis for a published "supported way to do agentic compute here" sentinel.

*(Low-confidence items to flag for the owner: the exact NERSC Workflow QOS approval latency and whether an AI-agent coordinator qualifies; the maximum sshproxy cert lifetime NERSC will approve for an automated workflow; whether a Red SFAPI client's IP whitelist can include a Perlmutter-login-node-hosted MCP server — all are open questions in the research, not established facts.)*
