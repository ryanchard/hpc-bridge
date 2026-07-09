"""discovery.py — probe parsing into a proposed FacilityDetails (no real SSH)."""
import pytest

from hpc_bridge import discovery
from hpc_bridge.discovery import discover_facility_details, parse_probe
from hpc_bridge.facility.remote import SshTarget

# globus1-shaped: no $SCRATCH, gce not installed but uv present, one real NIC, no accounting.
_GLOBUS = """\
login banner noise that must be ignored
HPCB_PROBE_BEGIN
USER=glabs
HOME=/home/glabs
SCRATCH=
WORK=
PSCRATCH=
SCHED=slurm
GCE=
UV=/usr/local/bin/uv
MYBALANCE=
XDUSAGE=
PART=main*|up
NIC=lo|127.0.0.1/8
NIC=enP7s7|128.135.123.175/24
HPCB_PROBE_END
trailing noise
"""

# Anvil-shaped: $SCRATCH set, gce in a venv, a dedicated ib fabric beside eth0, mybalance present.
_ANVIL = """\
HPCB_PROBE_BEGIN
USER=x-anvil
HOME=/home/x-anvil
SCRATCH=/anvil/scratch/x-anvil
WORK=
PSCRATCH=
SCHED=slurm
GCE=/home/x-anvil/hpc-bridge/gce-venv/bin/globus-compute-endpoint
UV=/usr/bin/uv
MYBALANCE=/apps/bin/mybalance
XDUSAGE=
PART=debug|up
PART=shared*|up
PART=wholenode|up
NIC=lo|127.0.0.1/8
NIC=eth0|10.0.0.5/24
NIC=ib0|10.1.0.5/24
HPCB_PROBE_END
"""


def test_parse_probe_globus_uv_bootstrap_no_scratch_no_alloc():
    draft, notes = parse_probe(_GLOBUS, ssh_host="globus1")
    assert draft.ssh_host == "globus1" and draft.scheduler == "slurm"
    assert draft.interface == "enP7s7"  # lo excluded, single real NIC
    assert draft.partition == "main"  # only partition (default-marked)
    assert draft.scratch_root == "/home/{user}/.hpc-bridge"  # no $SCRATCH -> $HOME, user templated
    assert "uv venv {venv}" in draft.env_setup and "uv pip install" in draft.env_setup  # uv-bootstrap
    assert draft.allocation_command is None and draft.allocation_parser is None
    joined = " ".join(notes)  # interface/env_setup/scratch flagged for confirmation
    assert "interface" in joined and "env_setup" in joined and "scratch_root" in joined


def test_parse_probe_anvil_scratch_ib0_venv_mybalance():
    draft, _notes = parse_probe(_ANVIL, ssh_host="anvil.rcac.purdue.edu")
    assert draft.interface == "ib0"  # fast-fabric NIC preferred over eth0
    assert draft.partition == "debug"  # cheap/quick queue preferred over the default 'shared'
    assert draft.scratch_root == "/anvil/scratch/{user}/.hpc-bridge"  # $SCRATCH, user templated
    assert draft.env_setup == "source /home/{user}/hpc-bridge/gce-venv/bin/activate"  # gce venv activate
    assert draft.allocation_command == "mybalance" and draft.allocation_parser == "mybalance"


def test_parse_probe_shared_scratch_base_gets_per_user_subdir_and_note():
    # Midway-shaped: $SCRATCH is a SHARED base (no login name in it) -> append a per-user subdir AND
    # flag it (a shared base ⇒ Permission denied on every session cd; this stranded a live run).
    stdout = "\n".join([
        "HPCB_PROBE_BEGIN", "USER=gusellerm", "HOME=/home/gusellerm", "SCRATCH=/scratch/midway3",
        "SCHED=slurm", "UV=/usr/bin/uv", "NIC=bond0|10.0.0.5/24", "HPCB_PROBE_END",
    ])
    draft, notes = parse_probe(stdout, ssh_host="midway")
    assert draft.scratch_root == "/scratch/midway3/{user}/.hpc-bridge"  # per-user subdir appended
    assert any("per-user" in n and "scratch_root" in n for n in notes)  # and flagged to confirm


def test_parse_probe_flags_missing_toolchain_and_scheduler():
    out = "HPCB_PROBE_BEGIN\nUSER=u\nHOME=/home/u\nSCHED=none\nGCE=\nUV=\nHPCB_PROBE_END\n"
    draft, notes = parse_probe(out, ssh_host="h")
    j = " ".join(notes)
    assert "scheduler" in j and "sbatch" in j  # non-Slurm flagged
    assert draft.env_setup == ""  # neither gce nor uv -> empty, and flagged for the user
    assert any("module load" in n for n in notes)


async def test_discover_runs_login_shell_probe_and_parses(monkeypatch):
    captured = {}

    async def fake_ssh_exec(target, cmd, **kw):
        captured["cmd"] = cmd
        return (0, _GLOBUS, "")

    monkeypatch.setattr(discovery, "ssh_exec", fake_ssh_exec)
    draft, _notes = await discover_facility_details(SshTarget("globus1", "glabs", "/k"))
    assert draft.interface == "enP7s7"
    assert captured["cmd"].startswith("bash -lc ")  # raw login-shell SSH, not over the endpoint
    assert "HPCB_PROBE_BEGIN" in captured["cmd"]  # the single batched probe script


async def test_discover_raises_when_probe_never_ran(monkeypatch):
    async def fake_ssh_exec(target, cmd, **kw):
        return (255, "", "ssh: connect to host h port 22: Connection refused")

    monkeypatch.setattr(discovery, "ssh_exec", fake_ssh_exec)
    with pytest.raises(RuntimeError, match="discovery probe failed"):
        await discover_facility_details(SshTarget("h", "u", "/k"))
