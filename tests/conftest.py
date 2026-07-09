"""Shared test fixtures."""
import pytest


@pytest.fixture(autouse=True)
def _isolate_hpc_bridge_state(monkeypatch, tmp_path):
    """Point ALL of hpc-bridge's local state — the whole ~/.hpc-bridge tree: `endpoints.json`
    (login-node pins), `facilities.json` (the local-discovery cache), and the SSH control-socket
    dir — at a per-test tmp dir. So no test reads stale entries or WRITES into the developer's real
    state (a test once polluted the real facilities.json before this existed). Belt-and-suspenders:
    the code defaults to ~/.hpc-bridge; this just relocates it via HPC_BRIDGE_STATE_DIR."""
    monkeypatch.setenv("HPC_BRIDGE_STATE_DIR", str(tmp_path / "hpc-bridge-state"))
