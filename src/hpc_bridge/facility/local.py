from __future__ import annotations

import asyncio

import yaml

from ..profile import Profile
from .base import EndpointHandle


class LocalFacility:
    name = "local"

    def __init__(self, cli, endpoint_name: str = "hpc-bridge-dev") -> None:
        self.cli = cli
        self.endpoint_name = endpoint_name

    def config_template(self, profile: Profile) -> dict:
        warm = profile.mode == "interactive"
        return {
            "display_name": self.endpoint_name,
            "engine": {
                "type": "GlobusComputeEngine",
                "max_workers_per_node": 1,
                "run_in_sandbox": False,
                "provider": {
                    "type": "LocalProvider",
                    "init_blocks": 1 if warm else 0,
                    "min_blocks": 1 if warm else 0,
                    "max_blocks": 1,
                },
            },
        }

    async def provision(self, profile: Profile) -> EndpointHandle:
        await self.cli.configure(self.endpoint_name)
        path = self.cli.config_path(self.endpoint_name)
        path.write_text(yaml.safe_dump(self.config_template(profile), sort_keys=False))
        eid = await self.cli.start(self.endpoint_name)
        return EndpointHandle(endpoint_id=eid, name=self.endpoint_name)

    async def restart(self, endpoint_id: str) -> None:
        await self.cli.stop(self.endpoint_name)
        await self.cli.start(self.endpoint_name)

    async def worker_count(self, endpoint_id: str) -> int:
        # Queries the Globus web service. NOTE: verify the status-dict keys against
        # the installed globus-compute-sdk during the integration test; adjust if needed.
        from globus_compute_sdk import Client

        status = await asyncio.to_thread(Client().get_endpoint_status, endpoint_id)
        details = status.get("details") or {}
        return int(details.get("total_workers") or details.get("managers") or 0)

    async def allocation_remaining(self) -> float | None:
        return None
