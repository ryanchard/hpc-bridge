# Viability, Moat & Go-to-Market

*Analysis companion to [`docs/vision.md`](../vision.md). Mandate: steelman + de-risk — assume hpc-bridge should exist, but be rigorously honest about where the opportunity, the moat, and the go-to-market actually hold versus where the consolidated analysis marked them CONTESTED or REFUTED. Claim IDs (C1–C10) refer to the adversarial verification of the vision's load-bearing claims; verification verdicts are cited inline.*

---

## 1. Executive summary

hpc-bridge is a genuinely good product idea sitting on top of **two marketing claims that the verification refuted** and **one cost mechanic that cannibalizes its only differentiating feature**. The honest version is *stronger* than the vision's version, but it is narrower, slower, and depends on facility partnership rather than stealth.

| Question | Vision's answer | Verified answer | Verdict |
|---|---|---|---|
| Is the opportunity real? | An agent needs a credentialed low-latency HPC foothold; nobody else can give it. | The *need* is real; the *exclusivity* is not. Free SSH+Slurm MCP servers ship today; Globus Labs ships its own MCP-over-Compute. | Opportunity yes, exclusivity no |
| What is the moat (C4)? | Globus Compute's federation/identity/provisioning. | The durable asset is **Globus Auth** identity federation — owned by Globus/UChicago, available to every competitor including Globus's own tooling. Compute provisioning is open-source Parsl HTEX. | **C4 REFUTED** |
| Does the MEP flywheel work (C5)? | Accumulate personal endpoints → facilities *want* an MEP. | Accumulation manufactures the strongest argument for a **crackdown** before it manufactures demand; MEPs already shipped top-down (ALCF Polaris, May 2024). | **C5 REFUTED** |
| Is "land with no security review" true (C1)? | Personal endpoint = identical to SSH, zero new attack surface. | False at the control boundary: an autonomous LLM over `run_shell` is a new, machine-speed, injectable surface; persistent login-node processes are a *separate* policy category at every facility. | **C1 REFUTED** |
| Who is the wedge user? | A computational scientist wanting a fast edit→run→debug loop. | Correct — but the small-allocation early adopter the flywheel needs is **bankrupted by the warm-block feature** that differentiates the product. | Wedge real, economics inverted |
| What is the real defensible position? | *(implied: the Compute substrate)* | The **bootstrapper + out-of-band credential boundary + reconcile loop + facility-policy navigation**, deployed NERSC-first as a *sanctioned pilot*. | This is the actual moat candidate |

**Bottom line:** the product should exist, but its viability rests on execution quality in a crowded field and on one named facility partnership — not on an inimitable moat and not on a bottom-up flywheel that the analysis shows trips the exact tripwire that kills it.

---

## 2. The opportunity: real need, contested exclusivity

The core observation is sound and worth restating because the rest of the analysis erodes the *framing*, not the *premise*:

- Interactive agents and batch queues are fundamentally mismatched. An agent that fires a job into a 45-minute queue cannot do a tight edit→run→debug loop.
- Globus Compute's pilot-job model (Parsl HTEX) genuinely holds a warm block of pre-allocated nodes and dispatches over AMQP with sub-second worker-level latency. **This mechanism is real** (C10 STANDS-WITH-CAVEATS).
- Cloud agent sandboxes (E2B: CPU-only, 24h cap; Modal: ≤64 GPUs multi-node beta, GPU-only, no scheduler/MPI) are **categorically below leadership-class scale** (Frontier 1.35 EF / 9M cores; Aurora 1.01 EF). This sub-claim is durable and is the one uncontested half of the moat argument.

What the verification removes is the word *nobody*. The opportunity is real; the claim that hpc-bridge can own it exclusively is not (Section 3).

---

## 3. The moat (C4) — **REFUTED as stated**, relocatable to a narrower truth

C4 claims: *"No competitor can give an AI agent a credentialed, low-latency foothold on leadership-class HPC; Globus Compute's federation, identity, and provisioning constitute a durable moat."* All three independent verifiers (technical, security-policy, strategic) returned **refuted**. The claim bundles four assertions of very different strength:

| Sub-claim | Verdict | Why |
|---|---|---|
| "No competitor *can*…" | Refuted | Shipping substitutes exist today (below). The exclusivity is false. |
| "low-latency foothold" is Compute-exclusive | Refuted | Warm-block latency is a property of the **pilot-job pattern**, not of Globus Compute. `salloc --qos interactive` (NERSC: node in ~6 min, held ≤4h) + `srun` against the warm allocation + ControlMaster gives sub-second dispatch with zero Globus infrastructure. Parsl's Low-Latency Executor does the same with no cloud hop. |
| "federation/identity/provisioning is a durable moat" | Mischaracterized | The durable asset is **Globus Auth** (420+ IdPs, ACCESS/XSEDE SSO). Provisioning is open-source Parsl HTEX. |
| "cloud sandboxes can't match" | **Holds** | True and durable — but it is the least load-bearing half (defends against a competitor nobody fields for this workload). |

### 3.1 The competitive / substitute landscape

| Substitute | What it gives an agent today | Threat level | Key limitation vs hpc-bridge |
|---|---|---|---|
| **SSH+Slurm MCP servers** (Charlie-Z-work/slurm-mcp-server, dongwookim-ml/slurm-mcp, ~5 others) | npx-installable, <1h setup, job submit/cancel/log, file I/O, shell, tmux-for-2FA. At NERSC + sshproxy → working agent-to-HPC in under an hour. | **HIGH** | Operates at sbatch/squeue *polling* latency unless it holds its own `salloc` block (which then reinvents the pilot job). |
| **globus/globus-mcp** (Globus Labs, first-party) | `register_shell_command`, `submit_task`, `get_task_status` over Compute. Demonstrated running MCP-driven agents over Globus Compute on HPC (arXiv 2508.18489). | **HIGH** | The moat-owner ships the core capability itself. hpc-bridge is, by the vision's own words, "a small delta to the existing globus-mcp server." No bootstrapper, no credential boundary, no reconcile loop. |
| **iowarp/agent-toolkit** (NSF-funded, IIT Gnosis, 15–16 MCP servers) | Slurm MCP that "allocates nodes," markets "operate HPC clusters." | **MEDIUM** | Institutional backing; trajectory uncertain. Scope may stay data-formats + basic Slurm. |
| **NERSC Superfacility API / TACC Tapis** | MFA-free OAuth2 job submission + arbitrary login-node commands (NERSC Red client). NERSC building its own agent on it. | **MEDIUM** | Facility-specific REST, not a low-latency push substrate; NERSC is building first-party. |
| **Open OnDemand / JupyterHub** | Browser portals; queue-wait per session, human-in-the-loop. | **LOW** | No programmatic API, not agent-friendly, categorically not a low-latency substrate. |
| **Cloud sandboxes** (E2B, Modal) | Commodity/GPU VMs. | **LOW** | Wrong tier of compute. |
| **Globus Flows / Expanse (YC P26)** | Pre-defined workflow orchestration / cluster optimization layer. | **NOT A THREAT** | Orthogonal use cases. |

### 3.2 What is genuinely defensible

The verification is consistent across lenses on where a real, narrower advantage lives:

1. **Globus Auth cross-facility identity federation** — for *genuinely multi-facility, simultaneous-dispatch* agent workflows (one credential, several DOE+NSF facilities at once), this is hard to replicate. **But it is Globus's moat, leverageable by every competitor equally, and largely irrelevant to the single-facility use case the first adopters actually have.**
2. **The warm-block latency edge** — real (~76ms warm AMQP RTT in the lab vs minutes of fresh queue wait), but a replicable pilot-job pattern, **not** an exclusive capability, and carrying a continuous allocation-burn cost (Section 5).
3. **hpc-bridge's own differentiated layer** — the SSH bootstrapper that provisions a personal endpoint on first use, out-of-band credential management, the reconcile loop, facility-specific config templates, and the safety hooks. The distributed-systems and competitive streams independently identify this as the tighter, more defensible scope. **This is execution quality, not an inimitable asset** — and it sits in a crowded field (globus-mcp, iowarp, hpcgpt-cli, multiple Slurm MCP servers).

> **Moat conclusion:** Replace "nobody else can give an agent a credentialed low-latency HPC foothold / Globus Compute is the moat" with the defensible claim: *"hpc-bridge differentiates on lowest-friction one-command provisioning, an out-of-band credential boundary, and self-heal, packaged on top of an open substrate — and on being the first to get a named facility to bless the pattern."* The durable strategic asset is the **facility reference**, not the plumbing.

### 3.3 The disintermediation risk

Two structural facts cap moat durability:
- **personal → MEP is a one-line endpoint-id change** (the vision's own framing). A successful MEP consolidation can be operated by the facility *without* hpc-bridge — the durable asset (MEP + Globus Auth) belongs to Globus/the facility, not to hpc-bridge's packaging layer.
- **DOE Genesis Mission** (Nov 2025, $320M, 24 AI partners including Anthropic, OpenAI, Google, Microsoft, AWS) and ALCF/Argonne first-party inference services point toward facilities building **their own sanctioned agent on-ramps**. If facilities stand up MEPs or first-party gateways, the personal-endpoint foothold loses differentiation — the end-state hands the value to the facility. *(Low-confidence on timing; the direction is clear, the speed is not.)*

---

## 4. The MEP flywheel (C5) — **REFUTED**: accumulation manufactures crackdown before demand

C5 is the GTM spine: *self-service personal endpoints accumulate → create the four pains an MEP resolves → facilities come to* want *the MEP rather than block the wave.* All three verifiers returned **refuted**. The chain has one strong link and several broken ones.

**What holds (Link 2 — the pain is real and named by Globus's own work):** The Globus Labs MEP paper (Ananthakrishnan et al., PEARC'24) states verbatim that "from an administrator perspective, there is no control of the user endpoints deployed on their machines" — the governance pain personal endpoints create is real, Globus's telemetry (registrations by facility/domain) is real, so the "walk in with data" step is mechanically executable.

**What breaks:**

| Broken link | Evidence |
|---|---|
| **Directionality is inverted** (b→c). The "four pains" register in HPC security culture as an *unsanctioned-process incident*, not as procurement demand. | OLCF kills disruptive login-node processes "without warning"; TACC "immediately suspends" and explicitly warns "running AI tools on login nodes may result in account suspension"; cgroup/Arbiter2 enforcement throttles or terminates. A CISO seeing "login-node load + security governance gaps" reaches for a **moratorium**, not a purchase order. |
| **The "failed top-down motion" premise is empirically false** (a, e). | Globus Compute MEPs shipped May 7 2024; **ALCF Polaris is the canonical MEP reference deployment**, arrived top-down with *no* personal-endpoint wave pulling it. The identity-mapping model is "validated by security reviews and in production in 30,000+ Globus Connect Server deployments." The flywheel claims to invert a motion that already succeeded. |
| **The MEP's value prop targets a different population** (c→d). | The MEP exists to let users "barely even need to open their own terminal, much less an SSH terminal." hpc-bridge users *already have SSH* (the bootstrapper requires it). A facility that wants the MEP wants to **ban** the personal-endpoint SSH flow — so even when (c) succeeds it breaks (d): the rational move is mandate-migrate-and-shut-down, not bless-the-wave. |
| **The cheapest facility response is a ban** (d). | The four pains accrue to support/sysadmin/CISO/allocation-office; the actor who can approve a root MEP daemon (CISO) finds an email/cgroup-rule/process-kill far cheaper than commissioning a security review. A single Sysdig-class LLM-agent incident under a user UID flips "tolerated in practice" to "banned" overnight. |
| **"one-line migration" is overstated.** | UEP IDs are dynamically generated per-user from the MEP-UUID + Globus identity + config tuple; the personal endpoint's UUID does **not** carry over — every client must re-point to the admin-issued MEP id. Operationally simple, not a trivial in-place swap. |
| **Self-refutation.** | The vision (line 81) concedes accumulation can "trigger a crackdown instead of a blessing" and proposes "engage facilities slightly ahead" — which *is* the top-down motion the flywheel claims to have escaped. |

### 4.1 The early-adopter betrayal structure

The non-obvious killer the GTM strategist surfaced: the wedge user (a scientist wanting a fast loop) and the buyer (facility ops/security) have **directly opposed interests** in the telemetry. The "Accumulate" step uses Globus's view of individuals' own-identity activity to lobby *their* facility. The first time a facility emails a user "we see you running an unsanctioned endpoint," adoption inverts — the tool gets a *snitch* reputation, and the SSH-MCP alternatives (which report nothing) win on **trust**, not just setup cost. HPC early adopters talk to each other (the same crowd sharing sshproxy 1Password tricks). Making the telemetry strictly opt-in and user-visible is the honest fix — but it materially weakens the involuntary-accumulation engine the flywheel was counting on.

> **Flywheel conclusion:** Strike "manufacture demand for MEPs via accumulated telemetry" as the GTM spine. Replace with **"land one facility as a named design partner who sanctions the personal endpoint as a pilot and co-builds the MEP."** Make the facility a co-author from phase 1, not a target ambushed with its own usage data in phase 5. **Gate marketplace launch on one on-record facility reference** — in HPC, the security-review-passed reference, not install count, is the unit of adoption.

---

## 5. The cost mechanic that cannibalizes the moat (C10 + allocation reality)

This is the single most important commercial finding, and it is where "viability" is most at risk. The warm-block latency edge (Section 3.2) is the *only* thing hpc-bridge adds over the free SSH+Slurm substitute. The cost streams establish that this feature is **economically disqualifying for exactly the users the land phase targets.**

**The mechanics (all [high] confidence):**
- Node-hours are charged for **all reserved walltime regardless of utilization**. NERSC: walltime × nodes × QOS factor, "regardless of use," no free idle grace, 2-hour minimum charge on preempt QOS.
- The "interactive/warm" profile requires `min_blocks ≥ 1`, which **structurally disables idle scale-in** — Parsl never scales below `min_blocks`. Flipping the knob to warm turns *off* the only cost protection.
- `max_idletime` default is **120s** — shorter than an agent's think-time between turns while a human reads the transcript. So mid-session scale-to-zero is the *default path*, not an edge case; the next turn silently re-queues a fresh `sbatch` (double-allocation risk if the agent mis-reads the stall as a hang and retries).

**The math, by allocation tier (1 warm GPU node, 8h/day ≈ 2,880 node-hours/yr):**

| Allocation | Size | Warm-block burn (coordinator alone) | Verdict |
|---|---|---|---|
| NERSC Exploratory (first award) | 250 GPU node-hrs | **gone in ~31 days** | Disqualifying |
| OLCF Director's Discretionary | ~20K node-hrs | ~14%/yr | Noticeable, painful |
| INCITE/ALCC | 500K–2M node-hrs | <1%/yr | Negligible |

**The trap (the kill-the-project reviewer's framing):** the warm-block feature that differentiates hpc-bridge from the free substitute is ruinous for the small-allocation beachhead user; the scale-to-zero config that is *affordable* for them is the cold-start path that makes hpc-bridge **no faster than the free SSH substitute** (minutes of queue wait, plus Globus-account + endpoint-install + AMQP-hop complexity). *The product only beats the substitute in the configuration that bankrupts its target user.*

**Compounding the latency claim (C10 STANDS-WITH-CAVEATS):**
- The seconds-scale latency is real **only** for warm calls. No published end-to-end benchmark exists for a warm `ShellFunction` RTT on a real Perlmutter/Polaris node — the 76ms figure is an AWS-co-located lab micro-benchmark.
- The closest real demonstration (Globus Labs' own science-mcps work, arXiv 2508.18489) uses a **submit-and-poll, one-minute-interval batch pattern** — explicitly *not* a seconds-scale interactive loop.
- Globus's own SDK framing is asynchronous/disconnection-tolerant batch (30-min result TTL, 20-req/10s rate limit, default `init_blocks=0`).

> **Cost/latency conclusion:** Cost governance must be a **runtime control plane in the MCP server**, not a documented YAML tradeoff. Make scale-to-zero the *only* no-questions-asked default; gate the warm/interactive profile behind a live `allocation_remaining` check (refuse warm under a configurable floor), a continuously-surfaced `session_spend` field on **every** tool result, a server-side wall-clock warm-block TTL independent of Parsl idle logic, and a per-block size cap. And before any marketing copy: **publish a measured warm-RTT benchmark on a real facility node** — the "interactive surface" claim is currently asserted, not demonstrated, under real deployment conditions.

---

## 6. The "land with no review" premise (C1) — **REFUTED**, and it is the GTM's load-bearing wall

The flywheel's "Land — no admin, no security review" depends on C1: *personal endpoint = identical to SSH, zero new attack surface, therefore accepted without new review.* All three verifiers returned **refuted**. The privilege sub-claim survives; the absolutes that the GTM rests on do not.

**What survives:** A personal endpoint runs as one local UID, no root, no identity mapping — *materially lighter than an MEP*, which needs a privileged `getpwnam→fork→setuid` daemon. This is real differentiation and is the strongest honest version of the claim.

**What is refuted (and why it blocks the GTM):**
1. **"Zero new attack surface" is false at the control boundary.** SSH safety rests on three properties the personal endpoint destroys: the human is the sole command originator; origination is gated by a per-session facility-MFA event; judgment runs at human cadence. The low-latency value prop *requires* not re-SSHing per task — so after one MFA-gated bootstrap the endpoint holds a standing AMQP subscription authorized by a **Globus Auth refresh token cached in `~/.globus_compute/storage.db` on a shared login-node filesystem**, auto-renewing headlessly, bypassing the facility's PAM/MFA stack, invisible to the facility SOC. That is a parallel, facility-invisible, MFA-free command channel — strictly worse than the SSH baseline a stolen key still hits the MFA wall.
2. **The autonomous LLM is the genuinely new risk.** Machine-speed action (the May 2026 Sysdig incident: LLM-agent-driven full-database exfiltration in under 2 minutes), triggerability by untrusted data (indirect prompt injection on shared Lustre/GPFS, which the best-defended agents still fail at ~45–52% in published benchmarks), and attribution collapse (all logs show the user UID). A CISO reviewing "an autonomous LLM with `run_shell` on a leadership-class machine" is reviewing genuinely new behavior — and there is **no published DOE/NSF/NIST guidance for LLM agents on HPC** (as of June 2026), so review defaults to conservative *deny*.
3. **Persistent login-node processes are a distinct policy category** (C8 REFUTED) — see Section 7.

> **C1 conclusion:** Delete "zero new attack surface / identical to SSH / no new review" — it is the single sentence that gets the project blocked the instant a CISO reads it. Replace with the honest, *stronger* framing: *"identical PRIVILEGE boundary to SSH (your UID, no root, no identity mapping), PLUS a new command channel we make facility-observable (SIEM-shippable per-task audit), facility-revocable (prefer NERSC SFAPI Red-client / facility-issued short-TTL credential over a user-held refresh token), and periodically re-MFA'd (TTL forces re-bootstrap)."* That is a claim a CISO can bless — it converts the weakest sentence into the security pitch.

---

## 7. Facility-by-facility GTM friction matrix

The friction is **highest at the most prestigious machines**, which is the inverse of where the GTM narrative wants to point. Every relevant claim is facility-conditional; the universal forms (C1, C2, C3, C8) were all refuted precisely on the "across the major facilities" quantifier.

| Facility | Non-interactive SSH (C3) | Persistent login-node process (C8) | Self-heal / reconcile (C2) | Cost posture | Net GTM verdict |
|---|---|---|---|---|---|
| **NERSC / Perlmutter** | **Works.** sshproxy 24h cert (extendable) + ControlMaster permitted; SFAPI Red client is MFA-free. | **Sanctioned** via Workflow QOS (90-day walltime, 25% login node) — *requires a Request Form*; Spin/scrontab are cleaner homes. | **Works** within cert window (cattle-not-pets is true here). | Charges all reserved walltime; 2h preempt minimum. | **Facility-one.** Only site where the architecture is over-determined. Political cover: NERSC's own Agentic-AI bootcamp. |
| **ALCF / Polaris+Aurora** | **Fails.** Per-login MobilePASS+ OTP, no cert reuse, no documented ControlMaster, bastion hop. | **Grey zone.** "Be courteous," no Workflow-QOS/Spin equivalent, ALCF "researching solutions." | **Human-in-the-loop only** — each restart needs a fresh OTP. | No documented cgroup caps. | Human-present restart only; informal sign-off needed before deploy. Note: ALCF *already runs an MEP* top-down. |
| **OLCF / Frontier** | **Fails hardest.** RSA SecurID single-use fob; **ControlMaster explicitly disabled system-wide** (both named C3 mechanisms blocked). | **Hostile.** Daemons "killed without warning"; sanctioned path is Slate (OpenShift, PI application) or looped chained jobs — *not* a login-node process. | **No autonomous self-heal.** Invert to keep-coordinator-alive (long walltime / chained job). | INCITE-class; warm-block negligible if awarded. | Re-architecture required; not a land-phase target. |
| **TACC / Frontera+Stampede3** | **Fails.** Per-session TOTP; ssh-keygen prohibited on login nodes (interferes with batch SSH). | **Most hostile.** "All AI-assisted workloads must run on compute nodes only"; "running AI tools on login nodes may result in account suspension" — names the exact pattern. | Not reliably automatable. | Smaller ACCESS allocations → burn proportionally more painful. | Avoid as launch target until formal dialogue. |

**Cross-cutting:** the NERSC reference does **not** transfer — the auth, persistence, and cost story that is hospitable at NERSC is exactly what ALCF/OLCF/TACC refuse. "We got blessed at NERSC" may produce a credential the hardest facilities specifically decline to honor.

---

## 8. The wedge user and the buyer

| | Wedge **user** | **Buyer** / blesser |
|---|---|---|
| Who | Computational scientist on a small-to-mid allocation (NERSC Exploratory / ERCAP / Director's Discretionary) wanting a tight edit→run→debug loop. | Facility HPC-ops + security (CISO), who controls whether the pattern is sanctioned, and the PI/allocation manager accountable for federally-audited node-hours. |
| Wants | Frictionless `/plugin install` → working; REPL feel; relative paths and activated envs that survive (C6 caveat — this needs server-enforced session-shell semantics, not skill discipline). | Facility-observable, facility-revocable, audited agent behavior; no unsanctioned persistent processes; no allocation-attribution surprises. |
| Friction | Cold-start UX; cost foot-gun the agent itself can pull; per-facility MFA recurrence outside NERSC. | Autonomous-agent attack surface; persistent login-node process; attribution collapse (every injected node-hour is indistinguishable from the PI's deliberate science in Iris/sacct). |
| Conflict | **The user's adoption is the evidence used to trigger the governance the user was routing around.** | The buyer's rational first move on accumulation is a ban, not a purchase. |

**The PI's accountability problem (often missed):** because the endpoint runs as the user, every node-hour an injected or over-eager agent burns is indistinguishable from deliberate science — same UID, same account, same project. A 250-hour award evaporating as *idle-node* charges reads as "low GPU utilization / poor allocation stewardship" in a NERSC annual review — the exact metric that gets renewals cut. Cost governance solves the user's wallet but not the PI's accountability unless the MCP server emits a facility-ingestible, tagged usage record (Slurm `--comment` / Globus telemetry field) separating agent-driven from human-driven node-hours.

---

## 9. Conditions under which the GTM works — or stalls

### 9.1 It works if

1. **NERSC-first, sanctioned-pilot, named design partner.** Enter through the open door (Workflow QOS / Spin), get listed as a reference pattern *in* NERSC's Agentic-AI program rather than discovered as shadow IT. One on-record facility reference flips every other facility's review from "novel scary thing" to "the thing NERSC vetted."
2. **Scale-to-zero is the only default; warm is a budget-gated, per-session, logged opt-in** with a server-side wall-clock TTL and a `session_spend` field on every tool result.
3. **Credentials are unreachable by the agent's execution context by construction** (C9 REFUTED as "effectively closed" — regex scrubbing is readily evaded under encoding/splitting). This means a dedicated broker UID/process holding the cert/socket, a credential-free hot path (the interactive loop rides a narrowly-scoped Globus Auth task-submit token, *not* SSH material), `ForwardAgent=no` (CVE-2023-38408), and structured allow-listed result fields — *not* a `PreToolUse` regex hook as the boundary.
4. **The autonomous-agent risk is neutralized with hard MCP-dispatch controls outside the LLM**: a Globus-Transfer destination allowlist + human confirm above a size threshold, a per-session node-hour action budget, a tamper-evident audit log of every tool call, and (ideally) a Rule-of-Two / dual-LLM separation so `run_shell` is unavailable in any context that has ingested untrusted content without out-of-band confirmation.
5. **Telemetry is strictly opt-in and user-visible from install**, reframed as "share my usage with my facility to help them support this."
6. **A measured warm-RTT benchmark on a real facility node** backs the "interactive surface" claim before it ships in marketing.

### 9.2 It stalls if

1. **The flywheel runs as written** (silent land → accumulate → ambush). The first injected-agent incident under a user UID — Globus-Transfer exfil of a project's Lustre directory at 40 Gbps — produces a facility-wide ban *and* taints Globus Auth/Compute institutionally (owner is Globus Labs; reputational blast radius is not contained to one plugin). This poisons the MEP upsell that is the actual endgame.
2. **The warm-block feature is left as a symmetric user choice.** The small-allocation beachhead user bankrupts themselves or the agent does it for them; word-of-mouth in the land phase dies on "flaky and expensive."
3. **The product leans on the moat claim.** The moment a buyer reads "nobody else can," they recall the free npx Slurm MCP server and Globus's own globus-mcp, and the differentiation evaporates to "a bootstrapper and some hooks."
4. **Non-NERSC facilities are pursued on the NERSC playbook.** ControlMaster bans (OLCF), AI-on-login-node suspension (TACC), and per-login OTP (ALCF) break the self-heal and persistence stories the same copy promises.

---

## 10. Recommendations (ordered by leverage)

1. **Rewrite the three refuted claims before anything else ships.** C1 ("identical to SSH / zero new attack surface / no review"), C4 ("nobody else can / Globus Compute is the moat"), and C5 ("accumulate → facilities want the MEP") are the sentences that get the project blocked or dismissed. The honest replacements are stronger and are drafted inline above.
2. **Re-spine the GTM around one named facility design partner (NERSC), and gate marketplace launch on that on-record reference.** Convert "accumulate then reveal" into "sanctioned pilot to production, co-authored." Ship the MEP config template + one-line swap *with* that facility's admins, so personal→MEP is a path walked together, not a pitch sprung.
3. **Make cost governance and the credential broker runtime control planes, not documentation.** Scale-to-zero only by default; warm is budget-gated and TTL-capped; credentials live behind a kernel-enforced inter-UID boundary with a credential-free hot path.
4. **Locate hpc-bridge's defensibility in execution, not in the substrate**: lowest-friction provisioning, the out-of-band credential boundary, the reconcile loop, facility-policy navigation, and the security hooks that the naive SSH-MCP servers lack. Treat the **facility reference** as the durable strategic asset.
5. **Publish the threat model + hard mitigations as a community document** (S-HPC, NIST CAISI comment process) to shape the agentic-HPC guidance vacuum rather than be blocked by it — and to differentiate from "vibe-coded" agent tools.
6. **Benchmark before you market.** A measured warm `ShellFunction` RTT on Perlmutter/Polaris, plus a "HPC as a REPL" transcript that includes one bare-relative-path turn and one turn separated by >120s of think-time (forcing scale-to-zero), both succeeding with honest latency messaging. If that passes, the interactive thesis is demonstrated, not asserted.

---

## Appendix: claim verdicts referenced

| Claim | Subject | Verdict | Bearing on this document |
|---|---|---|---|
| C1 | Trust boundary = SSH, zero new surface, no review | **REFUTED** | §6 — load-bearing wall of "Land — no review" |
| C2 | Disposable endpoints, transparent self-heal | **REFUTED** | §7 — reconcile works only at NERSC |
| C3 | Non-interactive SSH under MFA across facilities | **REFUTED** | §7 — works at NERSC, fails/blocked elsewhere |
| C4 | Globus Compute = durable moat | **REFUTED** | §3 — moat is Globus Auth, owned by Globus |
| C5 | Personal endpoints manufacture MEP demand | **REFUTED** | §4 — accumulation manufactures crackdown |
| C6 | Filesystem-as-state sufficient for v1 | STANDS-WITH-CAVEATS | §8 — needs server-enforced session semantics |
| C7 | Templates + probing solve per-facility config | STANDS-WITH-CAVEATS | (engineering; bears on "one command → working") |
| C8 | Persistent login-node process tolerable | **REFUTED** | §7 — distinct policy category at every facility |
| C9 | Credentials kept out of LLM via process + scrub | **REFUTED** | §9.1 — needs broker + credential-free hot path |
| C10 | Warm-pilot delivers seconds-scale interactivity | STANDS-WITH-CAVEATS | §5 — true only warm, unmeasured on real nodes |
