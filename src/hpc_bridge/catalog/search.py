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
        safe = subject.replace("/", "%2F")  # flatten any slash so the cache stays one file per subject
        return self._cache_dir / f"{safe}.json"

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
            try:
                return CatalogEntry.model_validate(json.loads(cached.read_text()))
            except Exception:
                pass  # corrupt/stale cache — fall through to the bundled fallback
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
            entries = gmeta.get("entries") or []
            if not entries:
                continue
            out.append(CatalogEntry.model_validate(entries[0]["content"]).summary())
        return out
