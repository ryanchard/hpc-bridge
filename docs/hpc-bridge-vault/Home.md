# hpc-bridge — Vault Home

**What this is.** A maintainer's map of hpc-bridge: a Claude Code plugin (FastMCP server) that stands up a *personal* Globus Compute endpoint on an HPC login node over SSH, then dispatches shell commands to a warm compute block over Globus Compute's credential-free AMQP path — making a batch supercomputer feel like a REPL.

This vault has two halves:
- **Implemented (ground truth)** — how the code works *today*. It tracks the codebase; update it when the code changes. ← you are here.
- **Planned (transient)** — designed work and refactors; churns as features land. *(phase 2 — will absorb `docs/design/`.)*

> [!info] Reading order
> New here? Read **[[Happy path]]** for the end-to-end flow, then the concepts ([[Two-channel architecture]] → [[Standing up the endpoint]] → [[MEP & templated endpoints]]). Contributing? Read the [[Vault style guide]] first.

## Concepts — how it works
- [[Two-channel architecture]] — SSH control plane vs AMQP hot path
- [[Standing up the endpoint]] — bootstrap on a login node · SSH-once · reuse
- [[MEP & templated endpoints]] — manager + per-task UEP template → Slurm block → compute node
- [[Credential seeding]] — why we ship a trimmed `storage.db`
- [[Warmth, the canary & cold-start]] — what "up" really means
- [[Resource shapes & the spend floor]] — `login` vs `slurm`; `confirm_spend`
- [[Session continuity]] — the cwd/env shim
- [[Cost control]] — idle-release · spend clock · budget gate
- [[Discovery today]] — login-shape probe · the catalog
- [[Facility catalog]] — index/seed → `MachineProfile`; `list_facilities` / `connect_facility`

## Modules — `src/hpc_bridge/`
**Server & runtime:** [[server]] · [[runner]] · [[dispatch]] · [[lifecycle]] · [[session_shell]] · [[cost]] · [[models]] · [[profile]] · [[shapes]] · [[discovery]]
**Facility seam:** [[facility-base]] · [[facility-local]] · [[facility-remote]]
**Bootstrap & state:** [[endpoint]] · [[credentials]] · [[state]]

## Reference
- [[The MCP tools]] — the agent-facing surface
- [[Plugin packaging]] — `.mcp.json` · `plugin.json` · the `driving-hpc` skill · `hpc-connect`
- [[Configuration]] — environment variables

## Planned — design notes (core built; deferred extras remain)
- [[Discovery channel model]] — the target model: channels, the provide-vs-discover matrix, the principles. Remaining: per-channel ablation flags + the resolution trace ([#7](https://github.com/ryanchard/hpc-bridge/issues/7))
- [[Globus index discovery channel]] — the catalog resolver + agentic selection + the raw-SSH discover-then-confirm sweep (all built). Remaining: seed-emission / write-back for curation ([#7](https://github.com/ryanchard/hpc-bridge/issues/7))

*(Persistent SSH / ControlMaster shipped — see [[facility-remote]].)*

## Meta
- [[Vault style guide]] — how to write & maintain these notes (for contributors and agents)
- [[Demos]] — archived, version-stamped demos from older conceptualizations

---
> [!note] Status
> **Section 1 (implemented) is complete** — all Concept, Module, and Reference notes track the current codebase, with [[Happy path]] as the end-to-end spine. **Section 2 (Planned) has begun** — [[Globus index discovery channel]] is the first thread; the rest of `docs/design/` is absorbed as work proceeds.
