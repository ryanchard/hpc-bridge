# Discovery today

> [!abstract] In one line
> What the plugin discovers *now*: after the one-time bootstrap, the agent probes the facility through the **login shape over AMQP** (no SSH), and the per-facility shape is still a **hardcoded `anvil_profile`** on `main` — a Globus index exists, and the catalog resolver that replaces the hardcode is built and **in review** ([PR #15](https://github.com/ryanchard/hpc-bridge/pull/15)), not yet merged here.

## What's implemented

- **Endpoint-first, SSH-once discovery.** The `driving-hpc` skill ([[Plugin packaging]]) sequences: establish the endpoint (`shape="login"`) → discover via `run_shell(shape="login")` — `sinfo`/`mybalance`/`squeue` over AMQP, **not** `login_shell` (SSH) → gate (partition + budget) → provision `slurm` with `confirm_spend` → wait by polling `squeue` via the login shape. `login_shell` (raw SSH, [[server]] `:494`) is the cold-start escape hatch only.
- **Endpoint reuse.** `find_online_endpoint` ([[facility-remote]] `:569`) is a web query (no SSH) that lets a reconnect reuse a running endpoint — the [[Two-channel architecture|SSH-once]] keystone.
- **The Globus index exists.** A public Globus Search index holds the facility shape (host, scheduler, interface, env_setup…), queryable via `globus_sdk.SearchClient`. On `main`, `make_facility` still builds a **hardcoded `anvil_profile`** ([[facility-remote]] `:109`) — but the **catalog resolver** that turns an index entry into a `MachineProfile` is **built and in review** ([PR #15](https://github.com/ryanchard/hpc-bridge/pull/15)); see [[Globus index discovery channel]].

## What's deliberately *not* here yet

The discovery-channel model — index → login-node probe → human, with ablation and a resolution trace — is **planned**, not implemented. See [[Globus index discovery channel]] (which absorbs `docs/design/discovery-channels.md`).

> [!note] Scope
> This note describes current behaviour only. The "facility shape comes from an index, not a hardcoded class" generalization is the next major thread — see [[Globus index discovery channel]].

## See also
[[Two-channel architecture]] · [[facility-remote]] · [[server]] · [[Standing up the endpoint]]
