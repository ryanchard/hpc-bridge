"""Regression tests for the 2026-09-03 adversarial review: fifteen confirmed bugs, each pinned here
by asserting the FIXED behaviour (the reviewer's reproductions asserted the buggy one)."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from hpc_bridge.profile import Profile
from hpc_bridge.server import (
    AppCtx,
    ShapeRuntime,
    _confirm_worker,
    _connect_facility,
    _explain_provision_error,
    _forget_identity_verdicts,
    _parse_hhmmss,
    _poll_task,
    _run_shell,
    _runner_for,
    _shape_runtime,
    _stop_endpoint,
    _task_ceiling_s,
    _transient_dispatch_failure,
)
from tests.fakes import FakeFacility, fake_entry, fake_mep_entry
from tests.test_server import _FakeRunner, _Res

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agentic" / "harness"))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HPC_BRIDGE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HPC_BRIDGE_RELEASE_BACKOFF_S", "0")
    monkeypatch.setenv("HPC_BRIDGE_RELEASE_ATTEMPTS", "1")


def _warm_app() -> AppCtx:
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.state.endpoint_id = "eid-1"
    rt = _shape_runtime(app, "compute")
    rt.spend_confirmed = True
    return app


# 1. a new login must not kill a running task -------------------------------------------------
async def test_new_login_does_not_drop_a_live_task():
    app = _warm_app()
    app.runner_factory = lambda eid, user_endpoint_config=None, **_kw: _FakeRunner(eid, _Res(0, "", ""), pending=True)
    out = await _run_shell(app, "sleep 30", shape="compute")
    assert out.phase == "running" and out.task_id
    runner = _shape_runtime(app, "compute").runner
    _forget_identity_verdicts(app)  # authenticate(force)/complete_login landed
    assert _runner_for(app, "compute") is runner  # the swap is DEFERRED while the task lives
    polled = await _poll_task(app, out.task_id)
    assert polled.phase in ("running", "complete") and "no task" not in (polled.notice or "").lower()


# 2. the cold-start grace clock resets wherever warmth is confirmed ---------------------------
async def test_provisioning_clock_resets_when_warm_by_any_route():
    app = _warm_app()
    app.runner_factory = lambda eid, user_endpoint_config=None, **_kw: _FakeRunner(eid, _Res(0, "", ""))
    rt = _shape_runtime(app, "compute")
    rt.provisioning_since = time.monotonic() - 9999
    assert await _confirm_worker(app, "compute", force=True) == "warm"
    assert rt.provisioning_since is None


# 3. Slurm's day-format walltime --------------------------------------------------------------
def test_day_walltime_parses_and_keeps_the_task_ceiling():
    assert _parse_hhmmss("2-00:00:00") == 172800 and _parse_hhmmss("1-12:30") == 131400 and _parse_hhmmss("3-5") == 277200
    assert _parse_hhmmss("48:00:00") == 172800 and _parse_hhmmss("x-1:00:00") == 0 and _parse_hhmmss("2-") == 0
    assert _task_ceiling_s({"walltime": "2-00:00:00"}) == _task_ceiling_s({"walltime": "48:00:00"}) > 100000


# 4. only sshd's denial is NO SSH ACCESS -------------------------------------------------------
def test_remote_filesystem_denial_is_not_an_ssh_refusal():
    fs = RuntimeError("seed storage.db (write) failed: bash: line 1: /home/alice/.globus_compute/storage.db: Permission denied")
    assert _explain_provision_error(fs, host="h", user="alice").startswith("REMOTE FILESYSTEM refused")
    auth = RuntimeError("seed storage.db (mkdir) failed: alice@h: Permission denied (publickey,gssapi-with-mic).")
    assert _explain_provision_error(auth, host="h", user="alice").startswith("NO SSH ACCESS to h")
    assert _explain_provision_error(RuntimeError("x: Permission denied, please try again."), host="h", user="a").startswith("NO SSH ACCESS")


# 5 + 6. login flow: TTL expiry is not a browser failure; an explicit mode re-arms ------------
class _Mgr:
    def __init__(self, on_url):
        self.on_url, self.ev, self.aborted = on_url, threading.Event(), None

    def abort(self, why):
        self.aborted = why
        self.ev.set()


class _App:
    def __init__(self, m, raise_on_abort=True):
        self.m, self.raise_on_abort = m, raise_on_abort

    def login_required(self):
        return True

    def login(self, force=False):
        self.m.on_url("https://auth.globus.org/v2/oauth2/authorize?fake=1")
        self.m.ev.wait(5)
        if self.raise_on_abort:
            raise TimeoutError(self.m.aborted or "never aborted")


def _browser_flow(monkeypatch, **kw):
    from hpc_bridge import login as login_mod
    from hpc_bridge import login_flow_manager as lfm
    monkeypatch.setattr(lfm.CapturingLocalServerManager, "build", staticmethod(lambda *, on_url: _Mgr(on_url)))
    monkeypatch.setattr(login_mod, "_browser_available", lambda: True)
    return login_mod.LoginFlow(app_factory=lambda m: _App(m), **kw)


def test_ttl_expiry_does_not_demote_later_logins_to_paste(monkeypatch):
    flow = _browser_flow(monkeypatch, ttl_s=0.05)
    assert flow.start().mode == "browser"
    time.sleep(0.1)
    assert flow.status() == "expired"
    flow._thread.join(2)
    assert flow._browser_failed is False and flow.start().mode == "browser"


def test_explicit_paste_mode_rearms_a_waiting_browser_flow(monkeypatch):
    flow = _browser_flow(monkeypatch)
    s1 = flow.start("browser")
    s2 = flow.start("paste")
    assert s1.mode == "browser" and s2.mode == "paste" and "auth-code" in s2.login_url
    assert flow.start().mode == "paste"  # idempotent on the re-armed flow


# 7 + 15. harness graders ---------------------------------------------------------------------
def test_decline_regex_does_not_match_no_preference():
    from invariants import _DECLINE
    assert _DECLINE.search("No preference") is None and _DECLINE.search("no problem, go ahead") is None
    assert _DECLINE.search("No, don't spend anything") and _DECLINE.search("no.") and _DECLINE.search("I'd rather not")


def test_teardown_counts_as_a_release():
    from invariants import ToolCall, Trace, ends_with_stop, stop_confirmed_or_retried
    t = Trace([
        ToolCall.of("mcp__endpoint__ensure_endpoint_up", {"shape": "compute", "confirm_spend": True}, {"status": "up"}),
        ToolCall.of("mcp__endpoint__run_shell", {"command": "hostname", "shape": "compute"}, {"phase": "complete"}),
        ToolCall.of("mcp__endpoint__teardown_endpoint", {}, {"status": "down"}),
    ])
    assert ends_with_stop(t).ok and stop_confirmed_or_retried(t).ok


# 8 + 9. the registry: offline cache keyed by every name; an outage is not a miss --------------
async def test_offline_cache_serves_the_bare_id_that_was_asked(tmp_path):
    from hpc_bridge.catalog.search import SearchCatalog
    from tests.test_catalog_search import _FakeSearchClient
    e = fake_entry(id="anvil", facility_key="purdue")
    live = SearchCatalog(index_id="idx", client=_FakeSearchClient(subjects={"purdue:anvil": e}), cache_dir=tmp_path)
    assert (await live.get("anvil")).id == "anvil"  # an id hit
    offline = SearchCatalog(index_id="idx", client=_FakeSearchClient(fail=True), cache_dir=tmp_path)
    assert (await offline.get("anvil")).id == "anvil" and (await offline.get("purdue:anvil")).id == "anvil"


async def test_registry_outage_surfaces_as_unavailable(monkeypatch, tmp_path):
    from hpc_bridge import server
    from hpc_bridge.catalog.search import SearchCatalog
    from tests.test_catalog_search import _FakeSearchClient
    monkeypatch.setattr(server, "make_catalog", lambda: SearchCatalog(index_id="idx", client=_FakeSearchClient(fail=True), cache_dir=tmp_path))
    app = AppCtx(facility=FakeFacility(), profile=Profile())
    res = await _connect_facility(app, "nowhere")
    assert res.phase == "needs_facility_details" and "registry unavailable" in res.notice


# 10. endpoint names are an allowlist at both boundaries ---------------------------------------
async def test_endpoint_name_cannot_carry_shell():
    from pydantic import ValidationError

    from hpc_bridge.catalog.entry import CatalogEntry
    from hpc_bridge.facility.remote import RemoteEndpointCLI, SshTarget
    with pytest.raises(ValidationError, match="endpoint_name"):
        CatalogEntry.model_validate({**fake_entry(id="a", facility_key="p").model_dump(mode="json"),
                                     "compute": {**fake_entry(id="a", facility_key="p").compute.model_dump(), "endpoint_name": "x$(id)"}})
    cli = RemoteEndpointCLI(SshTarget(host="h"), env_setup="true")
    with pytest.raises(ValueError, match="unsafe endpoint name"):
        await cli.write_config("x`id`", "a: 1", "b: 2")


# 11. stop on a GONE endpoint is terminal, not "call again" ------------------------------------
async def test_stop_on_a_gone_endpoint_is_terminal(monkeypatch):
    from hpc_bridge import server
    app = AppCtx(facility=FakeFacility(), profile=Profile())
    app.state.endpoint_id = "eid-1"
    app.shapes["compute"] = ShapeRuntime(user_endpoint_config={"compute": True}, runner=_FakeRunner("eid-1", _Res(0, "", "")))
    app.shapes["login"] = ShapeRuntime(user_endpoint_config={"provider_type": "LocalProvider"})

    async def cold(a, eid):
        return False, "login channel cold"

    async def nothing(a):
        return 0.0

    monkeypatch.setattr(server, "_release_blocks_over_login", cold)
    monkeypatch.setattr(server, "_drop_compute_shape", nothing)
    app.facility.manager_up = False  # the manager is GONE
    res = await _stop_endpoint(app)
    assert res.status == "down" and "OFFLINE" in res.notice and "ORPHANED" in res.notice and "call stop_endpoint again in a few" not in res.notice
    app.state.endpoint_id = "eid-1"
    app.facility.manager_up = True  # merely cold: the honest draining path
    app.shapes["compute"] = ShapeRuntime(user_endpoint_config={"compute": True}, runner=_FakeRunner("eid-1", _Res(0, "", "")))
    assert (await _stop_endpoint(app)).status == "draining"


# 12. "already in use" alone is not a Compute conflict -----------------------------------------
def test_transient_conflict_needs_the_endpoint_shape():
    assert not _transient_dispatch_failure("OSError: [Errno 48] Address already in use")
    assert _transient_dispatch_failure("ComputeAPIError[RESOURCE_CONFLICT]: Endpoint x is already in use: possibly due to concurrent requests")
    assert _transient_dispatch_failure("Endpoint da3d is already in use: possibly due to concurrent requests -- please try again")


# 13. a dual-reach entry is consumed as a MEP: no client-side templating ----------------------
def test_dual_reach_entry_rejects_client_side_templating():
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="client-side templating"):
        fake_mep_entry(ssh_host="h.example.edu", compute={"scheduler": "slurm", "interface": "ib0", "env_setup": "x", "scratch_root": "/s/{user}"})
    ok = fake_mep_entry(ssh_host="h.example.edu")
    assert ok.compute_mep_uuid and ok.ssh_host


# 14. a BYO config is cached only once the bootstrap accepted it ------------------------------
async def test_byo_details_cached_only_after_the_bootstrap_accepts(monkeypatch):
    from hpc_bridge import server
    from hpc_bridge.models import FacilityDetails
    details = FacilityDetails(ssh_host="h.example.edu", interface="ib0", env_setup="true", scratch_root="/s/{user}", partition="main")
    f = FakeFacility()
    f.workers = 1
    monkeypatch.setattr(server, "_facility_from_entry", lambda entry, *, account: f)

    async def refused(app, shape, **kw):
        raise RuntimeError("seed storage.db (mkdir) failed: u@h: Permission denied (publickey)")

    monkeypatch.setattr(server, "_provision", refused)
    app = AppCtx(facility=FakeFacility(), profile=Profile())
    assert (await _connect_facility(app, "byo", details=details)).phase == "failed"
    assert server._facility_store().get("h.example.edu") is None  # a config the bootstrap REFUSED is not remembered

    async def accepted(app, shape, **kw):
        return "warm"

    monkeypatch.setattr(server, "_provision", accepted)
    app2 = AppCtx(facility=FakeFacility(), profile=Profile())
    await _connect_facility(app2, "byo", details=details)
    assert server._facility_store().get("h.example.edu") is not None


# --- code-quality quick wins (2026-09-03) ----------------------------------------------------
async def test_list_facilities_hides_transport_only(monkeypatch):
    from hpc_bridge import server

    def down():
        raise OSError("network down")

    monkeypatch.setattr(server, "make_catalog", down)
    assert await server._list_facilities("") == []

    def bug():
        raise AttributeError("'NoneType' object has no attribute 'summary'")

    monkeypatch.setattr(server, "make_catalog", bug)
    with pytest.raises(AttributeError):
        await server._list_facilities("")


async def test_rebind_banks_the_warm_interval_instead_of_losing_it(monkeypatch):
    from hpc_bridge import server
    from tests.fakes import FakeCatalog
    app = _warm_app()
    app.charge_factor = 1.0
    rt = _shape_runtime(app, "compute")
    rt.warm_since = time.monotonic() - 3600  # an hour of warm block
    rt.runner = _FakeRunner("eid-1", _Res(0, "", ""))
    other = FakeFacility()
    other.workers = 1
    monkeypatch.setattr(server, "_facility_from_entry", lambda entry, *, account: other)
    monkeypatch.setattr(server, "make_catalog", lambda: FakeCatalog([fake_entry(id="b", facility_key="x")]))
    app.runner_factory = lambda eid, user_endpoint_config=None, **_kw: _FakeRunner(eid, _Res(0, "", ""))
    res = await _connect_facility(app, "b")
    assert "session spend so far" in (res.notice or "")


def test_idle_release_prefers_the_facility_window():
    from hpc_bridge.server import _idle_release_s
    app = AppCtx(facility=FakeFacility(), profile=Profile())
    app.facility.max_idletime_s = 1234
    assert _idle_release_s(app) == 1234
    del app.facility.max_idletime_s
    assert _idle_release_s(app) == app.profile.max_idletime_s


async def test_warm_status_notice_never_renders_none():
    from hpc_bridge.server import _ensure_endpoint_up
    app = _warm_app()
    app.runner_factory = lambda eid, user_endpoint_config=None, **_kw: _FakeRunner(eid, _Res(0, "", ""))
    await _ensure_endpoint_up(app, shape="compute", confirm_spend=True)
    rt = _shape_runtime(app, "compute")
    rt.last_canary = None  # the warm-by-live-task path leaves no canary
    res = await _ensure_endpoint_up(app, shape="compute", confirm_spend=True)
    assert res.notice and not res.notice.startswith("None")


# --- "proven", not "accepted" (decision 2026-09-03) ------------------------------------------
async def test_byo_details_cached_only_once_the_login_shape_is_proven_warm(monkeypatch):
    from hpc_bridge import server
    from hpc_bridge.models import FacilityDetails
    details = FacilityDetails(ssh_host="h.example.edu", interface="ib0", env_setup="true", scratch_root="/s/{user}", partition="main")
    f = FakeFacility(); f.workers = 1
    monkeypatch.setattr(server, "_facility_from_entry", lambda entry, *, account: f)
    outcomes = iter(["provisioning", "provisioning", "warm"])

    async def provision(app, shape, **kw):
        return next(outcomes)

    monkeypatch.setattr(server, "_provision", provision)
    app = AppCtx(facility=FakeFacility(), profile=Profile())
    assert (await _connect_facility(app, "byo", details=details)).phase == "provisioning"
    assert server._facility_store().get("h.example.edu") is None      # accepted, not yet proven
    assert (await _connect_facility(app, "byo")).phase == "provisioning"  # the session entry, no details
    assert server._facility_store().get("h.example.edu") is None
    res = await _connect_facility(app, "byo")                           # the canary answered
    assert res.phase != "provisioning" and server._facility_store().get("h.example.edu") is not None
    assert "byo" not in app.pending_facility_cache


async def test_unreachable_pinned_login_node_drops_the_pin(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from hpc_bridge import server
    from hpc_bridge.state import EndpointRecord, LoginNodeStore
    from tests.fakes import FakeCatalog
    store = LoginNodeStore(tmp_path / "endpoints.json")
    store.put(EndpointRecord(endpoint_id="e1", login_host="login03.example.edu", alias="anvil", user="u",
                             key_path="/k", name="hpc-bridge-anvil", provisioned_at="2026-09-03T00:00:00Z"))
    fac = SimpleNamespace(store=store, alias="anvil", profile=SimpleNamespace(endpoint_name="hpc-bridge-anvil", scheduler="slurm"),
                          cli=SimpleNamespace(target=SimpleNamespace(host="login03.example.edu", user="u")))
    monkeypatch.setattr(server, "_facility_from_entry", lambda entry, *, account: fac)
    monkeypatch.setattr(server, "make_catalog", lambda: FakeCatalog([fake_entry(id="anvil", facility_key="purdue")]))

    async def unreachable(app, shape, **kw):
        raise RuntimeError("bootstrap failed: ssh: connect to host login03.example.edu port 22: Connection timed out")

    monkeypatch.setattr(server, "_provision", unreachable)
    app = AppCtx(facility=FakeFacility(), profile=Profile())
    res = await _connect_facility(app, "anvil")
    assert res.phase == "failed" and res.notice.startswith("CANNOT REACH") and "pin was dropped" in res.notice
    assert store.get(alias="anvil", name="hpc-bridge-anvil") is None
    # a REFUSED login keeps the pin: the host is fine, the access is not
    store.put(EndpointRecord(endpoint_id="e1", login_host="login03.example.edu", alias="anvil", user="u",
                             key_path="/k", name="hpc-bridge-anvil", provisioned_at="2026-09-03T00:00:00Z"))

    async def refused(app, shape, **kw):
        raise RuntimeError("seed storage.db (mkdir) failed: u@login03.example.edu: Permission denied (publickey).")

    monkeypatch.setattr(server, "_provision", refused)
    res = await _connect_facility(app, "anvil")
    assert res.notice.startswith("NO SSH ACCESS") and store.get(alias="anvil", name="hpc-bridge-anvil") is not None
