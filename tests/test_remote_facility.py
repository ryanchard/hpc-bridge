import base64

import pytest

from hpc_bridge.facility import remote
from hpc_bridge.facility.base import EndpointHandle, Facility
from hpc_bridge.facility.remote import RemoteEndpointCLI, SlurmFacility, SshTarget, anvil_profile
from hpc_bridge.profile import Profile


def _profile():
    return anvil_profile(account="ACC", user="x-u")


def test_anvil_profile_fields():
    p = anvil_profile(account="cis250223", user="x-amcsweeneyel")
    assert p.name == "anvil" and p.account == "cis250223" and p.interface == "ib0"
    assert p.partition == "debug"
    assert "anaconda/2024.02-py311" in p.env_setup and "gce-venv/bin/activate" in p.env_setup
    assert p.scratch_root == "/anvil/scratch/x-amcsweeneyel/.hpc-bridge"
    assert p.worker_init == p.env_setup  # worker replays the same env


def test_config_template_interactive_eager_block_idle_releases():
    f = SlurmFacility(_profile(), cli=None)
    uep = f.config_template(Profile(mode="interactive"))
    eng = uep["engine"]
    assert eng["type"] == "GlobusComputeEngine"
    assert eng["address"] == {"type": "address_by_interface", "ifname": "ib0"}
    prov = eng["provider"]
    assert prov["type"] == "SlurmProvider"
    assert prov["account"] == "ACC" and prov["partition"] == "debug"
    assert prov["launcher"] == {"type": "SrunLauncher"}
    # eager block on first task (init_blocks=1), but min_blocks=0 so the idle timer can
    # release it (no SU leak); init_blocks acts when the UEP forks, not at provision()
    assert prov["init_blocks"] == 1 and prov["min_blocks"] == 0 and prov["max_blocks"] == 1
    # the idle cost-net is the block scale-in at ~max_idletime (default 600s = 10 min)
    assert eng["job_status_kwargs"]["max_idletime"] == 600.0
    # idle_heartbeats_soft is deliberately NOT set — proven a no-op in gce v4.x (validated live)
    assert "idle_heartbeats_soft" not in uep


def test_config_template_batch_is_on_demand():
    f = SlurmFacility(_profile(), cli=None)
    prov = f.config_template(Profile(mode="batch"))["engine"]["provider"]
    assert prov["init_blocks"] == 0 and prov["min_blocks"] == 0


def test_config_template_idle_timeout_follows_profile():
    f = SlurmFacility(_profile(), cli=None)
    uep = f.config_template(Profile(mode="interactive", max_idletime_s=300))
    assert uep["engine"]["job_status_kwargs"]["max_idletime"] == 300.0


def test_config_template_max_idletime_typing():
    f = SlurmFacility(_profile(), cli=None)
    eng = f.config_template(Profile(mode="interactive"))["engine"]
    # globus-compute expects a float max_idletime + a strategy_period under job_status_kwargs
    assert isinstance(eng["job_status_kwargs"]["max_idletime"], float)
    assert eng["job_status_kwargs"]["strategy_period"] == 30


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
        return "fake-eid"

    async def stop(self, name):
        self.calls.append(("stop", name))

    async def endpoint_id(self, name):
        return "running-eid"

    async def login_exec(self, command):
        self.calls.append(("login_exec", command))
        return (0, "OUT", "")


def _kinds(cli):
    return [c[0] for c in cli.calls]


async def test_provision_fresh_configures_writes_and_starts():
    cli = _FakeRemoteCLI(status=None)  # not configured yet
    handle = await SlurmFacility(_profile(), cli=cli).provision(Profile(mode="interactive"))
    assert isinstance(handle, EndpointHandle) and handle.endpoint_id == "fake-eid"
    assert ("configure", "hpc-bridge", False) in cli.calls  # forced single-user
    assert ("start", "hpc-bridge") in cli.calls
    assert "amqp_port: 443" in cli.written["manager"]  # firewall-friendly AMQP in manager cfg
    assert "SlurmProvider" in cli.written["uep"] and "ACC" in cli.written["uep"]


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
