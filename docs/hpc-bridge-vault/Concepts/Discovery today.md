# Discovery today

> [!abstract] In one line
> What the plugin discovers *now*. **Catalogued facility:** after the one-time bootstrap, the agent probes through the **login shape over AMQP** (no SSH); the per-facility shape comes from a **[[Facility catalog|catalog]]** (the Globus Search index), and machine + allocation are **agent-selected at runtime** (`list_facilities` → `connect_facility` → pick an allocation). **Un-indexed facility:** a **raw-SSH login-node probe** (before any endpoint exists) discovers the shape and proposes it for confirmation. No hardcoded machine profile, no bundled fallback.

## What's implemented

- **Catalogued facility — endpoint-first discovery.** The `driving-hpc` skill ([[Plugin packaging]]) sequences: establish the endpoint (`shape="login"`) → discover via `run_shell(shape="login")` — `sinfo`/`mybalance`/`squeue` over AMQP, **not** SSH → gate (partition + budget) → provision `slurm` with `confirm_spend` → poll `squeue` via the login shape. The agent-facing `login_shell` tool (raw SSH, [[server]] `:946`) stays the cold-start escape hatch.
- **Un-indexed facility — raw-SSH login-node probe (built).** `connect_facility(facility, ssh_host=…)` for a machine not in the index runs **one batched login-node command over raw SSH, *before* any endpoint** (`discover_facility_details`, [[discovery]] `:48`, via `_propose_or_ask` [[server]] `:796`) → a proposed [[models|FacilityDetails]] draft (`interface`/`scheduler`/`partition`/`scratch`/`env_setup`/allocation) the user confirms; the login-shape canary then validates. This is the pre-endpoint discovery channel the cascade reserved — now wired. See [[Globus index discovery channel]].
- **Endpoint reuse.** `find_online_endpoint` ([[facility-remote]] `:643`) is a web query (no SSH) that lets a reconnect reuse a running endpoint — the [[Two-channel architecture|authenticate-once]] keystone.
- **The catalog drives the shape.** A Globus Search index holds the facility shape; the **[[Facility catalog|catalog resolver]]** turns an entry into a `MachineProfile` — at startup (`HPC_BRIDGE_MACHINE`) or at runtime (`connect_facility`). No hardcoded profile, no bundled fallback. An unresolved machine is **not** a dead-end: it falls to the human channel below. See [[Globus index discovery channel]].
- **Agentic machine + allocation selection.** `list_facilities` browses the catalog; `connect_facility(facility)` brings up the free login shape, runs the allocation command (e.g. `mybalance`) over Compute, parses it in code, and returns the allocations to pick from — the choice flows into `ensure_endpoint_up(account=…)`. → [[The MCP tools]]
- **Session-local facilities — built.** An index miss (or index-down) is no dead-end: with an `ssh_host` the agent **probes and proposes** (above); without one `connect_facility` returns `needs_facility_details` and asks for the host. Either way the confirmed `connect_facility(details=…)` builds a **session-local** facility (user-supplied, never indexed), validated by the login-shape canary. See [[Globus index discovery channel]].

## What's deliberately *not* here yet

The remaining discovery-channel machinery — per-channel **ablation flags** and a **resolution trace** — is **planned**, not built (the **login-node probe** channel itself is now built — see above). Session-local entries don't persist across restarts, and seed-emission/write-back (offering a validated facility for curation) is deferred. See [[Discovery channel model]] (the frame) and [[Globus index discovery channel]] (the thread).

> [!note] Scope
> This note describes current behaviour only. The catalog generalization, the human/Socratic fallback, **and** the raw-SSH login-node probe are now **built**; only **ablation flags, the resolution trace, and write-back** remain — see [[Globus index discovery channel]].

## See also
[[Facility catalog]] · [[The MCP tools]] · [[Two-channel architecture]] · [[facility-remote]] · [[server]] · [[Standing up the endpoint]]
