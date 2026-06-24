# hpc-bridge plugin structure & implementation approach

Status: design-in-progress · 2026-05-30 · companion to [vision.md](vision.md)

> **Handoff note for a future session.** This captures the plugin-structure
> thinking and the implementation-planning state. Decisions marked **DECIDED**
> are settled; the **OPEN** item at the bottom is where the conversation paused
> (the user wanted to reframe the iteration-1 scope question before we write the
> actual implementation plan). Next step is the `writing-plans` skill once
> iteration-1 scope is fixed.

---

## What this project is (one line)

A Claude Code plugin that extends AI coding agents into HPC by provisioning a
*personal* Globus Compute endpoint behind the scenes and exposing interactive,
low-latency HPC tools. Full rationale, security model, and go-to-market in
`vision.md`.

## Key decision: relationship to globus-mcp — **DECIDED**

**Own server now, reuse globus-mcp, upstream later.**

- hpc-bridge ships its **own interactive MCP server**, reusing
  `globus_compute_sdk` directly (the `Executor` is the low-latency, blocking
  result path).
- It **bundles the existing `globus-mcp` server as a second MCP server** for
  what that already does well — endpoint discovery + Globus Transfer.
- The opinionated, agent-specific machinery (SSH bootstrap, self-heal, MFA
  handling) lives **only in hpc-bridge**, never in the official `globus-mcp`.
- Mature, non-SSH interactive tools can be **upstreamed into `globus-mcp`
  later** if they earn it.

Rationale: globus-mcp's compute tools are stateless/poll-based (register-per-
command, `submit` then `get_status`). The interactive workflow needs a different
design — blocking calls on the `Executor`'s AMQP stream, one generic runner
registered once — plus SSH logic that doesn't belong in the official server.
Owning it lets us iterate without coupling to globus-mcp's design/release cadence.
(Alternatives considered: build interactive tools into globus-mcp from the start;
or fully self-contained with no globus-mcp dependency. Both rejected — see vision
flywheel/ownership reasoning.)

## The four structural decisions — **DECIDED**

1. **Two MCP servers, one plugin.** Our interactive server + bundled globus-mcp.
2. **Secret-sensitive logic in a standalone endpoint-manager module with a CLI**,
   used by *both* the `/hpc-connect` slash command (user-initiated setup) and the
   `ensure_endpoint_up` MCP tool (mid-session self-heal). One implementation, two
   entry points. Credential handling (ControlMaster / SSH_ASKPASS / ssh-agent)
   isolated here, never in the model's path, never duplicated.
3. **Python server, launched via `uvx`.** Reuses `globus_compute_sdk`; same
   toolchain as globus-mcp so upstreaming stays easy. Repo carries both the plugin
   packaging and a real Python package.
4. **Two state locations.** Laptop-side `~/.hpc-bridge/` records which endpoints
   the user bootstrapped (facility, host, endpoint_id, profile). The endpoint's
   own config lives remotely in `~/.globus_compute/<name>/`. Per-facility
   scheduler-config templates live in the package.

## Proposed directory structure

```
hpc-bridge/
├── .claude-plugin/plugin.json     # manifest
├── .mcp.json                      # launches BOTH MCP servers (ours via uvx + globus-mcp)
├── commands/
│   └── hpc-connect.md             # slash cmd → endpoint-manager bootstrap (SSH/MFA here, outside model)
├── skills/
│   └── hpc/SKILL.md               # the one general "how to drive HPC" skill
├── hooks/hooks.json               # PreToolUse credential guard + PostToolUse output scrub
├── src/hpc_bridge/
│   ├── server.py                  # FastMCP: run_shell, read_file, write_file, ensure_endpoint_up
│   ├── compute.py                 # Executor wrapper: blocking run, generic runner fn, cwd discipline
│   ├── endpoint/                  # the endpoint-manager: bootstrap, status, restart, config-gen
│   │   ├── manager.py             #   used by BOTH the slash command and ensure_endpoint_up
│   │   ├── ssh.py                 #   ControlMaster/askpass/agent — secrets isolated here
│   │   └── templates/             #   per-facility scheduler config (jinja2): polaris, perlmutter, ...
│   └── state.py                   # ~/.hpc-bridge local state
├── scripts/askpass.sh             # out-of-band MFA/password prompt helper
├── tests/
├── pyproject.toml
├── README.md  ·  docs/{vision,plugin-design}.md
```

## Interactive tool design (details to carry into the plan)

- **`run_shell(endpoint_id, command, cwd=None, timeout=None)`** — blocking.
  - One **generic runner function registered once** per (server, endpoint) and
    cached — NOT register-per-command (globus-mcp's current pattern bakes the
    command into the source → a new function UUID per command).
  - Runner executes `cd {cwd} && {command}`, returns
    `{returncode, stdout, stderr, cwd}`.
  - Blocking via `Executor.submit(...).result(timeout)`. **Cache one Executor
    per endpoint** in server context to keep the AMQP connection warm (this is
    the low-latency path; globus-mcp instead polls `v2.get_task`).
- **`read_file(endpoint_id, path)` / `write_file(endpoint_id, path, content)`** —
  generic functions running on the endpoint, so the agent can see and fix remote
  source. Truncate large reads; flag the 10 MiB result cap.
- **State across calls = the shared filesystem.** No worker-pinning or warm
  in-memory kernel needed for v1: anchor every command to a `cwd` and the HPC
  parallel FS carries edits/artifacts/outputs across tasks even on different
  nodes. The skill teaches this discipline. (Warm in-memory kernel deferred.)
- **Result handling:** keep outputs small in the demo; truncate stdout/stderr
  with a marker; out-of-band large data (Transfer/object-store handles) is later.

## Security boundary (recap; full detail in vision.md)

- Agent runs **as the user** (personal endpoint, no privileged identity-mapping
  daemon) → trust boundary == SSH, which facilities already accept.
- **Credentials never reach the LLM:** MCP server is a separate process; the
  model calls abstract `ensure_endpoint_up(endpoint_id)`; the server acquires the
  SSH/MFA secret out-of-band (ssh-agent+cert e.g. NERSC sshproxy where available,
  else once-per-session ControlMaster via SSH_ASKPASS) and returns only
  `{status}`. PreToolUse hook rejects credential-looking inputs; server scrubs its
  own outputs.

## Iteration plan (proposed)

- **Iteration 1 — prove the interactive loop locally.** Build the hpc-bridge MCP
  server (blocking `run_shell` + `read_file`/`write_file`) + minimal plugin
  skeleton (manifest, `.mcp.json`, the one skill). Run against a hand-started
  **local** Compute endpoint (LocalProvider, `init_blocks=1`). Drive the "HPC as
  REPL" loop on a deliberately-buggy program. De-risks core UX/latency. **No
  SSH/bootstrap/hooks yet.**
- **Iteration 2 — bootstrap + self-heal on a real facility.** `/hpc-connect`
  slash command + endpoint-manager SSH bootstrap + `ensure_endpoint_up` reconcile
  loop + credential isolation (the only place SSH/MFA can be truly exercised).
  Facility config templates (Polaris/PBS, Perlmutter/Slurm).
- **Iteration 3 — hooks, cost guardrails, profiles.** Credential-guard hooks,
  `interactive`/`batch` block profiles, scale-to-zero, `allocation_remaining`.
- **Later — catalog + telemetry + marketplace launch** (the flywheel; vision.md).

## OPEN — where we paused

**Iteration-1 scope is not yet fixed.** Three candidate scopes were on the table:
(a) interactive loop + local endpoint only [recommended]; (b) also include the
SSH bootstrapper; (c) full plugin skeleton with bootstrap/self-heal stubbed. The
user wanted to **reframe this scope question** rather than pick from those three —
the reframing discussion had not happened yet when this doc was written.

**Next step:** resolve iteration-1 scope, then invoke the `writing-plans` skill to
produce the step-by-step implementation plan for it.

## Two unresolved strategy threads (not blocking; from vision.md)

- Cost/allocation guardrails (warm pilots spend node-hours even when disposable).
- What the very first "wave-seeding" example needs to be compelling.
