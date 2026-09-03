# Facility catalog

> [!abstract] In one line
> Where the per-facility shape comes from: a **Globus Search index** of machine entries, resolved into a `MachineProfile` — so adding a facility is *data*, not *code*. The single runtime source: no hardcoded profile and no bundled fallback — the index is it (the seed YAML is the curator's ingest input, not a runtime catalog).

## The entry — `CatalogEntry` (`catalog/entry.py`)

One entry per machine, a **superset of `MachineProfile`**, split by *who controls the value*:

- **`compute:`** — machine-invariant facts the plugin **pins** (user can't override): `scheduler`, `interface`, `env_setup`, `scratch_root`, `endpoint_name`, `amqp_port`. Getting one wrong breaks the endpoint silently (the wrong `interface` → workers never phone home) — the "look up, never infer" category, like the UUIDs.
- **`defaults:`** — per-run tunables the agent/user **may override** (`partition`, `walltime`, nodes/blocks, accelerators).
- plus identity, `ssh_host`, `auth_method`, the allocation `command` + named `parser`, and `provenance` / `last_validated`.

`{user}`/`{venv}` are templated at provision time; `worker_init` is *derived* (= `env_setup`); `account` is **never stored** (per-user, from allocation selection). `profile_kwargs()` → `profile_from_catalog_entry` ([[facility-remote]]) is the binding seam to `MachineProfile` — and derives `endpoint_name` = `hpc-bridge-<id>` when a seed omits it (never the bare `hpc-bridge`, which would collide — endpoints are keyed by identity + name). The Anvil entry resolves to the known-good config we've stood up live (verified by test).

## The provider seam — `CatalogProvider` (`catalog/base.py`)

A `Protocol` with `get(machine)` (exact → provisioning) and `discover(query)` (browse). Three implementations:

- **`SearchCatalog`** (`catalog/search.py`) — the runtime catalog: live `get_subject` → write-through cache (a *fetched-data* offline copy; **no bundled fallback**). An index miss returns `None`.
- **`BundledCatalog`** (`catalog/bundled.py`) — loads the checked-in seed YAML: the **curator's ingest source** + a test fixture. *Not* a runtime catalog.
- **`FakeCatalog`** (`tests/fakes.py`) — in-memory test double.

`make_catalog()` ([[server]]) **requires** `HPC_BRIDGE_SEARCH_INDEX` — the index is the only runtime catalog. No index (or no search scope) is a hard failure.

> [!info] The index — `6ff95fb8-1113-42be-a811-3d1cb5a67bd5`
> Our Globus Search index (display name `hpc-bridge-test`), owned by the maintainer's Globus identity. Set `HPC_BRIDGE_SEARCH_INDEX=6ff95fb8-1113-42be-a811-3d1cb5a67bd5` for the server (locally it lives in `.claude/settings.local.json`, which is gitignored — so an interactive shell does **not** have it; pass the UUID literally to `hpc-bridge-catalog`). Curated entries ingested so far (subject → seed):
> - `purdue:anvil` ← `catalog/seed/anvil.yaml` (SSH-bootstrap, personal endpoint; 2026-06)
> - `globus:globus1` ← `catalog/seed/globus-cluster.yaml` (the **MEP** entry for `globus-cluster-mep`, zero SSH; ingested 2026-08-19 — see [[Endpoint reuse and MEP integration]])
>
> Ingest is idempotent (keyed by subject): `hpc-bridge-catalog 6ff95fb8-1113-42be-a811-3d1cb5a67bd5 src/hpc_bridge/catalog/seed/<file>.yaml` (needs the one-time `search:all` consent). All entries are currently `visible_to: public` — `ingest.py` hardcodes it; per-entry visibility is a curator TODO. **Seed `aliases` are NOT indexed** (the loader resolves them; `ingest.py` drops them and `SearchCatalog` matches only `id`/subject) — at runtime use the `id` (`globus1`) or subject, as `list_facilities` shows. Verified live 2026-08-19: `get("globus1")` → the MEP entry → `MEPFacility`, and a real status read of the MEP returned online.

> [!note] Auth — anonymous by default (public registry); the Compute identity only when it already holds the Search scope
> Globus Search needs auth only for non-public entries, so a fresh install lists the registry with zero setup. The note below records the earlier authenticated-read design; see [[Globus index discovery channel]] for the 2026-09-03 flip.
>
> [!note] (Original) Auth — reuse the Compute identity
> Reads use `SearchClient(app=Client().app)` — the same Globus identity Compute already holds — needing a one-time search-scope consent (`hpc-bridge-catalog`); until granted, catalog discovery **hard-fails** (no bundled fallback). This unlocks `visible_to`-restricted entries. See [[Globus index discovery channel]].

## Three ways in — startup, agentic, or BYO

- **Startup (env-pinned):** `HPC_BRIDGE_MACHINE=<id>` → `make_facility` resolves the entry from the catalog at boot.
- **Agentic (runtime):** `list_facilities()` → `connect_facility(facility)` binds the machine (late-binds `AppCtx.facility`), brings up its **free login shape**, runs the allocation `command` over Compute, parses it, and returns the allocations → `ensure_endpoint_up(account=…)`. → [[The MCP tools]]
- **BYO / discovery (runtime):** a machine the index can't resolve isn't a dead-end — pass `connect_facility(facility, ssh_host=…)` and the server **probes the login node** ([[discovery]]) to *propose* a `FacilityDetails` draft (`proposed_facility_details`) the user confirms (or, with no host, returns `needs_facility_details` and asks). The confirmed `details=` builds a **session-local** entry (`provenance="session"`, on `AppCtx.session_facilities`, **never indexed**, endpoint name `hpc-bridge-<facility>`) that drives the same flow; the login-shape canary validates it. → [[Globus index discovery channel]]

Allocation output is parsed by a **deterministic, plugin-side parser** keyed by `entry.allocation.parser` (`catalog/parsers.py` — `mybalance` built; `sbank`/`iris` reserved). Stdout is parsed in code, **never** handed to the model — inference is exactly what the catalog removes.

> [!note] Runtime binding details
> `connect_facility(facility=…)` resolves its arg by **id or subject** (`anvil` or `purdue:anvil`). It also **moves the [[Session continuity|session-shell]] root** to the bound facility's remote scratch — else `run_shell` would run the session shell at the local `~/.hpc-bridge` path *on the remote node*. And the server **boots resiliently**: if `make_facility` fails at startup (a stale env var, no index), `lifespan` warns and starts *unbound* (`LocalFacility`) rather than crashing — the agent then binds via `connect_facility`.

> [!warning] Trust — read-only plugin, curator-only writes
> The plugin never writes the index — an open-write catalog of executable config (`env_setup` bash, UUIDs) is an injection vector. New machines are curated via the `hpc-bridge-catalog` ingest (`catalog/ingest.py`; PR review = the audit trail). The agent only ever sees a `CatalogSummary` (identity + provenance — no executable config or raw UUIDs).

## See also
[[The MCP tools]] · [[Discovery today]] · [[Globus index discovery channel]] · [[Discovery channel model]] · [[facility-remote]] · [[MEP & templated endpoints]] · [[server]]
