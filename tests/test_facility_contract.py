from hpc_bridge.facility.base import Facility, EndpointHandle
from hpc_bridge.profile import Profile
from tests.fakes import FakeFacility


async def test_fake_facility_satisfies_protocol_and_provisions():
    f = FakeFacility()
    assert isinstance(f, Facility)
    handle = await f.provision(Profile())
    assert isinstance(handle, EndpointHandle)
    assert handle.endpoint_id == "fake-eid"
    assert f.provisioned is True


async def test_fake_facility_manager_online_is_controllable():
    f = FakeFacility()
    assert await f.manager_online("fake-eid") is False
    f.workers = 2
    assert await f.manager_online("fake-eid") is True
