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
        self.manager_up: bool | None = None  # override manager liveness apart from worker warmth (None = workers >= 1)

    async def provision(self, profile: Profile) -> EndpointHandle:
        self.provisioned = True
        self.provisioned_profile = profile
        return EndpointHandle(endpoint_id="fake-eid", name="fake", reused=self.reused)

    async def manager_online(self, endpoint_id: str) -> bool:
        return self.manager_up if self.manager_up is not None else self.workers >= 1

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


MEP_UUID = "da3df250-4013-4d69-942c-eef1568f860c"


def fake_mep_entry(*, id: str = "globus1", facility_key: str = "globus", **over) -> CatalogEntry:
    """A facility-MEP entry (the globus1 testbed shape): compute_mep_uuid, NO ssh_host, NO allocation,
    worker-side $HOME scratch, the unconditional version-pinned worker_init, a warm block."""
    raw = {
        "id": id,
        "facility_key": facility_key,
        "facility": "Globus Labs cluster",
        "description": "lab DGX Spark cluster via its multi-user endpoint",
        "display_name": "HPC-Bridge globus1 (MEP)",
        "compute_mep_uuid": MEP_UUID,
        "ssh_host": None,
        "account_required": False,
        "compute": {
            "scheduler": "slurm", "interface": "enP7s7",
            "env_setup": "uv pip install -q globus-compute-endpoint==4.15.0",
            "scratch_root": "$HOME/.hpc-bridge",
        },
        "defaults": {"partition": "main", "walltime": "02:00:00", "init_blocks": 1},
        "last_validated": "2026-08-18",
    }
    raw.update(over)
    return CatalogEntry.model_validate(raw)


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
