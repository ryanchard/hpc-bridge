# tests/test_catalog_bundled.py
from hpc_bridge.catalog.base import CatalogProvider
from tests.fakes import FakeCatalog, fake_entry


async def test_fake_catalog_satisfies_protocol():
    c = FakeCatalog([fake_entry(id="anvil", facility_key="purdue")])
    assert isinstance(c, CatalogProvider)


async def test_fake_catalog_get_by_id_and_subject_and_alias():
    c = FakeCatalog(
        [fake_entry(id="anvil", facility_key="purdue")],
        aliases={"anvil": ["anvil.x.edu"]},
    )
    assert (await c.get("anvil")).id == "anvil"
    assert (await c.get("purdue:anvil")).id == "anvil"
    assert (await c.get("anvil.x.edu")).id == "anvil"
    assert await c.get("nope") is None


async def test_fake_catalog_discover_filters_by_query():
    c = FakeCatalog([
        fake_entry(id="anvil", facility_key="purdue", description="CPU cluster"),
        fake_entry(id="polaris", facility_key="alcf", description="GPU machine"),
    ])
    got = {s.id for s in await c.discover("gpu")}
    assert got == {"polaris"}
    assert {s.id for s in await c.discover("")} == {"anvil", "polaris"}
