"""Security review 2026-09-04 — the fixes, pinned. Findings A1-A4/B-01..B-06/C-1..C-8 in the vault's
Reference/Security review 2026-09-04.md. Every test here is a regression guard for one of them."""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import stat
import subprocess

import pytest

from hpc_bridge import config
from hpc_bridge.catalog.entry import CatalogEntry
from hpc_bridge.facility import remote
from hpc_bridge.facility.remote import RemoteEndpointCLI, SshTarget
from hpc_bridge.models import FacilityDetails, validate_host
from hpc_bridge.notices import _explain_provision_error, _first_contact_note


def _seed_entry():
    import yaml

    with open("src/hpc_bridge/catalog/seed/anvil.yaml") as fh:
        doc = yaml.safe_load(fh)
    return doc[0] if isinstance(doc, list) else doc


# ---- host string: the one input that reaches ssh's argv (A3 / C-2) --------------------------------------------

BAD_HOSTS = ["-oProxyCommand=touch /tmp/pwned", "-F/etc/passwd", "host name", "host;id", "h$(id)", "", "a\nb"]
OPTION_SHAPED = {"-oProxyCommand=touch /tmp/pwned", "-F/etc/passwd", "host name", "", "a\nb"}  # what argv itself must refuse
GOOD_HOSTS = ["globus1", "anvil.rcac.purdue.edu", "login-4.rcc.uchicago.edu", "10.0.0.7", "my_alias"]


@pytest.mark.parametrize("bad", BAD_HOSTS)
def test_bad_hosts_are_rejected_everywhere(bad):
    with pytest.raises(ValueError):
        validate_host(bad)  # the model boundary rejects every shape
    if bad in OPTION_SHAPED:  # argv is exec'd (no shell): the target itself only has to refuse option/whitespace shapes
        with pytest.raises(ValueError):
            SshTarget(host=bad)
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError wraps the ValueError
        FacilityDetails(ssh_host=bad, interface="ib0", scheduler="slurm", scratch_root="/scratch",
                        env_setup="true", default_partition="main")


@pytest.mark.parametrize("good", GOOD_HOSTS)
def test_good_hosts_pass(good):
    assert validate_host(good) == good
    assert SshTarget(host=good).argv("true")[-2] == good


def test_argv_never_lets_the_destination_be_read_as_an_option():
    argv = SshTarget(host="globus1").argv("true")
    assert argv[argv.index("--") + 1] == "globus1"
    assert not any(o.startswith("StrictHostKeyChecking") for o in argv)  # your ssh's trust decides, not ours


def test_registry_entry_with_a_bad_host_or_slug_is_refused():
    seed = _seed_entry()
    CatalogEntry.model_validate(seed)  # the real seed is fine
    for field, bad in (("ssh_host", "-oProxyCommand=x"), ("id", "x;$(id)"), ("facility_key", "a b")):
        with pytest.raises(Exception):  # noqa: B017 - ValidationError
            CatalogEntry.model_validate({**seed, field: bad})


# ---- pinned login node: the key is checked against the alias the user trusted (A10) ----------------------------

def test_rebind_to_a_pinned_node_keeps_the_trusted_name_as_host_key_alias(monkeypatch):
    # known_hosts is keyed by the RESOLVED HostName: an ssh-config alias resolves through `ssh -G` (live 2026-09-04)
    monkeypatch.setattr(remote, "_resolved_hostname", lambda h: {"anvil": "anvil.rcac.purdue.edu"}.get(h, h))
    cli = RemoteEndpointCLI(SshTarget(host="anvil", user="u"), "true")
    cli.rebind("login04.anvil.rcac.purdue.edu")
    argv = cli.target.argv("true")
    assert cli.target.host == "login04.anvil.rcac.purdue.edu"
    assert "HostKeyAlias=anvil.rcac.purdue.edu" in argv
    cli.rebind("login05.anvil.rcac.purdue.edu")  # a second pin still verifies against the ORIGINAL trusted name
    assert "HostKeyAlias=anvil.rcac.purdue.edu" in cli.target.argv("true")


def test_resolved_hostname_falls_back_to_the_name_itself(monkeypatch):
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("no ssh")))
    assert remote._resolved_hostname("anvil") == "anvil"


# ---- the user's paste-and-run line is quoted (A4 / C-9) --------------------------------------------------------

def test_preauth_command_is_shell_safe():
    cmd = SshTarget(host="mid.way", user="gu s", control_dir="/tmp/c m").preauth_command()
    tokens = shlex.split(cmd)  # round-trips: every token quoted, nothing is a shell metacharacter
    assert tokens[-1] == "gu s@mid.way" and "--" in tokens and "ControlPath=/tmp/c m/%C" in tokens


# ---- unknown / changed host key is explained, not swallowed as CANNOT REACH -----------------------------------

def test_unknown_host_key_is_explained_with_the_user_terminal_remedy():
    exc = RuntimeError("remote list failed: Host key verification failed.")
    msg = _explain_provision_error(exc, host="new.host.edu", user="me")
    assert msg.startswith("UNKNOWN HOST KEY for new.host.edu")
    assert "ssh me@new.host.edu" in msg and "Nothing was started" in msg


def test_changed_host_key_says_changed():
    exc = RuntimeError("seed storage.db (mkdir) failed: @@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@")
    msg = _explain_provision_error(exc, host="anvil.rcac.purdue.edu")
    assert msg.startswith("UNKNOWN HOST KEY") and "CHANGED" in msg


# ---- first contact is visible in the transcript (A2 / C-1) ---------------------------------------------------

def test_first_contact_note_names_user_host_and_env_setup(monkeypatch):
    class _Fac:
        cli = RemoteEndpointCLI(SshTarget(host="anvil.rcac.purdue.edu", user="alice"), "module load python; . ~/v/bin/activate")

    monkeypatch.delenv("HPC_BRIDGE_SSH_HOST", raising=False)
    note = _first_contact_note(_Fac())
    assert note.startswith("first contact over SSH: alice@anvil.rcac.purdue.edu")
    assert "module load python" in note and "pinned" not in note
    monkeypatch.setenv("HPC_BRIDGE_SSH_HOST", "anvil.rcac.purdue.edu")
    assert "pinned by HPC_BRIDGE_SSH_HOST" in _first_contact_note(_Fac())


def test_first_contact_note_is_empty_for_a_facility_without_ssh():
    assert _first_contact_note(object()) == ""


# ---- connect_facility refuses a bad ssh_host before any ssh (A3 / C-2) -----------------------------------------

async def test_connect_facility_refuses_a_bad_ssh_host_without_touching_ssh(monkeypatch):
    from hpc_bridge import connect
    from hpc_bridge.context import AppCtx

    calls = []

    async def spy(*a, **k):
        calls.append(a)
        return (0, "", "")

    monkeypatch.setattr(remote, "ssh_exec", spy)
    from hpc_bridge.profile import Profile
    from tests.fakes import FakeFacility

    app = AppCtx(facility=FakeFacility(), profile=Profile())
    app.login_flow = None

    async def _no_login(*a, **k):
        raise AssertionError("login must not run")

    res = await connect._connect_facility(app, "x", ssh_host="-oProxyCommand=touch /tmp/p", run_login=_no_login)
    assert res.phase == "failed" and "invalid login host" in (res.notice or "")
    assert calls == []


# ---- the /tmp ControlMaster fallback does not follow a planted symlink (B-04) ---------------------------------

def test_control_dir_fallback_skips_a_symlink_squat(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_CONTROL_PATH_BUDGET", 60)
    home = tmp_path / "h"
    (home / ".hpc-bridge").mkdir(parents=True)
    victim = tmp_path / "elsewhere"
    victim.mkdir()
    os.symlink(victim, home / ".hpc-bridge" / "cm")  # the attacker's symlink at our predictable name
    monkeypatch.setattr(config.Path, "home", staticmethod(lambda: home))
    chosen = config._short_control_dir("x" * 200)
    assert chosen != str(home / ".hpc-bridge" / "cm")
    assert not (os.path.lexists(chosen) and stat.S_ISLNK(os.lstat(chosen).st_mode))


# ---- registry cache: a definitive miss forgets; only a transport failure serves the offline copy (C-3) ---------

class _NotFound(Exception):
    http_status = 404


class _Client:
    def __init__(self, subject=None, search=None, fail=None):
        self.subject, self.search, self.fail = subject, search, fail

    def get_subject(self, index, subject):
        if self.fail:
            raise self.fail
        if self.subject is None:
            raise _NotFound("404")
        return {"entries": [{"content": self.subject}]}

    def post_search(self, index, q):
        if self.fail:
            raise self.fail
        return {"gmeta": self.search or []}




async def test_registry_404_forgets_the_cached_copy(tmp_path):
    from hpc_bridge.catalog.search import SearchCatalog

    cat = SearchCatalog("idx", _Client(subject=_seed_entry()), tmp_path)
    assert (await cat.get("anvil")) is not None  # live hit: cached under every name it was asked by
    assert any(tmp_path.iterdir())
    for f in tmp_path.iterdir():
        assert (f.stat().st_mode & 0o077) == 0  # 0600
    cat._client = _Client(subject=None)  # the curator retracts it: a clean 404
    assert (await cat.get("anvil")) is None  # not served from the cache…
    assert not (tmp_path / "anvil.json").exists()  # …and the copy is gone


async def test_registry_outage_still_serves_the_cached_copy(tmp_path):
    from hpc_bridge.catalog.search import SearchCatalog

    cat = SearchCatalog("idx", _Client(subject=_seed_entry()), tmp_path)
    assert (await cat.get("anvil")) is not None
    cat._client = _Client(fail=ConnectionError("network down"))
    assert (await cat.get("anvil")) is not None  # offline copy: the one legitimate use of the cache


# ---- supply chain: the server runs against the lockfile; the remote install is pinned (C-6) -------------------

def test_mcp_launch_is_locked():
    c = json.loads(open(".mcp.json").read())  # noqa: SIM115
    args = c["mcpServers"]["endpoint"]["args"]
    assert args[:2] == ["run", "--locked"]


def test_remote_endpoint_install_is_pinned_to_the_sdk_version():
    from hpc_bridge import discovery

    pin = discovery._gce_pin()
    try:
        from importlib.metadata import version

        expected = f"'globus-compute-endpoint=={version('globus-compute-sdk')}'"
    except Exception:  # noqa: BLE001 - hermetic env without the SDK
        expected = "globus-compute-endpoint"
    assert pin == expected and pin in discovery._UV_ENV_SETUP


# ---- the identity that landed is named (B-02) -------------------------------------------------------------------

async def test_logged_in_notice_names_the_identity(monkeypatch):
    from hpc_bridge import login_gate

    monkeypatch.setattr(login_gate, "globus_identity_label", lambda: "alice@uni.edu")
    assert await login_gate._as_identity() == " as alice@uni.edu"
    monkeypatch.setattr(login_gate, "globus_identity_label", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    assert await login_gate._as_identity() == ""


def test_sync_helpers_are_importable():  # keeps `asyncio`/`subprocess` imports honest for the async tests above
    assert asyncio and subprocess


# ---- teardown wipes ONLY the token store hpc-bridge seeded (B-03, refined) ------------------------------------

def _bootstrap_parts(tmp_path, monkeypatch, *, remote_db_present):
    from hpc_bridge.facility.remote import SlurmFacility
    from hpc_bridge.state import LoginNodeStore
    from tests.test_remote_facility import _BootstrapCLI, _no_endpoints, _profile

    cli = _BootstrapCLI(status=None, remote_db_present=remote_db_present)
    wiped = []

    async def _wipe():
        wiped.append(True)

    cli.wipe_storage_db = _wipe
    made = tmp_path / "trimmed.db"
    made.write_bytes(b"db")
    monkeypatch.setattr(remote, "build_minimal_storage_db", lambda **kw: made)
    store = LoginNodeStore(tmp_path / "endpoints.json")
    fac = SlurmFacility(_profile(), cli=cli, client_factory=_no_endpoints, store=store, alias="anvil.rcac.purdue.edu")
    return fac, cli, store, wiped


async def test_teardown_wipes_the_store_hpc_bridge_seeded(tmp_path, monkeypatch):
    from hpc_bridge.profile import Profile

    fac, cli, store, wiped = _bootstrap_parts(tmp_path, monkeypatch, remote_db_present=False)
    handle = await fac.bootstrap(Profile(mode="interactive"))
    assert cli.seeded is not None  # we placed it…
    assert store.get(alias="anvil.rcac.purdue.edu", name=fac.profile.endpoint_name).seeded_credentials is True
    await fac.teardown(handle.endpoint_id, wipe_credentials=True)
    assert wiped == [True]  # …so teardown removes it


async def test_teardown_never_wipes_a_store_that_was_already_there(tmp_path, monkeypatch):
    from hpc_bridge.profile import Profile

    fac, cli, _store, wiped = _bootstrap_parts(tmp_path, monkeypatch, remote_db_present=True)
    handle = await fac.bootstrap(Profile(mode="interactive"))
    assert cli.seeded is None  # the login node could already authenticate: not ours
    await fac.teardown(handle.endpoint_id, wipe_credentials=True)
    assert wiped == []  # a shared account's credential survives our teardown


async def test_teardown_in_a_later_session_reads_the_record(tmp_path, monkeypatch):
    from hpc_bridge.facility.remote import SlurmFacility
    from hpc_bridge.profile import Profile
    from tests.test_remote_facility import _BootstrapCLI, _no_endpoints, _profile

    fac, _cli, store, _ = _bootstrap_parts(tmp_path, monkeypatch, remote_db_present=False)
    handle = await fac.bootstrap(Profile(mode="interactive"))
    # a new process: no in-memory flag, only the endpoint record
    cli2 = _BootstrapCLI(status="running", remote_db_present=True)
    wiped = []

    async def _wipe():
        wiped.append(True)

    cli2.wipe_storage_db = _wipe
    fac2 = SlurmFacility(_profile(), cli=cli2, client_factory=_no_endpoints, store=store, alias="anvil.rcac.purdue.edu")
    await fac2.teardown(handle.endpoint_id, wipe_credentials=True)
    assert wiped == [True]


def test_old_endpoint_records_load_without_the_new_field(tmp_path):
    import json

    from hpc_bridge.state import LoginNodeStore

    p = tmp_path / "endpoints.json"
    p.write_text(json.dumps({"a::n": {"endpoint_id": "e", "login_host": "h", "alias": "a", "user": None,
                                       "key_path": None, "name": "n", "provisioned_at": "t"}}))
    rec = LoginNodeStore(p).get(alias="a", name="n")
    assert rec is not None and rec.seeded_credentials is False  # unknown ⇒ never wipe


# ---- consent is per conversation; a fresh bootstrap is proven and is not a "reuse" (live 2026-09-04) ----------

def _app():
    from hpc_bridge.context import AppCtx
    from hpc_bridge.profile import Profile
    from tests.fakes import FakeFacility

    return AppCtx(facility=FakeFacility(), profile=Profile())


def test_proven_commit_counts_an_endpoint_this_session_bootstrapped(monkeypatch, tmp_path):
    from hpc_bridge import binding, connect
    from hpc_bridge.models import FacilityDetails
    from hpc_bridge.state import FacilityStore

    store = FacilityStore(tmp_path / "facilities.json")
    monkeypatch.setattr(binding, "_facility_store", lambda: store)
    details = FacilityDetails(ssh_host="h.example.edu", interface="ib0", env_setup="true",
                              scratch_root="/s/{user}", partition="main").model_dump(mode="json")
    app = _app()
    app.state.reused = True  # the just-started endpoint was re-found online on the next connect
    app.bootstrapped_facilities.add("byo")  # …but THIS session started it
    app.pending_facility_cache["byo"] = ("h.example.edu", details)
    connect._commit_proven_facility(app, "byo")
    assert store.get("h.example.edu") is not None  # proven: the canary ran on these details
    app2 = _app()
    app2.state.reused = True  # reattached to an endpoint from BEFORE this session: proves nothing
    app2.pending_facility_cache["x"] = ("other.example.edu", details)
    connect._commit_proven_facility(app2, "x")
    assert store.get("other.example.edu") is None


def test_reuse_note_distinguishes_our_own_fresh_start_from_a_real_reuse():
    from hpc_bridge import connect

    app = _app()
    assert connect._reuse_note(app, "f", True, object()).startswith("reused the already-online endpoint")
    app.bootstrapped_facilities.add("f")
    assert connect._reuse_note(app, "f", True, object()).startswith("reconnected to the endpoint this session started")
    assert connect._reuse_note(app, "f", False, object()) == ""  # no cli on a bare object: no first-contact note


async def test_proposal_notice_forbids_reprobing_and_remembered_consent(monkeypatch):
    from hpc_bridge import connect
    from hpc_bridge.models import FacilityDetails

    draft = FacilityDetails(ssh_host="h.example.edu", interface="ib0", env_setup="true",
                            scratch_root="/s/{user}", partition="main")

    async def fake_discover(target):
        return draft, ["interface: guessed"]

    monkeypatch.setattr(connect, "discover_facility_details", fake_discover)
    res = await connect._propose_or_ask("f", "h.example.edu", "ask")
    assert res.phase == "proposed_facility_details"
    assert "do NOT re-probe" in res.notice and "THIS conversation" in res.notice


async def test_silent_login_path_names_the_identity(monkeypatch):
    from hpc_bridge import binding, connect, login_gate, warmth
    from tests.fakes import FakeFacility

    class _Flow:
        error = None

        def login_required(self):
            return True

    async def landed(flow, mode=None):
        return object(), "done"

    async def ident():
        return " as alice@uni.edu"

    monkeypatch.setattr(login_gate, "_start_login_and_wait", landed)
    monkeypatch.setattr(login_gate, "_as_identity", ident)
    f = FakeFacility()
    monkeypatch.setattr(binding, "_facility_from_entry", lambda entry, *, account: f)

    async def provision(app, shape, **kw):
        return "provisioning"

    monkeypatch.setattr(warmth, "_provision", provision)
    from hpc_bridge.models import FacilityDetails

    details = FacilityDetails(ssh_host="h.example.edu", interface="ib0", env_setup="true",
                              scratch_root="/s/{user}", partition="main")
    app = _app()
    app.login_flow = _Flow()

    async def _no_login_runner(*a, **k):
        raise AssertionError("not reached")

    res = await connect._connect_facility(app, "byo", details=details, run_login=_no_login_runner)
    assert res.notice.startswith("Globus login landed as alice@uni.edu. "), res.notice

