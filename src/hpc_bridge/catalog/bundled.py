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
        if path.is_dir():
            files = sorted(path.glob("*.yaml"))
        elif path.exists():
            files = [path]
        else:
            files = []  # path not present (e.g. seed dir not populated) -> empty catalog, not a crash
        out: list[dict] = []
        for f in files:
            loaded = yaml.safe_load(f.read_text()) or []
            out.extend(loaded if isinstance(loaded, list) else [loaded])
        return out

    def entries(self) -> list[CatalogEntry]:
        """All loaded, validated entries (ingest source / introspection)."""
        return list(self._by_subject.values())

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
            hay = f"{e.id} {e.facility_key} {e.facility} {e.description} {e.display_name}".lower()
            if not q or q in hay:
                out.append(e.summary())
        return out
