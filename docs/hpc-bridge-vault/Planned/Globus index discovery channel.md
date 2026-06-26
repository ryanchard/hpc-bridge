# Globus index discovery channel

> [!warning] Planned · transient
> **Plan 1 (the catalog data layer) is merged.** **Plan 2 (the agentic selection flow) is built — in review (this PR).** Tracking: [#7](https://github.com/ryanchard/hpc-bridge/issues/7). This note is the spec + status; it churns as the work lands.

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

## Our extras (later slices, optional)

From [[Discovery channel model]], not in the catalog yet: per-channel **ablation flags** + the **resolution trace** (resolution is single-source so a per-fact trace is less load-bearing today); the explicit **Socratic** human-elicitation fallback — **the next slice** (today an unknown machine, or no index, is a hard failure). Fold the rest in if/when the matrix-as-tests discipline is wanted.

## Status

- **Merged:** Plan 1 — the catalog data layer · catalog-driven `make_facility` · the `hpc-bridge-catalog` ingest curator ([#15](https://github.com/ryanchard/hpc-bridge/pull/15)).
- **Built (in review, this PR):** Plan 2 — `list_facilities` + `connect_facility` + the `mybalance` parser + account-from-selection. 5 → 7 [[The MCP tools|MCP tools]].
- **Deferred:** ACCESS MCP / Operations API channels; the ablation/trace/Socratic extras (see [[Discovery channel model]]).

## See also
[[Discovery channel model]] · [[Discovery today]] · [[facility-remote]] · [[Happy path]] · [[Home]]
