# tests/test_mep_facility.py
from hpc_bridge.facility.base import EndpointHandle, Facility
from hpc_bridge.facility.mep import MEPFacility
from hpc_bridge.lifecycle import EndpointState, ensure_warm, probe
from hpc_bridge.profile import Profile

UUID = "da3df250-4013-4d69-942c-eef1568f860c"


def _fac(**over):
    opts = {
        "compute": True,
        "partition": "main",
        "walltime": "00:10:00",
        "nodes_per_block": 1,
        "max_workers_per_node": 2,
        "init_blocks": 1,
        "max_blocks": 1,
        "worker_init": "uv pip install -q globus-compute-endpoint==4.15.0",
        "interface": "enP7s7",
    }
    kw = {"endpoint_id": UUID, "name": "globus-cluster-mep", "user_opts": opts}
    kw.update(over)
    return MEPFacility(**kw)


class _Client:
    def __init__(self, status="online", boom=False):
        self._status = status
        self._boom = boom

    def get_endpoint_status(self, endpoint_id):
        if self._boom:
            raise RuntimeError("403: not the endpoint owner")
        return {"status": self._status}


def test_satisfies_facility_protocol():
    assert isinstance(_fac(), Facility)  # provision / manager_online / config_template present


async def test_provision_hands_back_uuid_reused_no_ssh():
    h = await _fac().provision(Profile())
    assert isinstance(h, EndpointHandle)
    assert h.endpoint_id == UUID
    assert h.name == "globus-cluster-mep"
    assert h.reused is True  # a zero-SSH attach, never a fresh bootstrap


def test_config_template_returns_uec_defaults_only():
    slot, defaults = _fac().config_template(Profile())
    assert slot == ""  # we don't own the template
    # the verified globus1 UEC: compute-shape defaults that shape_config('compute') tops with compute=True
    assert defaults["compute"] is True
    assert defaults["partition"] == "main"
    assert defaults["init_blocks"] == 1  # the warm-block login-shape replacement
    assert defaults["worker_init"].endswith("==4.15.0")
    # returned copy is defensive — mutating it can't corrupt the facility's own opts
    defaults["partition"] = "mutated"
    assert _fac().config_template(Profile())[1]["partition"] == "main"


async def test_manager_online_reads_status_when_readable():
    assert await _fac(client_factory=lambda: _Client("online")).manager_online(UUID) is True
    assert await _fac(client_factory=lambda: _Client("offline")).manager_online(UUID) is False


async def test_manager_online_degrades_to_true_on_error():
    # a status-API error / foreign-endpoint read must not falsely strand us — the canary is authoritative
    assert await _fac(client_factory=lambda: _Client(boom=True)).manager_online(UUID) is True


async def test_ensure_warm_attaches_without_provisioning_work():
    # the lifecycle seam: provision returns the UUID, probe reports warm (manager online), reused threads up
    f = _fac(client_factory=lambda: _Client("online"))
    block, state = await ensure_warm(f, Profile(), EndpointState())
    assert state.endpoint_id == UUID
    assert state.reused is True
    assert block == "warm"


async def test_probe_provisioning_when_manager_reports_offline():
    f = _fac(client_factory=lambda: _Client("offline"))
    assert await probe(f, EndpointState(endpoint_id=UUID)) == "provisioning"
