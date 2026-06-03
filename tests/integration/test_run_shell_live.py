import os

import pytest

from hpc_bridge.dispatch import execute
from hpc_bridge.runner import GlobusRunner

pytestmark = pytest.mark.skipif(
    os.environ.get("HPC_BRIDGE_RUN_INTEGRATION") != "1"
    or not os.environ.get("HPC_BRIDGE_LIVE_ENDPOINT"),
    reason="integration: set HPC_BRIDGE_RUN_INTEGRATION=1 and HPC_BRIDGE_LIVE_ENDPOINT=<uuid>",
)


async def test_run_shell_dispatches_to_live_endpoint():
    runner = GlobusRunner(os.environ["HPC_BRIDGE_LIVE_ENDPOINT"])
    try:
        out = await execute("echo hpc-bridge-m1-live", runner)
        assert out.phase == "complete"
        assert out.exit_code == 0
        assert "hpc-bridge-m1-live" in out.stdout
    finally:
        runner.close()
