from __future__ import annotations

from hpc_bridge.profile import Profile
from hpc_bridge.runner import CanaryResult
from hpc_bridge.server import AppCtx, _ensure_endpoint_up, _list_facilities, mcp
from tests.fakes import FakeCatalog, FakeFacility, fake_entry


class _Res:
    def __init__(self, rc, out, err):
        self.returncode, self.stdout, self.stderr = rc, out, err


class _FakeRunner:
    def __init__(self, endpoint_id, res, *, canary_result=None):
        self.endpoint_id = endpoint_id
        self._res = res
        self._canary = canary_result or CanaryResult(
            ok=True, worker_host="a070", worker_python="3.11.7", worker_dill="0.3.9"
        )
        self.closed = False
        self.commands: list[str] = []
        self.canaries = 0

    async def run(self, command):
        self.commands.append(command)
        return self._res

    async def canary(self, timeout=8.0):
        self.canaries += 1
        return self._canary

    def close(self):
        self.closed = True


# --- account selection: connect_facility's choice -> the Slurm block --------------------------


async def test_ensure_endpoint_up_provisions_with_selected_account():
    # The chosen allocation flows into the shape's user_endpoint_config (the per-task render var),
    # exactly like the partition selection — account -> SlurmProvider.account.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "", ""))
    res = await _ensure_endpoint_up(app, account="cis250223", confirm_spend=True)
    assert res.account == "cis250223"
    assert app.shapes["slurm"].user_endpoint_config["account"] == "cis250223"


async def test_account_change_invalidates_runner():
    # A different account is a different Slurm block: the cached Executor captured the old account
    # at build time, so the runner must be rebuilt (mirrors the partition-change behaviour).
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "", ""))
    await _ensure_endpoint_up(app, account="cis250223", confirm_spend=True)
    r1 = app.shapes["slurm"].runner
    await _ensure_endpoint_up(app, account="cis999999", confirm_spend=True)
    assert app.shapes["slurm"].runner is not r1
    assert app.shapes["slurm"].user_endpoint_config["account"] == "cis999999"


async def test_ensure_endpoint_up_rejects_invalid_account():
    # account renders into a remote Jinja template -> reject anything outside the allowlist before
    # provisioning (same guard as partition).
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    res = await _ensure_endpoint_up(app, account="bad; rm -rf /")
    assert res.status == "down"
    assert res.notice and "invalid account" in res.notice


async def test_login_shape_ignores_account():
    # A LocalProvider (login) shape charges nothing: a supplied account is not forced onto it.
    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=f, profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, "", ""))
    res = await _ensure_endpoint_up(app, shape="login", account="cis250223")
    assert "account" not in app.shapes["login"].user_endpoint_config
    assert res.account is None


# --- list_facilities: browse the catalog (agent-safe, no SSH) ----------------------------------


async def test_list_facilities_returns_summaries(monkeypatch):
    from hpc_bridge import server

    cat = FakeCatalog(
        [fake_entry(id="anvil", facility_key="purdue"), fake_entry(id="polaris", facility_key="alcf")]
    )
    monkeypatch.setattr(server, "make_catalog", lambda: cat)
    out = await _list_facilities("")
    assert {s.id for s in out} == {"anvil", "polaris"}
    assert [s.id for s in await _list_facilities("polaris")] == ["polaris"]


async def test_list_facilities_summaries_are_agent_safe(monkeypatch):
    from hpc_bridge import server

    monkeypatch.setattr(
        server, "make_catalog", lambda: FakeCatalog([fake_entry(id="anvil", facility_key="purdue")])
    )
    fields = set(type((await _list_facilities(""))[0]).model_fields)
    # identity/provenance only — never executable config or raw UUIDs.
    assert {"subject", "id", "facility", "provenance", "last_validated"} <= fields
    assert not ({"env_setup", "compute", "transfer_endpoint_uuid", "ssh_host"} & fields)


async def test_server_registers_list_facilities_tool():
    assert "list_facilities" in {t.name for t in await mcp.list_tools()}


# --- connect_facility: bind a machine, bring up its login node, list allocations ---------------

# Real Anvil `mybalance` output (the login-shape allocation command returns this).
MYBALANCE = """
Allocation     Type    SU Limit    SU Usage   SU Usage  SU Balance
Account                           (account)     (user)
=============  ====  ==========  ========== ==========  ==========
cis250223       CPU     10001.0      1014.9      214.4      8986.1
cis250223-gpu   GPU      1000.0         0.0        0.0      1000.0
"""


async def test_connect_facility_brings_up_login_and_lists_allocations(monkeypatch):
    from hpc_bridge import server

    f = FakeFacility()
    f.workers = 1  # manager online; the runner's canary (below) confirms the login worker
    app = AppCtx(facility=FakeFacility(), profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, MYBALANCE, ""))
    monkeypatch.setattr(
        server, "make_catalog", lambda: FakeCatalog([fake_entry(id="anvil", facility_key="purdue")])
    )
    monkeypatch.setattr(server, "_facility_from_entry", lambda entry, *, account: f)

    res = await server._connect_facility(app, "anvil")
    assert res.phase == "needs_account"
    assert res.machine == "anvil"
    assert [a.account for a in res.allocations] == ["cis250223", "cis250223-gpu"]
    assert app.facility is f and app.machine == "anvil"  # late-bound the chosen machine


async def test_connect_facility_unknown_machine_is_not_found(monkeypatch):
    from hpc_bridge import server

    app = AppCtx(facility=FakeFacility(), profile=Profile())
    monkeypatch.setattr(
        server, "make_catalog", lambda: FakeCatalog([fake_entry(id="anvil", facility_key="purdue")])
    )
    res = await server._connect_facility(app, "nope")
    assert res.phase == "not_found" and res.allocations == []


async def test_connect_facility_provisioning_when_login_worker_cold(monkeypatch):
    # Manager online but the login worker hasn't answered the canary -> phase="provisioning" (no
    # allocations yet, command never dispatched); the agent calls connect_facility again shortly.
    from hpc_bridge import server
    from hpc_bridge.runner import CanaryResult

    f = FakeFacility()
    f.workers = 1
    app = AppCtx(facility=FakeFacility(), profile=Profile())
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(
        eid, _Res(0, MYBALANCE, ""), canary_result=CanaryResult(ok=False, error="timeout")
    )
    monkeypatch.setattr(
        server, "make_catalog", lambda: FakeCatalog([fake_entry(id="anvil", facility_key="purdue")])
    )
    monkeypatch.setattr(server, "_facility_from_entry", lambda entry, *, account: f)
    res = await server._connect_facility(app, "anvil")
    assert res.phase == "provisioning" and res.allocations == []


async def test_server_registers_connect_facility_tool():
    assert "connect_facility" in {t.name for t in await mcp.list_tools()}


# --- hard failure when the catalog is unavailable (no bundled fallback) ------------------------


def _no_catalog():
    raise RuntimeError("HPC_BRIDGE_SEARCH_INDEX is required")


async def test_connect_facility_hard_fails_without_catalog(monkeypatch):
    # No index / search scope -> make_catalog raises -> connect surfaces a structured failure,
    # never a hardcoded default.
    from hpc_bridge import server

    monkeypatch.setattr(server, "make_catalog", _no_catalog)
    app = AppCtx(facility=FakeFacility(), profile=Profile())
    res = await server._connect_facility(app, "anvil")
    assert res.phase == "failed" and res.notice and "catalog unavailable" in res.notice


async def test_list_facilities_empty_without_catalog(monkeypatch):
    from hpc_bridge import server

    monkeypatch.setattr(server, "make_catalog", _no_catalog)
    assert await _list_facilities("") == []


# --- regressions from the live test --------------------------------------------------------------


async def test_connect_facility_moves_scratch_root_to_the_facility(monkeypatch):
    # Live-test bug: the server boots LocalFacility (scratch ~/.hpc-bridge -> /Users/...), and
    # connect_facility late-binds the remote facility but must ALSO move the session-shell root,
    # else run_shell uses the local path ON the remote node (`mkdir /Users/...: Permission denied`).
    from hpc_bridge import server

    f = FakeFacility()
    f.workers = 1
    f.scratch_root = "/anvil/scratch/me/.hpc-bridge"  # the bound facility's remote scratch
    app = AppCtx(facility=FakeFacility(), profile=Profile())  # starts with a different (local) facility
    app.runner_factory = lambda eid, user_endpoint_config=None: _FakeRunner(eid, _Res(0, MYBALANCE, ""))
    monkeypatch.delenv("HPC_BRIDGE_SCRATCH", raising=False)
    monkeypatch.setattr(
        server, "make_catalog", lambda: FakeCatalog([fake_entry(id="anvil", facility_key="purdue")])
    )
    monkeypatch.setattr(server, "_facility_from_entry", lambda entry, *, account: f)
    await server._connect_facility(app, "anvil")
    assert app.scratch_root == "/anvil/scratch/me/.hpc-bridge"


async def test_stop_endpoint_bounds_a_slow_teardown(monkeypatch):
    # Live test "hung on stop": teardown makes 2-3 SSH calls at 120s each on a loaded login node.
    # _stop_endpoint must cap it so the tool returns promptly with a clear notice (pin kept).
    import asyncio

    from hpc_bridge import server
    from hpc_bridge.lifecycle import EndpointState

    f = FakeFacility()

    async def slow_teardown(eid):
        await asyncio.sleep(1)

    f.teardown = slow_teardown
    app = AppCtx(facility=f, profile=Profile(), state=EndpointState(endpoint_id="eid-1"))
    monkeypatch.setattr(server, "STOP_TIMEOUT_S", 0.05)
    res = await server._stop_endpoint(app)
    assert res.notice and "timed out" in res.notice


async def test_stop_endpoint_releases_block_over_login_amqp(monkeypatch):
    # The block release should go over the WARM login shape (AMQP) — not a fresh SSH — before the
    # SSH teardown, so a loaded login node can't make stop hang.
    from hpc_bridge import server
    from hpc_bridge.lifecycle import EndpointState
    from hpc_bridge.models import ShellOutcome
    from hpc_bridge.server import ShapeRuntime

    app = AppCtx(facility=FakeFacility(), profile=Profile(), state=EndpointState(endpoint_id="eid-xyz"))
    app.shapes["login"] = ShapeRuntime(
        user_endpoint_config={"provider_type": "LocalProvider"},
        runner=_FakeRunner("eid", _Res(0, "", "")),
        warm_confirmed_at=1.0,
    )
    seen = {}

    async def fake_run_shell(a, command, session_id="default", shape="slurm"):
        seen["shape"], seen["cmd"] = shape, command
        return ShellOutcome(phase="complete", block_state="warm")

    monkeypatch.setattr(server, "_run_shell", fake_run_shell)
    await server._stop_endpoint(app)
    assert seen.get("shape") == "login"
    assert "scancel" in seen["cmd"] and "uep.eid-xyz" in seen["cmd"]


async def test_stop_endpoint_skips_amqp_when_login_not_warm(monkeypatch):
    # No warm login shape -> don't try AMQP (run_shell would SSH-bootstrap); the SSH teardown backstops.
    from hpc_bridge import server
    from hpc_bridge.lifecycle import EndpointState
    from hpc_bridge.models import ShellOutcome

    app = AppCtx(facility=FakeFacility(), profile=Profile(), state=EndpointState(endpoint_id="eid-1"))
    called = {"n": 0}

    async def fake_run_shell(*a, **k):
        called["n"] += 1
        return ShellOutcome(phase="complete", block_state="warm")

    monkeypatch.setattr(server, "_run_shell", fake_run_shell)
    await server._stop_endpoint(app)  # no app.shapes["login"] -> guard skips the AMQP path
    assert called["n"] == 0
