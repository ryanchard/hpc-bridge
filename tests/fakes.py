from __future__ import annotations

from hpc_bridge.catalog.entry import CatalogEntry, CatalogSummary
from hpc_bridge.facility.base import EndpointHandle
from hpc_bridge.profile import Profile


class FakeFacility:
    name = "fake"

    def __init__(self) -> None:
        self.workers = 0  # >=1 => manager_online() True (drives warm/cold in tests)
        self.provisioned = False
        self.provisioned_profile: Profile | None = None
        self.reused = False  # set True to simulate reattaching to an already-online endpoint (#20)

    async def provision(self, profile: Profile) -> EndpointHandle:
        self.provisioned = True
        self.provisioned_profile = profile
        return EndpointHandle(endpoint_id="fake-eid", name="fake", reused=self.reused)

    async def manager_online(self, endpoint_id: str) -> bool:
        return self.workers >= 1

    def config_template(self, profile: Profile) -> dict:
        return {}


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
            hay = f"{e.id} {e.facility_key} {e.facility} {e.description} {e.display_name}".lower()
            if not q or q in hay:
                out.append(e.summary())
        return out
