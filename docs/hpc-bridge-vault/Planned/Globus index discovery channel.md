# Globus index discovery channel

> [!warning] Planned · transient
> **Plans 1–2 (catalog data layer + agentic selection flow) are merged**, as is the **discover-first sweep + persistent SSH** (this note's "built" sections). Tracking: [#7](https://github.com/ryanchard/hpc-bridge/issues/7). This note is the spec + status; it churns as the work lands — remaining: seed-emission / write-back.

## Goal

Replace the hardcoded `anvil_profile` ([[facility-remote]]) with a **catalog-driven resolver** that builds a `MachineProfile` from an entry in a self-owned **Globus Search index** — so adding a facility is *data*, not *code*. The index is the *happiest* discovery channel; everything else (login-node probe, the human) is fallback. Contrast with what exists now: [[Discovery today]].

## Design — as built (Plan 1)

The conceptual frame (channel model, provide-vs-discover matrix, principles, the trace) lives in **[[Discovery channel model]]**. A `catalog/` package implements the resolver:

- **`CatalogEntry`** (Pydantic) — `compute:` (pinned, user can't override) / `defaults:` (overridable) split; named allocation `parser`; `{user}`/`{venv}` templating; `worker_init` *derived*; `account` *not* stored; UUIDs validated on read. `profile_kwargs()` is the binding seam → `MachineProfile`.
- **`CatalogProvider`** seam — `SearchCatalog` (live `get_subject` → write-through cache; **no bundled fallback** — an index miss is `None`), `BundledCatalog` (the seed loader — curator ingest source + test fixture, *not* a runtime catalog), `FakeCatalog` (the test double).
- **`make_facility`** — `HPC_BRIDGE_MACHINE` → catalog; else local (the agent binds a machine at runtime via `connect_facility`). The hardcoded `anvil_profile` + `HPC_BRIDGE_FACILITY` path is **removed**, and so is the bundled-seed fallback; the Globus index is the only runtime source.
- **Trust** — the plugin is **read-only**; writes are **curator-only** via the `hpc-bridge-catalog` ingest (PR review = the audit trail), because an open-write catalog of executable config (`env_setup` bash, UUIDs) is an injection vector. A `CatalogSummary` is the **agent-safe view** (no executable config / raw UUIDs). `provenance: plugin-validated` is *reserved, not built* — this supersedes our earlier "plugin write-back loop."

> [!note] Decided — authenticated read (reuse the Compute identity)
> We already require Globus Auth for Compute, so the index reuses **the same identity** (`SearchClient(app=Client().app)`) rather than an anonymous client. Rationale: the curator/write path needs auth *anyway* (ingest → `search:all`, else `403`), so unified auth is the coherent model — and it unlocks **`visible_to`-restricted entries** (a facility's config / sensitive UUIDs visible only to its allocation-holders), the actual reason to use Globus Search over a checked-in file. The marginal cost is a **one-time search-scope consent** per identity that wants *live* reads (run `hpc-bridge-catalog`; the server never prompts and **hard-fails** until granted — no bundled fallback). *(Reading purely-public entries anonymously, to skip even that consent, stays available as a later optimization.)*

## Plan 2 — built (the agentic selection flow)

Machine + allocation are now **agent-chosen at runtime**, not fixed by env ([[The MCP tools]]):
- **`list_facilities(query)`** → browse the catalog (agent-safe `CatalogSummary`s).
- **`connect_facility(facility)`** → bind the machine (late-binds `AppCtx.facility`, resetting shapes/state on a switch), bring up its **free login shape** (SSH cold-bootstrap once, or reuse an online endpoint), run the allocation command over Compute, parse, and return `needs_account` with the allocations. `provisioning` ⇒ login node still warming.
- **deterministic parsers** (`catalog/parsers.py`): `mybalance` built (real Anvil output); `sbank`/`iris` reserved. Stdout parsed in code, never handed to the model.
- **`ensure_endpoint_up(account=…)`** → the chosen allocation threads into the Slurm shape's `user_endpoint_config` (mirrors `partition`); `account` is no longer env-only.
- `_facility_from_entry` / `_unsupported_entry_reason` factored out of `make_facility`'s startup path and shared with `connect_facility`.

## The Socratic fallback — built (session-local)

A machine the index can't resolve is **no longer a hard failure**. `connect_facility(X)` returns `phase="needs_facility_details"`; the agent elicits the config from the **user** (the `FacilityDetails` schema is the question list — `ssh_host`, `interface`, `env_setup`, `scratch_root`, `partition`, optional allocation command), calls `connect_facility(X, details=…)`, and the server builds a **session-local** `CatalogEntry` (`provenance="session"`, remembered on `AppCtx.session_facilities`), then runs the **normal** flow. The login-shape canary **validates** the supplied values (a wrong `interface`/`env_setup` ⇒ the worker never registers) — *elicit-then-validate*, the [[Discovery channel model|human channel]] wired in.

- **Trust:** the session-local entry holds executable config (`env_setup`, `ssh_host`) but it's **user-supplied** (Tier-1, like credentials), **never written to the index** (curator-only writes stay the boundary). The agent is a conduit for the user's answers; it must not *invent* config — it **proposes discovered facts** for the user to confirm. SSH user + key come from `~/.ssh/config` (read live; optional env overrides), never a boot-env var the running server can't see.
- **Isolated endpoint name:** a session facility registers as `hpc-bridge-<facility>` (e.g. `hpc-bridge-globus`), never the bare `hpc-bridge`. Globus Compute keys endpoints by *identity + name*, so a shared name lets `find_online_endpoint` reuse another facility's (or a stale "online") registration — stranding a canary that can never warm. `_entry_from_details` derives it for session facilities; curated seeds set it explicitly (e.g. `hpc-bridge-anvil`). **The standard is `hpc-bridge-<facility>` everywhere — the bare `hpc-bridge` is banned.**
- **Also covers index-down:** if `make_catalog()` errors, the same fallback fires (supply `details` to proceed) rather than a hard fail.
- **Deferred:** write-back / seed-emission for curation; parsers beyond `mybalance`; persisting session facilities across restarts; non-Slurm.

### Discover-first — built (this branch)

Pure elicitation was too much to ask: `interface` / `env_setup` / `scratch_root` / `partition` are facts the login node can *tell* you. So an index miss now **discovers before it asks**. `connect_facility(X, ssh_host="…")` builds a **bare `SshTarget`** (just `ssh_host` + env creds — nothing else from `FacilityDetails` is needed to open SSH), runs **one batched login-node probe** ([[discovery|discovery.py]] · `discover_facility_details`), and returns `phase="proposed_facility_details"` with a filled-in `FacilityDetails` **draft** + notes flagging the low-confidence fields. The agent reviews/corrects the draft *with the user* (above all `interface`) and calls `connect_facility(X, details=…)`, re-entering the **same** session-local flow above — the canary still validates. With no host, `needs_facility_details` simply asks for one. `_propose_or_ask` (`server.py:796`) is the router; the probe rides the persistent-SSH ([[facility-remote]]) master the bootstrap then reuses (no extra auth).

The [[Discovery channel model|human channel]] minimized: **the user provides access, the agent discovers the config.** "Elicit-then-validate" becomes **probe → propose → confirm → validate** — proposing *discovered* facts (user-confirmed, canary-checked) is not "inventing."

## Our extras (later slices, optional)

From [[Discovery channel model]], not in the catalog yet: per-channel **ablation flags** + the **resolution trace** (resolution is single-source so a per-fact trace is less load-bearing today). Fold in if/when the matrix-as-tests discipline is wanted.

## Status

- **Merged:** Plan 1 (catalog data layer · catalog-driven `make_facility` · `hpc-bridge-catalog` ingest, [#15](https://github.com/ryanchard/hpc-bridge/pull/15)) **and** Plan 2 (`list_facilities` + `connect_facility` + `mybalance` parser + account-from-selection, [#17](https://github.com/ryanchard/hpc-bridge/pull/17)).
- **Built + merged:** the **Socratic fallback** + **discover-first sweep** above — `connect_facility(ssh_host=…)` → `proposed_facility_details` → confirm → session-local `connect_facility(details=…)` — plus persistent SSH ([[facility-remote]], ControlMaster). Validated live on the globus1 cluster.
- **Deferred:** ACCESS MCP / Operations API channels; the ablation/trace extras; seed-emission/write-back (see [[Discovery channel model]]).

## See also
[[Discovery channel model]] · [[Discovery today]] · [[facility-remote]] · [[Happy path]] · [[Home]]
