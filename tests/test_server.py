from hpc_bridge.lifecycle import EndpointState
from hpc_bridge.profile import Profile
from hpc_bridge.runner import CanaryResult
from hpc_bridge.server import AppCtx, _ensure_endpoint_up, _run_shell, _shape_runtime, mcp
from tests.fakes import FakeFacility


def _confirm_slurm(app):
    """Acknowledge spend for the default billed (slurm) shape, as the budget gate would — so
    run_shell/reset tests exercise dispatch rather than tripping the deterministic spend floor."""
    _shape_runtime(app, "compute").spend_confirmed = True


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
    res = await _ensure_endpoint_up(app, confirm_spend=True)
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
    res = await _ensure_endpoint_up(app, confirm_spend=True)
    assert res.status == "provisioning" and res.block_state == "provisioning"
    assert res.notice and "allocating" in res.notice.lower()


async def test_ensure_endpoint_up_reports_provisioning_when_cold():
    f = FakeFacility()
    f.workers = 0
    app = AppCtx(facility=f, profile=Profile())
    res = await _ensure_endpoint_up(app, confirm_spend=True)
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
    _confirm_slurm(app)
    out = await _run_shell(app, "echo hi")
    assert out.phase == "complete"
    assert out.exit_code == 0 and out.stdout == "hi\n"
    assert out.block_state == "warm"


async def test_run_shell_cold_returns_cold_start():
    f = FakeFacility()
    f.workers = 0
    app = AppCtx(facility=f, profile=Profile())
    _confirm_slurm(app)  # spend ack'd, so we reach the cold-block path (not the spend floor)
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
    _confirm_slurm(app)
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
    _confirm_slurm(app)
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
    await _run_shell(app, "echo b", shape="compute")
    assert set(app.shapes) == {"login", "compute"}
    assert app.shapes["login"].runner is not app.shapes["compute"].runner


async def test_server_registers_run_shell_tool():
    tools = await mcp.list_tools()
    assert any(t.name == "run_shell" for t in tools)


async def test_run_shell_wraps_command_with_session_shim():
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    runner = _FakeRunner("fake-eid", _Res(0, "", ""))
    app.runner_factory = lambda eid, user_endpoint_config=None: runner
    _confirm_slurm(app)
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
    _confirm_slurm(app)
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
    res = await _ensure_endpoint_up(app, confirm_spend=True)
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
    res = await _ensure_endpoint_up(app, confirm_spend=True)
    assert res.status == "down"
    assert res.notice and "Linux" in res.notice


async def test_stop_releases_block_over_login_and_keeps_endpoint(monkeypatch):
    # Option A: stop `scancel`s the block over the LOGIN shape (AMQP, no SSH) and LEAVES the manager
    # online for reuse — it must NOT call the facility teardown / gce stop, and must keep the
    # endpoint_id + login shape so a reconnect is zero-SSH.
    from hpc_bridge import server
    from hpc_bridge.models import ShellOutcome
    from hpc_bridge.server import ShapeRuntime, _stop_endpoint

    class _NoTeardown(FakeFacility):
        async def teardown(self, eid):  # must NOT be called under Option A
            raise AssertionError("stop must not tear the endpoint down")

    f = _NoTeardown()
    app = AppCtx(facility=f, profile=Profile(), state=EndpointState(endpoint_id="eid-1"))
    slurm_runner = _FakeRunner("eid-1", _Res(0, "", ""))
    app.shapes["compute"] = ShapeRuntime(user_endpoint_config={"compute": True}, runner=slurm_runner)
    app.shapes["login"] = ShapeRuntime(
        user_endpoint_config={"provider_type": "LocalProvider"}, warm_confirmed_at=1.0
    )
    seen = {}

    async def fake_run_shell(a, command, session_id="default", shape="compute"):
        seen["shape"], seen["cmd"] = shape, command
        return ShellOutcome(phase="complete", exit_code=0, stdout="released 123\n", block_state="warm")

    monkeypatch.setattr(server, "_run_shell", fake_run_shell)
    res = await _stop_endpoint(app)
    assert seen["shape"] == "login"  # the scancel rode AMQP, not SSH
    assert "scancel" in seen["cmd"] and "uep.eid-1" in seen["cmd"]
    assert "compute" not in app.shapes  # billed shape dropped -> a later run re-provisions fresh
    assert slurm_runner.closed  # its (now-dead) runner was closed
    assert "login" in app.shapes  # login shape kept (warm, free, for cheap reconnect)
    assert app.state.endpoint_id == "eid-1"  # endpoint NOT torn down
    assert res.status == "down"  # cancel CONFIRMED (login channel was warm) -> honest "down"
    assert res.endpoint_id == "eid-1" and res.block_state == "cold"
    assert "online for reuse" in (res.notice or "")


async def test_stop_is_honest_when_release_channel_is_cold(monkeypatch):
    # Issue #24: if the login release channel is cold, the scancel dispatch comes back non-complete
    # ("allocating nodes…"), so the cancel is NOT confirmed. stop_endpoint must NOT then claim
    # status="down" (an agent reading that walks away while the block keeps burning). It reports the
    # honest status="draining" and says spend is not confirmed stopped — idle-release backstops it.
    from hpc_bridge import server
    from hpc_bridge.models import ShellOutcome
    from hpc_bridge.server import ShapeRuntime, _stop_endpoint

    monkeypatch.setenv("HPC_BRIDGE_RELEASE_BACKOFF_S", "0")  # no real sleeps in the retry loop
    app = AppCtx(facility=FakeFacility(), profile=Profile(), state=EndpointState(endpoint_id="eid-1"))
    slurm_runner = _FakeRunner("eid-1", _Res(0, "", ""))
    app.shapes["compute"] = ShapeRuntime(user_endpoint_config={"compute": True}, runner=slurm_runner)
    app.shapes["login"] = ShapeRuntime(user_endpoint_config={"provider_type": "LocalProvider"})
    calls = {"n": 0}

    async def cold_run_shell(a, command, session_id="default", shape="compute"):
        calls["n"] += 1  # login worker never warms -> every dispatch is a cold_start (not complete)
        return ShellOutcome(phase="cold_start", block_state="cold", notice="allocating nodes…")

    monkeypatch.setattr(server, "_run_shell", cold_run_shell)
    res = await _stop_endpoint(app)
    assert res.status == "draining"  # honest: NOT "down" while the cancel is unconfirmed
    assert res.status not in ("down", "stopped")
    assert "not confirmed" in (res.notice or "").lower()
    assert calls["n"] >= 2  # it RETRIED the cold channel rather than giving up on the first miss
    assert "compute" not in app.shapes and slurm_runner.closed  # billed shape still dropped (spend clock banked)


async def test_stop_retries_cold_channel_then_confirms(monkeypatch):
    # The first dispatch wakes the cold login worker (returns cold_start); a bounded retry catches
    # it once warm and CONFIRMS the cancel -> honest "down". This is the common, recoverable case.
    from hpc_bridge import server
    from hpc_bridge.models import ShellOutcome
    from hpc_bridge.server import ShapeRuntime, _stop_endpoint

    monkeypatch.setenv("HPC_BRIDGE_RELEASE_BACKOFF_S", "0")
    app = AppCtx(facility=FakeFacility(), profile=Profile(), state=EndpointState(endpoint_id="eid-1"))
    app.shapes["compute"] = ShapeRuntime(user_endpoint_config={"compute": True}, runner=_FakeRunner("eid-1", _Res(0, "", "")))
    app.shapes["login"] = ShapeRuntime(user_endpoint_config={"provider_type": "LocalProvider"})
    calls = {"n": 0}

    async def warming_run_shell(a, command, session_id="default", shape="compute"):
        calls["n"] += 1
        if calls["n"] == 1:  # cold on the first hit (worker scaled in) ...
            return ShellOutcome(phase="cold_start", block_state="cold", notice="allocating nodes…")
        return ShellOutcome(phase="complete", exit_code=0, stdout="released 456\n", block_state="warm")

    monkeypatch.setattr(server, "_run_shell", warming_run_shell)
    res = await _stop_endpoint(app)
    assert res.status == "down"  # ... confirmed on retry
    assert calls["n"] == 2 and "online for reuse" in (res.notice or "")


# --- cancel: qdel/qstat on the cost-critical stop path (PBS) --------------------------------


def test_release_cmd_pbs_uses_qstat_and_qdel():
    from hpc_bridge.server import _release_cmd

    cmd = _release_cmd("pbs", "abc-123")
    assert "qstat -f" in cmd and "qdel" in cmd
    assert "uep.abc-123" in cmd
    assert "scancel" not in cmd and "squeue" not in cmd


def test_release_cmd_slurm_uses_squeue_and_scancel():
    from hpc_bridge.server import _release_cmd

    cmd = _release_cmd("slurm", "abc-123")
    assert "squeue" in cmd and "scancel" in cmd and "uep.abc-123" in cmd


def test_release_cmd_slurm_matches_prior_inline_command():
    # Byte-for-byte: the extracted helper must build the EXACT string the old inline
    # marker=...+cmd=(...) block in _release_blocks_over_login used to build.
    import shlex

    from hpc_bridge.server import _release_cmd

    eid = "eid-1"
    marker = shlex.quote(f"uep.{eid}")
    expected = (
        'ids=$(squeue -u "$USER" -h -O "JobID:30,StdOut:1024" 2>/dev/null '
        f"| grep -F {marker} | awk '{{print $1}}'); "
        '[ -n "$ids" ] && scancel $ids; echo "released ${ids:-none}"'
    )
    assert _release_cmd("slurm", eid) == expected


async def test_stop_dispatches_pbs_release_cmd_when_facility_scheduler_is_pbs(monkeypatch):
    # _release_blocks_over_login must branch on the facility's scheduler (PBS uses
    # qstat/qdel, never squeue/scancel) so a PBS block is actually cancelled, not silently
    # missed by a Slurm-only command.
    from hpc_bridge import server
    from hpc_bridge.models import ShellOutcome
    from hpc_bridge.server import ShapeRuntime, _stop_endpoint
    from types import SimpleNamespace

    class _PbsFacility(FakeFacility):
        def __init__(self):
            super().__init__()
            self.profile = SimpleNamespace(scheduler="pbs")

    app = AppCtx(facility=_PbsFacility(), profile=Profile(), state=EndpointState(endpoint_id="eid-1"))
    app.shapes["compute"] = ShapeRuntime(user_endpoint_config={"compute": True}, runner=_FakeRunner("eid-1", _Res(0, "", "")))
    app.shapes["login"] = ShapeRuntime(user_endpoint_config={"provider_type": "LocalProvider"}, warm_confirmed_at=1.0)
    seen = {}

    async def fake_run_shell(a, command, session_id="default", shape="compute"):
        seen["cmd"] = command
        return ShellOutcome(phase="complete", exit_code=0, stdout="released 123\n", block_state="warm")

    monkeypatch.setattr(server, "_run_shell", fake_run_shell)
    await _stop_endpoint(app)
    assert "qstat -f" in seen["cmd"] and "qdel" in seen["cmd"]
    assert "scancel" not in seen["cmd"] and "squeue" not in seen["cmd"]
    assert "uep.eid-1" in seen["cmd"]


async def test_teardown_endpoint_stops_manager_and_clears_state(monkeypatch):
    # teardown_endpoint (the explicit "destroy it") releases the block, calls the facility teardown
    # (gce stop + delete), and clears ALL shape/state so a stray run_shell can't revive a stale endpoint.
    from hpc_bridge import server
    from hpc_bridge.models import ShellOutcome
    from hpc_bridge.server import ShapeRuntime, _teardown_endpoint

    torn = []

    class _F(FakeFacility):
        async def teardown(self, eid):
            torn.append(eid)

    app = AppCtx(facility=_F(), profile=Profile(), state=EndpointState(endpoint_id="eid-1"))
    slurm_runner = _FakeRunner("eid-1", _Res(0, "", ""))
    app.shapes["compute"] = ShapeRuntime(user_endpoint_config={"compute": True}, runner=slurm_runner)
    app.shapes["login"] = ShapeRuntime(user_endpoint_config={"provider_type": "LocalProvider"})

    async def fake_run_shell(a, command, session_id="default", shape="compute"):
        return ShellOutcome(phase="complete", exit_code=0, stdout="released 1\n", block_state="warm")

    monkeypatch.setattr(server, "_run_shell", fake_run_shell)
    res = await _teardown_endpoint(app)
    assert torn == ["eid-1"]  # the facility teardown (gce stop + delete) was invoked
    assert res.status == "down" and "torn down" in (res.notice or "")
    assert app.shapes == {} and app.state.endpoint_id is None  # ALL state cleared (no stale revive)
    assert slurm_runner.closed


# --- partition loop: the discovery gate's selection -> provisioning -------------------------


async def test_ensure_endpoint_up_provisions_onto_selected_partition():
    # The gate's selection flows into the shape's user_endpoint_config (the per-task render var)
    # and is echoed back on the status.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "", ""))
    res = await _ensure_endpoint_up(app, partition="shared", confirm_spend=True)
    assert res.partition == "shared"
    assert app.shapes["compute"].user_endpoint_config["partition"] == "shared"


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
    await _ensure_endpoint_up(app, partition="shared", confirm_spend=True)
    r1 = app.shapes["compute"].runner
    await _ensure_endpoint_up(app, partition="gpu", confirm_spend=True)
    r2 = app.shapes["compute"].runner
    assert r2 is not r1  # runner rebuilt for the new partition
    assert r1.closed  # old runner torn down (its block idle-releases via min_blocks=0)
    assert app.shapes["compute"].user_endpoint_config["partition"] == "gpu"


async def test_no_partition_is_noop_and_persists_previous_selection():
    # Omitting partition keeps the prior selection (or facility default) and does NOT churn the
    # runner — the selection persists for the session.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "", ""))
    await _ensure_endpoint_up(app, partition="debug", confirm_spend=True)
    r1 = app.shapes["compute"].runner
    res = await _ensure_endpoint_up(app)  # no partition, already confirmed -> no-op
    assert app.shapes["compute"].runner is r1  # runner NOT rebuilt
    assert not r1.closed
    assert app.shapes["compute"].user_endpoint_config["partition"] == "debug"  # selection persisted
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
    assert "compute" not in app.shapes  # no shape state was mutated


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


async def test_make_facility_rejects_removed_facility_env(monkeypatch):
    import pytest

    from hpc_bridge.server import make_facility

    # HPC_BRIDGE_FACILITY was removed — machines are catalog data now. Setting it without
    # HPC_BRIDGE_MACHINE fails loudly (a migration aid), not a silent fallback to local.
    monkeypatch.delenv("HPC_BRIDGE_MACHINE", raising=False)
    monkeypatch.setenv("HPC_BRIDGE_FACILITY", "anvil")
    with pytest.raises(RuntimeError, match="HPC_BRIDGE_FACILITY was removed"):
        await make_facility()


async def test_lifespan_boots_unbound_when_facility_setup_fails(monkeypatch, capsys):
    # A startup crash would silently register no tools (the agent just sees "no hpc-bridge tools"),
    # so lifespan must catch a failed make_facility (here: the removed HPC_BRIDGE_FACILITY) and boot
    # local/unbound instead — the agent then binds a machine via connect_facility.
    from hpc_bridge.server import lifespan, mcp

    monkeypatch.delenv("HPC_BRIDGE_MACHINE", raising=False)
    monkeypatch.setenv("HPC_BRIDGE_FACILITY", "anvil")
    async with lifespan(mcp) as app:
        assert app.facility.name == "local"
    assert "facility setup failed at startup" in capsys.readouterr().err


async def test_make_facility_sources_ssh_user_from_config_when_env_absent(monkeypatch):
    # SSH creds are NO LONGER required boot-env vars (they don't reach an already-running server):
    # with HPC_BRIDGE_SSH_USER/KEY absent, the login name comes from ~/.ssh/config (`ssh -G`) and the
    # key defers to the config's IdentityFile. (Account is separate — still env-pinned at startup.)
    import hpc_bridge.server as server
    import hpc_bridge.state as state_mod
    from hpc_bridge.catalog.bundled import BundledCatalog
    from hpc_bridge.server import make_facility

    class _NoPinStore:
        def __init__(self, *a, **k):
            pass

        def get(self, *, alias, name):
            return None

    monkeypatch.setattr(state_mod, "LoginNodeStore", _NoPinStore)
    monkeypatch.setattr(server, "make_catalog", lambda: BundledCatalog())
    monkeypatch.setattr(server, "_ssh_config_user", lambda host: "cfg-user")  # from ~/.ssh/config
    monkeypatch.delenv("HPC_BRIDGE_FACILITY", raising=False)
    monkeypatch.setenv("HPC_BRIDGE_MACHINE", "anvil")
    monkeypatch.setenv("HPC_BRIDGE_ACCOUNT", "ACC")  # the one boot value still required (billing)
    for v in ("HPC_BRIDGE_SSH_USER", "HPC_BRIDGE_SSH_KEY", "HPC_BRIDGE_SSH_HOST"):
        monkeypatch.delenv(v, raising=False)
    fac = await make_facility()
    assert fac.name == "anvil"
    assert fac.cli.target.user == "cfg-user"  # login name from ssh_config, not an env var
    assert fac.cli.target.key_path is None  # key deferred to ~/.ssh/config IdentityFile
    assert "/home/cfg-user/hpc-bridge/gce-venv/bin/activate" in fac.profile.env_setup  # templated


def test_ssh_config_user_parses_ssh_dash_g(monkeypatch):
    import subprocess

    from hpc_bridge.server import _ssh_config_user

    class _R:
        stdout = "hostname globus1.cs.uchicago.edu\nuser glabs\nidentityfile ~/.ssh/globus\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
    assert _ssh_config_user("globus1") == "glabs"  # read the User ~/.ssh/config resolves


def test_ssh_config_user_falls_back_to_local_user(monkeypatch):
    import getpass
    import subprocess

    from hpc_bridge.server import _ssh_config_user

    def _boom(*a, **k):
        raise FileNotFoundError("no ssh")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert _ssh_config_user("whatever") == getpass.getuser()  # ssh missing -> local username


async def test_make_facility_defaults_local(monkeypatch):
    from hpc_bridge.server import make_facility

    monkeypatch.delenv("HPC_BRIDGE_MACHINE", raising=False)
    monkeypatch.delenv("HPC_BRIDGE_FACILITY", raising=False)
    assert (await make_facility()).name == "local"


async def test_make_facility_reconnects_to_pinned_login_node(monkeypatch):
    import hpc_bridge.server as server
    import hpc_bridge.state as state_mod
    from hpc_bridge.catalog.bundled import BundledCatalog
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
    monkeypatch.setattr(server, "make_catalog", lambda: BundledCatalog())
    monkeypatch.delenv("HPC_BRIDGE_FACILITY", raising=False)
    monkeypatch.setenv("HPC_BRIDGE_MACHINE", "purdue:anvil")
    monkeypatch.setenv("HPC_BRIDGE_SSH_USER", "x-u")
    monkeypatch.setenv("HPC_BRIDGE_SSH_KEY", "/tmp/k")
    monkeypatch.setenv("HPC_BRIDGE_ACCOUNT", "ACC")
    fac = await make_facility()
    assert fac.cli.target.host == "login05.anvil.rcac.purdue.edu"  # rebound to pinned node


async def test_make_facility_builds_from_catalog_when_machine_set(monkeypatch):
    # HPC_BRIDGE_MACHINE sources the profile from the catalog (bundled seed here; the live
    # Globus Search index when HPC_BRIDGE_SEARCH_INDEX is set). FACILITY is unset, so a slurm
    # "anvil" facility can ONLY come from the catalog branch.
    import hpc_bridge.server as server
    import hpc_bridge.state as state_mod
    from hpc_bridge.catalog.bundled import BundledCatalog
    from hpc_bridge.server import make_facility

    class _NoPinStore:  # isolate from any real ~/.hpc-bridge/endpoints.json on this machine
        def __init__(self, *a, **k):
            pass

        def get(self, *, alias, name):
            return None

    monkeypatch.setattr(state_mod, "LoginNodeStore", _NoPinStore)
    monkeypatch.setattr(server, "make_catalog", lambda: BundledCatalog())
    monkeypatch.delenv("HPC_BRIDGE_FACILITY", raising=False)
    monkeypatch.setenv("HPC_BRIDGE_MACHINE", "purdue:anvil")
    monkeypatch.setenv("HPC_BRIDGE_SSH_USER", "x-u")
    monkeypatch.setenv("HPC_BRIDGE_SSH_KEY", "/tmp/k")
    monkeypatch.setenv("HPC_BRIDGE_ACCOUNT", "ACC")
    fac = await make_facility()
    assert fac.name == "anvil"  # profile.name == entry.id
    assert fac.cli.target.host == "anvil.rcac.purdue.edu"  # from entry.ssh_host
    assert fac.cli.target.user == "x-u"
    assert "{venv}" not in fac.profile.env_setup  # template resolved
    assert "/home/x-u/hpc-bridge/gce-venv/bin/activate" in fac.profile.env_setup
    assert "/anvil/scratch/x-u/.hpc-bridge" == fac.profile.scratch_root


async def test_make_facility_catalog_unknown_machine_errors(monkeypatch):
    import pytest

    import hpc_bridge.server as server
    from hpc_bridge.catalog.bundled import BundledCatalog
    from hpc_bridge.server import make_facility

    # Inject the seed loader as the catalog: an unknown machine is a hard "not found", not a
    # silent fallback.
    monkeypatch.setattr(server, "make_catalog", lambda: BundledCatalog())
    monkeypatch.setenv("HPC_BRIDGE_MACHINE", "nope:nope")
    with pytest.raises(RuntimeError, match="not found"):
        await make_facility()


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
    app.shapes = {"compute": slurm, "login": login}

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
    _confirm_slurm(app)
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
    assert res.exit_code == 1 and "connect_facility" in (res.notice or "")  # nudges the entry point, not login_shell


async def test_server_registers_login_shell_tool():
    tools = await mcp.list_tools()
    assert any(t.name == "login_shell" for t in tools)


async def test_stop_keeps_login_node_pin_for_reuse(tmp_path, monkeypatch):
    # Option A: stop leaves the endpoint online, so the login-node pin MUST survive — a reconnect
    # rebinds straight to the pinned node (zero SSH). Stop never removes it.
    from hpc_bridge import server
    from hpc_bridge.models import ShellOutcome
    from hpc_bridge.server import _stop_endpoint
    from hpc_bridge.state import EndpointRecord, LoginNodeStore

    store = LoginNodeStore(tmp_path / "endpoints.json")
    store.put(EndpointRecord(
        endpoint_id="eid-1", login_host="login03.x", alias="anvil.x", user="u",
        key_path="/k", name="hpc-bridge", provisioned_at="2026-06-06T00:00:00Z",
    ))

    class _Fac(FakeFacility):
        def __init__(self):
            super().__init__()
            self.store = store
            self.alias = "anvil.x"

    async def fake_run_shell(a, command, session_id="default", shape="compute"):
        return ShellOutcome(phase="complete", exit_code=0, stdout="released none\n", block_state="warm")

    monkeypatch.setattr(server, "_run_shell", fake_run_shell)
    app = AppCtx(facility=_Fac(), profile=Profile(), state=EndpointState(endpoint_id="eid-1"))
    await _stop_endpoint(app)
    rec = store.get(alias="anvil.x", name="hpc-bridge")
    assert rec is not None and rec.login_host == "login03.x"  # pin kept for cheap reconnect


# --- budget gate: the deterministic spend floor (confirm before a billed block) -------------


async def test_billed_provision_needs_confirmation():
    # A billed (Slurm) shape must not start a block until spend is acknowledged. Without
    # confirm_spend the call returns needs_confirmation and provisions NOTHING.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    created = []

    def factory(eid, user_endpoint_config=None):
        r = _FakeRunner(eid, _Res(0, "", ""))
        created.append(r)
        return r

    app.runner_factory = factory
    res = await _ensure_endpoint_up(app)  # no confirm_spend
    assert res.status == "needs_confirmation" and res.block_state == "cold"
    assert res.notice and "confirm_spend=True" in res.notice and "balance" in res.notice
    assert f.provisioned is False  # nothing started
    assert created == []  # no runner built, no canary, no block kicked
    assert app.shapes["compute"].spend_confirmed is False


async def test_confirm_spend_provisions_and_persists_for_session():
    # confirm_spend=True provisions and records the ack; a later call needs no re-confirmation.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "", ""))
    res = await _ensure_endpoint_up(app, confirm_spend=True)
    assert res.status == "up"
    assert app.shapes["compute"].spend_confirmed is True
    res2 = await _ensure_endpoint_up(app)  # no confirm_spend, but ack persists
    assert res2.status == "up"  # NOT needs_confirmation


async def test_login_shape_never_needs_confirmation():
    # A login (LocalProvider) shape is free: it provisions without a spend ack.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "", ""))
    res = await _ensure_endpoint_up(app, shape="login")  # no confirm_spend
    assert res.status == "up"


async def test_run_shell_blocked_until_spend_confirmed():
    # The floor covers run_shell too (its canary submit would otherwise kick a billed block):
    # an unconfirmed billed shape returns needs_confirmation and dispatches nothing.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    created = []

    def factory(eid, user_endpoint_config=None):
        r = _FakeRunner(eid, _Res(0, "", ""))
        created.append(r)
        return r

    app.runner_factory = factory
    out = await _run_shell(app, "echo hi")
    assert out.phase == "needs_confirmation"
    assert created == []  # no runner, no canary, no block — the command never dispatched
    assert f.provisioned is False


async def test_run_shell_runs_after_spend_confirmed():
    # Once ensure_endpoint_up(confirm_spend=True) acknowledges spend, run_shell dispatches.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "hi\n", ""))
    await _ensure_endpoint_up(app, confirm_spend=True)
    out = await _run_shell(app, "echo hi")
    assert out.phase == "complete" and out.stdout == "hi\n"


def test_pbs_entry_is_supported():
    from hpc_bridge.server import _unsupported_entry_reason
    from hpc_bridge.catalog.entry import CatalogEntry, Compute, Defaults
    import datetime
    entry = CatalogEntry(
        id="polaris", facility_key="alcf", facility="ALCF", description="d",
        display_name="Polaris", ssh_host="polaris",
        compute=Compute(scheduler="pbs", interface="hsn0",
                        env_setup="x", scratch_root="/home/{user}/.hpc-bridge"),
        defaults=Defaults(partition="debug"),
        last_validated=datetime.date(2026, 7, 10),
    )
    assert _unsupported_entry_reason(entry) is None
