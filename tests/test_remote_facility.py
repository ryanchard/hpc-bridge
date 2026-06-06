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


def test_anvil_profile_fields():
    p = anvil_profile(account="cis250223", user="x-amcsweeneyel")
    assert p.name == "anvil" and p.account == "cis250223" and p.interface == "ib0"
    assert p.partition == "debug"
    assert "anaconda/2024.02-py311" in p.env_setup and "gce-venv/bin/activate" in p.env_setup
    assert p.scratch_root == "/anvil/scratch/x-amcsweeneyel/.hpc-bridge"
    assert p.worker_init == p.env_setup  # worker replays the same env


def _render(template_str, user_opts):
    # mirror the endpoint's render: user_opts are the top-level template variables
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    return _yaml.safe_load(env.from_string(template_str).render(**user_opts))


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


def _kinds(cli):
    return [c[0] for c in cli.calls]


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

    def rebind(self, host):
        self.calls.append(("rebind", host))


async def test_bootstrap_seeds_when_remote_db_absent(monkeypatch, tmp_path):
    cli = _BootstrapCLI(status=None, remote_db_present=False)
    fac = SlurmFacility(_profile(), cli=cli)
    made = tmp_path / "trimmed.db"
    made.write_bytes(b"db")
    monkeypatch.setattr(remote, "build_minimal_storage_db", lambda **kw: made)
    handle = await fac.bootstrap(Profile(mode="interactive"))
    assert cli.seeded == made  # creds shipped because remote db was absent
    assert handle.login_host == "login03.anvil.rcac.purdue.edu"
    assert ("start", "hpc-bridge") in cli.calls


async def test_bootstrap_skips_seed_when_remote_db_present(monkeypatch, tmp_path):
    cli = _BootstrapCLI(status="running", remote_db_present=True)
    fac = SlurmFacility(_profile(), cli=cli)
    monkeypatch.setattr(remote, "build_minimal_storage_db", lambda **kw: tmp_path / "x.db")
    handle = await fac.bootstrap(Profile(mode="interactive"))
    assert cli.seeded is None  # already had creds -> no reseed
    assert handle.login_host is None  # reused (already-running) endpoint isn't re-probed


async def test_bootstrap_records_login_node_in_store(monkeypatch, tmp_path):
    from hpc_bridge.state import LoginNodeStore

    cli = _BootstrapCLI(status=None, remote_db_present=True)  # present -> no seed needed
    store = LoginNodeStore(tmp_path / "endpoints.json")
    fac = SlurmFacility(_profile(), cli=cli, store=store, alias="anvil.rcac.purdue.edu")
    await fac.bootstrap(Profile(mode="interactive"))
    rec = store.get(alias="anvil.rcac.purdue.edu", name="hpc-bridge")
    assert rec is not None and rec.login_host == "login03.anvil.rcac.purdue.edu"
    assert rec.user == "x-u" and rec.key_path == "/tmp/k"


async def test_provision_fresh_configures_writes_and_starts():
    cli = _FakeRemoteCLI(status=None)  # not configured yet
    handle = await SlurmFacility(_profile(), cli=cli).provision(Profile(mode="interactive"))
    assert isinstance(handle, EndpointHandle) and handle.endpoint_id == "fake-eid"
    assert ("configure", "hpc-bridge", False) in cli.calls  # forced single-user
    assert ("start", "hpc-bridge") in cli.calls
    assert "amqp_port: 443" in cli.written["manager"]  # firewall-friendly AMQP in manager cfg
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


async def test_teardown_stops_endpoint():
    cli = _FakeRemoteCLI()
    await SlurmFacility(_profile(), cli=cli).teardown("fake-eid")
    assert ("stop", "hpc-bridge") in cli.calls


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
