# hpc-bridge — Facility Catalog (Globus Search) Design Spec

Status: design spec · created 2026-06-04 · revised 2026-06-25 · branch `facility-catalog`
(rebased onto `main`)
Builds on: [`../plugin-design.md`](../plugin-design.md) — the `Facility` seam and
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
- **Facility conventions** — the network interface a worker must bind
  (`address_by_interface` ifname), allocation-listing commands (ALCF `sbank`,
  NERSC `iris`, Purdue `mybalance`), module/env recipes, scheduler quirks. The
  model's training knowledge is fuzzy and stale; web-scraping docs mid-session is
  slow, often behind auth, and breaks the low-latency REPL the project is built
  around.

Today this metadata lives hardcoded in `facility/remote.py` as `MachineProfile` /
`anvil_profile()` — whose own docstring promises "new HPC systems are added as
profiles rather than code." The catalog is the externalization that finally
delivers that: **new machines become data, not code.**

## 2. Scope

- **Primary consumer: the plugin code (deterministic lookup by machine name).** A
  catalog entry is the input that builds a `MachineProfile`.
- **The allocation-selection flow is built *on top of* the catalog**, not in it. A
  user's allocations are per-user and dynamic, so the catalog stores *how to
  discover* them (the command + a named parser), not the allocations themselves.
  The plugin runs that command **through Compute** (see §5) and feeds the resulting
  list back to the agent as a structured result — the same "plugin hands the agent
  structured data" pattern `run_shell` already uses. The agent never queries the
  index directly.

### Non-goals (v1)

- **Write-back from the plugin/community.** Deferred. The plugin is **read-only**
  against the index. Write access is **curator-only** (a closed set of Globus
  identities), because an open-write catalog of executable config (`env_setup`
  bash, UUIDs) is an injection vector.
- Doc-scraping ingestion, `provenance: community|scraped`, staging-index curation —
  all forward-compat (the schema reserves the values) but unbuilt.
- Non-`ssh-key` access methods (`mfa-otp`, `sfapi`). The `auth_method` field is
  recorded, but only `ssh-key` is wired in v1; the others are reserved.

## 3. The catalog entry schema (`CatalogEntry`, Pydantic)

One entry per machine. The field set is a **superset of `MachineProfile`** (every
profile field is derivable from an entry), split by *who controls the value*:

- **`compute:`** — machine-invariant facts the plugin **pins**. The user/agent
  cannot override these; getting them wrong breaks the endpoint silently (e.g. the
  wrong `interface` means workers never phone home). Same "look up, never infer"
  category as the UUIDs.
- **`defaults:`** — per-run tunables the agent/user **may override** at submit time
  via `user_endpoint_config`.

```yaml
# identity
id: anvil                         # canonical key; subject = "<facility>:<id>"
aliases: [anvil.rcac.purdue.edu]
facility: "Purdue / ACCESS"
description: "Anvil CPU cluster"
display_name: "HPC-Bridge Anvil"  # label shown in the Globus web UI / `gce list`

# identifiers  (must look up, never infer)
compute_mep_uuid: <uuid|null>     # facility's multi-user endpoint, if one exists
transfer_endpoint_uuid: <uuid>    # Globus Transfer collection

# access
ssh_host: anvil.rcac.purdue.edu
auth_method: ssh-key              # ssh-key (v1) | mfa-otp | sfapi (reserved)

# allocation discovery  (how to LIST allocations, not the allocations themselves)
allocation:
  command: "mybalance"            # ALCF: sbank | NERSC: iris | Purdue: mybalance
  parser: mybalance               # named, deterministic plugin-side parser (§5)

# compute — machine-invariant facts the plugin PINS (user cannot override)
compute:
  scheduler: slurm                # slurm | pbs | lsf
  interface: ib0                  # address_by_interface ifname — wrong fabric = workers never connect
  env_setup: "module load anaconda/2024.02-py311 && source {venv}/bin/activate"
  scratch_root: "/anvil/scratch/{user}/.hpc-bridge"   # {user} templated at runtime
  endpoint_name: hpc-bridge       # registration / on-disk dir name (constant today)
  scheduler_options: null         # optional machine #SBATCH constraints
  # amqp_port: 443                # OPTIONAL — defaults to 443; set only if a facility firewalls it differently

# defaults — per-run tunables the agent/user MAY override via user_endpoint_config
defaults:
  partition: debug
  walltime: "00:30:00"
  max_workers_per_node: 2
  nodes_per_block: 1
  max_blocks: 1
  available_accelerators: null    # GPU count or device IDs

# trust / provenance
provenance: curated               # curated (v1) | community | scraped | plugin-validated (reserved)
last_validated: 2026-06-03
```

- **`MachineProfile.worker_init` is not stored** — in the code it is literally
  `= env_setup` (the same module/venv bash, replayed on the compute worker). The
  builder derives it; the entry must not carry a redundant copy that can drift.
- **`amqp_port` defaults to 443** (matching `MachineProfile`) and is omitted from
  almost every entry. Facilities firewall the default AMQPS port 5671; 443 is the
  near-universal allowed port, so it is set explicitly only on the rare machine
  that needs something else.
- **`account`** is never a catalog field: it is per-user and comes from the
  allocation-selection flow (§5), then feeds `SlurmProvider.account`.
- **Subject scheme:** `"<facility>:<id>"` — e.g. `alcf:polaris`, `nersc:perlmutter`,
  `purdue:anvil`. Readable, namespaced by facility, no URN.
- **Templating:** entries are user-agnostic; `{user}`/`{venv}` are filled from the
  SSH target at provision time, exactly as `anvil_profile()` builds the venv path
  today.
- **Re-validated on read:** the plugin parses every fetched entry through the
  Pydantic schema (UUID format, required fields, known `parser`/`scheduler` enum)
  before it reaches provisioning — defense-in-depth against a malformed entry.
- The field set is intentionally lean; expect to grow it (e.g. `quirks`,
  `status: active|deprecated`, data-transfer paths) as real machines are added.

## 4. Index structure & the `CatalogProvider` seam

**Globus Search:** one index. Each machine is one `GMetaEntry` whose `subject` is
`<facility>:<id>`. Lookup is a direct `get_subject(index_id, subject)` — exact,
fast, no fuzzy ranking to misfire; `post_search` with a query is reserved for the
*discovery* path. `visible_to` is `["public"]` for curated entries (or a Globus
Group UUID for facility-restricted ones — the access control a checked-in file
can't do). Read via `globus_sdk.SearchClient` using **the same Globus Auth
identity the Compute SDK already holds** (adds the `search.api.globus.org` scope,
no new login — see §8 verification). `globus-sdk` becomes a direct dependency
(already transitive via `globus-compute-sdk`).

**The seam** (mirrors the existing `Facility` / `FakeFacility` pattern):

```python
class CatalogProvider(Protocol):
    async def get(self, machine_id: str) -> CatalogEntry | None: ...    # exact lookup -> provisioning
    async def discover(self, query: str) -> list[CatalogSummary]: ...   # list/search machines for the agent
    # propose(...) write-back: deferred (non-goal v1); read-only providers omit it
```

- **`SearchCatalog`** — Globus Search backed (primary). On query error / offline,
  falls back to the bundled seed and serves from a local cache in
  `${CLAUDE_PLUGIN_DATA}` (cache policy: §8).
- **`BundledCatalog`** — reads the checked-in seed YAML. Triple duty: offline
  fallback, the *source* the initial curated machines are ingested from, and a
  zero-network test fixture.
- **`FakeCatalog`** — in-memory, for unit tests (like `FakeFacility`).

A `make_catalog()` in `server.py` selects the provider from env
(`HPC_BRIDGE_SEARCH_INDEX=<uuid>` …), exactly like `make_facility()`.

## 5. Retrieval & the allocation-selection flow

Machine selection becomes **agent-driven** (today it is fixed by
`HPC_BRIDGE_FACILITY`). The plugin runs a small state machine, each gap returning a
structured "here's what I need next," never a hang.

**Design principle — Compute-first; SSH is irreducible-bootstrap only.** SSH does
*only* what cannot be done without a Compute presence: seed credentials and launch
the endpoint daemon on a cold machine. Everything after that — allocation
discovery, the worker canary/wait, and dispatch — runs **through Compute**, on the
`login` shape (a Parsl `LocalProvider` that runs on the login node with no
scheduler and **no allocation required**). This is already how the code discovers
and waits (PR #13, "endpoint-first discovery — discover/wait through the login
shape, not SSH"); allocation discovery is the same move.

```
list_facilities(query?)  -> discover machines in the catalog

connect_facility(machine):
    entry  = catalog.get(machine)
    runner = ensure_login_shape_up(entry)   # endpoint already online (MEP, or reused
                                            #   personal endpoint per PR #12 "SSH-once")? use it.
                                            #   otherwise SSH cold-bootstrap the daemon, then
                                            #   the login shape — NO account needed to start it.
    out    = await runner.run(entry.allocation.command)   # ShellFunction on the login shape (via Compute)
    allocs = PARSERS[entry.allocation.parser](out)        # deterministic, in plugin code
    return {phase: "needs_account",
            allocations: [{name, balance, units}],
            provenance, last_validated, notice: "pick an allocation"}

ensure_endpoint_up(account=...) -> provision the slurm shape with the chosen
                                   allocation (account -> SlurmProvider.account)
```

- **Two ways to get compute, chosen by the entry** — but they differ *only* in
  whether a usable endpoint already exists, not in how discovery runs:
  - **Endpoint already online** — a present `compute_mep_uuid` (+ user mapping), or
    a reused personal endpoint (PR #12): **no SSH at all**. Run allocation discovery
    as a `ShellFunction` straight through that endpoint's login shape.
  - **Cold machine** — SSH performs the one irreducible step (seed creds + start the
    daemon via `RemoteEndpointCLI`); the login shape comes up needing no allocation,
    and discovery proceeds over Compute exactly as above.
- There is **no chicken-and-egg**: the login shape requires no Slurm account, so
  allocations can be listed before any allocation is chosen. The earlier claim that
  discovery must run over the bootstrap SSH connection is superseded by this design.

### Allocation output parsing

`entry.allocation.parser` names a **deterministic, plugin-side** parser
(`sbank` / `iris` / `mybalance`), not free-text guidance handed to the model.
The `ShellFunction` returns the command's stdout to the plugin, which parses it in
code — keeping inference out of the loop the catalog exists to remove. A new
machine with a new output format adds one parser function (generalize at the 4th
machine, per the project's instinct, but deterministically).

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
  audit trail for trusted entries (this is also where per-entry validation notes
  like "worker ran on compute node a006" belong — in the commit message, not the
  entry).
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

- **Unit (hermetic):** `CatalogEntry` schema (valid/invalid, UUID format, known
  `parser`/`scheduler` enums, `{user}`/`{venv}` templating, `compute`/`defaults`
  split → `MachineProfile`); `BundledCatalog` get/discover against a fixture YAML;
  `FakeCatalog`; `SearchCatalog` with a **mocked `SearchClient`** (subject → entry;
  query error → bundled fallback + cache write); allocation **parsers** for each
  command style (`sbank`/`iris`/`mybalance` sample outputs → list); the new tools
  (`list_facilities`, `connect_facility`) and the `needs_account` state machine
  wired with `FakeCatalog` + a fake runner (allocation discovery returns canned
  stdout — no SSH, no live Compute); the ingest script's validate-and-upsert
  against a mocked client.
- **Integration (gated, like the existing live tests):** a real `get_subject`
  against a throwaway index; real allocation discovery **through the login shape**
  (a `ShellFunction` running the allocation command) behind the existing
  `HPC_BRIDGE_RUN_INTEGRATION` gate.

## 8. Dependencies & resolved/open questions

- **New dependency:** `globus-sdk` (for `SearchClient`) — already transitive via
  `globus-compute-sdk`; promote to a direct dependency.
- **Resolved:**
  - *Allocation output parsing* — a named, deterministic plugin-side parser keyed
    by `entry.allocation.parser` (not a free-text `parse_hint`). See §5.
  - *`SearchCatalog` cache policy* — the local cache is **purely an offline
    fallback**: always prefer a live `get_subject`, write-through on success, and
    fall back to the cache only on error. No TTL, no staleness window to reason
    about in v1.
  - *The `search.api.globus.org` scope / second-login question* — **answered live
    (2026-06-25): a second login IS required.** The Compute SDK's `GlobusApp` does
    not hold the search scope by default, so `app.get_authorizer("search.api.globus.org")`
    has no token and triggers a fresh login. The fix is to construct
    `SearchClient(app=Client().app)` (which *registers* the scope on the app) and grant
    it via **one interactive login** — done once by the curator running
    `hpc-bridge-catalog` (which calls `app.login()` when `app.login_required()`). After
    that the token is cached and reuse is silent. The server-side `_make_search_client`
    **never** prompts: it checks `app.login_required()` and raises so `make_catalog()`
    falls back to the bundled catalog (a blocking prompt on the MCP stdio channel would
    hang the server). Verified against a live index (`6ff95fb8-…`, "hpc-bridge-test"):
    the data round-trip (serialize → `GMetaList` → ingest → query) works; the
    `transfer_endpoint_uuid` in the seed is still the placeholder and must be replaced
    before the entry is treated as production.
- **Open / verify at implementation:**
  - Wiring `SearchCatalog` into the server lifespan (Plan 2) must keep the
    non-interactive guarantee above: the read path may need a cached search token, and
    must degrade to the bundled catalog rather than attempt an interactive login.
