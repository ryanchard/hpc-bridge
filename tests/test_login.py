# tests/test_login.py — the in-terminal Globus login: the flow state machine (hermetic; the Globus SDK
# is used only for the paste-mode URL, which is built locally — no network) and the connect gate.
import time

import pytest

from hpc_bridge import login as login_mod
from hpc_bridge.login import FLOW_TTL_S, LoginFlow, required_scopes
from hpc_bridge.profile import Profile
from hpc_bridge.server import AppCtx, _authenticate, _complete_login
from tests.fakes import FakeCatalog, FakeFacility, fake_entry


class _FakeApp:
    """Stands in for the Compute-SDK-backed UserApp: `login_required()` is a knob; `login()` runs
    the given manager's URL callback (as the SDK does inside run_login_flow) then 'stores tokens'."""

    def __init__(self, required: bool, manager=None, fail: Exception | None = None):
        self.required, self.manager, self.fail = required, manager, fail
        self.logged_in = False

    def login_required(self):
        return self.required

    def login(self, force=False):
        if self.manager is not None and hasattr(self.manager, "emit"):
            self.manager.emit("https://auth.globus.org/v2/oauth2/authorize?fake=1")
        if self.fail:
            raise self.fail
        self.logged_in = True
        self.required = False


class _FakeManager:
    def __init__(self, on_url):
        self.on_url, self.aborted = on_url, None

    def emit(self, url):
        self.on_url(url)

    def abort(self, why):
        self.aborted = why


@pytest.fixture
def browser_flow(monkeypatch):
    """A LoginFlow whose browser mode uses the fake manager + fake app (no SDK, no browser)."""
    from hpc_bridge import login_flow_manager as lfm

    monkeypatch.setattr(lfm.CapturingLocalServerManager, "build", staticmethod(lambda *, on_url: _FakeManager(on_url)))
    apps = {}

    def factory(manager):
        apps["app"] = _FakeApp(required=True, manager=manager)
        return apps["app"]

    return LoginFlow(app_factory=factory, mode_override="browser"), apps


def test_required_scopes_cover_endpoint_start_and_search():
    rs = required_scopes()
    assert "search.api.globus.org" in rs and any("search.api.globus.org:search" in s for s in rs["search.api.globus.org"])
    assert any("manage_projects" in s for s in rs.get("auth.globus.org", []))  # what a started manager requires
    assert any(rs_ for rs_ in rs if "funcx" in rs_ or "compute" in rs_.lower())  # the compute scope's resource server


def test_login_required_is_non_prompting_and_conservative():
    assert LoginFlow(app_factory=lambda m: _FakeApp(required=True)).login_required() is True
    assert LoginFlow(app_factory=lambda m: _FakeApp(required=False)).login_required() is False

    def broken(m):
        raise OSError("no storage.db")

    assert LoginFlow(app_factory=broken).login_required() is True  # unreadable credential == absent


def test_browser_flow_returns_the_captured_url_then_completes(browser_flow):
    flow, apps = browser_flow
    start = flow.start()
    assert start.mode == "browser" and start.login_url.startswith("https://auth.globus.org/")
    # idempotent while waiting: the same URL, no second thread/flow
    assert flow.start().login_url == start.login_url
    flow._thread.join(timeout=5)
    assert apps["app"].logged_in and flow.status() == "done"


def test_browser_flow_failure_before_url_falls_back_to_paste(monkeypatch):
    from hpc_bridge import login_flow_manager as lfm

    class _NoUrlManager(_FakeManager):
        def emit(self, url):  # the SDK's flow died before producing a URL (no browser / remote)
            pass

    monkeypatch.setattr(lfm.CapturingLocalServerManager, "build", staticmethod(lambda *, on_url: _NoUrlManager(on_url)))
    flow = LoginFlow(app_factory=lambda m: _FakeApp(required=True, manager=m, fail=RuntimeError("no browser")), mode_override="browser")
    start = flow.start()
    assert start.mode == "paste"
    assert "auth.globus.org/v2/oauth2/authorize" in start.login_url and "4cf29807-cf21-49ec-9443-ff9a3fb9f81c" in start.login_url
    assert "auth-code" in start.login_url  # Globus's own copy-the-code page is the redirect
    assert flow.error and "paste-back" in flow.error


def test_paste_flow_url_carries_every_required_scope_and_pkce():
    flow = LoginFlow(app_factory=lambda m: _FakeApp(required=True), mode_override="paste")
    url = flow.start().login_url
    assert "manage_projects" in url and "search.api.globus.org" in url and "code_challenge=" in url
    assert "access_type=offline" in url  # refresh tokens: one round-trip for the life of the install


def test_expiry_rearms_a_fresh_flow():
    # paste mode: nothing completes on its own, so the flow WAITS until its TTL lapses
    flow = LoginFlow(app_factory=lambda m: _FakeApp(required=True), mode_override="paste", ttl_s=0.01)
    first = flow.start()
    time.sleep(0.05)
    assert flow.status() == "expired"
    second = flow.start()
    assert second.expires_at > first.expires_at  # a new flow, not the stale one


def test_complete_with_code_requires_a_waiting_paste_flow(monkeypatch):
    flow = LoginFlow(app_factory=lambda m: _FakeApp(required=True), mode_override="paste")
    with pytest.raises(RuntimeError, match="authenticate"):
        flow.complete_with_code("abc")  # nothing waiting
    flow.start()
    stored = {}
    monkeypatch.setattr("hpc_bridge.login_flow_manager.store_paste_tokens", lambda client, code, f: stored.update(code=code))
    flow.complete_with_code("  the-code  ")
    assert stored["code"] == "the-code" and flow.status() == "done"


# --- the connect gate + tools -----------------------------------------------------------------------


class _StubFlow:
    def __init__(self, required):
        self.required, self.error, self.started = required, None, 0

    def login_required(self):
        return self.required

    def start(self, mode=None):
        self.started += 1
        from hpc_bridge.login import LoginStart
        return LoginStart(login_url="https://auth.globus.org/v2/oauth2/authorize?x=1", mode="browser", expires_at=time.monotonic() + FLOW_TTL_S)

    def complete_with_code(self, code):
        self.required = False


async def test_connect_gates_on_login_before_any_ssh(monkeypatch):
    from hpc_bridge import server

    app = AppCtx(facility=FakeFacility(), profile=Profile())
    app.login_flow = _StubFlow(required=True)
    monkeypatch.setattr(server, "make_catalog", lambda: FakeCatalog([fake_entry(id="anvil", facility_key="purdue")]))
    built = []
    monkeypatch.setattr(server, "_facility_from_entry", lambda e, *, account: built.append(1))
    res = await server._connect_facility(app, "anvil")
    assert res.phase == "needs_login" and res.login_mode == "browser"
    assert res.login_url.startswith("https://auth.globus.org/")
    assert "connect_facility again" in res.notice and "never ask" in res.notice.lower()
    assert built == []  # no facility, no SSH, no bootstrap until the login lands
    assert app.login_flow.started == 1


async def test_connect_proceeds_when_logged_in_and_when_ungated(monkeypatch):
    from hpc_bridge import server
    from tests.test_server import _FakeRunner, _Res

    for flow in (_StubFlow(required=False), None):
        f = FakeFacility()
        f.workers = 1
        app = AppCtx(facility=FakeFacility(), profile=Profile())
        app.login_flow = flow
        app.runner_factory = lambda eid, user_endpoint_config=None, **_kw: _FakeRunner(eid, _Res(0, "", ""))
        entry = fake_entry(id="anvil", facility_key="purdue")
        entry.allocation = None
        monkeypatch.setattr(server, "make_catalog", lambda: FakeCatalog([entry]))
        monkeypatch.setattr(server, "_facility_from_entry", lambda e, *, account: f)
        res = await server._connect_facility(app, "anvil")
        assert res.phase == "needs_account", (flow, res.notice)


async def test_authenticate_and_complete_login_tools():
    app = AppCtx(facility=FakeFacility(), profile=Profile())
    app.login_flow = _StubFlow(required=True)
    st = await _authenticate(app)
    assert st.phase == "needs_login" and st.login_url and st.login_mode == "browser"
    done = await _complete_login(app, "code")
    assert done.phase == "logged_in"
    assert (await _authenticate(app)).phase == "logged_in"
    # force re-login re-arms even when logged in
    assert (await _authenticate(app, force=True)).phase == "needs_login"


async def test_complete_login_without_a_flow_is_structured():
    app = AppCtx(facility=FakeFacility(), profile=Profile())
    assert (await _complete_login(app, "x")).phase == "failed"


def test_needs_login_is_a_known_phase():
    from hpc_bridge.models import ConnectFacilityResult
    r = ConnectFacilityResult(phase="needs_login", facility="f", login_url="https://x", login_mode="paste")
    assert r.login_mode == "paste"
