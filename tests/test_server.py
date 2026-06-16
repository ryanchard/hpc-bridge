from hpc_bridge.lifecycle import EndpointState
from hpc_bridge.profile import Profile
from hpc_bridge.runner import CanaryResult
from hpc_bridge.server import AppCtx, _ensure_endpoint_up, _run_shell, mcp
from tests.fakes import FakeFacility


class _Res:
    def __init__(self, rc, out, err):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


class _FakeRunner:
    def __init__(self, endpoint_id, res, *, canary_result=None):
        self.endpoint_id = endpoint_id
        self._res = res
        # default: a live worker (so existing warm-path tests stay warm); pass a not-ok
        # canary_result to simulate the cold-start gap (manager up, no worker yet).
        self._canary = canary_result or CanaryResult(
            ok=True, worker_host="a070", worker_python="3.11.7", worker_dill="0.3.9"
        )
        self.closed = False
        self.commands = []
        self.canaries = 0

    async def run(self, command):
        self.commands.append(command)
        return self._res

    async def canary(self, timeout=8.0):
        self.canaries += 1
        return self._canary

    def close(self):
        self.closed = True


async def test_ensure_endpoint_up_reports_up_when_warm():
    f = FakeFacility()
    f.workers = 1  # manager online; the canary (below) confirms a live worker
    app = AppCtx(facility=f, profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "", ""))
    res = await _ensure_endpoint_up(app)
    assert res.status == "up" and res.block_state == "warm"
    assert res.endpoint_id == "fake-eid"
    assert res.notice and "worker live" in res.notice  # warm => a worker answered, not just the manager


async def test_ensure_endpoint_up_provisioning_when_manager_up_but_worker_cold():
    # The canary gap: manager_online() True but no worker answers -> NOT warm. Without the
    # canary this wrongly reported 'up' and the next run_shell 124'd on a cold start.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(
        eid, _Res(0, "", ""), canary_result=CanaryResult(ok=False, error="timeout")
    )
    res = await _ensure_endpoint_up(app)
    assert res.status == "provisioning" and res.block_state == "provisioning"
    assert res.notice and "allocating" in res.notice.lower()


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
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "hi\n", ""))
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


async def test_run_shell_cold_start_when_worker_not_registered():
    # Manager online but the canary fails -> cold_start, and the command must NOT be dispatched
    # into the void (no run() call) where it would hang for the full dispatch timeout.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    runner = _FakeRunner("fake-eid", _Res(0, "", ""), canary_result=CanaryResult(ok=False, error="timeout"))
    app.runner_factory = lambda eid, user_endpoint_config=None: runner
    out = await _run_shell(app, "echo hi")
    assert out.phase == "cold_start"
    assert runner.canaries == 1 and runner.commands == []  # canaried, never dispatched


async def test_canary_ttl_skips_repeat_canary_on_hot_path():
    # Two run_shells in quick succession: the first canaries, the second trusts the <45s TTL
    # (and a successful dispatch refreshes it) so interactive bursts don't pay the round-trip.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    runner = _FakeRunner("fake-eid", _Res(0, "ok\n", ""))
    app.runner_factory = lambda eid, user_endpoint_config=None: runner
    await _run_shell(app, "echo a")
    await _run_shell(app, "echo b")
    assert runner.canaries == 1  # second call skipped the canary
    assert len(runner.commands) == 2  # both commands still dispatched


async def test_run_shell_login_shape_uses_localprovider_config():
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    seen = {}

    def factory(eid, user_endpoint_config=None):
        seen["uec"] = user_endpoint_config
        return _FakeRunner(eid, _Res(0, "", ""))

    app.runner_factory = factory
    await _run_shell(app, "echo hi", shape="login")
    assert seen["uec"]["provider_type"] == "LocalProvider"


async def test_two_shapes_keep_independent_runners():
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "", ""))
    await _run_shell(app, "echo a", shape="login")
    await _run_shell(app, "echo b", shape="slurm")
    assert set(app.shapes) == {"login", "slurm"}
    assert app.shapes["login"].runner is not app.shapes["slurm"].runner


async def test_server_registers_run_shell_tool():
    tools = await mcp.list_tools()
    assert any(t.name == "run_shell" for t in tools)


async def test_run_shell_wraps_command_with_session_shim():
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    runner = _FakeRunner("fake-eid", _Res(0, "", ""))
    app.runner_factory = lambda eid, user_endpoint_config=None: runner
    await _run_shell(app, "make", session_id="s1")
    sent = runner.commands[-1]
    assert "sessions/s1" in sent  # routed through the session dir
    assert ".cwd" in sent  # shim rehydrates/persists cwd
    assert "base64 -d" in sent  # command carried inertly, not raw


async def test_reset_session_dispatches_reset_command():
    from hpc_bridge.server import _reset_session

    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    runner = _FakeRunner("fake-eid", _Res(0, "", ""))
    app.runner_factory = lambda eid, user_endpoint_config=None: runner
    await _reset_session(app, "s1")
    sent = runner.commands[-1]
    assert sent.startswith("rm -f")
    assert "sessions/s1" in sent


async def test_server_registers_reset_session_tool():
    tools = await mcp.list_tools()
    assert any(t.name == "reset_session" for t in tools)


async def test_run_shell_rejects_traversal_session_id():
    import pytest

    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "", ""))
    with pytest.raises(ValueError):
        await _run_shell(app, "echo hi", session_id="../../etc")


async def test_byo_endpoint_skips_provisioning():
    # HPC_BRIDGE_ENDPOINT_ID seeds the state, so the server dispatches to an existing
    # endpoint and never provisions a local one (the macOS / remote-endpoint path).
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile(), state=EndpointState(endpoint_id="byo-uuid"))
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "", ""))
    res = await _ensure_endpoint_up(app)
    assert res.status == "up" and res.endpoint_id == "byo-uuid"
    assert f.provisioned is False


def test_env_endpoint_id_reads_and_trims(monkeypatch):
    from hpc_bridge.server import _env_endpoint_id

    monkeypatch.delenv("HPC_BRIDGE_ENDPOINT_ID", raising=False)
    assert _env_endpoint_id() is None
    monkeypatch.setenv("HPC_BRIDGE_ENDPOINT_ID", "  ep-42  ")
    assert _env_endpoint_id() == "ep-42"
    monkeypatch.setenv("HPC_BRIDGE_ENDPOINT_ID", "   ")
    assert _env_endpoint_id() is None


async def test_ensure_endpoint_up_reports_down_on_provision_failure():
    # A non-Linux host (or any provisioning error) yields a structured 'down', not a crash.
    class BoomFacility(FakeFacility):
        async def provision(self, profile):
            raise RuntimeError("globus-compute-endpoint runs only on Linux")

    app = AppCtx(facility=BoomFacility(), profile=Profile())  # cold -> provisions -> boom
    res = await _ensure_endpoint_up(app)
    assert res.status == "down"
    assert res.notice and "Linux" in res.notice


async def test_stop_endpoint_tears_down_and_resets():
    from hpc_bridge.server import ShapeRuntime, _stop_endpoint

    class _TeardownFacility(FakeFacility):
        def __init__(self):
            super().__init__()
            self.torn = None

        async def teardown(self, eid):
            self.torn = eid

    f = _TeardownFacility()
    app = AppCtx(facility=f, profile=Profile(), state=EndpointState(endpoint_id="eid-1"))
    app.shapes["slurm"] = ShapeRuntime(
        user_endpoint_config={}, runner=_FakeRunner("eid-1", _Res(0, "", ""))
    )
    res = await _stop_endpoint(app)
    assert res.status == "down" and res.block_state == "cold"
    assert f.torn == "eid-1"  # facility teardown called with the live endpoint
    assert app.shapes == {} and app.state.endpoint_id is None  # reset for re-provision
    assert "released" in (res.notice or "")


# --- partition loop: the discovery gate's selection -> provisioning -------------------------


async def test_ensure_endpoint_up_provisions_onto_selected_partition():
    # The gate's selection flows into the shape's user_endpoint_config (the per-task render var)
    # and is echoed back on the status.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "", ""))
    res = await _ensure_endpoint_up(app, partition="shared")
    assert res.partition == "shared"
    assert app.shapes["slurm"].user_endpoint_config["partition"] == "shared"


async def test_partition_change_invalidates_runner():
    # Changing partition means a different Slurm block: the cached Executor captured the old
    # partition at build time, so the runner must be rebuilt (and the old one torn down).
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    built = []

    def factory(eid, user_endpoint_config=None):
        r = _FakeRunner(eid, _Res(0, "", ""))
        built.append(r)
        return r

    app.runner_factory = factory
    await _ensure_endpoint_up(app, partition="shared")
    r1 = app.shapes["slurm"].runner
    await _ensure_endpoint_up(app, partition="gpu")
    r2 = app.shapes["slurm"].runner
    assert r2 is not r1  # runner rebuilt for the new partition
    assert r1.closed  # old runner torn down (its block idle-releases via min_blocks=0)
    assert app.shapes["slurm"].user_endpoint_config["partition"] == "gpu"


async def test_no_partition_is_noop_and_persists_previous_selection():
    # Omitting partition keeps the prior selection (or facility default) and does NOT churn the
    # runner — the selection persists for the session.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "", ""))
    await _ensure_endpoint_up(app, partition="debug")
    r1 = app.shapes["slurm"].runner
    res = await _ensure_endpoint_up(app)  # no partition -> no-op
    assert app.shapes["slurm"].runner is r1  # runner NOT rebuilt
    assert not r1.closed
    assert app.shapes["slurm"].user_endpoint_config["partition"] == "debug"  # selection persisted
    assert res.partition == "debug"


async def test_ensure_endpoint_up_rejects_invalid_partition():
    # A partition is agent/user-supplied and renders into a remote Jinja template -> reject any
    # token with shell/YAML metacharacters at the boundary, before touching any state.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    res = await _ensure_endpoint_up(app, partition="bad; rm -rf /")
    assert res.status == "down"
    assert res.notice and "invalid partition" in res.notice
    assert not f.provisioned  # rejected before any provisioning
    assert "slurm" not in app.shapes  # no shape state was mutated


async def test_login_shape_ignores_partition():
    # A LocalProvider (login) shape has no partition: a supplied one is ignored (not forced onto
    # the config) and the status says so.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "", ""))
    res = await _ensure_endpoint_up(app, shape="login", partition="shared")
    uec = app.shapes["login"].user_endpoint_config
    assert "partition" not in uec  # not forced onto a LocalProvider config
    assert res.partition is None
    assert res.notice and "login shape has no partition" in res.notice


async def test_stop_endpoint_noop_when_nothing_up():
    from hpc_bridge.server import _stop_endpoint

    app = AppCtx(facility=FakeFacility(), profile=Profile())
    res = await _stop_endpoint(app)
    assert res.status == "down" and "no endpoint" in (res.notice or "")


async def test_server_registers_stop_endpoint_tool():
    tools = await mcp.list_tools()
    assert any(t.name == "stop_endpoint" for t in tools)


def test_make_facility_selects_slurm_for_anvil(monkeypatch):
    from hpc_bridge.server import make_facility

    monkeypatch.setenv("HPC_BRIDGE_FACILITY", "anvil")
    monkeypatch.setenv("HPC_BRIDGE_SSH_USER", "x-u")
    monkeypatch.setenv("HPC_BRIDGE_SSH_KEY", "/tmp/anvil-key")
    monkeypatch.setenv("HPC_BRIDGE_ACCOUNT", "ACC")
    assert make_facility().name == "anvil"


def test_make_facility_requires_env_for_anvil(monkeypatch):
    import pytest

    from hpc_bridge.server import make_facility

    monkeypatch.setenv("HPC_BRIDGE_FACILITY", "anvil")
    for v in ("HPC_BRIDGE_SSH_USER", "HPC_BRIDGE_SSH_KEY", "HPC_BRIDGE_ACCOUNT"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(RuntimeError, match="required"):
        make_facility()


def test_make_facility_defaults_local(monkeypatch):
    from hpc_bridge.server import make_facility

    monkeypatch.delenv("HPC_BRIDGE_FACILITY", raising=False)
    assert make_facility().name == "local"


def test_make_facility_reconnects_to_pinned_login_node(monkeypatch):
    import hpc_bridge.state as state_mod
    from hpc_bridge.state import EndpointRecord
    from hpc_bridge.server import make_facility

    rec = EndpointRecord(
        endpoint_id="eid", login_host="login05.anvil.rcac.purdue.edu",
        alias="anvil.rcac.purdue.edu", user="x-u", key_path="/tmp/k",
        name="hpc-bridge", provisioned_at="2026-06-06T00:00:00Z",
    )

    class _FakeStore:
        def __init__(self, *a, **k):
            pass

        def get(self, *, alias, name):
            return rec

    monkeypatch.setattr(state_mod, "LoginNodeStore", _FakeStore)
    monkeypatch.setenv("HPC_BRIDGE_FACILITY", "anvil")
    monkeypatch.setenv("HPC_BRIDGE_SSH_USER", "x-u")
    monkeypatch.setenv("HPC_BRIDGE_SSH_KEY", "/tmp/k")
    monkeypatch.setenv("HPC_BRIDGE_ACCOUNT", "ACC")
    fac = make_facility()
    assert fac.cli.target.host == "login05.anvil.rcac.purdue.edu"  # rebound to pinned node


def test_billing_banks_warm_interval_across_idle_release(monkeypatch):
    # The canary makes warm_since track a TRUE worker, so the clock stops on idle release.
    # Spend must (a) exclude the idle gap (no over-report) and (b) retain prior warm time
    # (no under-report) — i.e. accrue across intervals.
    import hpc_bridge.server as srv
    from hpc_bridge.server import ShapeRuntime

    clock = {"t": 1000.0}
    monkeypatch.setattr(srv.time, "monotonic", lambda: clock["t"])
    app = AppCtx(facility=FakeFacility(), profile=Profile(nodes_per_block=1), charge_factor=1.0)
    rt = ShapeRuntime(user_endpoint_config={})

    srv._settle_billing(rt, app, "warm")  # worker confirmed at t=1000
    assert rt.warm_since == 1000.0
    clock["t"] += 3600  # held warm for 1h
    srv._settle_billing(rt, app, "provisioning")  # idle release: bank 1.0 node-hour, stop the clock
    assert rt.warm_since is None and abs(rt.spend_accrued - 1.0) < 1e-9
    clock["t"] += 7200  # 2h cold — must NOT be billed
    srv._settle_billing(rt, app, "warm")  # warm again
    clock["t"] += 1800  # +0.5h
    assert abs(srv._session_spend(rt, app) - 1.5) < 1e-9  # 1.0 banked + 0.5 current; idle gap excluded


def test_login_shape_is_not_billed(monkeypatch):
    # LocalProvider (login) shapes consume no allocation: the spend clock never starts.
    import hpc_bridge.server as srv
    from hpc_bridge.server import ShapeRuntime

    clock = {"t": 1000.0}
    monkeypatch.setattr(srv.time, "monotonic", lambda: clock["t"])
    app = AppCtx(facility=FakeFacility(), profile=Profile(nodes_per_block=1), charge_factor=1.0)
    rt = ShapeRuntime(user_endpoint_config={"provider_type": "LocalProvider"})

    srv._settle_billing(rt, app, "warm")
    assert rt.warm_since is None  # login never starts the spend clock
    clock["t"] += 3600
    assert srv._session_spend(rt, app) == 0.0  # no allocation -> no spend


def test_total_session_spend_sums_only_billable_shapes(monkeypatch):
    import hpc_bridge.server as srv
    from hpc_bridge.server import ShapeRuntime

    clock = {"t": 1000.0}
    monkeypatch.setattr(srv.time, "monotonic", lambda: clock["t"])
    app = AppCtx(facility=FakeFacility(), profile=Profile(nodes_per_block=1), charge_factor=1.0)
    slurm = ShapeRuntime(user_endpoint_config={"provider_type": "SlurmProvider"})
    login = ShapeRuntime(user_endpoint_config={"provider_type": "LocalProvider"})
    app.shapes = {"slurm": slurm, "login": login}

    srv._settle_billing(slurm, app, "warm")
    srv._settle_billing(login, app, "warm")
    clock["t"] += 3600  # 1h warm
    # only the slurm shape bills: 1 node * 1h * charge_factor 1.0 = 1.0
    assert abs(srv._total_session_spend(app) - 1.0) < 1e-9


def test_worker_notice_flags_dill_skew(monkeypatch):
    import hpc_bridge.server as srv

    monkeypatch.setattr(srv, "_local_dill", lambda: "0.3.9")
    skewed = srv._worker_notice(
        CanaryResult(ok=True, worker_host="a070", worker_python="3.11.7", worker_dill="0.3.8")
    )
    assert "a070" in skewed and "skew" in skewed and "0.3.8" in skewed and "0.3.9" in skewed
    matched = srv._worker_notice(CanaryResult(ok=True, worker_dill="0.3.9"))
    assert matched and "skew" not in matched


def test_note_dispatch_refreshes_on_complete_and_voids_on_timeout(monkeypatch):
    import hpc_bridge.server as srv
    from hpc_bridge.models import ShellOutcome
    from hpc_bridge.server import ShapeRuntime

    rt = ShapeRuntime(user_endpoint_config={})
    rt.warm_confirmed_at = 5.0
    srv._note_dispatch(rt, ShellOutcome(phase="failed", block_state="warm", exit_code=124))
    assert rt.warm_confirmed_at is None  # a dispatch timeout forces a re-canary next call
    monkeypatch.setattr(srv.time, "monotonic", lambda: 999.0)
    srv._note_dispatch(rt, ShellOutcome(phase="complete", block_state="warm", exit_code=0))
    assert rt.warm_confirmed_at == 999.0  # a real result refreshes liveness


async def test_concurrent_run_shell_serializes_runner_creation():
    # The lock must serialize provision + runner-swap: two run_shells racing on a fresh app
    # create exactly ONE runner (without it, both could see app.runner is None and double up).
    import asyncio

    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    created = []

    def factory(eid, user_endpoint_config=None):
        r = _FakeRunner(eid, _Res(0, "ok\n", ""))
        created.append(r)
        return r

    app.runner_factory = factory
    outs = await asyncio.gather(_run_shell(app, "echo a"), _run_shell(app, "echo b"))
    assert all(o.phase == "complete" for o in outs)
    assert len(created) == 1  # the second call reused the runner instead of racing a new one


async def test_login_shell_runs_on_ssh_facility():
    from hpc_bridge.server import _login_shell

    class _SshFacility(FakeFacility):  # an SSH facility exposes login_exec; local does not
        async def login_exec(self, command):
            return (0, "shared*|up|infinite|250|128|257400|226/12/12/250\n", "")

    res = await _login_shell(AppCtx(facility=_SshFacility(), profile=Profile()), "sinfo -h")
    assert res.exit_code == 0 and "shared" in res.stdout


async def test_login_shell_unavailable_on_local_facility():
    from hpc_bridge.server import _login_shell

    res = await _login_shell(AppCtx(facility=FakeFacility(), profile=Profile()), "sinfo")
    assert res.exit_code == 1 and "SSH facility" in (res.notice or "")  # local has no login node


async def test_server_registers_login_shell_tool():
    tools = await mcp.list_tools()
    assert any(t.name == "login_shell" for t in tools)


async def test_stop_endpoint_removes_login_node_record(tmp_path):
    from hpc_bridge.server import _stop_endpoint
    from hpc_bridge.state import EndpointRecord, LoginNodeStore

    store = LoginNodeStore(tmp_path / "endpoints.json")
    store.put(EndpointRecord(
        endpoint_id="eid-1", login_host="login03.x", alias="anvil.x", user="u",
        key_path="/k", name="hpc-bridge", provisioned_at="2026-06-06T00:00:00Z",
    ))

    class _Prof:
        endpoint_name = "hpc-bridge"

    class _Fac(FakeFacility):
        def __init__(self):
            super().__init__()
            self.store = store
            self.alias = "anvil.x"
            self.profile = _Prof()

        async def teardown(self, eid):
            pass

    app = AppCtx(facility=_Fac(), profile=Profile(), state=EndpointState(endpoint_id="eid-1"))
    await _stop_endpoint(app)
    assert store.get(alias="anvil.x", name="hpc-bridge") is None  # stale pin cleared


async def test_stop_endpoint_keeps_pin_when_teardown_fails(tmp_path):
    # A failed teardown may leave the daemon running on the pinned node, so the pin must
    # survive — dropping it would orphan the still-running endpoint on reconnect.
    from hpc_bridge.server import _stop_endpoint
    from hpc_bridge.state import EndpointRecord, LoginNodeStore

    store = LoginNodeStore(tmp_path / "endpoints.json")
    store.put(EndpointRecord(
        endpoint_id="eid-1", login_host="login03.x", alias="anvil.x", user="u",
        key_path="/k", name="hpc-bridge", provisioned_at="2026-06-06T00:00:00Z",
    ))

    class _Prof:
        endpoint_name = "hpc-bridge"

    class _Fac(FakeFacility):
        def __init__(self):
            super().__init__()
            self.store = store
            self.alias = "anvil.x"
            self.profile = _Prof()

        async def teardown(self, eid):
            raise RuntimeError("ssh unreachable")

    app = AppCtx(facility=_Fac(), profile=Profile(), state=EndpointState(endpoint_id="eid-1"))
    res = await _stop_endpoint(app)
    assert "stop attempted" in (res.notice or "")
    rec = store.get(alias="anvil.x", name="hpc-bridge")
    assert rec is not None and rec.login_host == "login03.x"  # pin kept for reconnect
