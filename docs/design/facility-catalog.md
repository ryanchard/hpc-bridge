# hpc-bridge — Facility Catalog (Globus Search) Design Spec

Status: design spec · 2026-06-04 · branch `facility-catalog` (off `claude-effort`)
Builds on: [`./plugin-v1-design.md`](./plugin-v1-design.md) — the `Facility` seam and
`MachineProfile` this catalog feeds.

## 1. Summary

A **Globus Search index** that catalogs per-machine facility metadata so the
`hpc-bridge` plugin can stand up a *valid* endpoint on a named HPC system without
the agent guessing — and so it can present the user a list of allocations to pick
from. The plugin is the consumer; the agent only ever receives structured results.

**Why this exists (the problem it solves).** Two classes of facility data are not
safely obtainable at runtime:

- **Exact identifiers** — Globus Compute MEP UUIDs, Globus Transfer endpoint UUIDs.
  An LLM will *confidently hallucinate* a plausible-but-wrong UUID; the failure is
  silent (dispatch to nothing / the wrong place). This data must be **looked up,
  never inferred.**
- **Facility conventions** — allocation-listing commands (ALCF `sbank`, NERSC
  `iris`, Purdue `mybalance`), module/env recipes, scheduler quirks. The model's
  training knowledge is fuzzy and stale; web-scraping docs mid-session is slow,
  often behind auth, and breaks the low-latency REPL the project is built around.

Today this metadata lives hardcoded in `facility/remote.py` as `MachineProfile` /
`anvil_profile()` — whose own docstring promises "new HPC systems are added as
profiles rather than code." The catalog is the externalization that finally
delivers that: **new machines become data, not code.**

## 2. Scope

- **Primary consumer: the plugin code (deterministic lookup by machine name).** A
  catalog entry is the input that builds a `MachineProfile`.
- **The allocation-selection flow is built *on top of* the catalog**, not in it. A
  user's allocations are per-user and dynamic, so the catalog stores *how to
  discover* them (the command), not the allocations themselves. The plugin runs
  that command at bootstrap and feeds the resulting list back to the agent as a
  structured result — the same "plugin hands the agent structured data" pattern
  `run_shell` already uses. The agent never queries the index directly.

### Non-goals (v1)

- **Write-back from the plugin/community.** Deferred. The plugin is **read-only**
  against the index. Write access is **curator-only** (a closed set of Globus
  identities), because an open-write catalog of executable config (`env_setup`
  bash, UUIDs) is an injection vector.
- Doc-scraping ingestion, `provenance: community|scraped`, staging-index curation —
  all forward-compat (the schema reserves the fields) but unbuilt.

## 3. The catalog entry schema (`CatalogEntry`, Pydantic)

One entry per machine. Fields map ~1:1 onto what `SlurmFacility` /
`MachineProfile` / `SshTarget` already need, so an entry is the input to a
`MachineProfile`.

```yaml
# identity
id: anvil                         # canonical key; subject = "<facility>:<id>"
aliases: [anvil.rcac.purdue.edu]
facility: "Purdue / ACCESS"
description: "Anvil CPU cluster"

# identifiers  (must look up, never infer)
compute_mep_uuid: <uuid|null>     # facility's multi-user endpoint, if one exists
transfer_endpoint_uuid: <uuid>    # Globus Transfer collection

# access
ssh_host: anvil.rcac.purdue.edu
auth_method: ssh-key              # ssh-key | mfa-otp | sfapi  -> drives broker routing (M5)

# allocation discovery  (how to LIST allocations, not the allocations themselves)
allocation:
  command: "mybalance"            # ALCF: sbank | NERSC: iris | Purdue: mybalance
  parse_hint: "..."               # optional: how to read output into a list

# environment / scheduler  (feeds MachineProfile + the UEP config template)
scheduler: slurm                  # slurm | pbs | lsf
env_setup: "module load anaconda/2024.02-py311 && source {venv}/bin/activate"
defaults: {partition: debug, walltime: "00:30:00", interface: ib0,
           amqp_port: 443, max_workers_per_node: 2}
scratch_root: "/anvil/scratch/{user}/.hpc-bridge"   # {user} templated at runtime

# trust / provenance
provenance: curated               # curated | community | scraped | plugin-validated
last_validated: 2026-06-03
validated_notes: "worker on compute node a006"
```

- **Subject scheme:** `"<facility>:<id>"` — e.g. `alcf:polaris`, `nersc:perlmutter`,
  `purdue:anvil`. Readable, namespaced by facility, no URN.
- **Templating:** entries are user-agnostic; `{user}`/`{venv}` are filled from the
  SSH target at provision time, exactly as `anvil_profile()` builds the venv path
  today.
- **Re-validated on read:** the plugin parses every fetched entry through the
  Pydantic schema (UUID format, required fields) before it reaches provisioning —
  defense-in-depth against a malformed entry.
- The field set is intentionally lean; expect to grow it (e.g. `notes`/`quirks`,
  `status: active|deprecated`, data-transfer paths) as real machines are added.

## 4. Index structure & the `CatalogProvider` seam

**Globus Search:** one index. Each machine is one `GMetaEntry` whose `subject` is
`<facility>:<id>`. Lookup is a direct `get_subject(index_id, subject)` — exact,
fast, no fuzzy ranking to misfire; `post_search` with a query is reserved for the
*discovery* path. `visible_to` is `["public"]` for curated entries (or a Globus
Group UUID for facility-restricted ones — the access control a checked-in file
can't do). Read via `globus_sdk.SearchClient` using **the same Globus Auth
identity the Compute SDK already holds** (adds the `search.api.globus.org` scope,
no new login). `globus-sdk` becomes a direct dependency (already transitive via
`globus-compute-sdk`).

**The seam** (mirrors the existing `Facility` / `FakeFacility` pattern):

```python
class CatalogProvider(Protocol):
    async def get(self, machine_id: str) -> CatalogEntry | None: ...    # exact lookup -> provisioning
    async def discover(self, query: str) -> list[CatalogSummary]: ...   # list/search machines for the agent
    # propose(...) write-back: deferred (non-goal v1); read-only providers omit it
```

- **`SearchCatalog`** — Globus Search backed (primary). On query error / offline,
  falls back to the bundled seed and serves from a local cache in
  `${CLAUDE_PLUGIN_DATA}`.
- **`BundledCatalog`** — reads the checked-in seed YAML. Triple duty: offline
  fallback, the *source* the initial curated machines are ingested from, and a
  zero-network test fixture.
- **`FakeCatalog`** — in-memory, for unit tests (like `FakeFacility`).

A `make_catalog()` in `server.py` selects the provider from env
(`HPC_BRIDGE_SEARCH_INDEX=<uuid>` …), exactly like `make_facility()`.

## 5. Retrieval & the allocation-selection flow

Machine selection becomes **agent-driven** (today it is fixed by
`HPC_BRIDGE_FACILITY`). The plugin runs a small state machine, each gap returning a
structured "here's what I need next," never a hang:

```
list_facilities(query?)  -> discover machines in the catalog
connect_facility(machine) -> catalog.get(); if MEP UUID present & user maps, use it
                             (BYO-endpoint path); else prepare SSH bootstrap.
                             Run entry.allocation.command over the bootstrap SSH,
                             parse via parse_hint -> return
                             {phase:"needs_account", allocations:[{name,balance,units}],
                              provenance, last_validated, notice:"pick an allocation"}
ensure_endpoint_up(account=...) -> provision with the chosen allocation
                                   (Profile.account -> SlurmProvider account)
```

- **Two ways to get compute, chosen by the entry:** a present `compute_mep_uuid`
  (+ user mapping) lets the plugin dispatch to the existing MEP via the
  BYO-endpoint path (no SSH bootstrap); otherwise it bootstraps a personal
  endpoint over SSH via `SlurmFacility`, building the `MachineProfile` from the
  entry.
- **Allocation discovery runs over the bootstrap SSH connection** that
  `RemoteEndpointCLI` already opens — there is no Compute path before the endpoint
  exists. It is part of the bootstrap, not a separate subsystem.

### MCP tool surface (Approach B — dedicated tools)

- `list_facilities(query?)` — discover catalogued machines.
- `connect_facility(machine)` — returns machine info + allocation options
  (`needs_account`).
- `ensure_endpoint_up(account=…)` — existing tool, finishes provisioning with the
  selected allocation.

Each tool has one clear purpose and returns a structured result, matching the
project's existing style; the allocation-selection step is an explicit, testable
tool rather than hidden multi-phase state inside `ensure_endpoint_up`.

## 6. Ingestion & trust (curator-only)

- **Seed catalog in the repo** (`catalog/seed/*.yaml`) — the curated machines,
  version-controlled. Curated changes go through **PR review**, so git is the
  audit trail for trusted entries.
- **Admin ingest script** (`scripts/ingest_catalog.py` or a `hpc-bridge-catalog`
  console entry): reads the seed YAML, **validates every entry against
  `CatalogEntry`**, and upserts them as `GMetaEntry`s (keyed by `<facility>:<id>`).
  Idempotent. Run **by a curator** holding the index's writer role — users and the
  plugin never write.
- **Index access:** curator owns/admins it; curated entries `visible_to: public`
  (or a group); writer role granted only to curator identities. That is the whole
  "who can write" answer for v1: a closed set the curator controls.
- **Trust surfaced, not assumed:** every entry carries `provenance` +
  `last_validated`. v1 entries are all `curated`, but the plugin still echoes them
  to the agent (the user sees "config validated 2026-06-03"), and re-validates the
  schema on read. This is the forward-compat hook for community/scraped entries.

## 7. Testing (TDD, matching the project)

- **Unit (hermetic):** `CatalogEntry` schema (valid/invalid, UUID format,
  `{user}`/`{venv}` templating); `BundledCatalog` get/discover against a fixture
  YAML; `FakeCatalog`; `SearchCatalog` with a **mocked `SearchClient`** (subject ->
  entry; query error -> bundled fallback + cache write); allocation **parsing**
  for each command style (`sbank`/`iris`/`mybalance` sample outputs -> list); the
  new tools (`list_facilities`, `connect_facility`) and the `needs_account` state
  machine wired with `FakeCatalog` + `FakeFacility`; the ingest script's
  validate-and-upsert against a mocked client.
- **Integration (gated, like the existing live tests):** a real `get_subject`
  against a throwaway index; real SSH allocation discovery behind the existing
  `HPC_BRIDGE_RUN_INTEGRATION` gate.

## 8. Dependencies & open questions

- **New dependency:** `globus-sdk` (for `SearchClient`) — already transitive via
  `globus-compute-sdk`; promote to a direct dependency.
- **Open / verify at implementation:**
  - The exact `search.api.globus.org` scope and how it composes with the Compute
    SDK's existing token cache (confirm no second login is triggered).
  - `parse_hint` format: free-text guidance vs. a small structured parser per
    command. Start with the simplest thing that parses the three known commands;
    generalize once a 4th machine forces it.
  - Cache invalidation policy for `SearchCatalog`'s local cache (TTL vs.
    explicit refresh).
