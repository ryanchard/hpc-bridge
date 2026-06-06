from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..profile import Profile


@dataclass(frozen=True)
class EndpointHandle:
    endpoint_id: str
    name: str
    login_host: str | None = None  # resolved FQDN the manager daemon landed on


@runtime_checkable
class Facility(Protocol):
    name: str

    async def provision(self, profile: Profile) -> EndpointHandle: ...
    async def manager_online(self, endpoint_id: str) -> bool: ...
    def config_template(self, profile: Profile) -> dict | tuple[str, dict]: ...
