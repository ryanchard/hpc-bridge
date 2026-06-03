from hpc_bridge.server import AppCtx, _ensure_endpoint_up, _run_shell, mcp
from hpc_bridge.profile import Profile
from tests.fakes import FakeFacility


class _Res:
    def __init__(self, rc, out, err):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


class _FakeRunner:
    def __init__(self, endpoint_id, res):
        self.endpoint_id = endpoint_id
        self._res = res
        self.closed = False
        self.commands = []

    async def run(self, command):
        self.commands.append(command)
        return self._res

    def close(self):
        self.closed = True


async def test_ensure_endpoint_up_reports_up_when_warm():
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    res = await _ensure_endpoint_up(app)
    assert res.status == "up" and res.block_state == "warm"
    assert res.endpoint_id == "fake-eid" and res.notice is None


async def test_ensure_endpoint_up_reports_provisioning_when_cold():
    f = FakeFacility()
    f.workers = 0
    app = AppCtx(facility=f, profile=Profile())
    res = await _ensure_endpoint_up(app)
    assert res.status == "provisioning"
    assert res.notice and "allocating" in res.notice.lower()


async def test_server_registers_ensure_endpoint_up_tool():
    tools = await mcp.list_tools()
    assert any(t.name == "ensure_endpoint_up" for t in tools)


async def test_run_shell_warm_returns_complete_outcome():
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner = _FakeRunner("fake-eid", _Res(0, "hi\n", ""))
    out = await _run_shell(app, "echo hi")
    assert out.phase == "complete"
    assert out.exit_code == 0 and out.stdout == "hi\n"
    assert out.block_state == "warm"


async def test_run_shell_cold_returns_cold_start():
    f = FakeFacility()
    f.workers = 0
    app = AppCtx(facility=f, profile=Profile())
    out = await _run_shell(app, "echo hi")
    assert out.phase == "cold_start"
    assert out.notice and "allocating" in out.notice.lower()


async def test_server_registers_run_shell_tool():
    tools = await mcp.list_tools()
    assert any(t.name == "run_shell" for t in tools)


async def test_run_shell_wraps_command_with_session_shim():
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner = _FakeRunner("fake-eid", _Res(0, "", ""))
    await _run_shell(app, "make", session_id="s1")
    sent = app.runner.commands[-1]
    assert "make" in sent
    assert "sessions/s1" in sent  # routed through the session dir
    assert ".cwd" in sent  # shim rehydrates/persists cwd


async def test_reset_session_dispatches_reset_command():
    from hpc_bridge.server import _reset_session

    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner = _FakeRunner("fake-eid", _Res(0, "", ""))
    await _reset_session(app, "s1")
    sent = app.runner.commands[-1]
    assert sent.startswith("rm -f")
    assert "sessions/s1" in sent


async def test_server_registers_reset_session_tool():
    tools = await mcp.list_tools()
    assert any(t.name == "reset_session" for t in tools)
