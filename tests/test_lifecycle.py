from hpc_bridge.lifecycle import EndpointState, ensure_warm, probe
from hpc_bridge.profile import Profile
from tests.fakes import FakeFacility


async def test_ensure_warm_provisions_when_no_endpoint():
    f = FakeFacility()
    f.workers = 1
    block, state = await ensure_warm(f, Profile(), EndpointState())
    assert f.provisioned is True
    assert state.endpoint_id == "fake-eid"
    assert block == "warm"


async def test_probe_reports_provisioning_until_worker_registers():
    f = FakeFacility()
    f.workers = 0
    state = EndpointState(endpoint_id="fake-eid")
    assert await probe(f, state) == "provisioning"
    f.workers = 1
    assert await probe(f, state) == "warm"


async def test_probe_cold_when_no_endpoint():
    f = FakeFacility()
    assert await probe(f, EndpointState()) == "cold"


async def test_ensure_warm_reuses_existing_endpoint():
    f = FakeFacility()
    f.workers = 1
    state = EndpointState(endpoint_id="fake-eid")
    block, out = await ensure_warm(f, Profile(), state)
    assert f.provisioned is False
    assert out.endpoint_id == "fake-eid" and block == "warm"
