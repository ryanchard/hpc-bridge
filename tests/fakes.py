from __future__ import annotations

from hpc_bridge.facility.base import EndpointHandle
from hpc_bridge.profile import Profile


class FakeFacility:
    name = "fake"

    def __init__(self) -> None:
        self.workers = 0
        self.provisioned = False
        self.restarts = 0

    async def provision(self, profile: Profile) -> EndpointHandle:
        self.provisioned = True
        return EndpointHandle(endpoint_id="fake-eid", name="fake")

    async def restart(self, endpoint_id: str) -> None:
        self.restarts += 1

    async def worker_count(self, endpoint_id: str) -> int:
        return self.workers

    async def allocation_remaining(self) -> float | None:
        return None

    def config_template(self, profile: Profile) -> dict:
        return {}
