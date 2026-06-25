# Facility catalog

> [!abstract] In one line
> Where the per-facility shape comes from: a **Globus Search index** of machine entries, resolved into a `MachineProfile` — so adding a facility is *data*, not *code*. The single runtime source: no hardcoded profile and no bundled fallback — the index is it (the seed YAML is the curator's ingest input, not a runtime catalog).

## The entry — `CatalogEntry` (`catalog/entry.py`)

One entry per machine, a **superset of `MachineProfile`**, split by *who controls the value*:

- **`compute:`** — machine-invariant facts the plugin **pins** (user can't override): `scheduler`, `interface`, `env_setup`, `scratch_root`, `endpoint_name`, `amqp_port`. Getting one wrong breaks the endpoint silently (the wrong `interface` → workers never phone home) — the "look up, never infer" category, like the UUIDs.
- **`defaults:`** — per-run tunables the agent/user **may override** (`partition`, `walltime`, nodes/blocks, accelerators).
- plus identity, `ssh_host`, `auth_method`, the allocation `command` + named `parser`, and `provenance` / `last_validated`.

`{user}`/`{venv}` are templated at provision time; `worker_init` is *derived* (= `env_setup`); `account` is **never stored** (per-user, from allocation selection). `profile_kwargs()` → `profile_from_catalog_entry` ([[facility-remote]]) is the binding seam to `MachineProfile`. The Anvil entry resolves to the known-good config we've stood up live (verified by test).

## The provider seam — `CatalogProvider` (`catalog/base.py`)

A `Protocol` with `get(machine)` (exact → provisioning) and `discover(query)` (browse). Three implementations:

- **`SearchCatalog`** (`catalog/search.py`) — the runtime catalog: live `get_subject` → write-through cache (a *fetched-data* offline copy; **no bundled fallback**). An index miss returns `None`.
- **`BundledCatalog`** (`catalog/bundled.py`) — loads the checked-in seed YAML: the **curator's ingest source** + a test fixture. *Not* a runtime catalog.
- **`FakeCatalog`** (`tests/fakes.py`) — in-memory test double.

`make_catalog()` ([[server]]) **requires** `HPC_BRIDGE_SEARCH_INDEX` — the index is the only runtime catalog. No index (or no search scope) is a hard failure.

> [!note] Auth — reuse the Compute identity
> Reads use `SearchClient(app=Client().app)` — the same Globus identity Compute already holds — needing a one-time search-scope consent (`hpc-bridge-catalog`); until granted, catalog discovery **hard-fails** (no bundled fallback). This unlocks `visible_to`-restricted entries. See [[Globus index discovery channel]].

## Two ways in — startup or agentic

- **Startup (env-pinned):** `HPC_BRIDGE_MACHINE=<id>` → `make_facility` resolves the entry from the catalog at boot.
- **Agentic (runtime):** `list_facilities()` → `connect_facility(machine)` binds the machine (late-binds `AppCtx.facility`), brings up its **free login shape**, runs the allocation `command` over Compute, parses it, and returns the allocations → `ensure_endpoint_up(account=…)`. → [[The MCP tools]]

Allocation output is parsed by a **deterministic, plugin-side parser** keyed by `entry.allocation.parser` (`catalog/parsers.py` — `mybalance` built; `sbank`/`iris` reserved). Stdout is parsed in code, **never** handed to the model — inference is exactly what the catalog removes.

> [!warning] Trust — read-only plugin, curator-only writes
> The plugin never writes the index — an open-write catalog of executable config (`env_setup` bash, UUIDs) is an injection vector. New machines are curated via the `hpc-bridge-catalog` ingest (`catalog/ingest.py`; PR review = the audit trail). The agent only ever sees a `CatalogSummary` (identity + provenance — no executable config or raw UUIDs).

## See also
[[The MCP tools]] · [[Discovery today]] · [[Globus index discovery channel]] · [[Discovery channel model]] · [[facility-remote]] · [[MEP & templated endpoints]] · [[server]]
