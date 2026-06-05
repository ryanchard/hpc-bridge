from __future__ import annotations

from hpc_bridge.facility.base import EndpointHandle
from hpc_bridge.profile import Profile


class FakeFacility:
    name = "fake"

    def __init__(self) -> None:
        self.workers = 0  # >=1 => manager_online() True (drives warm/cold in tests)
        self.provisioned = False
        self.provisioned_profile: Profile | None = None

    async def provision(self, profile: Profile) -> EndpointHandle:
        self.provisioned = True
        self.provisioned_profile = profile
        return EndpointHandle(endpoint_id="fake-eid", name="fake")

    async def manager_online(self, endpoint_id: str) -> bool:
        return self.workers >= 1

    def config_template(self, profile: Profile) -> dict:
        return {}
