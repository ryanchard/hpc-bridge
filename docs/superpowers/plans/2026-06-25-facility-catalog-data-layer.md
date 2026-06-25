# Facility Catalog — Data Layer Implementation Plan (Plan 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the read-only facility catalog data layer — a validated `CatalogEntry` schema plus `Fake`/`Bundled`/`Search` providers, a seed entry, an env-driven selector, and a curator ingest script — so machines become data the plugin can look up.

**Architecture:** A new `hpc_bridge.catalog` package. `CatalogEntry` (Pydantic v2) is a superset of `MachineProfile`, split into pinned `compute:` vs overridable `defaults:`. Providers implement a `CatalogProvider` Protocol mirroring the existing `Facility`/`FakeFacility` seam: `BundledCatalog` reads packaged seed YAML, `SearchCatalog` reads Globus Search with bundled+cache fallback, `FakeCatalog` is in-memory for tests. `make_catalog()` in `server.py` selects by env, exactly like `make_facility()`. A standalone ingest script validates seed YAML and upserts `GMetaEntry`s.

**Tech Stack:** Python ≥3.11, Pydantic v2, PyYAML, `globus-sdk` (new direct dep, for `SearchClient`), pytest + pytest-asyncio (`asyncio_mode = "auto"`).

**Spec:** `docs/design/facility-catalog.md` (§3 schema, §4 providers, §6 ingest, §7 testing).

**Scope boundary:** This plan delivers lookup/discovery of validated entries and the curator ingest path. It does NOT build the MCP tools, allocation parsers, or provisioning wiring — that is Plan 2 (`facility-catalog-allocation-flow`), previewed at the end. `CatalogEntry.profile_kwargs()` is provided here as the hand-off seam Plan 2 consumes.

**One schema refinement introduced here (flag for reviewer):** the spec's subject scheme is `"<facility>:<id>"` with examples `purdue:anvil`, but the `facility` field holds a display string (`"Purdue / ACCESS"`). These are two different things, so this plan adds an explicit `facility_key` slug field (e.g. `purdue`) for the subject, distinct from the display `facility`. If you'd rather derive the slug, say so before execution.

---

## File Structure

**Create:**
- `src/hpc_bridge/catalog/__init__.py` — package exports
- `src/hpc_bridge/catalog/entry.py` — `Allocation`, `Compute`, `Defaults`, `CatalogEntry`, `CatalogSummary`
- `src/hpc_bridge/catalog/base.py` — `CatalogProvider` Protocol
- `src/hpc_bridge/catalog/bundled.py` — `BundledCatalog`
- `src/hpc_bridge/catalog/search.py` — `SearchCatalog`
- `src/hpc_bridge/catalog/seed/anvil.yaml` — first curated entry (packaged with the wheel)
- `src/hpc_bridge/catalog/ingest.py` — `ingest()` + `main()` console entry
- `tests/test_catalog_entry.py`
- `tests/test_catalog_bundled.py`
- `tests/test_catalog_search.py`
- `tests/test_catalog_make.py`
- `tests/test_catalog_ingest.py`
- `tests/catalog_fixtures/two_machines.yaml` — test fixture seed dir

**Modify:**
- `tests/fakes.py` — add `FakeCatalog`
- `src/hpc_bridge/server.py` — add `make_catalog()`
- `pyproject.toml` — add `globus-sdk` dep, `hpc-bridge-catalog` console script, package the seed YAML
- `tests/test_catalog_make.py` covers `make_catalog`; contract test for `FakeCatalog` goes in `tests/test_catalog_bundled.py`

---

## Task 1: `CatalogEntry` schema

**Files:**
- Create: `src/hpc_bridge/catalog/__init__.py`
- Create: `src/hpc_bridge/catalog/entry.py`
- Test: `tests/test_catalog_entry.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_catalog_entry.py
import datetime

import pytest
from pydantic import ValidationError

from hpc_bridge.catalog.entry import CatalogEntry

VALID_UUID = "11111111-2222-3333-4444-555555555555"


def _entry(**over):
    base = {
        "id": "anvil",
        "facility_key": "purdue",
        "facility": "Purdue / ACCESS",
        "description": "Anvil CPU cluster",
        "display_name": "HPC-Bridge Anvil",
        "transfer_endpoint_uuid": VALID_UUID,
        "ssh_host": "anvil.rcac.purdue.edu",
        "allocation": {"command": "mybalance", "parser": "mybalance"},
        "compute": {
            "scheduler": "slurm",
            "interface": "ib0",
            "env_setup": "module load x && source {venv}/bin/activate",
            "scratch_root": "/anvil/scratch/{user}/.hpc-bridge",
        },
        "defaults": {"partition": "debug"},
        "last_validated": "2026-06-03",
    }
    base.update(over)
    return base


def test_valid_entry_parses_and_applies_defaults():
    e = CatalogEntry.model_validate(_entry())
    assert e.id == "anvil"
    assert e.compute.amqp_port == 443             # defaulted
    assert e.compute.endpoint_name == "hpc-bridge"  # defaulted
    assert e.defaults.walltime == "00:30:00"      # defaulted
    assert e.auth_method == "ssh-key"             # defaulted
    assert e.provenance == "curated"              # defaulted
    assert e.compute_mep_uuid is None             # optional
    assert e.last_validated == datetime.date(2026, 6, 3)


def test_subject_is_facility_key_colon_id():
    assert CatalogEntry.model_validate(_entry()).subject == "purdue:anvil"


def test_summary_is_agent_safe_subset():
    s = CatalogEntry.model_validate(_entry()).summary()
    assert s.subject == "purdue:anvil"
    assert s.display_name == "HPC-Bridge Anvil"
    # summary must NOT leak executable config
    assert not hasattr(s, "env_setup")


def test_bad_uuid_rejected():
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(_entry(transfer_endpoint_uuid="not-a-uuid"))


def test_unknown_parser_rejected():
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(
            _entry(allocation={"command": "x", "parser": "bogus"})
        )


def test_profile_kwargs_maps_every_machineprofile_field():
    kw = CatalogEntry.model_validate(_entry()).profile_kwargs()
    # superset-of-MachineProfile contract; account/worker_init are intentionally absent
    expected = {
        "name", "endpoint_name", "display_name", "env_setup", "interface",
        "partition", "walltime", "max_workers_per_node", "nodes_per_block",
        "max_blocks", "available_accelerators", "amqp_port", "scheduler_options",
        "scratch_root",
    }
    assert set(kw) == expected
    assert "account" not in kw
    assert "worker_init" not in kw
    assert kw["interface"] == "ib0"
    assert kw["name"] == "anvil"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/ryan/Work/Projects/Compute/hpc-bridge && python -m pytest tests/test_catalog_entry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hpc_bridge.catalog'`

- [ ] **Step 3: Create the package and schema**

```python
# src/hpc_bridge/catalog/__init__.py
from __future__ import annotations

from .entry import Allocation, CatalogEntry, CatalogSummary, Compute, Defaults

__all__ = ["Allocation", "CatalogEntry", "CatalogSummary", "Compute", "Defaults"]
```

```python
# src/hpc_bridge/catalog/entry.py
from __future__ import annotations

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Allocation(BaseModel):
    """How to LIST a user's allocations on this machine — not the allocations themselves.

    `parser` names a deterministic, plugin-side parser (Plan 2). The command's stdout is
    parsed in code, never handed to the model — inference is exactly what the catalog removes.
    """

    command: str
    parser: Literal["sbank", "iris", "mybalance"]


class Compute(BaseModel):
    """Machine-invariant facts the plugin PINS — the user/agent cannot override these.

    Getting one wrong breaks the endpoint silently (e.g. the wrong `interface` means workers
    never phone home), so this is the same "look up, never infer" category as the UUIDs.
    """

    scheduler: Literal["slurm", "pbs", "lsf"]
    interface: str  # address_by_interface ifname (e.g. ib0)
    env_setup: str  # bash that puts globus-compute-endpoint on PATH (module + venv)
    scratch_root: str  # session-shell root on the shared filesystem; {user} templated
    endpoint_name: str = "hpc-bridge"  # registration / on-disk dir name
    amqp_port: int = 443  # facilities firewall AMQPS 5671; 443 is the near-universal allowed port
    scheduler_options: str | None = None  # optional machine #SBATCH constraints


class Defaults(BaseModel):
    """Per-run tunables the agent/user MAY override at submit time via user_endpoint_config."""

    partition: str
    walltime: str = "00:30:00"
    max_workers_per_node: int = 2
    nodes_per_block: int = 1
    max_blocks: int = 1
    available_accelerators: int | list[str] | None = None


class CatalogSummary(BaseModel):
    """The agent-safe view of an entry — identity only, no executable config or raw UUIDs."""

    subject: str
    id: str
    facility: str
    description: str
    display_name: str
    provenance: str
    last_validated: datetime.date


class CatalogEntry(BaseModel):
    """One machine. A superset of MachineProfile; `profile_kwargs()` is the binding seam."""

    # identity
    id: str
    facility_key: str  # short slug for the subject, e.g. "purdue" (distinct from display `facility`)
    facility: str  # display, e.g. "Purdue / ACCESS"
    description: str
    display_name: str

    # identifiers (look up, never infer)
    compute_mep_uuid: str | None = None
    transfer_endpoint_uuid: str

    # access
    ssh_host: str
    auth_method: Literal["ssh-key", "mfa-otp", "sfapi"] = "ssh-key"  # only ssh-key wired in v1

    allocation: Allocation
    compute: Compute
    defaults: Defaults

    # trust / provenance
    provenance: Literal["curated", "community", "scraped", "plugin-validated"] = "curated"
    last_validated: datetime.date

    @field_validator("compute_mep_uuid", "transfer_endpoint_uuid")
    @classmethod
    def _valid_uuid(cls, v: str | None) -> str | None:
        if v is None:
            return v
        uuid.UUID(v)  # raises ValueError -> ValidationError on a malformed UUID
        return v

    @property
    def subject(self) -> str:
        return f"{self.facility_key}:{self.id}"

    def summary(self) -> CatalogSummary:
        return CatalogSummary(
            subject=self.subject,
            id=self.id,
            facility=self.facility,
            description=self.description,
            display_name=self.display_name,
            provenance=self.provenance,
            last_validated=self.last_validated,
        )

    def profile_kwargs(self) -> dict:
        """Constructor kwargs for MachineProfile (Plan 2 builds the profile from these).

        `account` is intentionally absent — it is per-user, from allocation selection.
        `worker_init` is absent — in the code it is derived as `= env_setup`.
        """
        return {
            "name": self.id,
            "endpoint_name": self.compute.endpoint_name,
            "display_name": self.display_name,
            "env_setup": self.compute.env_setup,
            "interface": self.compute.interface,
            "partition": self.defaults.partition,
            "walltime": self.defaults.walltime,
            "max_workers_per_node": self.defaults.max_workers_per_node,
            "nodes_per_block": self.defaults.nodes_per_block,
            "max_blocks": self.defaults.max_blocks,
            "available_accelerators": self.defaults.available_accelerators,
            "amqp_port": self.compute.amqp_port,
            "scheduler_options": self.compute.scheduler_options,
            "scratch_root": self.compute.scratch_root,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/ryan/Work/Projects/Compute/hpc-bridge && python -m pytest tests/test_catalog_entry.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
cd /Users/ryan/Work/Projects/Compute/hpc-bridge
git add src/hpc_bridge/catalog/__init__.py src/hpc_bridge/catalog/entry.py tests/test_catalog_entry.py
git commit -m "feat(catalog): CatalogEntry schema (compute/defaults split, profile_kwargs seam)"
```

---

## Task 2: `CatalogProvider` Protocol + `FakeCatalog`

**Files:**
- Create: `src/hpc_bridge/catalog/base.py`
- Modify: `tests/fakes.py`
- Test: `tests/test_catalog_bundled.py` (contract test lives here; BundledCatalog added in Task 3)

- [ ] **Step 1: Write the failing contract test**

```python
# tests/test_catalog_bundled.py
from hpc_bridge.catalog.base import CatalogProvider
from tests.fakes import FakeCatalog, fake_entry


async def test_fake_catalog_satisfies_protocol():
    c = FakeCatalog([fake_entry(id="anvil", facility_key="purdue")])
    assert isinstance(c, CatalogProvider)


async def test_fake_catalog_get_by_id_and_subject_and_alias():
    c = FakeCatalog(
        [fake_entry(id="anvil", facility_key="purdue")],
        aliases={"anvil": ["anvil.x.edu"]},
    )
    assert (await c.get("anvil")).id == "anvil"
    assert (await c.get("purdue:anvil")).id == "anvil"
    assert (await c.get("anvil.x.edu")).id == "anvil"
    assert await c.get("nope") is None


async def test_fake_catalog_discover_filters_by_query():
    c = FakeCatalog([
        fake_entry(id="anvil", facility_key="purdue", description="CPU cluster"),
        fake_entry(id="polaris", facility_key="alcf", description="GPU machine"),
    ])
    got = {s.id for s in await c.discover("gpu")}
    assert got == {"polaris"}
    assert {s.id for s in await c.discover("")} == {"anvil", "polaris"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_catalog_bundled.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hpc_bridge.catalog.base'`

- [ ] **Step 3: Write the Protocol**

```python
# src/hpc_bridge/catalog/base.py
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .entry import CatalogEntry, CatalogSummary


@runtime_checkable
class CatalogProvider(Protocol):
    async def get(self, machine_id: str) -> CatalogEntry | None:
        """Exact lookup by id, subject (`<facility_key>:<id>`), or alias -> provisioning input."""
        ...

    async def discover(self, query: str) -> list[CatalogSummary]:
        """List/search machines for the agent. Empty query -> all entries."""
        ...
    # propose(...) write-back is deferred (non-goal v1); read-only providers omit it.
```

- [ ] **Step 4: Add `FakeCatalog` + `fake_entry` to the shared fakes**

Append to `tests/fakes.py`:

```python
from hpc_bridge.catalog.entry import CatalogEntry, CatalogSummary

_FAKE_UUID = "11111111-2222-3333-4444-555555555555"


def fake_entry(*, id: str, facility_key: str, description: str = "a machine") -> CatalogEntry:
    """Build a valid CatalogEntry for tests. Aliases are passed to FakeCatalog(aliases=...),
    not to the entry — the schema has no alias field; aliases are a loader/index concern."""
    return CatalogEntry.model_validate({
        "id": id,
        "facility_key": facility_key,
        "facility": facility_key.upper(),
        "description": description,
        "display_name": f"HPC-Bridge {id}",
        "transfer_endpoint_uuid": _FAKE_UUID,
        "ssh_host": f"{id}.example.edu",
        "allocation": {"command": "mybalance", "parser": "mybalance"},
        "compute": {
            "scheduler": "slurm", "interface": "ib0",
            "env_setup": "module load x", "scratch_root": f"/scratch/{{user}}/{id}",
        },
        "defaults": {"partition": "debug"},
        "last_validated": "2026-06-03",
    })


class FakeCatalog:
    """In-memory CatalogProvider for unit tests (mirrors FakeFacility)."""

    def __init__(self, entries: list[CatalogEntry], aliases: dict[str, list[str]] | None = None):
        self._by_subject = {e.subject: e for e in entries}
        self._by_id = {e.id: e for e in entries}
        self._aliases = aliases or {}

    async def get(self, machine_id: str) -> CatalogEntry | None:
        if machine_id in self._by_subject:
            return self._by_subject[machine_id]
        if machine_id in self._by_id:
            return self._by_id[machine_id]
        for ent_id, names in self._aliases.items():
            if machine_id in names:
                return self._by_id.get(ent_id)
        return None

    async def discover(self, query: str) -> list[CatalogSummary]:
        q = query.lower().strip()
        out = []
        for e in self._by_id.values():
            hay = f"{e.id} {e.facility} {e.description} {e.display_name}".lower()
            if not q or q in hay:
                out.append(e.summary())
        return out
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_catalog_bundled.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add src/hpc_bridge/catalog/base.py tests/fakes.py tests/test_catalog_bundled.py
git commit -m "feat(catalog): CatalogProvider Protocol + FakeCatalog test double"
```

---

## Task 3: `BundledCatalog` (reads packaged seed YAML)

**Files:**
- Create: `src/hpc_bridge/catalog/bundled.py`
- Create: `tests/catalog_fixtures/two_machines.yaml`
- Test: `tests/test_catalog_bundled.py` (extend)

- [ ] **Step 1: Write the fixture seed**

```yaml
# tests/catalog_fixtures/two_machines.yaml
- id: anvil
  facility_key: purdue
  facility: "Purdue / ACCESS"
  description: "Anvil CPU cluster"
  display_name: "HPC-Bridge Anvil"
  aliases: [anvil.rcac.purdue.edu]
  transfer_endpoint_uuid: "11111111-2222-3333-4444-555555555555"
  ssh_host: anvil.rcac.purdue.edu
  allocation: {command: mybalance, parser: mybalance}
  compute:
    scheduler: slurm
    interface: ib0
    env_setup: "module load anaconda && source {venv}/bin/activate"
    scratch_root: "/anvil/scratch/{user}/.hpc-bridge"
  defaults: {partition: debug}
  last_validated: 2026-06-03
- id: polaris
  facility_key: alcf
  facility: "ALCF"
  description: "Polaris GPU machine"
  display_name: "HPC-Bridge Polaris"
  transfer_endpoint_uuid: "99999999-8888-7777-6666-555555555555"
  ssh_host: polaris.alcf.anl.gov
  allocation: {command: sbank, parser: sbank}
  compute:
    scheduler: pbs
    interface: bond0
    env_setup: "module use /soft/modulefiles && module load conda"
    scratch_root: "/eagle/{user}/.hpc-bridge"
  defaults: {partition: debug}
  last_validated: 2026-06-03
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_catalog_bundled.py`:

```python
from pathlib import Path

from hpc_bridge.catalog.bundled import BundledCatalog

FIX = Path(__file__).parent / "catalog_fixtures"


async def test_bundled_loads_dir_and_gets_by_subject_id_alias():
    c = BundledCatalog(FIX / "two_machines.yaml")
    assert (await c.get("anvil")).compute.interface == "ib0"
    assert (await c.get("purdue:anvil")).id == "anvil"
    assert (await c.get("anvil.rcac.purdue.edu")).id == "anvil"   # alias
    assert await c.get("absent") is None


async def test_bundled_discover_filters():
    c = BundledCatalog(FIX / "two_machines.yaml")
    assert {s.id for s in await c.discover("gpu")} == {"polaris"}
    assert {s.id for s in await c.discover("")} == {"anvil", "polaris"}


async def test_bundled_rejects_a_malformed_entry():
    import pytest
    bad = FIX / "bad.yaml"
    bad.write_text("- id: x\n")  # missing required fields
    try:
        with pytest.raises(Exception):
            BundledCatalog(bad)
    finally:
        bad.unlink()
```

- [ ] **Step 3: Run to verify fail**

Run: `python -m pytest tests/test_catalog_bundled.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hpc_bridge.catalog.bundled'`

- [ ] **Step 4: Implement `BundledCatalog`**

```python
# src/hpc_bridge/catalog/bundled.py
from __future__ import annotations

from importlib import resources
from pathlib import Path

import yaml

from .entry import CatalogEntry, CatalogSummary


def _default_seed_path() -> Path:
    """The seed YAML packaged inside the wheel (src/hpc_bridge/catalog/seed/)."""
    return Path(str(resources.files("hpc_bridge.catalog") / "seed"))


class BundledCatalog:
    """Reads checked-in seed YAML. Offline fallback, ingest source, and test fixture.

    Accepts either a single .yaml file (a list of entries) or a directory of .yaml files.
    Aliases are a loader concern (the schema has no alias field): they index extra lookup keys.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else _default_seed_path()
        self._by_subject: dict[str, CatalogEntry] = {}
        self._by_id: dict[str, CatalogEntry] = {}
        self._alias_to_id: dict[str, str] = {}
        for raw in self._load_raw(self._path):
            aliases = raw.pop("aliases", []) or []
            entry = CatalogEntry.model_validate(raw)  # raises on a malformed entry
            self._by_subject[entry.subject] = entry
            self._by_id[entry.id] = entry
            for a in aliases:
                self._alias_to_id[a] = entry.id

    @staticmethod
    def _load_raw(path: Path) -> list[dict]:
        files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
        out: list[dict] = []
        for f in files:
            loaded = yaml.safe_load(f.read_text()) or []
            out.extend(loaded if isinstance(loaded, list) else [loaded])
        return out

    async def get(self, machine_id: str) -> CatalogEntry | None:
        if machine_id in self._by_subject:
            return self._by_subject[machine_id]
        if machine_id in self._by_id:
            return self._by_id[machine_id]
        if machine_id in self._alias_to_id:
            return self._by_id[self._alias_to_id[machine_id]]
        return None

    async def discover(self, query: str) -> list[CatalogSummary]:
        q = query.lower().strip()
        out = []
        for e in self._by_id.values():
            hay = f"{e.id} {e.facility} {e.description} {e.display_name}".lower()
            if not q or q in hay:
                out.append(e.summary())
        return out
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_catalog_bundled.py -q`
Expected: PASS (6 passed)

- [ ] **Step 6: Commit**

```bash
git add src/hpc_bridge/catalog/bundled.py tests/catalog_fixtures/two_machines.yaml tests/test_catalog_bundled.py
git commit -m "feat(catalog): BundledCatalog — load + validate seed YAML, lookup by subject/id/alias"
```

---

## Task 4: Real Anvil seed entry (packaged)

**Files:**
- Create: `src/hpc_bridge/catalog/seed/anvil.yaml`
- Modify: `pyproject.toml` (ensure the seed ships in the wheel)
- Test: `tests/test_catalog_bundled.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_catalog_bundled.py`:

```python
async def test_default_bundled_catalog_has_anvil():
    c = BundledCatalog()  # default packaged seed dir
    anvil = await c.get("purdue:anvil")
    assert anvil is not None
    assert anvil.compute.interface == "ib0"
    assert anvil.compute.amqp_port == 443
    assert anvil.allocation.parser == "mybalance"
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_catalog_bundled.py::test_default_bundled_catalog_has_anvil -q`
Expected: FAIL — `get("purdue:anvil")` returns `None` (no seed dir yet)

- [ ] **Step 3: Write the seed entry**

```yaml
# src/hpc_bridge/catalog/seed/anvil.yaml
# Curated. Mirrors anvil_profile() in facility/remote.py (validated 2026-06-03, worker on a006).
- id: anvil
  facility_key: purdue
  facility: "Purdue / ACCESS"
  description: "Anvil CPU cluster"
  display_name: "HPC-Bridge Anvil"
  aliases: [anvil.rcac.purdue.edu]
  compute_mep_uuid: null            # no facility MEP wired; personal-endpoint bootstrap path
  # TODO(curator): replace with Anvil's real Globus Transfer collection UUID before ingest.
  transfer_endpoint_uuid: "00000000-0000-0000-0000-000000000000"
  ssh_host: anvil.rcac.purdue.edu
  auth_method: ssh-key
  allocation: {command: mybalance, parser: mybalance}
  compute:
    scheduler: slurm
    interface: ib0
    env_setup: "module load anaconda/2024.02-py311 && source {venv}/bin/activate"
    scratch_root: "/anvil/scratch/{user}/.hpc-bridge"
    # amqp_port: 443 -> default
    # endpoint_name: hpc-bridge -> default
  defaults:
    partition: debug
    walltime: "00:30:00"
    max_workers_per_node: 2
    nodes_per_block: 1
    max_blocks: 1
  provenance: curated
  last_validated: 2026-06-03
```

> The `transfer_endpoint_uuid` placeholder is a real-format zero-UUID so the schema validates; it MUST be replaced with Anvil's actual collection UUID before the entry is ingested to a live index (the ingest script, Task 7, does not invent UUIDs).

- [ ] **Step 4: Verify the seed ships in the wheel (no pyproject change needed)**

Hatchling's existing `packages = ["src/hpc_bridge"]` already includes **all** files under
`src/hpc_bridge/` — including non-`.py` data like the seed YAML. Do NOT add a
`force-include` stanza: it duplicates the same path and makes the wheel build fail with
"A second file is being added to the wheel archive at the same path". Instead, verify:

```bash
uv build --wheel
python -c "import zipfile,glob; z=zipfile.ZipFile(sorted(glob.glob('dist/*.whl'))[-1]); print([n for n in z.namelist() if 'seed' in n])"
rm -rf dist build   # don't commit build artifacts
```
Expected: the build succeeds and the printed list contains `hpc_bridge/catalog/seed/anvil.yaml`.

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_catalog_bundled.py -q`
Expected: PASS (7 passed)

- [ ] **Step 6: Commit**

```bash
git add src/hpc_bridge/catalog/seed/anvil.yaml pyproject.toml tests/test_catalog_bundled.py
git commit -m "feat(catalog): seed Anvil entry; package seed YAML in the wheel"
```

---

## Task 5: `SearchCatalog` (Globus Search + bundled/cache fallback)

**Files:**
- Create: `src/hpc_bridge/catalog/search.py`
- Modify: `pyproject.toml` (add `globus-sdk` dependency)
- Test: `tests/test_catalog_search.py`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, change the core `dependencies` line to add `globus-sdk` (pure-Python, cross-platform — unlike `globus-compute-endpoint`):

```toml
dependencies = ["mcp>=1.23,<2", "pydantic>=2", "pyyaml>=6", "globus-sdk>=3,<4"]
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_catalog_search.py
import json
from pathlib import Path

from hpc_bridge.catalog.search import SearchCatalog
from tests.fakes import FakeCatalog, fake_entry

VALID_UUID = "11111111-2222-3333-4444-555555555555"


def _gmeta(entry):
    # Mimic a Globus Search get_subject response: entry content under entries[].content
    return {"entries": [{"content": json.loads(entry.model_dump_json())}]}


class _FakeSearchClient:
    def __init__(self, subjects=None, fail=False):
        self._subjects = subjects or {}
        self._fail = fail
        self.calls = []

    def get_subject(self, index_id, subject):
        self.calls.append((index_id, subject))
        if self._fail:
            raise RuntimeError("search offline")
        if subject not in self._subjects:
            return {"entries": []}  # a simple miss
        return _gmeta(self._subjects[subject])


async def test_search_get_hits_live_and_writes_through_cache(tmp_path):
    e = fake_entry(id="anvil", facility_key="purdue")
    client = _FakeSearchClient(subjects={"purdue:anvil": e})
    c = SearchCatalog(index_id="idx", client=client,
                      fallback=FakeCatalog([]), cache_dir=tmp_path)
    got = await c.get("purdue:anvil")
    assert got.id == "anvil"
    assert client.calls == [("idx", "purdue:anvil")]
    assert (tmp_path / "purdue:anvil.json").exists()  # write-through


async def test_search_falls_back_to_cache_then_bundled_on_error(tmp_path):
    e = fake_entry(id="anvil", facility_key="purdue")
    # prime the cache
    (tmp_path / "purdue:anvil.json").write_text(e.model_dump_json())
    client = _FakeSearchClient(fail=True)
    c = SearchCatalog(index_id="idx", client=client,
                      fallback=FakeCatalog([]), cache_dir=tmp_path)
    got = await c.get("purdue:anvil")
    assert got.id == "anvil"  # served from cache, not the failing client


async def test_search_falls_back_to_bundled_when_no_cache(tmp_path):
    e = fake_entry(id="anvil", facility_key="purdue")
    client = _FakeSearchClient(fail=True)
    c = SearchCatalog(index_id="idx", client=client,
                      fallback=FakeCatalog([e]), cache_dir=tmp_path)
    got = await c.get("purdue:anvil")
    assert got.id == "anvil"  # served from the bundled fallback


async def test_search_miss_returns_none(tmp_path):
    client = _FakeSearchClient(subjects={})
    c = SearchCatalog(index_id="idx", client=client,
                      fallback=FakeCatalog([]), cache_dir=tmp_path)
    assert await c.get("purdue:absent") is None
```

- [ ] **Step 3: Run to verify fail**

Run: `python -m pytest tests/test_catalog_search.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hpc_bridge.catalog.search'`

- [ ] **Step 4: Implement `SearchCatalog`**

```python
# src/hpc_bridge/catalog/search.py
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .base import CatalogProvider
from .entry import CatalogEntry, CatalogSummary


class SearchCatalog:
    """Globus Search backed (primary).

    Cache policy (spec §8): the local cache is PURELY an offline fallback — always prefer a
    live get_subject, write-through on success, fall back to cache then the bundled provider
    only on error. No TTL.
    """

    def __init__(self, index_id: str, client, fallback: CatalogProvider, cache_dir: Path) -> None:
        self._index_id = index_id
        self._client = client
        self._fallback = fallback
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_file(self, subject: str) -> Path:
        return self._cache_dir / f"{subject}.json"

    async def get(self, machine_id: str) -> CatalogEntry | None:
        subject = machine_id  # exact-subject lookup; id/alias resolution is a discover concern
        try:
            resp = await asyncio.to_thread(self._client.get_subject, self._index_id, subject)
        except Exception:
            return await self._from_cache_or_fallback(subject, machine_id)
        entries = resp.get("entries") or []
        if not entries:
            return None
        entry = CatalogEntry.model_validate(entries[0]["content"])  # re-validate on read
        self._cache_file(subject).write_text(entry.model_dump_json())  # write-through
        return entry

    async def _from_cache_or_fallback(self, subject: str, machine_id: str) -> CatalogEntry | None:
        cached = self._cache_file(subject)
        if cached.exists():
            return CatalogEntry.model_validate(json.loads(cached.read_text()))
        return await self._fallback.get(machine_id)

    async def discover(self, query: str) -> list[CatalogSummary]:
        try:
            resp = await asyncio.to_thread(
                self._client.post_search, self._index_id, {"q": query or "*"}
            )
        except Exception:
            return await self._fallback.discover(query)
        out = []
        for gmeta in resp.get("gmeta", []):
            content = gmeta["entries"][0]["content"]
            out.append(CatalogEntry.model_validate(content).summary())
        return out
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_catalog_search.py -q`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add src/hpc_bridge/catalog/search.py pyproject.toml tests/test_catalog_search.py
git commit -m "feat(catalog): SearchCatalog — live get_subject, write-through cache, bundled fallback"
```

---

## Task 6: `make_catalog()` selector in `server.py`

**Files:**
- Modify: `src/hpc_bridge/server.py`
- Test: `tests/test_catalog_make.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_catalog_make.py
import importlib

from hpc_bridge.catalog.bundled import BundledCatalog
from hpc_bridge.catalog.search import SearchCatalog


def _make(monkeypatch, **env):
    import hpc_bridge.server as server
    for k in ("HPC_BRIDGE_SEARCH_INDEX",):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    importlib.reload(server)
    return server.make_catalog()


def test_make_catalog_defaults_to_bundled(monkeypatch):
    assert isinstance(_make(monkeypatch), BundledCatalog)


def test_make_catalog_uses_search_when_index_set(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    c = _make(monkeypatch, HPC_BRIDGE_SEARCH_INDEX="idx-uuid")
    assert isinstance(c, SearchCatalog)
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_catalog_make.py -q`
Expected: FAIL — `AttributeError: module 'hpc_bridge.server' has no attribute 'make_catalog'`

- [ ] **Step 3: Add `make_catalog()` to `server.py`**

Add near `make_facility()` (after line ~95). Place the imports at the top of the new function to keep `server` import-light:

```python
def make_catalog():
    """Select the catalog provider from env, mirroring make_facility().

    HPC_BRIDGE_SEARCH_INDEX set -> SearchCatalog (Globus Search) with bundled+cache fallback.
    Otherwise -> BundledCatalog (the packaged seed YAML).
    """
    from .catalog.bundled import BundledCatalog

    index = os.environ.get("HPC_BRIDGE_SEARCH_INDEX", "").strip()
    if not index:
        return BundledCatalog()

    from globus_compute_sdk import Client  # reuse the Compute SDK's authorizer (no new login)
    from globus_sdk import SearchClient

    from .catalog.search import SearchCatalog

    authorizer = Client().login_manager.get_authorizer("search.api.globus.org")
    client = SearchClient(authorizer=authorizer)
    cache_dir = Path(os.environ.get("CLAUDE_PLUGIN_DATA", str(Path.home() / ".hpc-bridge"))) / "catalog-cache"
    return SearchCatalog(index_id=index, client=client, fallback=BundledCatalog(), cache_dir=cache_dir)
```

> The exact authorizer accessor (`login_manager.get_authorizer`) is the spec §8 open item — confirm it composes with the Compute SDK token cache and triggers no second login. If the accessor differs in the installed SDK version, adjust here; the unit tests inject a fake client and do not exercise this path.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_catalog_make.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/hpc_bridge/server.py tests/test_catalog_make.py
git commit -m "feat(catalog): make_catalog() env selector (BundledCatalog | SearchCatalog)"
```

---

## Task 7: Curator ingest script

**Files:**
- Create: `src/hpc_bridge/catalog/ingest.py`
- Modify: `pyproject.toml` (`hpc-bridge-catalog` console script)
- Test: `tests/test_catalog_ingest.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_catalog_ingest.py
from pathlib import Path

from hpc_bridge.catalog.ingest import ingest

FIX = Path(__file__).parent / "catalog_fixtures" / "two_machines.yaml"


class _FakeIngestClient:
    def __init__(self):
        self.ingested = []

    def ingest(self, index_id, doc):
        self.ingested.append((index_id, doc))
        return {"task_id": "t"}


def test_ingest_validates_and_upserts_gmeta(monkeypatch):
    client = _FakeIngestClient()
    n = ingest(index_id="idx", seed_path=FIX, client=client)
    assert n == 2
    index_id, doc = client.ingested[0]
    assert index_id == "idx"
    assert doc["ingest_type"] == "GMetaList"
    subjects = {g["subject"] for g in doc["ingest_data"]["gmeta"]}
    assert subjects == {"purdue:anvil", "alcf:polaris"}
    # visibility + content present
    g0 = doc["ingest_data"]["gmeta"][0]
    assert g0["visible_to"] == ["public"]
    assert g0["content"]["id"] in {"anvil", "polaris"}


def test_ingest_rejects_a_malformed_seed(tmp_path):
    import pytest
    bad = tmp_path / "bad.yaml"
    bad.write_text("- id: x\n")
    with pytest.raises(Exception):
        ingest(index_id="idx", seed_path=bad, client=_FakeIngestClient())
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/test_catalog_ingest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'hpc_bridge.catalog.ingest'`

- [ ] **Step 3: Implement the ingest module**

```python
# src/hpc_bridge/catalog/ingest.py
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bundled import BundledCatalog


def ingest(index_id: str, seed_path: str | Path, client) -> int:
    """Validate every seed entry against CatalogEntry and upsert them as GMetaEntries.

    Idempotent: keyed by subject (<facility_key>:<id>), so re-running overwrites in place.
    Returns the number of entries ingested. Run by a curator holding the index writer role.
    """
    catalog = BundledCatalog(Path(seed_path))  # construction re-validates every entry
    gmeta = []
    for entry in catalog._by_subject.values():  # validated entries
        gmeta.append({
            "subject": entry.subject,
            "visible_to": ["public"],
            "content": json.loads(entry.model_dump_json()),
        })
    doc = {
        "ingest_type": "GMetaList",
        "ingest_data": {"gmeta": gmeta},
    }
    client.ingest(index_id, doc)
    return len(gmeta)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hpc-bridge-catalog",
                                     description="Validate seed YAML and ingest to a Globus Search index.")
    parser.add_argument("index_id", help="target Globus Search index UUID")
    parser.add_argument("seed_path", help="seed .yaml file or directory")
    args = parser.parse_args(argv)

    from globus_compute_sdk import Client
    from globus_sdk import SearchClient

    authorizer = Client().login_manager.get_authorizer("search.api.globus.org")
    client = SearchClient(authorizer=authorizer)
    n = ingest(index_id=args.index_id, seed_path=args.seed_path, client=client)
    print(f"ingested {n} entr{'y' if n == 1 else 'ies'} to {args.index_id}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

> `ingest()` reads `catalog._by_subject` directly — acceptable coupling within the package. If a public accessor is preferred, add `BundledCatalog.entries()` and use it; not required for v1.

- [ ] **Step 4: Add the console script**

In `pyproject.toml` under `[project.scripts]`:

```toml
[project.scripts]
hpc-bridge = "hpc_bridge.server:main"
hpc-bridge-catalog = "hpc_bridge.catalog.ingest:main"
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_catalog_ingest.py -q`
Expected: PASS (2 passed)

- [ ] **Step 6: Run the full suite + lint**

Run: `python -m pytest tests/ -q && python -m pytest tests/test_catalog_entry.py tests/test_catalog_bundled.py tests/test_catalog_search.py tests/test_catalog_make.py tests/test_catalog_ingest.py -q`
Expected: PASS (all green; no regressions in the existing suite)

- [ ] **Step 7: Commit**

```bash
git add src/hpc_bridge/catalog/ingest.py pyproject.toml tests/test_catalog_ingest.py
git commit -m "feat(catalog): curator ingest script (validate seed -> upsert GMetaList) + console entry"
```

---

## Plan 2 preview — Allocation-selection flow (separate plan, to be written next)

Builds on this data layer. Scope:
1. **Allocation parsers** — `parse_sbank`/`parse_iris`/`parse_mybalance(stdout) -> list[Allocation balance]`, dispatched by `entry.allocation.parser`. Deterministic, in code. Unit-tested against captured sample outputs.
2. **`list_facilities(query?)`** MCP tool — wraps `catalog.discover()`, returns `CatalogSummary` list to the agent.
3. **`connect_facility(machine)`** MCP tool — `catalog.get()` → `profile_kwargs()` → `MachineProfile`; ensure the login shape is up (reuse existing endpoint / cold-bootstrap over SSH only if needed); run `entry.allocation.command` via the existing `run_shell(shape="login")` Compute path; parse; return `{phase: "needs_account", allocations, provenance, last_validated}`.
4. **`ensure_endpoint_up(account=…)`** — thread the chosen account into `Profile.account` → `SlurmProvider.account`.
5. Integration test behind `HPC_BRIDGE_RUN_INTEGRATION`: real `get_subject` + real allocation discovery through the login shape.

Key seam already in place: `run_shell(command, shape="login")` (server.py) runs a login-node command through Compute today; PRs #12/#13 already minimized SSH. Plan 2 is wiring, not new infrastructure.
```
