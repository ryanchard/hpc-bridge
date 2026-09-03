# tests/test_mep_server.py — the server seams on a facility-run multi-user endpoint (MEP): zero SSH,
# compute-only, draining-only stop. Drives a REAL MEPFacility through the real _connect_facility /
# _ensure_endpoint_up / _run_shell / _stop_endpoint / _teardown_endpoint paths.
from hpc_bridge import server
from hpc_bridge.facility.mep import MEPFacility
from hpc_bridge.profile import Profile
from hpc_bridge.runner import CanaryResult
from hpc_bridge.server import AppCtx, _ensure_endpoint_up, _run_shell, _stop_endpoint, _teardown_endpoint
from tests.fakes import MEP_UUID, FakeCatalog, FakeFacility, fake_mep_entry
from tests.test_server import _FakeRunner, _Res

# The admin-verified globus1 UEC (+ the entry's pinned interface/worker_init): what the compute
# shape must submit, and NOTHING login-shaped may ever be submitted.
VERIFIED_UEC = {
    "compute": True, "partition": "main", "walltime": "02:00:00", "nodes_per_block": 1,
    "max_workers_per_node": 2, "init_blocks": 1, "max_blocks": 1,
    "interface": "enP7s7", "worker_init": "uv pip install -q globus-compute-endpoint==4.15.0",
}


class _Status:
    def __init__(self, status="online"):
        self.status = status

    def get_endpoint_status(self, endpoint_id):
        return {"status": self.status}


def _mep(status="online") -> MEPFacility:
    return MEPFacility.from_entry(fake_mep_entry(), client_factory=lambda: _Status(status))


def _app(fac=None) -> AppCtx:
    app = AppCtx(facility=fac or _mep(), profile=Profile())
    app.built = []  # every runner the server asks for, with its user_endpoint_config

    def factory(eid, user_endpoint_config=None, **_kw):
        r = _FakeRunner(eid, _Res(0, "glabs\n", ""))
        app.built.append((eid, dict(user_endpoint_config or {})))
        return r

    app.runner_factory = factory
    return app


async def _connect(app, monkeypatch, entry=None):
    monkeypatch.setattr(server, "make_catalog", lambda: FakeCatalog([entry or fake_mep_entry()]))
    monkeypatch.setattr(server, "_facility_from_entry", lambda e, *, account: app.facility)
    return await server._connect_facility(app, "globus1")


# --- connect: attach, don't provision ---------------------------------------------------------------


async def test_connect_attaches_with_zero_ssh_and_no_login_runtime(monkeypatch):
    app = _app()
    res = await _connect(app, monkeypatch)
    assert res.phase == "needs_account"
    assert res.reused is True  # attached to the facility's always-on endpoint
    assert res.allocations == []
    assert app.state.endpoint_id == MEP_UUID
    assert "login" not in app.shapes  # NEVER a login ShapeRuntime on a MEP
    assert app.built == []  # and no runner (no canary, no block) — warming is behind the spend gate
    assert app.scratch_root == "$HOME/.hpc-bridge"  # worker-side root, untouched
    assert "compute-only" in res.notice.lower() or "compute-only" in res.notice
    assert "no account is needed" in res.notice.lower()  # account_required=False -> don't go hunting


async def test_connect_account_required_mep_says_pass_account(monkeypatch):
    fac = MEPFacility.from_entry(fake_mep_entry(account_required=True), client_factory=_Status)
    app = _app(fac)
    res = await _connect(app, monkeypatch)
    assert res.phase == "needs_account"
    assert "ensure_endpoint_up(account=" in res.notice


async def test_connect_offline_mep_is_failed_not_call_again(monkeypatch):
    app = _app(_mep(status="offline"))
    res = await _connect(app, monkeypatch)
    assert res.phase == "failed"
    assert "facility" in res.notice and "offline" in res.notice
    assert "call connect_facility again shortly" not in res.notice  # nothing we do brings it up


# --- compute shape: exactly the verified UEC; login shape: refused structurally ---------------------


async def test_compute_shape_submits_exactly_the_verified_uec(monkeypatch):
    app = _app()
    await _connect(app, monkeypatch)
    res = await _ensure_endpoint_up(app, shape="compute", confirm_spend=True)
    assert res.status == "up"
    assert app.built and app.built[-1][0] == MEP_UUID
    assert app.built[-1][1] == VERIFIED_UEC  # no account key, no login keys, compute=True
    out = await _run_shell(app, "whoami", shape="compute")
    assert out.phase == "complete" and "glabs" in (out.stdout or "")


async def test_login_shape_is_refused_without_building_a_runtime(monkeypatch):
    app = _app()
    await _connect(app, monkeypatch)
    for call in (
        lambda: _ensure_endpoint_up(app, shape="login"),
        lambda: _run_shell(app, "ls", shape="login"),
        lambda: server._reset_session(app, shape="login"),
    ):
        res = await call()
        status = getattr(res, "status", None) or getattr(res, "phase", None)
        assert status in ("down", "failed")
        assert "compute" in (res.notice or "") and "login" in (res.notice or "")
    assert "login" not in app.shapes  # refused BEFORE any ShapeRuntime exists
    assert all(uec.get("compute") for _eid, uec in app.built)  # nothing LocalProvider-shaped was ever built


async def test_needs_confirmation_on_mep_does_not_point_at_login(monkeypatch):
    app = _app()
    await _connect(app, monkeypatch)
    res = await _ensure_endpoint_up(app, shape="compute")  # no confirm_spend
    assert res.status == "needs_confirmation"
    assert "shape='login'" not in res.notice and "compute-only" in res.notice
    out = await _run_shell(app, "hostname", shape="compute")
    assert out.phase == "needs_confirmation" and "shape='login'" not in out.notice


async def test_cold_compute_block_skips_the_login_pilot_query(monkeypatch):
    # the #32 provisioning-notice augmenter queries the scheduler over shape="login" — skipped on a MEP
    import time as _time

    app = _app()
    await _connect(app, monkeypatch)
    app.runner_factory = lambda eid, user_endpoint_config=None, **_kw: _FakeRunner(
        eid, _Res(0, "", ""), canary_result=CanaryResult(ok=False, error="timeout"))
    server._shape_runtime(app, "compute").provisioning_since = _time.monotonic() - (server.PROVISION_GRACE_S + 10)
    res = await _ensure_endpoint_up(app, shape="compute", confirm_spend=True)
    assert res.status == "provisioning"
    assert "login" not in app.shapes


async def test_unmapped_identity_is_a_terminal_down_not_allocating(monkeypatch):
    # the #32 provisioning-notice augmenter queries the scheduler over shape="login" — skipped on a MEP
    import time as _time

    app = _app()
    await _connect(app, monkeypatch)
    app.runner_factory = lambda eid, user_endpoint_config=None, **_kw: _FakeRunner(
        eid, _Res(0, "", ""), canary_result=CanaryResult(ok=False, error="TaskExecutionFailed: Identity failed to map to a local user name.  (LookupError) "))
    server._shape_runtime(app, "compute").provisioning_since = _time.monotonic() - (server.PROVISION_GRACE_S + 10)
    monkeypatch.setattr("hpc_bridge.login.globus_identity_label", lambda fetch=True: "someone@example.org")
    res = await _ensure_endpoint_up(app, shape="compute", confirm_spend=True)
    assert res.status == "down" and res.block_state == "cold"
    assert "NO ACCOUNT" in res.notice and "someone@example.org" in res.notice and "TERMINAL" in res.notice
    assert "allocating" not in res.notice and "failed to map" in res.notice.lower()


def test_no_account_markers_cover_the_manager_messages():
    from hpc_bridge.server import _no_account_failure
    assert _no_account_failure("TaskExecutionFailed: Identity failed to map to a local user name.  (LookupError) ")
    assert _no_account_failure("(KeyError)\n  Identity mapped to a local user name, but local user does not exist.")
    assert _no_account_failure("Ignoring start request for untrusted identity.")
    assert not _no_account_failure("timeout") and not _no_account_failure(None)
    assert not _no_account_failure("RuntimeError: Executor is shutdown")


def test_cold_run_shell_on_no_account_is_failed_not_cold_start():
    from hpc_bridge.server import _cold_outcome
    out = _cold_outcome("cold", CanaryResult(ok=False, error="TaskExecutionFailed: Identity failed to map to a local user name."))
    assert out.phase == "failed" and "NO ACCOUNT" in out.notice
    assert _cold_outcome("cold", CanaryResult(ok=False, error="timeout")).phase == "cold_start"



# --- stop / teardown: draining-only, no release channel ----------------------------------------------


async def test_stop_is_draining_only_and_never_touches_login(monkeypatch):
    app = _app()
    await _connect(app, monkeypatch)
    await _ensure_endpoint_up(app, shape="compute", confirm_spend=True)
    called = []
    monkeypatch.setattr(server, "_release_blocks_over_login", lambda *a, **k: called.append(1))
    res = await _stop_endpoint(app)
    assert res.status == "draining"  # NEVER "down": no cancel can be confirmed from here
    assert called == []  # no scancel-over-login attempt (and so no login-shape submit)
    assert "do not re-poll" in res.notice.lower()  # draining is terminal here (the skill's re-poll loop must not spin)
    assert "compute" not in app.shapes  # the shape is dropped: nothing further lands on the block
    assert app.state.endpoint_id == MEP_UUID  # the facility's endpoint stays attached/available
    # a second stop is honest too, not a lie
    assert (await _stop_endpoint(app)).status == "draining"


async def test_teardown_is_a_detach_not_a_destroy(monkeypatch):
    app = _app()
    await _connect(app, monkeypatch)
    await _ensure_endpoint_up(app, shape="compute", confirm_spend=True)
    called = []
    monkeypatch.setattr(server, "_release_blocks_over_login", lambda *a, **k: called.append(1))
    res = await _teardown_endpoint(app)
    assert res.status == "down" and "nothing of ours" in res.notice
    assert called == []
    assert app.state.endpoint_id is None and app.shapes == {}  # our state fully cleared


async def test_login_shell_on_mep_explains_no_ssh():
    app = _app()
    res = await server._login_shell(app, "hostname")
    assert res.exit_code == 1 and "compute-only" in res.notice and "no SSH" in res.notice


# --- the SSH path is untouched by the capability plumbing --------------------------------------------


def test_default_facility_has_every_shape():
    app = AppCtx(facility=FakeFacility(), profile=Profile())
    assert server._supported_shapes(app) == ("login", "compute")
    assert server._shape_reject(app, "login") is None


# --- final-review fixes -------------------------------------------------------------------------------


def test_startup_pin_account_reaches_the_uec():
    # _catalog_facility demanded HPC_BRIDGE_ACCOUNT for an account-required MEP, then from_entry dropped
    # it — the UEC went out with no account and sbatch rejected the pilot. Now threaded through.
    fac = server._facility_from_entry(fake_mep_entry(account_required=True), account="proj123")
    assert fac.config_template(Profile())[1]["account"] == "proj123"
    assert "account" not in server._facility_from_entry(fake_mep_entry(), account="").config_template(Profile())[1]


async def test_offline_mep_reads_offline_not_allocating(monkeypatch):
    # with the manager OFFLINE, probe short-circuits before any canary; the generic "allocating nodes…"
    # told the agent to wait on a queue that doesn't exist (no login shape -> no #32 pilot query).
    app = _app(_mep(status="offline"))
    app.state = server.EndpointState(endpoint_id=MEP_UUID, reused=True)  # startup-pinned: no connect ran
    res = await _ensure_endpoint_up(app, shape="compute", confirm_spend=True)
    assert res.status == "provisioning"
    assert "OFFLINE" in res.notice and "facility" in res.notice and "allocating nodes" not in res.notice
    # manager online + block merely cold -> the normal wording
    app2 = _app()
    await _connect(app2, monkeypatch)
    app2.runner_factory = lambda eid, user_endpoint_config=None, **_kw: _FakeRunner(
        eid, _Res(0, "", ""), canary_result=CanaryResult(ok=False, error="timeout"))
    res2 = await _ensure_endpoint_up(app2, shape="compute", confirm_spend=True)
    assert res2.status == "provisioning" and "allocating nodes" in res2.notice and "OFFLINE" not in res2.notice


async def test_stop_mep_refuses_while_a_task_runs_and_keeps_its_handle(monkeypatch):
    # no cancel channel on a MEP: a running task keeps the block BUSY (billing) to its ceiling. Draining
    # its handle would lose the result while claiming "idle-release in ~600s". Refuse instead.
    app = _app()
    await _connect(app, monkeypatch)
    app.runner_factory = lambda eid, user_endpoint_config=None, **_kw: _FakeRunner(eid, _Res(0, "", ""), pending=True)
    await _ensure_endpoint_up(app, shape="compute", confirm_spend=True)
    out = await _run_shell(app, "sleep 3000", shape="compute")
    assert out.phase == "running" and out.task_id
    res = await _stop_endpoint(app)
    assert res.status == "up" and "can't stop" in res.notice and out.task_id in res.notice
    assert "poll_task" in res.notice and "no cancel channel" in res.notice.lower()
    assert out.task_id in app.tasks and "compute" in app.shapes  # handle + shape survive; result retrievable
