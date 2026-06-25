# Globus index discovery channel

> [!warning] Planned · transient
> **Plan 1 (the catalog data layer) is built — in review ([PR #15](https://github.com/ryanchard/hpc-bridge/pull/15), branch `facility-catalog`).** Plan 2 (the agentic selection flow) is next. Tracking: [#7](https://github.com/ryanchard/hpc-bridge/issues/7). This note is the spec + status; it churns as the work lands.

## Goal

Replace the hardcoded `anvil_profile` ([[facility-remote]]) with a **catalog-driven resolver** that builds a `MachineProfile` from an entry in a self-owned **Globus Search index** — so adding a facility is *data*, not *code*. The index is the *happiest* discovery channel; everything else (login-node probe, the human) is fallback. Contrast with what exists now: [[Discovery today]].

## Design — as built (Plan 1)

The conceptual frame (channel model, provide-vs-discover matrix, principles, the trace) lives in **[[Discovery channel model]]**. A `catalog/` package implements the resolver:

- **`CatalogEntry`** (Pydantic) — `compute:` (pinned, user can't override) / `defaults:` (overridable) split; named allocation `parser`; `{user}`/`{venv}` templating; `worker_init` *derived*; `account` *not* stored; UUIDs validated on read. `profile_kwargs()` is the binding seam → `MachineProfile`.
- **`CatalogProvider`** seam — `SearchCatalog` (live `get_subject` → write-through cache → bundled fallback), `BundledCatalog` (seed YAML, shipped in the wheel), `FakeCatalog` (the test double = our "`SearchClient` injection" seam).
- **3-way `make_facility`** — `HPC_BRIDGE_MACHINE` → catalog; `HPC_BRIDGE_FACILITY=anvil` → the hardcoded path (kept); else local. *Validated:* the catalog path produces a **byte-identical `MachineProfile`** to `anvil_profile`.
- **Trust** — the plugin is **read-only**; writes are **curator-only** via the `hpc-bridge-catalog` ingest (PR review = the audit trail), because an open-write catalog of executable config (`env_setup` bash, UUIDs) is an injection vector. A `CatalogSummary` is the **agent-safe view** (no executable config / raw UUIDs). `provenance: plugin-validated` is *reserved, not built* — this supersedes our earlier "plugin write-back loop."

> [!note] Decided — authenticated read (reuse the Compute identity)
> We already require Globus Auth for Compute, so the index reuses **the same identity** (`SearchClient(app=Client().app)`) rather than an anonymous client. Rationale: the curator/write path needs auth *anyway* (ingest → `search:all`, else `403`), so unified auth is the coherent model — and it unlocks **`visible_to`-restricted entries** (a facility's config / sensitive UUIDs visible only to its allocation-holders), the actual reason to use Globus Search over a checked-in file. The marginal cost is a **one-time search-scope consent** per identity that wants *live* reads (run `hpc-bridge-catalog`; the server never prompts and **falls back to bundled** until granted). *(Reading purely-public entries anonymously, to skip even that consent, stays available as a later optimization.)*

## Plan 2 — next (the agentic selection flow)

Designed (`docs/design/facility-catalog.md` §5 — to be absorbed here), **not built**:
- tools `list_facilities` / `connect_facility` → a `needs_account` state machine → `ensure_endpoint_up(account=…)`;
- deterministic allocation **parsers** (`mybalance`/`sbank`/`iris`) — stdout parsed in code, never handed to the model;
- **account-from-selection** (today it's still the `HPC_BRIDGE_ACCOUNT` env var);
- allocation discovery runs **through the login shape** (Compute), not SSH — the same endpoint-first move as [[Discovery today]].

## Our extras (later slices, optional)

From [[Discovery channel model]], not in the catalog yet: per-channel **ablation flags** + the **resolution trace** (today the bundled-vs-search selector is a *coarse* ablation, and resolution is single-source so a per-fact trace is less load-bearing); the explicit **Socratic** human-elicitation fallback (today an unknown machine simply isn't found). Fold in if/when the matrix-as-tests discipline is wanted.

## Status

- **Built (in review, [#15](https://github.com/ryanchard/hpc-bridge/pull/15)):** Plan 1 — the catalog data layer · 3-way `make_facility` · bundled fallback · the `hpc-bridge-catalog` ingest curator · `globus-sdk` as a direct dep.
- **Next:** Plan 2 — the allocation-selection flow.
- **Deferred:** ACCESS MCP / Operations API channels; the ablation/trace/Socratic extras.

## See also
[[Discovery channel model]] · [[Discovery today]] · [[facility-remote]] · [[Happy path]] · [[Home]]
