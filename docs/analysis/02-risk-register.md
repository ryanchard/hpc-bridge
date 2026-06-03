# Risk Register & Mitigations

Companion to [`../vision.md`](../vision.md). Part of the committed steelman + de-risk analysis of the hpc-bridge proposition.

This register assumes hpc-bridge **should** exist and is worth building — and then states, honestly, what will get it banned, breached, or quietly abandoned, and what concrete controls keep each risk off the critical path. Mitigations are drawn from the debate and are written to be implementable in the MCP server / bootstrapper, not aspirational.

A note on honesty: several load-bearing claims in the vision were adversarially verified and came back **REFUTED** or **STANDS-WITH-CAVEATS**. Where a risk touches one of those claims, the verification verdict is cited inline (C1..C10). This register does **not** repeat the vision's "zero new attack surface / identical to SSH" framing as fact — that framing is the single most dangerous sentence in the document (C1 REFUTED at the control boundary) and is treated here as a risk, not a mitigation.

---

## Scoring key

- **Likelihood / Impact**: `Low` / `Med` / `High` / `Critical`, judged for the v1 launch target (NERSC-first, personal endpoint, autonomous Claude Code agent with `run_shell`).
- **Verification status**: the adversarial-verification verdict of the most relevant load-bearing claim, or `n/a` where the risk is not tied to a single claim.
- **Residual**: what remains after the mitigation is fully implemented — the honest floor, never zero for the top items.
- **Confidence**: `[high]` / `[med]` / `[low]` on the *evidence*, not on the outcome. `[low]` items are flagged explicitly and must not be cited as settled facility facts.

---

## Summary table (ordered by severity)

| ID | Risk | Lens | Likelihood | Impact | Verification | Residual |
|----|------|------|-----------|--------|--------------|----------|
| **R1** | Indirect prompt injection on the shared filesystem = RCE at allocation scale (agent-on-HPC threat model / blast radius) | Security | High | Critical | C1 **REFUTED** | High — injection is not reliably preventable; controls cap blast radius, not occurrence |
| **R2** | Credential isolation does not hold: `run_shell`-as-you can read the user's own at-rest secrets; regex scrubbing is evadable (C9) | Security | High | Critical | C9 **REFUTED** | Med-High — broker/UID separation closes the channel; agent-reads-own-cred-files is irreducible |
| **R3** | Facility security review blocks/ bans the pattern; "identical to SSH / no new review" is false at the control boundary (C1) | Security / Policy | High | Critical | C1 **REFUTED** | Med — sanctioned-pilot path converts ban risk to a review step, not to acceptance |
| **R4** | Login-node persistence trips AUP / process-reaping at most facilities (C8) | Policy / Ops | High | High | C8 **REFUTED** | Med — NERSC workflow-QOS is sanctioned; OLCF/TACC require re-architecture or are hostile |
| **R5** | Allocation burn: warm-block profile silently bankrupts small awards; no in-loop accounting | Cost / Governance | High | High | C10 **STANDS-WITH-CAVEATS** | Med — budget gate + TTL bound the burn; facility ledger attribution stays collapsed |
| **R6** | Persistence/reconcile fragility: "survives endpoint death transparently" fails off-NERSC and in warm mode (C2) | Distributed-systems / Ops | High | High | C2 **REFUTED** | Med — durable-handle + NERSC scrontab self-heal; off-NERSC restart needs a human MFA event |
| **R7** | Config correctness: `worker_init` / reproducible env / network interface not solvable by templates+probing (C7) | Engineering | Med-High | Med-High | C7 **STANDS-WITH-CAVEATS** | Med — pin/containerize env + canary-probe; facility template drift remains |
| **R8** | Correlated reconcile retry storm + slurmctld RPC saturation across many endpoints | Ops / Scheduler | Med | High | n/a | Med — jittered backoff + facility-side self-heal; aggregate poll load remains a scheduler concern |

Severity ordering rationale: R1–R3 are the items a facility CISO blocks the project on (each tied to a REFUTED load-bearing claim and to a documented incident class). R4–R6 are the items that get the project *operationally* killed after the first incident or the first surprise invoice. R7–R8 are real but bounded engineering/ops risks that degrade UX and scheduler health rather than ending the project.

---

## R1 — Indirect prompt injection on the shared filesystem = RCE at allocation scale

| | |
|---|---|
| **Lens** | Security (agent-on-HPC threat model, blast radius) |
| **Likelihood** | High |
| **Impact** | Critical |
| **Verification** | C1 **REFUTED** — "the agent runs as you, doing only what you could already do by typing it" understates the autonomous-agent attack surface; injection is net-new vs. SSH |

**The risk.** An autonomous agent with `run_shell` that reads any user-writable file on a shared Lustre/GPFS filesystem — READMEs, `.cursorrules`, job scripts, package metadata, **another job's output** — is exposed to indirect prompt injection. The research is blunt: published agent-injection benchmarks (InjecAgent, AgentDojo) report success rates ranging from ~25% up to ~45–52% for the strongest adaptive attacks, defenses are routinely bypassed, and agent-focused red-team studies demonstrate high data-exfiltration success across dozens of MITRE ATT&CK techniques. On HPC the blast radius is not a laptop: it is the user's entire allocation, every dataset reachable on the global filesystem (potentially petabytes across projects), plus a pre-wired Globus Transfer exfil channel. The vision's own "Expand" phase concedes personal endpoints manufacture a *security-governance pain* — i.e. by the project's own logic they are not zero-new-surface (C1 self-refutation).

Why this is categorically worse than the SSH baseline the vision invokes: autonomous agents act at machine speed and can be triggered by untrusted data — neither applies to a human typing. The Sysdig May 2026 incident (an LLM-agent-driven intrusion, with the model making real-time post-exploitation decisions) exfiltrated the full contents of an internal PostgreSQL database in under two minutes.

**Mitigation (concrete, from the debate).**

1. **Treat injection as unfilterable; cap blast radius architecturally.** Do not rely on the skill or on output scrubbing to prevent injection. The only architecture with provable properties is **dual-LLM / CaMeL channel separation** (quarantined reader that has *no* `run_shell`; privileged executor that never ingests untrusted content; no direct path between them — 77% task completion, zero injection successes in the cited work). This is a redesign, not a config flag, and it contradicts the current interleaved `run_shell`/`read_file`-in-one-context design — flag it as a v1-or-v2 architectural decision.
2. **Rule-of-Two as a hard MCP-dispatch constraint:** the agent must not invoke `run_shell` in any session that has ingested untrusted content (shared files, job output) without an explicit out-of-band human confirmation. Enforced in tool dispatch, outside the LLM.
3. **Irreversible-action confirmation + governors** (also serve R5): hard concurrency cap on parallel shell calls; per-session action budget; mandatory confirm before `rm`, large writes, Slurm submission above N node-hours, and **any** Globus Transfer.
4. **Globus Transfer destination allowlist** enforced in the MCP server before any transfer; deny agent-initiated endpoint creation / permission changes; log every transfer (src/dst/size) to a tamper-evident local log before dispatch.
5. **Filesystem-as-state is also the injection persistence channel** (C6 caveat): content written in session N is read in N+1. Apply provenance/isolation to externally-sourced content before it is written to the working directory.

**Residual.** High and irreducible: even with allowlist + audit + budget + Rule-of-Two, an injected agent can still do damage *within the user's own legitimate scope* (read+exfil within an approved endpoint, burn the session budget on a poisoned task). Detection is post-hoc; SIEM ingestion + human triage is slower than a 2-minute agent-driven exfil. **This is the risk that a facility CISO blocks the project on, and no mitigation closes it — only narrows it.** State this plainly in facility outreach.

---

## R2 — Credential isolation does not hold against `run_shell`-as-you

| | |
|---|---|
| **Lens** | Security (credential isolation) |
| **Likelihood** | High |
| **Impact** | Critical |
| **Verification** | C9 **REFUTED** — "credentials reliably kept out of the LLM via separate process + PreToolUse scrubbing, risk effectively closed" |

**The risk.** The vision draws the credential boundary in the wrong place. Process separation isolates the LLM's *address space* from the secret, but every credential lives at the **user's UID**, and `run_shell` executes **as that same UID**. The agent never needs the secret to "reach the LLM" — it issues `run_shell("base64 -w0 ~/.ssh/nersc-cert.pub")` or `run_shell("sqlite3 ~/.globus_compute/storage.db .dump")` and the credential leaves through the tool's *own legitimate output channel* (base64-encoded, so no key-shaped string for the scrubber), or is exfiltrated server-side and never returns to the model at all (nothing to scrub). The two named defenses guard channels the attack does not use: the PreToolUse hook inspects the *inbound* call (`base64`/`curl`/a path — nothing credential-looking), and regex output-scrubbing is evadable by base64/hex/gzip/splitting/legitimate-URL routing (OWASP LLM Top-10; arXiv 2604.03070 finds 73.5% of leaks flow via stdout/debug straight into context).

Worse, the **ControlMaster socket is a bearer credential**: anything running as the user during the `ControlPersist` window (proposed 8h) can issue `ssh -S <socket> login.facility <cmd>` with zero credential and zero MFA. The "authenticate once per session" property the vision sells as UX is exactly what makes post-injection lateral movement free. Cached Globus **refresh tokens** in `~/.globus_compute/storage.db` are worse than an SSH key: theft is silent, grants execution *without* further MFA, and auto-renews — strictly worse than the SSH baseline, where a stolen key still hits the facility MFA wall.

**Mitigation (concrete, from the debate).**

1. **Move the boundary from "separate process" to "separate UID."** Run an SSH/credential **broker** as a dedicated service/collaboration account (or systemd `--user` instance with a private `$XDG_RUNTIME_DIR`), *not* the UID `run_shell` executes as. The broker holds the sshproxy cert / agent / ControlMaster socket at `0600` in a `0700` dir; the agent UID has no read access. This converts an intra-UID boundary (which `/proc` and filesystem perms cannot enforce) into a kernel-enforced inter-UID one.
2. **Make the hot path credential-free by construction.** The interactive loop rides Globus Compute over a **narrowly-scoped Globus Auth bearer token** (task-submit to one endpoint UUID) — never SSH material. SSH is touched *only* by the broker at bootstrap/repair. `{status:up}` is then genuinely empty of secrets rather than scrubbed-clean.
3. **Harden broker SSH:** `ForwardAgent=no` (closes CVE-2023-38408 ssh-agent-forwarding RCE on shared login nodes), `IdentitiesOnly=yes`, dedicated agent socket *not* exported into any `run_shell` task env, short `ControlPersist`, ssh verbosity OFF in production. Expose only a narrow `ensure_endpoint_up`/`restart_endpoint` RPC — never a passthrough `ssh`.
4. **Structured, allow-listed tool results** built from typed fields (exit code, named outputs, bounded stdout snippet) — never raw child stdout or exception tracebacks. Demote the PreToolUse hook and output scrubber to documented **non-load-bearing backstops** (exact-match redaction of the specific cert/token the broker loaded is the one scrubbing variant that works).
5. **Drive MFA without it touching the LLM:** use MCP **URL-mode elicitation** (Claude Code ≥ 2.1.76) as the primary OTP-collection channel — the spec guarantees the secret never passes through the LLM context, client, or intermediate servers; the user types password+OTP into the broker's own loopback page. `SSH_ASKPASS` (`C1` variant) is the no-browser fallback; form-mode is last-resort + redaction hook.

**Residual.** Med-High. Three gaps no design removes: **(a)** `run_shell` legitimately runs on a host where the user's *own* at-rest credentials live (`~/.ssh/*`, sshproxy cert, `storage.db` tokens, kerberos cache); the global-mounted shared filesystem means the worker can see `~/.ssh` and `~/.globus_compute` unless actively mount-namespaced/permissioned away — so an injected agent can read and exfiltrate the user's own credentials. This is the irreducible consequence of shell-as-you and must be stated to facility security. **(b)** The scoped Globus token is necessarily on the hot path. **(c)** The one-time MFA entry moment is observable to anything sharing the user's process tree. **Facility-divergence note `[high]`:** OLCF **bans** ControlMaster and ALCF/OLCF have **no documented service-account home** for a broker, so credential isolation is *weakest exactly where the machines are most prestigious*. Mark ALCF/OLCF as "human-in-the-loop restart only, no autonomous reconcile" until a broker principal exists.

---

## R3 — Facility security review blocks or bans the pattern

| | |
|---|---|
| **Lens** | Security / Facility policy / GTM |
| **Likelihood** | High |
| **Impact** | Critical |
| **Verification** | C1 **REFUTED** (trust boundary), C5 **REFUTED** (flywheel) |

**The risk.** The vision's GTM ("Land silently → Accumulate persistent login-node coordinators → walk in with telemetry") manufactures the strongest argument for a **crackdown** before it manufactures demand for consolidation. A crowd of unsanctioned, self-identifying, persistent login-node coordinators that an LLM drives at machine speed does not read to a CISO as "give me an MEP" — it reads as "shadow-IT incident I must shut down." The control-boundary objection is decisive and is what a CISO actually rests their AUP on: SSH safety depends on (1) a human as sole command originator, (2) per-session MFA against the *facility's* auth stack, (3) human-cadence judgment. The warm endpoint destroys all three — after one MFA-gated bootstrap, every subsequent task is authorized by a Globus Auth refresh token the facility cannot see, kill, or review, creating a standing, MFA-free, facility-invisible reverse command channel for up to the endpoint's lifetime. The MEP root/identity-mapping review is genuinely avoided (the one part of C1 that **holds**), but that supports only "lower review burden than an MEP," **not** "zero new attack surface / no new review."

Reputational coupling raises the stakes: the project is owned by Globus Labs. An incident attributed to "the Globus agent plugin" taints Globus Compute and Globus Auth at the exact DOE facilities whose blessing the MEP endgame depends on. `[high]` Facilities are also building their own sanctioned on-ramps (NERSC "Agentic AI" bootcamp; ALCF/Argonne first-party inference service) — a bottom-up unsanctioned wave *competes* with the facility's own program and gives them a reason to name-and-block exactly this pattern.

**Mitigation (concrete, from the debate).**

1. **Invert the GTM from adversarial to sanctioned-pilot.** Co-design with **one named facility (NERSC) from day zero**, entering through the already-open door: workflow-QOS / Spin + sshproxy cert + scrontab reconcile, with the NERSC Agentic-AI bootcamp as political cover. Gate marketplace launch on **one on-record facility reference** ("this is the supported way to do agentic compute here"). In HPC, the security-review-passed reference — not install count — is the unit of adoption; it flips every other facility's review from "novel scary thing" to "the thing NERSC vetted."
2. **Re-anchor the control plane to be facility-observable and facility-revocable** (the CISO's actual ask): prefer a facility-issued, IP-pinned, short-TTL, **facility-revocable** credential over a user-held Globus refresh token. At NERSC the Superfacility "Red" client (OAuth2 client-credentials, 2–48 day expiry, IP-whitelisted, killable via Iris) is exactly this — mandate it; forbid bare cached-refresh-token operation. Emit a **per-task, append-only, SIEM-shippable audit record** (UID, endpoint UUID, SHA of command, cwd, node-hours, Globus task-id, timestamp). Impose a **facility-set hard TTL** on the warm subscription that forces re-bootstrap = re-MFA, periodically re-coupling MFA to command origination and giving the SOC a kill switch it can pull without the user.
3. **Facility-gated capability handshake** (also de-risks R4): the bootstrapper fetches and validates a signed facility policy sentinel before running; **no sentinel ⇒ scale-to-zero, no persistent login-node process, dormant between sessions.** Accumulate only among facilities that already said yes.
4. **Rewrite the dangerous sentence.** Replace "zero new attack surface / identical to SSH" with the honest, *stronger* framing: "identical **privilege** boundary to SSH (your UID, no root, no identity mapping), **plus** a new command channel we make facility-observable, facility-revocable, and periodically re-MFA'd." That is a claim a CISO can bless.
5. **Make telemetry opt-in and user-visible from install** (C5 second-order): the "Accumulate" engine, if it reports user behavior to the user's own security org, is a betrayal-of-the-early-adopter structure that makes hpc-bridge a "snitch" the moment a facility emails a user about an unsanctioned endpoint. Opt-in framing ("share my usage with my facility to help them support this") is honest but materially weakens the involuntary-accumulation flywheel — accept the slower, partnership-based motion.

**Residual.** Med. A NERSC reference does **not** transfer to OLCF (ControlMaster banned, RSA SecurID single-use, no sshproxy) or TACC (suspends accounts for "running AI tools on login nodes" — a clause naming the exact pattern) or ALCF (per-login OTP, no cert reuse, no persistent-services platform). The motion may succeed at exactly one site and stall where the machines are most impressive. Second residual: a facility-co-owned MEP, once built, is a one-line endpoint-id change in *either* direction — successful consolidation can disintermediate the plugin, since the durable asset (MEP + Globus Auth) belongs to Globus/the facility.

---

## R4 — Login-node persistence trips AUP / process-reaping

| | |
|---|---|
| **Lens** | Facility policy / Ops |
| **Likelihood** | High |
| **Impact** | High |
| **Verification** | C8 **REFUTED** — "persistent coordinator is tolerable because lightweight … will not trip resource policing or AUP at the major facilities" |

**The risk.** The claim is true on the **resource-quantity** axis (the ~200 MB / <1% CPU coordinator sits well under NERSC's 30 GB / 12.5% cgroup caps) but false on the **acceptable-use** axis, which is policy/discretion independent of footprint. The vision's own line 97 concedes "a few sites forbid persistent personal processes on login nodes." Facility-by-facility `[high]`:

- **NERSC** — tolerable, but **not because it is lightweight**: only via the purpose-built **workflow QOS** (≤90-day walltime, ≤25% of a login node) which requires a Workflow-QOS Request Form. Scrontab and Spin are sanctioned alternatives. "Processes that take an unreasonable amount of resources for a long time … might get canceled involuntarily."
- **OLCF/Frontier** — bare persistent login-node daemons "may be killed without warning"; sanctioned paths are **looped chained jobs** or **Slate (OpenShift)**, not a login-node process. Lightweight does not exempt it.
- **TACC** — "All AI-assisted workloads must be executed on compute nodes only"; running AI tools on login nodes "may result in account suspension." An AI-agent-coupled coordinator violates this **by construction**, regardless of footprint.
- **ALCF** — grey zone: login nodes "for compilation, editing, job submission only"; no workflow-QOS or persistent-services equivalent; ALCF "is researching solutions for user service orchestration." Tolerated in practice (appears in Globus example configs) but with no written blessing — "acceptable until it isn't."

The causal mechanism in the claim is wrong even where the outcome is favorable: facilities that tolerate it do so via a **specific sanctioned mechanism**, not because the process is small.

**Mitigation (concrete, from the debate).**

1. **Default to scale-to-zero, no persistent login-node process between sessions** unless a facility has explicitly opted in (sentinel from R3.3). A fully dormant cold-start endpoint is dramatically more facility-acceptable.
2. **Run the coordinator only via the facility's sanctioned mechanism**, not as a bare daemon: NERSC workflow-QOS / scrontab / Spin; OLCF Slate or a self-resubmitting chained job; ALCF case-by-case with informal written sign-off.
3. **Re-architect the coordinator** so it *can* run as a chained batch job or managed-container service where login-node daemons are forbidden — so "lightweight" stops being the load-bearing assumption.
4. **TACC: do not deploy the login-node pattern** until explicit confirmation that a Globus Compute coordinator is exempt from the AI-tools-on-login-nodes rule. Treat as non-viable for v1 otherwise.

**Residual.** Med. NERSC has a clean sanctioned path; ALCF is undefined; OLCF needs a different architecture (chained job / Slate); TACC is actively hostile to the class of activity. The scope of "works by default" is essentially NERSC-only at launch.

---

## R5 — Allocation burn: warm-block profile bankrupts small awards

| | |
|---|---|
| **Lens** | Cost / Allocation governance |
| **Likelihood** | High |
| **Impact** | High |
| **Verification** | C10 **STANDS-WITH-CAVEATS** (warm latency real but cost-bounded); C2/C8 reinforce |

**The risk.** Facilities charge for **all reserved node-hours regardless of utilization** (walltime × nodes × QOS factor); there is no free idle grace, and NERSC's preempt QOS has a 2-hour minimum charge. The "scale-to-zero on idle" safety net is mechanically unreliable for an interactive agent, and flipping the knob to **warm turns OFF the only cost protection** (the interactive profile needs `min_blocks ≥ 1`, and Parsl never scales below `min_blocks`). The math is brutal for exactly the land-phase user:

- A single warm GPU node held 8 h/day ≈ **2,880 node-hours/yr**.
- A NERSC Exploratory award is **250 GPU node-hours** — exhausted in **~31 days by the warm coordinator alone**, before any science.
- ~14% / yr of a 20K Director's-Discretionary award; <1% of a 500K INCITE award. So the warm "interactive REPL" demo is affordable only for users who least need a frictionless on-ramp, and ruinous for the small-allocation early adopters the flywheel targets.

`[med]` Scale-to-zero can also cost *more*: with `max_idletime` default 120 s, every human think-pause >2 min tears down the block, and the next turn pays a fresh minimum-charge floor at floored-charge facilities. None of this is visible in the v1 tool set — the first signal of depletion is a job submission failing with "insufficient allocation." An injected agent looping `sbatch` (R1) is a same-week certainty at 250 node-hours.

**Mitigation (concrete, from the debate). Make the MCP server the accounting authority, not the scheduler.**

1. **Pre-flight allocation gate:** `ensure_endpoint_up` and every block-provisioning call queries the facility accounting API (NERSC Iris / `sacctmgr`; SchedMD `sshare`) and **refuses a warm block below a configurable floor** (default: refuse warm under 1000 node-hours remaining; force batch).
2. **Real-time session budget** the server debits (`walltime_of_live_blocks × nodes × charge_factor`), surfaced as `allocation_remaining` / `session_spend` on **every tool result** (not a separate tool the model must remember). Hard-stop + human re-auth on budget exceed.
3. **Wall-clock warm-block TTL independent of Parsl idle logic:** the server `scancel`s its own blocks after N minutes of no MCP tool activity (default 5 min) — the server knows true agent-idle state; `max_idletime` only sees worker queue depth.
4. **Per-block size cap** in the provision path (default 1–2 nodes) so an injected "benchmark the cluster 1000×" payload cannot expand a block (also R1).
5. **Ship scale-to-zero / batch as the ONLY default;** warm is a privileged, logged, per-session, budget-checked opt-in.

**Residual.** Med. The server's ledger is an **estimate** — facility accounting lags (post-job charging; Iris reconciliation trails by minutes-to-a-day), and the agent + the user's own batch jobs draw the same allocation with no shared lock. TTL-scancel can itself waste money by tearing down a block the agent would have reused in 30 s. **Attribution residual `[high]`:** because the endpoint runs as the user, every node-hour an injected/over-eager agent burns is indistinguishable in Iris/sacct from deliberate science — same UID, same project. When a 250-hour award evaporates, the PI cannot tell the program manager "the agent did it," and warm-block idle-node charges read as "low GPU utilization / poor allocation stewardship," the exact metric that gets a renewal cut. Mitigate by emitting a facility-ingestible signed usage tag (Slurm `--comment`) separating agent-driven from human-driven node-hours — otherwise cost governance solves the wallet but not the PI's accountability.

---

## R6 — Persistence / reconcile fragility

| | |
|---|---|
| **Lens** | Distributed systems / Ops |
| **Likelihood** | High |
| **Impact** | High |
| **Verification** | C2 **REFUTED** — "endpoints are disposable cattle … agent session survives endpoint death transparently" |

**The risk.** The load-bearing word is **"transparently,"** and it fails three ways even at NERSC, and the whole reconcile premise fails off-NERSC:

- **In-flight running tasks raise, not resume.** Globus docs require wrapping `.result()` in try/except; a task executing on a worker when the endpoint dies surfaces an exception. Resubmitting a non-idempotent `run_shell` (already wrote files, consumed allocation) is at-least-once, not exactly-once.
- **30-minute result TTL is a hard, unrecoverable wall.** If a result completes, the endpoint dies, and SSH-restart needs a human MFA event + cold-start requeue, elapsed time can exceed 30 min ⇒ result permanently lost. The reconcile latency itself can blow the budget the claim depends on.
- **Warm block is lost on restart.** The reconcile preserves UUID/config but **not** the pilot block; the restarted endpoint re-submits `sbatch`/`qsub` and re-incurs full queue wait — the next agent call is a **cold start (minutes-to-hours), not seconds** (the entire value prop, C10). "Disconnected" state also requires cleanup before restart ("not stopped correctly previously").
- **The restart leg is SSH, gated by per-restart human MFA at 3 of 4 facilities (C3 REFUTED).** Non-interactive reconcile works *only* at NERSC (sshproxy 24h cert + ControlMaster). ALCF/TACC need a fresh single-use OTP per login; **OLCF bans ControlMaster and uses single-use RSA fobs** — every restart is a human-in-the-loop event, and the autonomous self-heal claim collapses exactly where it is most needed.

What **holds** (steelman): UUID/config are stable on disk; `GLOBUS_COMPUTE_CLIENT_ID/SECRET` enable headless re-auth *to Globus* (not to the login node); AMQP retries reconnection for 1–20 h on transient drops (self-heals the common case with no SSH); and the v3.3.0 shutdown-race fix retains *queued-not-yet-running* tasks for the next instance.

**Mitigation (concrete, from the debate).**

1. **Build a durable-handle pattern, not a single blocking RPC** (this is real v1 core engineering, not a small delta to globus-mcp): run one long-lived Executor per endpoint inside the MCP process so the AMQP subscription outlives tool calls; on submit, persist `task_id, task_group_id, endpoint_id, cwd, submit_ts` to sqlite **before** returning; on MCP/reconcile startup call `reload_tasks(task_group_id)` to re-attach orphaned futures and fetch completed-but-unfetched results inside the 30-min window.
2. **Probe warm-vs-cold by worker REGISTRATION at the interchange, not endpoint `Running` state** (the endpoint reports Running the instant the coordinator starts, before any worker block registers — a state-keyed probe misroutes the first call into the fast path and then blocks unboundedly). Branch the tool contract: warm ⇒ short bounded blocking wait returning the value; cold ⇒ structured pending result + handle the agent polls.
3. **Move self-heal off the client onto a facility-side primitive:** NERSC **scrontab** (Slurm-managed, resilient across login-node failure, 30-day walltime) or an OLCF chained job — restart in place with no inbound SSH/MFA. Client does read-only status only.
4. **Idempotency via filesystem-as-state:** the skill/runtime must dedup non-idempotent `run_shell` (filesystem sentinel) so at-least-once resubmission is safe.
5. **Default timeout on `.result()`** converting an indefinite hang into a structured "endpoint unreachable, run `ensure_endpoint_up`" — a **hang** is the single worst interactive failure mode.

**Residual.** Med. The 30-min post-completion TTL is a hard server ceiling: a cold task completing while no MCP process is alive (laptop closed overnight, queue resolves at 3am) loses the `ShellResult` — only filesystem side-effects survive, so filesystem-as-state must be the source of truth. Off-NERSC, restart genuinely requires a human MFA event; for ALCF/OLCF/TACC **invert the cattle framing** and keep the coordinator persistently alive via the blessed mechanism (chained job / tmux / workflow-QOS) — persistence matters *more* there, not less.

---

## R7 — Config correctness: templates + probing don't cover the hard fields

| | |
|---|---|
| **Lens** | Engineering |
| **Likelihood** | Med-High |
| **Impact** | Med-High |
| **Verification** | C7 **STANDS-WITH-CAVEATS** — solvable for structural fields, **not** for `worker_init` / reproducible env / network interface |

**The risk.** The config decomposes into a probeable layer and an un-probeable residue, and the residue is exactly where newcomers face-plant:

- **Probeable `[high]`:** scheduler type (`sbatch` vs `qsub`), account + partition + QOS (`sacctmgr show assoc user=$USER`), walltime/node bounds (`sinfo`/`scontrol`/`qstat`), launcher and block sizing (facility constants, already in the ~14 shipped Globus example configs). The vision's "answer two questions (account, queue)" covers 2 of 8–12 fields — and not the hard ones.
- **Not probeable, not templateable `[high]`:** **`worker_init`** (the user's specific conda/venv *name* + facility module incantations that go stale — e.g. Polaris's `module use /soft/modulefiles; module load conda`); the **network interface** `ifname` (Globus's own docs say "there is no one answer … ask a more knowledgeable person … or trial and error"; varies per facility *and* per partition: hsn0/ib0/bond0/ens3f0/…); and the **reproducible environment** — the worker's Python + `dill` + `globus-compute-sdk` versions must match the **client's**, which a login-node probe cannot observe (issue #1197: a version skew yields unrecoverable serialization failure that looks like "the command failed," and the agent will try to fix it by editing user code). There is **no "capability probing" feature in Globus Compute today** — it would be built from scratch and still cannot see the client env.

A "shipped facility template" that someone hand-authors per facility and maintains against module/interface/QOS drift **is** a hand-written per-facility artifact under a different name — the claim relocates the work, it does not eliminate it.

**Mitigation (concrete, from the debate).**

1. **Stop trying to template/probe the environment — PIN it.** Ship the worker in a container (Apptainer/Shifter/Podman-HPC) fixing Python + `globus-compute-endpoint` + `dill` identically on client and worker, or auto-provision a managed venv on the login node whose versions are *derived from the connecting SDK client*. This eliminates the #1197 skew class and turns `worker_init` from "unknowable user env" into "an artifact the plugin created."
2. **Replace static templates with live validation:** after generating `config.yaml`, submit a trivial round-trip ShellFunction (`echo` + `import globus_compute_sdk`; assert version parity; assert a worker connects back within N s) through the warm path and only report "ready" on success — catching stale module names, wrong env names, and version skew at provision time, not at first real task. This is failure-detection, not auto-discovery.
3. **Handle `ifname` by probe-then-validate:** enumerate UP interfaces, launch a one-node canary block, confirm a worker actually connects back before committing — automated trial-and-error.
4. **Gate `run_shell` on a worker-side SDK version-compatibility check, not just endpoint-is-up** (capability probing currently framed only as a scheduler concern).
5. **Scope honestly:** structural fields are template+probe solvable; environment is solved by pinning/containerization; unsupported facilities fall back to manual config.

**Residual.** Med. Templates and canary expectations are per-facility *content* (curated as data) that decays with facility changes (e.g. Polaris's conda-module rename) and needs a maintained update channel. Containerization adds its own facility-specific friction (Shifter vs Apptainer vs Podman-HPC).

---

## R8 — Correlated reconcile retry storm + slurmctld RPC saturation

| | |
|---|---|
| **Lens** | Ops / Scheduler |
| **Likelihood** | Med |
| **Impact** | High |
| **Verification** | n/a (sysadmin debate finding; not tied to a single C-claim) |

**The risk.** Two distinct failure modes that a facility models during review:

1. **Thundering herd.** When Arbiter2/cgroup reaps coordinators on a busy login node, every MCP server independently SSHes back to restart and resubmit a pilot block *at once* on the same login nodes. Restart re-spikes RSS and is reaped again — positive feedback under contention. At 2am with 40 endpoints this looks like a fork-bomb, and killing it makes it worse because agents auto-restart. A retry-without-backoff loop removes the human pause that lets contention self-resolve.
2. **slurmctld RPC saturation (the real killer).** Each HTEX block watchdog plus the 5 s scale-to-zero strategy loop drips `squeue`/`sacct`/`sbatch` RPCs. slurmctld serializes them, so hundreds of idle endpoints push thousands of RPCs/minute and the **whole facility's scheduler goes slow with no single abusive user**. The pilot pattern was built for *few endpoints, many tasks*; the flywheel inverts that to *many endpoints*. Scale-to-zero (the cost-friendly default from R5) *raises* RPC load via block churn — the cost-friendly default is scheduler-hostile.

**Mitigation (concrete, from the debate).**

1. **Facility-side scheduler-resilient self-heal** (shared with R6.3): scrontab / chained job / systemd `--user` lingering with a memory ceiling and pre-imported modules; client does read-only status only.
2. **Full-jitter backoff** (base 30 s, cap 30 min) plus a **centrally pullable circuit breaker / restart budget**: the herd backs off above K restarts in N minutes; the breaker can be tripped without per-user cooperation.
3. **Aggregate scheduler-RPC budget as a first-class constraint** beside the cost knob: cap poll frequency, coalesce status queries, prefer event/heartbeat-driven over polling where possible.
4. **Prove it pre-deployment:** run 25 endpoints on a dev login node and induce a reap; measure slurmctld RPC load and confirm backoff prevents the storm.

**Residual.** Med. Watchdogs still resubmit pilot blocks, so aggregate `sbatch`/`squeue` load from N coordinators can degrade a single-threaded slurmctld even with per-client politeness — a facility cannot throttle every user polling `squeue`. The circuit breaker depends on the Globus service the facility cannot see into, so a 2am response may need a call to Globus Labs, not a local `scancel`. This is the RPC-load argument that ultimately **pulls the MEP in** (one endpoint polling slurmctld once) — i.e. scheduler health, not security, may be the strongest facility-side case for consolidation.

---

## Cross-cutting observations

- **The personal-endpoint privilege argument is sound; the inferences built on it are not.** Every REFUTED claim (C1, C2, C8, C9) shares a structure: a true narrow premise ("runs as your UID, no root, no identity mapping") is over-extended into a false absolute ("zero new attack surface," "no new review," "survives transparently," "credentials effectively closed"). The honest, *stronger* posture is to keep the privilege claim, drop the absolutes, and lead facility outreach with the observable/revocable/audited control plane (R2.2, R3.2).
- **NERSC-first is not a preference, it is a structural requirement.** sshproxy certs + permitted ControlMaster + workflow-QOS + scrontab + Spin + Superfacility "Red" client + collaboration accounts (a broker home) make NERSC the *only* facility where R2–R6 mitigations all have a real implementation today. ALCF/OLCF/TACC each break at least one load-bearing mitigation.
- **The latency moat is self-cannibalizing under the cost brief (C10 + R5).** The only thing hpc-bridge adds over a free SSH+Slurm MCP server (`salloc` + ControlMaster reproduces the warm-block pattern, C4 REFUTED) is warm-block dispatch — which is economically disqualifying for the small-allocation land-phase user and indistinguishable in audit logs from an allocation-exhaustion attack (R1/R5). Resolve this tension explicitly before treating warm-block latency as the differentiator.

---

## Confidence and provenance

Likelihood/impact are author judgments for the v1 NERSC-first target. Verification verdicts (C1–C10) are from the adversarial-verification pass and are reproduced faithfully — REFUTED claims are not laundered into mitigations. Facility-specific policy facts are cited at `[high]` where backed by primary facility docs in the research streams; items marked `[med]`/`[low]` are inferences or unmeasured and **must not** be cited as settled facility facts. Open questions that materially affect this register (NERSC extended-cert SLA and Red-client review timeline; ALCF undocumented service-account path; whether a Globus coordinator is exempt from TACC's AI-tools-on-login-nodes rule; measured warm round-trip latency on a real leadership-class node) remain unresolved and are flagged where relevant rather than assumed away.
