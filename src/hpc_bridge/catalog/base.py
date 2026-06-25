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
