# hpc-bridge

**Extend AI coding agents into HPC.** A Claude Code plugin that gives an agent
interactive, low-latency access to real supercomputer compute — by standing up a
*personal* Globus Compute endpoint behind the scenes, no admin and no multi-user
endpoint (MEP) deployment required.

Globus Compute is the engine under the hood; `hpc-bridge` is the agent-facing
packaging and the frictionless on-ramp.

## Why

Agents are interactive; HPC batch queues are not. Globus Compute's pilot-job
model holds a warm block of allocated nodes and dispatches work onto them in
seconds — turning a batch supercomputer into an interactive surface for an agent.
By provisioning a *personal* endpoint (which runs as the user, with no privileged
identity-mapping daemon), we sidestep the facility security review that has
stalled MEP adoption, and seed a bottom-up wave that ultimately *pulls* MEPs out
of facilities rather than pushing them top-down.

See **[docs/vision.md](docs/vision.md)** for the full vision, architecture,
security model, go-to-market, and phased plan.

## Status

Early planning. First milestone: the "HPC as a REPL" demo — an agent doing a
tight edit → run → debug loop on a real compute node via a warm pilot.

## Shape (planned)

A single Claude Code plugin bundling:

- **MCP server** — HPC actions as agent tools (`run_shell`, `read_file`, `write_file`, `ensure_endpoint_up`)
- **One general skill** — how to drive HPC well (working-dir discipline, gotchas, safety)
- **Slash command** — `/hpc-connect` one-line endpoint bootstrapper
- **Hooks** — keep credentials (passwords, MFA tokens) out of the LLM entirely
