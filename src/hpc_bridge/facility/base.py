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
    async def restart(self, endpoint_id: str) -> None: ...  # reserved for restart-on-failure (M5)
    async def manager_online(self, endpoint_id: str) -> bool: ...
    async def allocation_remaining(self) -> float | None: ...
    def config_template(self, profile: Profile) -> dict: ...
