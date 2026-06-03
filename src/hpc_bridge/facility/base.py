from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..profile import Profile


@dataclass(frozen=True)
class EndpointHandle:
    endpoint_id: str
    name: str


@runtime_checkable
class Facility(Protocol):
    name: str

    async def provision(self, profile: Profile) -> EndpointHandle: ...
    async def restart(self, endpoint_id: str) -> None: ...
    async def worker_count(self, endpoint_id: str) -> int: ...
    async def allocation_remaining(self) -> float | None: ...
    def config_template(self, profile: Profile) -> dict: ...
