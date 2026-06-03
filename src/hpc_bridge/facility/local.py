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
        # The per-user-process (UEP) template content. In globus-compute-endpoint 4.x
        # the engine lives here, not in the manager config.yaml. The interactive
        # profile holds a warm block (min_blocks>=1); batch scales to zero.
        warm = profile.mode == "interactive"
        return {
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
        # configure() forces --multi-user false (personal, no identity-mapping);
        # then write our engine into the UEP template, leaving config.yaml as the
        # engine-free manager config that `start` requires.
        await self.cli.configure(self.endpoint_name)
        template = self.cli.user_template_path(self.endpoint_name)
        template.write_text(yaml.safe_dump(self.config_template(profile), sort_keys=False))
        eid = await self.cli.start(self.endpoint_name)
        return EndpointHandle(endpoint_id=eid, name=self.endpoint_name)

    async def restart(self, endpoint_id: str) -> None:
        await self.cli.stop(self.endpoint_name)
        await self.cli.start(self.endpoint_name)

    async def worker_count(self, endpoint_id: str) -> int:
        # globus-compute-endpoint 4.x exposes only {"status": "online"|"offline"} here —
        # NOT a worker count (confirmed against 4.12). Treat manager-online as ready (1).
        # True warm/cold worker-block readiness is observed at dispatch time (M1), not here.
        from globus_compute_sdk import Client

        status = await asyncio.to_thread(Client().get_endpoint_status, endpoint_id)
        return 1 if status.get("status") == "online" else 0

    async def allocation_remaining(self) -> float | None:
        return None
