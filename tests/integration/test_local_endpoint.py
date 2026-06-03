import asyncio
import os
import shutil
import uuid
from pathlib import Path

import pytest

from hpc_bridge.endpoint import EndpointCLI
from hpc_bridge.facility.local import LocalFacility
from hpc_bridge.lifecycle import EndpointState, ensure_warm
from hpc_bridge.profile import Profile

pytestmark = pytest.mark.skipif(
    os.environ.get("HPC_BRIDGE_RUN_INTEGRATION") != "1"
    or shutil.which("globus-compute-endpoint") is None,
    reason="integration: set HPC_BRIDGE_RUN_INTEGRATION=1 and install globus-compute-endpoint",
)


async def test_local_endpoint_reaches_warm(tmp_path: Path):
    name = f"hpcb-test-{uuid.uuid4().hex[:8]}"
    cli = EndpointCLI(user_dir=tmp_path)
    fac = LocalFacility(cli=cli, endpoint_name=name)
    try:
        _block, state = await ensure_warm(fac, Profile(mode="interactive"), EndpointState())
        assert state.endpoint_id
        # interactive profile has init_blocks=1; a worker should register shortly
        for _ in range(30):
            if await fac.worker_count(state.endpoint_id) >= 1:
                break
            await asyncio.sleep(1)
        assert await fac.worker_count(state.endpoint_id) >= 1
    finally:
        await cli.stop(name)
