import asyncio
import base64

import jinja2
import pytest
import yaml as _yaml

from hpc_bridge.facility import remote
from hpc_bridge.facility.base import EndpointHandle, Facility
from hpc_bridge.facility.remote import RemoteEndpointCLI, SlurmFacility, SshTarget, anvil_profile
from hpc_bridge.profile import Profile
from hpc_bridge.shapes import shape_config


def _profile():
    return anvil_profile(account="ACC", user="x-u")


async def test_ssh_exec_kills_child_on_timeout(monkeypatch):
    # A connected ssh session that wedges mid-command must not leak: on timeout the child is
    # killed and reaped (else process + 3 pipe FDs leak per stuck control-plane call).
    state = {"killed": False, "waited": False}

    class _HangingProc:
        returncode = None

        async def communicate(self, payload=None):
            await asyncio.sleep(30)  # never returns within the test timeout

        def kill(self):
            state["killed"] = True
            self.returncode = -9

        async def wait(self):
            state["waited"] = True
            return -9

    async def _fake_create(*_a, **_k):
        return _HangingProc()

    monkeypatch.setattr(remote.asyncio, "create_subprocess_exec", _fake_create)
    with pytest.raises(TimeoutError):
        await remote.ssh_exec(SshTarget("h", "u", "/k"), "hang", timeout=0.05)
    assert state["killed"] and state["waited"]  # child killed + reaped, not abandoned


def test_anvil_profile_fields():
    p = anvil_profile(account="cis250223", user="x-amcsweeneyel")
    assert p.name == "anvil" and p.account == "cis250223" and p.interface == "ib0"
    assert p.partition == "debug"
    assert "anaconda/2024.02-py311" in p.env_setup and "gce-venv/bin/activate" in p.env_setup
    assert p.scratch_root == "/anvil/scratch/x-amcsweeneyel/.hpc-bridge"
    assert p.worker_init == p.env_setup  # worker replays the same env
    assert p.endpoint_name == "hpc-bridge"  # registration/dir name
    assert p.display_name == "HPC-Bridge Anvil"  # human label for the web UI


def _sanitize_user_json(opts):
    """Mirror the endpoint manager's `_sanitize_user_json`: json.dumps every string (its
    YAML-injection guard), which QUOTES strings ("x" -> '"x"'). Tests MUST apply this or
    they miss the whole class of bug where a quoted string breaks an in-template compare or
    gets double-encoded by `| tojson` (both shipped to Anvil before being caught)."""
    import json

    def inner(v):
        if isinstance(v, dict):
            return {k: inner(x) for k, x in v.items()}
        if isinstance(v, list):
            return [inner(x) for x in v]
        if isinstance(v, str):
            return json.dumps(v)
        if isinstance(v, (int, float)):  # bool is an int -> passes through unchanged
            return v
        if v is None:
            return "null"
        return v

    return inner(opts)


def _render(template_str, user_opts):
    # Faithfully mirror render_config_user_template: sanitize user_opts (json.dumps strings)
    # THEN render with StrictUndefined, exactly as the endpoint manager does.
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    return _yaml.safe_load(env.from_string(template_str).render(**_sanitize_user_json(user_opts)))


def test_template_renders_localprovider_for_login_shape():
    f = SlurmFacility(_profile(), cli=None)
    tmpl, _defaults = f.config_template(Profile(mode="interactive"))
    cfg = _render(tmpl, shape_config("login"))
    assert cfg["engine"]["provider"]["type"] == "LocalProvider"


def test_template_renders_slurmprovider_for_slurm_shape():
    f = SlurmFacility(_profile(), cli=None)
    tmpl, _defaults = f.config_template(Profile(mode="interactive"))
    cfg = _render(tmpl, shape_config("slurm", partition="debug", account="ACC"))
    prov = cfg["engine"]["provider"]
    assert prov["type"] == "SlurmProvider"
    assert prov["partition"] == "debug" and prov["account"] == "ACC"
    assert prov["min_blocks"] == 0 and prov["max_blocks"] == 1


def test_template_defaults_to_slurm_account_from_profile():
    f = SlurmFacility(_profile(), cli=None)
    tmpl, defaults = f.config_template(Profile(mode="interactive"))
    cfg = _render(tmpl, {**defaults, **shape_config("slurm")})
    assert cfg["engine"]["provider"]["account"] == "ACC"


def test_slurm_provider_params_survive_the_manager_sanitizer():
    # Regression: the manager json.dumps's string user_opts, so a template `{% if
    # provider_type == 'SlurmProvider' %}` saw '"SlurmProvider"' and fell through to the
    # else branch, dropping partition/account/walltime entirely (the job then ran on the
    # facility default partition with no account). The bool `is_slurm` guard fixes it.
    f = SlurmFacility(_profile(), cli=None)
    tmpl, defaults = f.config_template(Profile(mode="interactive"))
    prov = _render(tmpl, {**defaults, **shape_config("slurm")})["engine"]["provider"]
    assert prov["partition"] == "debug" and prov["account"] == "ACC"
    assert prov["walltime"] and "launcher" in prov  # full slurm block, not the else branch


def test_worker_init_not_double_encoded_through_sanitizer():
    # Regression: the sanitizer already quotes strings, so an extra `| tojson` on worker_init
    # double-encoded it ('"module load ..."' with literal quotes), and the worker tried to run
    # the whole quoted string as one command. worker_init must be the bare command.
    f = SlurmFacility(_profile(), cli=None)
    tmpl, defaults = f.config_template(Profile(mode="interactive"))
    wi = _render(tmpl, {**defaults, **shape_config("slurm")})["engine"]["provider"]["worker_init"]
    assert wi == _profile().worker_init  # exact command, no wrapping quotes
    assert not wi.startswith('"')


def test_template_max_blocks_and_nodes_per_block_default_and_override():
    from dataclasses import replace as _dc_replace

    # profile defaults flow into the rendered slurm provider
    prof = _dc_replace(_profile(), max_blocks=4, nodes_per_block=3)
    f = SlurmFacility(prof, cli=None)
    tmpl, defaults = f.config_template(Profile(mode="interactive"))
    prov = _render(tmpl, {**defaults, **shape_config("slurm")})["engine"]["provider"]
    assert prov["max_blocks"] == 4 and prov["nodes_per_block"] == 3
    assert prov["min_blocks"] == 0  # idle-release cost net unchanged

    # a per-task user_endpoint_config overrides the profile default
    over = _render(tmpl, shape_config("slurm", max_blocks=9, nodes_per_block=2))
    assert over["engine"]["provider"]["max_blocks"] == 9
    assert over["engine"]["provider"]["nodes_per_block"] == 2


def test_template_max_blocks_configurable_for_local_shape():
    from dataclasses import replace as _dc_replace

    f = SlurmFacility(_dc_replace(_profile(), max_blocks=5), cli=None)
    tmpl, defaults = f.config_template(Profile(mode="interactive"))
    prov = _render(tmpl, {**defaults, **shape_config("login")})["engine"]["provider"]
    assert prov["type"] == "LocalProvider" and prov["max_blocks"] == 5


def test_template_emits_available_accelerators_only_when_set():
    from dataclasses import replace as _dc_replace

    # unset -> the key is omitted entirely
    f0 = SlurmFacility(_profile(), cli=None)
    t0, d0 = f0.config_template(Profile(mode="interactive"))
    assert "available_accelerators" not in _render(t0, {**d0, **shape_config("slurm")})["engine"]

    # int count
    f1 = SlurmFacility(_dc_replace(_profile(), available_accelerators=4), cli=None)
    t1, d1 = f1.config_template(Profile(mode="interactive"))
    assert _render(t1, {**d1, **shape_config("slurm")})["engine"]["available_accelerators"] == 4

    # explicit device list
    f2 = SlurmFacility(
        _dc_replace(_profile(), available_accelerators=["gpu0", "gpu1"]), cli=None
    )
    t2, d2 = f2.config_template(Profile(mode="interactive"))
    eng = _render(t2, {**d2, **shape_config("slurm")})["engine"]
    assert eng["available_accelerators"] == ["gpu0", "gpu1"]


def test_slurm_facility_satisfies_protocol():
    assert isinstance(SlurmFacility(_profile(), cli=None), Facility)


class _FakeRemoteCLI:
    def __init__(self, status=None):
        self.calls = []
        self.written = {}
        self._status = status

    async def status(self, name):
        self.calls.append(("status", name))
        return self._status

    async def configure(self, name, multi_user=False):
        self.calls.append(("configure", name, multi_user))

    async def write_config(self, name, manager_yaml, uep_yaml):
        self.written = {"name": name, "manager": manager_yaml, "uep": uep_yaml}

    async def start(self, name):
        self.calls.append(("start", name))
        return ("fake-eid", "login03.anvil.rcac.purdue.edu")

    async def stop(self, name):
        self.calls.append(("stop", name))

    async def endpoint_id(self, name):
        return "running-eid"

    async def login_exec(self, command):
        self.calls.append(("login_exec", command))
        return (0, "OUT", "")

    async def wipe_storage_db(self):
        self.calls.append(("wipe", "hpc-bridge"))

    async def cancel_blocks(self, endpoint_id):
        self.calls.append(("cancel_blocks", endpoint_id))
        return []

    def rebind(self, host):
        self.calls.append(("rebind", host))


def _kinds(cli):
    return [c[0] for c in cli.calls]


class _FakeGCClient:
    """Stand-in for the Globus Compute web Client (get_endpoints / get_endpoint_status)."""

    def __init__(self, endpoints=(), statuses=None, raises=False):
        self._endpoints = list(endpoints)
        self._statuses = statuses or {}
        self._raises = raises

    def get_endpoints(self, role=None):
        if self._raises:
            raise RuntimeError("web boom")
        return self._endpoints

    def get_endpoint_status(self, eid):
        return {"status": self._statuses.get(eid, "offline")}


def _no_endpoints():
    """client_factory that reports no reusable endpoint -> bootstrap takes the SSH path."""
    return _FakeGCClient()


class _BootstrapCLI(_FakeRemoteCLI):
    def __init__(self, status=None, remote_db_present=False):
        super().__init__(status=status)
        self.remote_db_present = remote_db_present
        self.seeded = None
        self.fqdn = "login03.anvil.rcac.purdue.edu"
        self.target = SshTarget("anvil.rcac.purdue.edu", "x-u", "/tmp/k")

    async def whoami(self):
        return self.remote_db_present

    async def seed_storage_db(self, local_db):
        self.seeded = local_db
        self.remote_db_present = True

    async def hostname_fqdn(self):
        return self.fqdn


async def test_bootstrap_seeds_when_remote_db_absent(monkeypatch, tmp_path):
    cli = _BootstrapCLI(status=None, remote_db_present=False)
    fac = SlurmFacility(_profile(), cli=cli, client_factory=_no_endpoints)
    made = tmp_path / "trimmed.db"
    made.write_bytes(b"db")
    monkeypatch.setattr(remote, "build_minimal_storage_db", lambda **kw: made)
    handle = await fac.bootstrap(Profile(mode="interactive"))
    assert cli.seeded == made  # creds shipped because remote db was absent
    assert handle.login_host == "login03.anvil.rcac.purdue.edu"
    assert ("start", "hpc-bridge") in cli.calls


async def test_bootstrap_skips_seed_when_remote_db_present(monkeypatch, tmp_path):
    cli = _BootstrapCLI(status="running", remote_db_present=True)
    fac = SlurmFacility(_profile(), cli=cli, client_factory=_no_endpoints)
    monkeypatch.setattr(remote, "build_minimal_storage_db", lambda **kw: tmp_path / "x.db")
    handle = await fac.bootstrap(Profile(mode="interactive"))
    assert cli.seeded is None  # already had creds -> no reseed
    assert handle.login_host is None  # reused (already-running) endpoint isn't re-probed


async def test_bootstrap_records_login_node_in_store(monkeypatch, tmp_path):
    from hpc_bridge.state import LoginNodeStore

    cli = _BootstrapCLI(status=None, remote_db_present=True)  # present -> no seed needed
    store = LoginNodeStore(tmp_path / "endpoints.json")
    fac = SlurmFacility(
        _profile(), cli=cli, store=store, alias="anvil.rcac.purdue.edu", client_factory=_no_endpoints
    )
    await fac.bootstrap(Profile(mode="interactive"))
    rec = store.get(alias="anvil.rcac.purdue.edu", name="hpc-bridge")
    assert rec is not None and rec.login_host == "login03.anvil.rcac.purdue.edu"
    assert rec.user == "x-u" and rec.key_path == "/tmp/k"


# --- SSH-once: reuse an already-online endpoint over the web, no SSH -------------------------


async def test_find_online_endpoint_returns_uuid_when_owned_and_online():
    client = _FakeGCClient(
        endpoints=[{"name": "other", "uuid": "x"}, {"name": "hpc-bridge", "uuid": "ours"}],
        statuses={"ours": "online"},
    )
    f = SlurmFacility(_profile(), cli=None, client_factory=lambda: client)
    assert await f.find_online_endpoint("hpc-bridge") == "ours"


async def test_find_online_endpoint_none_when_offline_or_name_mismatch():
    offline = SlurmFacility(
        _profile(), cli=None,
        client_factory=lambda: _FakeGCClient([{"name": "hpc-bridge", "uuid": "ep"}], {"ep": "offline"}),
    )
    assert await offline.find_online_endpoint("hpc-bridge") is None  # registered but not online
    mismatch = SlurmFacility(
        _profile(), cli=None,
        client_factory=lambda: _FakeGCClient([{"name": "other", "uuid": "ep"}], {"ep": "online"}),
    )
    assert await mismatch.find_online_endpoint("hpc-bridge") is None  # not ours by name


async def test_find_online_endpoint_swallows_web_error():
    # A web/auth failure must not crash provisioning — return None and let the SSH path run.
    f = SlurmFacility(_profile(), cli=None, client_factory=lambda: _FakeGCClient(raises=True))
    assert await f.find_online_endpoint("hpc-bridge") is None


async def test_bootstrap_reuses_online_endpoint_without_any_ssh():
    # The SSH-once keystone: a web-online endpoint we own is reused over AMQP and NOT a single
    # SSH op (status/configure/start/whoami/seed) runs.
    cli = _BootstrapCLI(status=None, remote_db_present=False)
    client = _FakeGCClient([{"name": "hpc-bridge", "uuid": "reused-eid"}], {"reused-eid": "online"})
    fac = SlurmFacility(_profile(), cli=cli, client_factory=lambda: client)
    handle = await fac.bootstrap(Profile(mode="interactive"))
    assert handle.endpoint_id == "reused-eid"
    assert cli.calls == []  # zero SSH: no status/configure/start/login_exec
    assert cli.seeded is None  # and no credential seeding


async def test_provision_fresh_configures_writes_and_starts():
    cli = _FakeRemoteCLI(status=None)  # not configured yet
    handle = await SlurmFacility(_profile(), cli=cli).provision(Profile(mode="interactive"))
    assert isinstance(handle, EndpointHandle) and handle.endpoint_id == "fake-eid"
    assert ("configure", "hpc-bridge", False) in cli.calls  # forced single-user
    assert ("start", "hpc-bridge") in cli.calls
    assert "amqp_port: 443" in cli.written["manager"]  # firewall-friendly AMQP in manager cfg
    assert "HPC-Bridge Anvil" in cli.written["manager"]  # human display_name, not the dir name
    assert "provider_type" in cli.written["uep"]  # template references the var, not a literal


async def test_provision_skips_configure_when_already_configured():
    cli = _FakeRemoteCLI(status="configured")  # Initialized/Stopped from a prior run
    handle = await SlurmFacility(_profile(), cli=cli).provision(Profile(mode="interactive"))
    assert handle.endpoint_id == "fake-eid"
    assert "configure" not in _kinds(cli)  # re-configuring raises ConfigExists — must skip
    assert cli.written and ("start", "hpc-bridge") in cli.calls  # config rewritten, then started


async def test_provision_reuses_running_endpoint():
    cli = _FakeRemoteCLI(status="running")
    handle = await SlurmFacility(_profile(), cli=cli).provision(Profile(mode="interactive"))
    assert handle.endpoint_id == "running-eid"
    assert "configure" not in _kinds(cli) and "start" not in _kinds(cli)  # reuse, don't restart
    assert "rebind" not in _kinds(cli)  # nothing was (re)started -> stay on the current host


async def test_provision_rebinds_cli_to_the_node_the_daemon_landed_on():
    # After `start` captures the manager's real login node, the live CLI must repoint
    # there so later control-plane ops (esp. teardown) reach THIS node instead of the
    # round-robin alias — otherwise stop hits the wrong node and orphans the daemon.
    cli = _FakeRemoteCLI(status=None)
    handle = await SlurmFacility(_profile(), cli=cli).provision(Profile(mode="interactive"))
    assert ("rebind", "login03.anvil.rcac.purdue.edu") in cli.calls
    assert _kinds(cli).index("rebind") > _kinds(cli).index("start")  # rebind AFTER start
    assert handle.login_host == "login03.anvil.rcac.purdue.edu"


async def test_teardown_stops_endpoint():
    cli = _FakeRemoteCLI()
    await SlurmFacility(_profile(), cli=cli).teardown("fake-eid")
    assert ("stop", "hpc-bridge") in cli.calls


async def test_teardown_cancels_blocks_as_backstop():
    # an ungraceful stop can orphan the running block until walltime -> teardown must scancel
    cli = _FakeRemoteCLI()
    await SlurmFacility(_profile(), cli=cli).teardown("eid-123")
    assert ("stop", "hpc-bridge") in cli.calls
    assert ("cancel_blocks", "eid-123") in cli.calls  # cancel this endpoint's blocks
    assert _kinds(cli).index("cancel_blocks") > _kinds(cli).index("stop")  # after stop


async def test_cancel_blocks_targets_only_this_endpoints_jobs(monkeypatch):
    # precision: match blocks by StdOut path under uep.<endpoint_id>, never another
    # GlobusComputeEngine endpoint's jobs sharing the parsl job-name prefix.
    eid = "8791269d-47f4-47f7-91a6-3485b4289269"
    other = "c5fd7ad7-0b92-4b50-83e1-56f7b9c1f91d"
    seen = []

    async def fake_ssh(target, cmd, **kw):
        seen.append(cmd)
        if "squeue" in cmd:
            return (
                0,
                f"111   /home/u/.globus_compute/uep.{eid}.aaa/submit_scripts/x.stdout\n"
                f"222   /home/u/.globus_compute/uep.{other}.bbb/submit_scripts/y.stdout\n"
                f"333   /home/u/.globus_compute/uep.{eid}.ccc/submit_scripts/z.stdout\n",
                "",
            )
        return (0, "", "")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh)
    cli = remote.RemoteEndpointCLI(SshTarget("h", "u", "k"), "env")
    cancelled = await cli.cancel_blocks(eid)
    assert cancelled == ["111", "333"]  # ours only; 222 (other endpoint) untouched
    scancel = [c for c in seen if "scancel" in c]
    assert len(scancel) == 1
    assert "111" in scancel[0] and "333" in scancel[0] and "222" not in scancel[0]


async def test_cancel_blocks_no_jobs_is_noop(monkeypatch):
    async def fake_ssh(target, cmd, **kw):
        return (0, "", "") if "squeue" in cmd else (0, "", "")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh)
    cli = remote.RemoteEndpointCLI(SshTarget("h", "u", "k"), "env")
    assert await cli.cancel_blocks("any-eid") == []  # nothing matched -> no scancel


async def test_manager_online_uses_injected_client():
    class _Client:
        def get_endpoint_status(self, eid):
            return {"status": "online"}

    f = SlurmFacility(_profile(), cli=None, client_factory=lambda: _Client())
    assert await f.manager_online("fake-eid") is True


async def test_endpoint_id_parses_list_and_picks_named_row(monkeypatch):
    sample = (
        "| Endpoint ID | Status | Endpoint Name |\n"
        "| 358c89fb-2774-4a5c-8dc5-da2406ccdc9c | Running | hpc-bridge |\n"
        "| c5fd7ad7-0b92-4b50-83e1-56f7b9c1f91d | Disconnected | default |\n"
    )

    async def fake_ssh_exec(target, cmd, **kw):
        return (0, sample, "")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh_exec)
    cli = RemoteEndpointCLI(SshTarget("h", "u", "k"), "true")
    assert await cli.endpoint_id("hpc-bridge") == "358c89fb-2774-4a5c-8dc5-da2406ccdc9c"
    with pytest.raises(RuntimeError, match="could not find"):
        await cli.endpoint_id("nope")


async def test_list_parse_fails_loud_on_unrecognized_format(monkeypatch):
    # A gce version/format change away from the pipe table must NOT read as "no endpoints"
    # (silent mis-provision); it must raise a clear, actionable error (issue #8).
    borderless = (
        "Endpoint ID                           Status   Endpoint Name\n"
        "358c89fb-2774-4a5c-8dc5-da2406ccdc9c  Running  hpc-bridge\n"
    )

    async def fake_ssh_exec(target, cmd, **kw):
        return (0, borderless, "")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh_exec)
    cli = RemoteEndpointCLI(SshTarget("h", "u", "k"), "true")
    with pytest.raises(RuntimeError, match="could not parse"):
        await cli.status("hpc-bridge")
    with pytest.raises(RuntimeError, match="could not parse"):
        await cli.endpoint_id("hpc-bridge")


async def test_list_no_endpoints_is_not_a_parse_error(monkeypatch):
    # The legitimate "nothing configured" message must stay quiet: status -> None,
    # endpoint_id -> the normal "could not find", NOT the parse-error path.
    empty = "No endpoints configured!\n\n (Hint: globus-compute-endpoint configure)\n"

    async def fake_ssh_exec(target, cmd, **kw):
        return (0, empty, "")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh_exec)
    cli = RemoteEndpointCLI(SshTarget("h", "u", "k"), "true")
    assert await cli.status("hpc-bridge") is None
    with pytest.raises(RuntimeError, match="could not find"):
        await cli.endpoint_id("hpc-bridge")


async def test_write_file_lets_remote_expand_home(monkeypatch):
    # Regression: shlex.quote single-quoted the path, so `~`/$HOME never expanded
    # remotely and the write hit a literal path → "No such file or directory".
    captured = {}

    async def fake_ssh_exec(target, cmd, *, stdin=None, **kw):
        captured["cmd"] = cmd
        captured["stdin"] = stdin
        return (0, "", "")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh_exec)
    cli = RemoteEndpointCLI(SshTarget("h", "u", "k"), "true")
    await cli._write_file("$HOME/.globus_compute/hpc-bridge/config.yaml", "display_name: x")
    assert captured["stdin"] == "display_name: x"
    assert "$HOME" in captured["cmd"] and "'$HOME" not in captured["cmd"]  # not single-quoted


async def test_configure_tolerates_already_configured(monkeypatch):
    async def fake_ssh_exec(target, cmd, **kw):
        return (1, "", "Endpoint hpc-bridge is already configured")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh_exec)
    cli = RemoteEndpointCLI(SshTarget("h", "u", "k"), "true")
    await cli.configure("hpc-bridge")  # must not raise — re-provision overwrites the config


async def test_configure_raises_on_real_error(monkeypatch):
    async def fake_ssh_exec(target, cmd, **kw):
        return (1, "", "globus-compute-endpoint: command not found")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh_exec)
    cli = RemoteEndpointCLI(SshTarget("h", "u", "k"), "true")
    with pytest.raises(RuntimeError, match="configure failed"):
        await cli.configure("hpc-bridge")


async def test_status_parses_running_configured_absent(monkeypatch):
    out = {"v": ""}

    async def fake_ssh_exec(target, cmd, **kw):
        return (0, out["v"], "")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh_exec)
    cli = RemoteEndpointCLI(SshTarget("h", "u", "k"), "true")
    out["v"] = "| 358c89fb-... | Running | hpc-bridge |\n"
    assert await cli.status("hpc-bridge") == "running"
    out["v"] = "| None | Initialized | hpc-bridge |\n"  # the exact state we hit on Anvil
    assert await cli.status("hpc-bridge") == "configured"
    out["v"] = "| 358c89fb-... | Running | anvil-dev |\n"  # different endpoint only
    assert await cli.status("hpc-bridge") is None


async def test_status_and_endpoint_id_match_name_exactly_not_substring(monkeypatch):
    # Regression (caught live on Anvil): a pre-existing 'hpc-bridge-login' row contains
    # the substring 'hpc-bridge', so a naive `name in line` mis-identified it as ours and
    # skipped configure. status()/endpoint_id() must match the Endpoint Name column exactly.
    listing = (
        "+--------------------------------------+---------+------------------+\n"
        "|             Endpoint ID              | Status  |  Endpoint Name   |\n"
        "+======================================+=========+==================+\n"
        "| c76765f0-fcf7-4fce-afa9-2dda47677fcc | Stopped | hpc-bridge-login |\n"
        "+--------------------------------------+---------+------------------+\n"
    )

    async def fake_ssh_exec(target, cmd, **kw):
        return (0, listing, "")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh_exec)
    cli = RemoteEndpointCLI(SshTarget("h", "u", "k"), "true")
    assert await cli.status("hpc-bridge") is None  # must NOT match hpc-bridge-login
    assert await cli.status("hpc-bridge-login") == "configured"  # exact match works
    with pytest.raises(RuntimeError, match="could not find"):
        await cli.endpoint_id("hpc-bridge")  # no exact 'hpc-bridge' row


async def test_remote_cli_login_exec_runs_bash_lc_over_ssh(monkeypatch):
    captured = {}

    async def fake_ssh_exec(target, cmd, **kw):
        captured["cmd"] = cmd
        return (0, "shared*|up|infinite|250|128|257400|226/12/12/250\n", "")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh_exec)
    cli = RemoteEndpointCLI(SshTarget("h", "u", "k"), "true")
    rc, out, _ = await cli.login_exec("sinfo -h")
    assert rc == 0 and "shared" in out
    assert captured["cmd"].startswith("bash -lc ") and "sinfo" in captured["cmd"]  # login shell, no venv


async def test_slurm_facility_login_exec_delegates_to_cli():
    cli = _FakeRemoteCLI()
    rc, out, _ = await SlurmFacility(_profile(), cli=cli).login_exec("sinfo -h")
    assert rc == 0 and out == "OUT"
    assert ("login_exec", "sinfo -h") in cli.calls  # discovery routed through the SSH CLI, not _gce


async def test_seed_storage_db_streams_b64_and_locks_permissions(monkeypatch, tmp_path):
    db = tmp_path / "storage.db"
    db.write_bytes(b"\x00sqlite-bytes\xff")
    cmds = []

    async def fake_ssh_exec(target, cmd, *, stdin=None, **kw):
        cmds.append((cmd, stdin))
        return (0, "", "")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh_exec)
    cli = RemoteEndpointCLI(SshTarget("h", "u", "k"), "true")
    await cli.seed_storage_db(db)

    mkdir_i = next(i for i, (c, _) in enumerate(cmds) if "mkdir -p" in c)
    write_i = next(i for i, (c, _) in enumerate(cmds) if "base64 -d" in c)
    chmod_file_i = next(i for i, (c, _) in enumerate(cmds) if "chmod 600" in c)
    # dir created, then file written, then file locked
    assert mkdir_i < write_i < chmod_file_i
    assert any("chmod 700" in c for c, _ in cmds)  # dir locked to user-only
    # the base64 payload round-trips to the original bytes
    payload = next(s for c, s in cmds if "base64 -d" in c)
    assert base64.b64decode(payload) == b"\x00sqlite-bytes\xff"


async def test_seed_storage_db_raises_on_remote_failure(monkeypatch, tmp_path):
    db = tmp_path / "storage.db"
    db.write_bytes(b"x")

    async def fake_ssh_exec(target, cmd, *, stdin=None, **kw):
        return (1, "", "Permission denied")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh_exec)
    cli = RemoteEndpointCLI(SshTarget("h", "u", "k"), "true")
    with pytest.raises(RuntimeError, match="seed storage.db"):
        await cli.seed_storage_db(db)


async def test_hostname_fqdn_reads_login_node(monkeypatch):
    async def fake_ssh_exec(target, cmd, **kw):
        assert "hostname -f" in cmd
        return (0, "login03.anvil.rcac.purdue.edu\n", "")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh_exec)
    cli = RemoteEndpointCLI(SshTarget("anvil.rcac.purdue.edu", "u", "k"), "true")
    assert await cli.hostname_fqdn() == "login03.anvil.rcac.purdue.edu"


async def test_hostname_fqdn_raises_on_remote_failure(monkeypatch):
    async def fake_ssh_exec(target, cmd, **kw):
        return (1, "", "ssh: connect to host failed")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh_exec)
    cli = RemoteEndpointCLI(SshTarget("anvil.rcac.purdue.edu", "u", "k"), "true")
    with pytest.raises(RuntimeError, match="hostname -f failed"):
        await cli.hostname_fqdn()


def test_rebind_points_cli_at_a_specific_host():
    cli = RemoteEndpointCLI(SshTarget("anvil.rcac.purdue.edu", "u", "k"), "true")
    cli.rebind("login03.anvil.rcac.purdue.edu")
    assert cli.target.host == "login03.anvil.rcac.purdue.edu"
    assert cli.target.user == "u"
    assert cli.target.key_path == "k"  # other fields preserved


def test_template_max_idletime_is_float_with_strategy_period():
    f = SlurmFacility(_profile(), cli=None)
    tmpl, defaults = f.config_template(Profile(mode="interactive"))
    cfg = _render(tmpl, {**defaults, **shape_config("slurm")})
    jsk = cfg["engine"]["job_status_kwargs"]
    assert isinstance(jsk["max_idletime"], float) and jsk["max_idletime"] == 600.0
    assert jsk["strategy_period"] == 30


def test_template_idle_timeout_follows_profile():
    f = SlurmFacility(_profile(), cli=None)
    tmpl, defaults = f.config_template(Profile(mode="interactive", max_idletime_s=300))
    cfg = _render(tmpl, {**defaults, **shape_config("slurm")})
    assert cfg["engine"]["job_status_kwargs"]["max_idletime"] == 300.0


def test_template_batch_mode_init_blocks_zero_interactive_one():
    f = SlurmFacility(_profile(), cli=None)
    tmpl, defaults = f.config_template(Profile(mode="batch"))
    cfg = _render(tmpl, {**defaults, **shape_config("slurm")})
    assert cfg["engine"]["provider"]["init_blocks"] == 0
    tmpl2, defaults2 = f.config_template(Profile(mode="interactive"))
    cfg2 = _render(tmpl2, {**defaults2, **shape_config("slurm")})
    assert cfg2["engine"]["provider"]["init_blocks"] == 1


def test_template_emits_scheduler_options_when_profile_sets_it():
    from dataclasses import replace as _dc_replace
    base = _profile()
    prof = _dc_replace(base, scheduler_options="#SBATCH --constraint=A100")
    f = SlurmFacility(prof, cli=None)
    tmpl, defaults = f.config_template(Profile(mode="interactive"))
    cfg = _render(tmpl, {**defaults, **shape_config("slurm")})
    assert cfg["engine"]["provider"]["scheduler_options"] == "#SBATCH --constraint=A100"


def test_template_survives_special_chars_in_profile():
    from dataclasses import replace as _dc_replace
    base = _profile()
    # apostrophe, percent, and braces in worker_init must NOT break Jinja compile
    prof = _dc_replace(base, worker_init="source it's/venv && export P=50% {ok}")
    f = SlurmFacility(prof, cli=None)
    tmpl, defaults = f.config_template(Profile(mode="interactive"))
    cfg = _render(tmpl, {**defaults, **shape_config("slurm")})
    expected = "source it's/venv && export P=50% {ok}"
    assert cfg["engine"]["provider"]["worker_init"] == expected


async def test_wipe_storage_db_removes_remote_credential(monkeypatch):
    wiped = {}

    async def fake_ssh_exec(target, cmd, **kw):
        if "rm -f" in cmd and "storage.db" in cmd:
            wiped["yes"] = True
        return (0, "", "")

    monkeypatch.setattr(remote, "ssh_exec", fake_ssh_exec)
    cli = RemoteEndpointCLI(SshTarget("h", "u", "k"), "true")
    await cli.wipe_storage_db()
    assert wiped.get("yes") is True


async def test_teardown_keeps_credentials_by_default():
    cli = _FakeRemoteCLI()
    await SlurmFacility(_profile(), cli=cli).teardown("fake-eid")
    assert ("stop", "hpc-bridge") in cli.calls
    assert ("wipe", ) not in [(c[0],) for c in cli.calls]  # default keeps creds for reconnect


async def test_teardown_wipes_credentials_when_requested():
    cli = _FakeRemoteCLI()
    await SlurmFacility(_profile(), cli=cli).teardown("fake-eid", wipe_credentials=True)
    assert ("wipe", "hpc-bridge") in cli.calls
