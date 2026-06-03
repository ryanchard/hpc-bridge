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

    async def run(self, command):
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
