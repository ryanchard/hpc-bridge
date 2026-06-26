# Discovery today

> [!abstract] In one line
> What the plugin discovers *now*: after the one-time bootstrap, the agent probes the facility through the **login shape over AMQP** (no SSH). The per-facility shape comes from a **[[Facility catalog|catalog]]** (the Globus Search index), and machine + allocation are **agent-selected at runtime** (`list_facilities` → `connect_facility` → pick an allocation). There is no hardcoded machine profile and no bundled fallback — the index is the only runtime source.

## What's implemented

- **Endpoint-first, SSH-once discovery.** The `driving-hpc` skill ([[Plugin packaging]]) sequences: establish the endpoint (`shape="login"`) → discover via `run_shell(shape="login")` — `sinfo`/`mybalance`/`squeue` over AMQP, **not** `login_shell` (SSH) → gate (partition + budget) → provision `slurm` with `confirm_spend` → wait by polling `squeue` via the login shape. `login_shell` (raw SSH, [[server]] `:494`) is the cold-start escape hatch only.
- **Endpoint reuse.** `find_online_endpoint` ([[facility-remote]] `:569`) is a web query (no SSH) that lets a reconnect reuse a running endpoint — the [[Two-channel architecture|SSH-once]] keystone.
- **The catalog drives the shape.** A Globus Search index holds the facility shape; the **[[Facility catalog|catalog resolver]]** turns an entry into a `MachineProfile` — at startup (`HPC_BRIDGE_MACHINE`) or at runtime (`connect_facility`). No hardcoded profile, no bundled fallback: an unresolved machine is a hard failure (agent-discovery is next). See [[Globus index discovery channel]].
- **Agentic machine + allocation selection.** `list_facilities` browses the catalog; `connect_facility(facility)` brings up the free login shape, runs the allocation command (e.g. `mybalance`) over Compute, parses it in code, and returns the allocations to pick from — the choice flows into `ensure_endpoint_up(account=…)`. → [[The MCP tools]]

## What's deliberately *not* here yet

The fuller discovery-channel cascade — the login-node probe and the **human (Socratic)** as explicit fallback channels, with **ablation flags** and a **resolution trace** — is **planned**, not built. See [[Discovery channel model]] (the frame) and [[Globus index discovery channel]] (the thread).

> [!note] Scope
> This note describes current behaviour only. The "facility shape comes from a catalog, not a hardcoded class" generalization is now **built**; the *fuller cascade* (probe/human fallback, ablation, trace) is what remains — see [[Globus index discovery channel]].

## See also
[[Facility catalog]] · [[The MCP tools]] · [[Two-channel architecture]] · [[facility-remote]] · [[server]] · [[Standing up the endpoint]]
