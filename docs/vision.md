# Agentic HPC via Globus Compute

**A Claude Code plugin that extends AI coding agents into HPC — by standing up personal Compute endpoints behind the scenes.**

Status: vision / planning draft · 2026-05-30 · owner: Ryan Chard

---

## One-line summary

Ship a single installable Claude Code plugin that gives an AI agent interactive, low-latency access to real HPC compute by transparently provisioning a *personal* Globus Compute endpoint for the user — sidestepping the multi-user endpoint (MEP) deployments that have been our biggest adoption roadblock, and seeding a bottom-up adoption wave that ultimately *pulls* MEPs out of facilities instead of pushing them.

## The opportunity

Claude Code and similar agents are interactive; HPC batch queues are not. An agent that has to fire a job into a 45-minute queue and wait cannot do a tight edit → run → debug loop. **Globus Compute's pilot-job model is the bridge:** a Compute endpoint can hold a warm block of already-allocated nodes and dispatch work onto them with seconds-scale latency. That turns a batch supercomputer into an interactive surface for an agent. Crucially, nobody else can give an AI agent a *credentialed, low-latency foothold on a leadership-class HPC system* — cloud agent sandboxes top out at commodity VMs. Compute's federation, identity, and provisioning are the moat.

Globus Compute is already ~80% of an agent runtime. Its core primitive — "register an arbitrary Python callable, get a UUID, invoke it on a remote resource, stream results back" — is exactly what an agent needs to do real work on real compute. What's missing is the *packaging, the interactive ergonomics, and a frictionless way to get an endpoint in place.*

## The idea

Distribute the whole capability as **one Claude Code plugin**. Installing it gives the agent a set of HPC tools and a one-command path to a working endpoint:

- **MCP server** exposing HPC actions as agent tools: run a shell command, read/edit a file, submit work — each as a blocking call that returns results over Compute's low-latency path.
- **A bootstrapper** that, on first use, SSHes to the user's login node and stands up a *personal* Compute endpoint, configured to elastically acquire compute nodes through the local scheduler (Slurm/PBS) and hold a warm pilot block for interactive turnaround.
- **One general skill** teaching the agent how to drive HPC well (anchor every command to a working directory so the shared filesystem carries state across tasks; the cold-start, result-size, and serialization gotchas; safety norms).
- **Hooks** that enforce a hard security boundary around credentials.

The user's path to agentic HPC becomes: `/plugin install globus-compute` → answer two questions (`account`, `queue`) → working.

## Why we can avoid MEPs

MEPs (multi-user endpoints) have been our hardest deployment problem: they require facility admin buy-in and security review, because an MEP runs a **privileged daemon that authenticates Globus identities and maps them to local POSIX users, spawning processes as those users**. That privileged identity-mapping is exactly what facility security teams agonize over — and the reason MEPs stall.

A **personal endpoint eliminates all of it.** It runs as *you*, work runs as *you*, with *your* permissions. No root, no identity mapping, no privilege escalation, zero new attack surface. The trust boundary collapses to *"the agent runs as you, doing only what you could already do by typing it"* — identical to SSH, a boundary every facility already accepts. So "an LLM runs code on HPC" stops being a scary novelty and becomes a thing facilities already permit.

The endpoint itself is a **lightweight coordinator**, not a workload — it subscribes to the task queue and submits scheduler jobs to bring up workers on compute nodes. That's *why* it's tolerable to run persistently on a login node: the heavy work happens in scheduled jobs on compute nodes, not on the login node. And because standing one up is now a single command, **persistence stops mattering** — we treat endpoints as disposable cattle, not pets. If one goes offline, we detect it (the web service tracks endpoint heartbeats) and restart it over SSH. Combined with scale-to-zero on idle blocks, this also means we stop burning allocation the moment the agent stops working.

**Net:** make a properly-configured personal endpoint trivial to launch, and the MEP becomes a niche fallback rather than a prerequisite.

## How it works (implementation sketch)

```
Claude Code (laptop)
  │  calls MCP tools: run_shell / read_file / write_file / ensure_endpoint_up
  ▼
globus-mcp server  ──(reconcile: is endpoint up? if not, SSH-restart)──┐
  │  submit + block for result (low-latency Compute path)              │
  ▼                                                                    │
Globus web-service (cloud) ── AMQP ──► Personal Compute endpoint  ◄────┘ SSH (repair only)
                                          (login node, persistent-ish coordinator)
                                          │ elastically submits scheduler jobs
                                          ▼
                                       Warm pilot block on compute node(s)
                                          │ runs the agent's command
                                          ▼  state persists via shared filesystem
                                       result ── AMQP ──► back to the agent
```

Key implementation choices:

- **Disposable endpoints + reconcile loop.** Desired state is "up and warm during my session." The common path is a cheap status check via the web service (no SSH). SSH is fired only to *repair* — restart reuses the same stable endpoint UUID and on-disk config.
- **Filesystem-as-state.** We do *not* need worker-pinning or a warm in-memory kernel for v1. The HPC shared filesystem carries edits, build artifacts, and outputs across tasks as long as commands are anchored to a working directory. The skill teaches the agent this discipline.
- **Warm-pilot vs. cost as one labeled knob.** An `interactive` profile holds a warm block (low latency, spends allocation while active); a `batch` profile scales to zero (cheap, cold-start). The user picks; the tradeoff is explicit, not buried in YAML.
- **Config generation is the real content problem.** Correct per-facility scheduler config (scheduler type, account, queue, `worker_init` modules, launcher, block sizing) is what newcomers face-plant on. We ship facility templates and lean on endpoint capability probing — not hand-written per-facility skills.

## Security model

Two boundaries, both already supported by Claude Code's architecture:

1. **The agent runs as the user.** No privileged daemon (vs. MEP). The agent can only do what the user can. Claude Code's permission/approval model layers on top (confirm destructive ops); the audit is simply the user's own actions.
2. **Credentials never reach the LLM.** The MCP server is a *separate process*; the secret never transits the model's channel. The agent calls an abstract `ensure_endpoint_up(endpoint_id)`; the MCP server acquires the SSH/MFA credential out-of-band (ssh-agent + short-lived cert where available, e.g. NERSC `sshproxy`; otherwise a `ControlMaster` socket authenticated once per session via an `SSH_ASKPASS` prompt) and SSHes. The model receives only `{status: "up"}`. A `PreToolUse` hook rejects any tool call that smuggles credential-looking strings; the server scrubs its own outputs so no secret leaks back through tool results. Asking the user for an MFA token at session start is acceptable — we explain it's needed to (re)start Compute.

## Go-to-market: the flywheel

Self-service personal endpoints don't make MEPs irrelevant forever — they *manufacture* the demand that finally justifies them, inverting the failed top-down motion:

1. **Land.** Trivial personal endpoints deliver value today — no admin, no security review, no privileged daemon.
2. **Accumulate.** Usage grows on a facility's login nodes. We already have the telemetry: the Globus web service sees endpoint registrations by facility and user domain.
3. **Expand.** We walk into the facility with data — *"N of your users stood up Compute endpoints last quarter"* — turning a cold infrastructure ask into a demand-backed conversation. The crowd of personal endpoints creates exactly the four pains an MEP resolves (login-node load, security governance, support burden, visibility/accounting), so the MEP becomes the *consolidation the admin now wants*. Personal → MEP migration is a one-line endpoint-id change.

To keep this from triggering a crackdown instead of a blessing: ship personal endpoints as obvious good citizens (lightweight, self-identifying, documented) and engage facilities slightly ahead — "here's the sanctioned way your users are already doing this, and here's the MEP upgrade path."

## Packaging

Everything above ships as **one Claude Code plugin** (MCP server + skill + slash commands + hooks), distributed through the plugin marketplace. The marketplace *is* the distribution channel for the bottom-up flywheel: `/plugin install globus-compute` is the on-ramp.

## First demo: "HPC as a REPL"

The hook that makes the thesis visceral. Claude Code is given a buggy program living on the HPC filesystem; it reads it, runs it on a real compute node via the warm pilot, reads the error, edits, and reruns — several iterations at a few seconds each — arriving at a working program. The transcript shows an agent using a supercomputer interactively.

Built on a small delta to the existing `globus-mcp` server: a blocking `run_shell(endpoint_id, command, cwd)` (one generic runner registered once, results via the Executor's AMQP streaming rather than polling) plus `read_file` / `write_file` so the agent can see and fix remote source. Develop against a local dev endpoint; showcase against a real facility.

## Open risks / gaps to pressure-test

- **Non-interactive SSH under facility MFA** — the dependency that replaces persistence. Workable via ssh-agent/cert or a once-per-session `ControlMaster`, but MFA policy varies by facility and is the sharpest edge.
- **Cost/allocation guardrails** — even disposable endpoints spend node-hours while warm. Need budget caps, idle scale-down defaults, and an `allocation_remaining` surface so an agent can't quietly burn an allocation.
- **Facility policy** — a few sites forbid persistent personal processes on login nodes; there, an MEP remains the sanctioned path. Worth a per-facility reality check.
- **Config correctness** — the per-facility scheduler config is the substantive content work; templates + probing must produce *runnable* configs, including a reproducible environment (`worker_init` / container).
- **Cold-start UX** — the interactive promise holds only after the pilot warms; the first call after a (re)start eats a cold start. Needs honest "allocating nodes…" messaging.

## Phased plan

1. **Demo (now):** blocking `run_shell` + file tools on `globus-mcp`; "HPC as a REPL" against a local dev endpoint, then a real facility.
2. **Plugin v1:** package MCP server + general skill + `/hpc-connect` bootstrapper + credential hooks; the one-line personal-endpoint bootstrapper with `interactive`/`batch` profiles for 1–2 flagship facilities (e.g. Polaris/PBS, Perlmutter/Slurm).
3. **Self-heal + cost guardrails:** the reconcile loop (detect offline → SSH-restart), scale-to-zero defaults, allocation reporting.
4. **Catalog + telemetry:** productize endpoint capability descriptors and the facility-usage view that drives the MEP conversation.
5. **Marketplace launch + facility engagement:** publish the plugin; begin the land-and-expand motion with telemetry in hand.
