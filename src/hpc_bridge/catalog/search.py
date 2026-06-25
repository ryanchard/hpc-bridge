# src/hpc_bridge/catalog/search.py
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .entry import CatalogEntry, CatalogSummary


class SearchCatalog:
    """Globus Search backed — the runtime catalog. There is **no bundled fallback**: a subject the
    index can't resolve returns ``None`` (a hard failure; the soft agent-discovery fallback is a
    later slice). The local cache is *fetched* index data — a write-through offline copy of what
    the index already returned, the channel's only resilience (no TTL, no hardcoded seed).
    """

    def __init__(self, index_id: str, client, cache_dir: Path) -> None:
        self._index_id = index_id
        self._client = client
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_file(self, subject: str) -> Path:
        # percent-escape chars illegal in filenames on some OSes (':' on Windows, '/' everywhere)
        safe = subject.replace("%", "%25").replace("/", "%2F").replace(":", "%3A")
        return self._cache_dir / f"{safe}.json"

    async def get(self, machine_id: str) -> CatalogEntry | None:
        try:
            resp = await asyncio.to_thread(self._client.get_subject, self._index_id, machine_id)
        except Exception:
            return self._from_cache(machine_id)  # offline: cached index data only, else None
        entries = resp.get("entries") or []
        if entries:
            entry = CatalogEntry.model_validate(entries[0]["content"])  # re-validate on read
            self._cache_file(machine_id).write_text(entry.model_dump_json())  # write-through
            return entry
        return await self._by_id(machine_id)  # exact-subject miss: try resolving a bare id

    async def _by_id(self, machine_id: str) -> CatalogEntry | None:
        # connect_facility("anvil") should work, not only the full subject "purdue:anvil" — match
        # the BundledCatalog/FakeCatalog convention (id resolves too). Search, then match the id.
        try:
            resp = await asyncio.to_thread(
                self._client.post_search, self._index_id, {"q": machine_id, "limit": 20}
            )
        except Exception:
            return None
        for gmeta in resp.get("gmeta", []):
            for e in gmeta.get("entries") or []:
                entry = CatalogEntry.model_validate(e["content"])
                if entry.id == machine_id:
                    self._cache_file(entry.subject).write_text(entry.model_dump_json())
                    return entry
        return None

    def _from_cache(self, subject: str) -> CatalogEntry | None:
        cached = self._cache_file(subject)
        if cached.exists():
            try:
                return CatalogEntry.model_validate(json.loads(cached.read_text()))
            except Exception:
                pass  # corrupt/stale cache — a hard miss, not a hardcoded fallback
        return None

    async def discover(self, query: str) -> list[CatalogSummary]:
        try:
            resp = await asyncio.to_thread(
                self._client.post_search, self._index_id, {"q": query or "*"}
            )
        except Exception:
            return []  # offline: no fallback (discover isn't cached)
        out = []
        for gmeta in resp.get("gmeta", []):
            entries = gmeta.get("entries") or []
            if not entries:
                continue
            out.append(CatalogEntry.model_validate(entries[0]["content"]).summary())
        return out
