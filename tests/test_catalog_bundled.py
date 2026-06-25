# tests/test_catalog_bundled.py
from pathlib import Path

import pytest

from hpc_bridge.catalog.base import CatalogProvider
from hpc_bridge.catalog.bundled import BundledCatalog
from tests.fakes import FakeCatalog, fake_entry

FIX = Path(__file__).parent / "catalog_fixtures"


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


async def test_bundled_gets_by_subject_id_alias():
    c = BundledCatalog(FIX / "two_machines.yaml")
    assert (await c.get("anvil")).compute.interface == "ib0"
    assert (await c.get("purdue:anvil")).id == "anvil"
    assert (await c.get("anvil.rcac.purdue.edu")).id == "anvil"   # alias
    assert await c.get("absent") is None


async def test_bundled_discover_filters():
    c = BundledCatalog(FIX / "two_machines.yaml")
    assert {s.id for s in await c.discover("gpu")} == {"polaris"}
    assert {s.id for s in await c.discover("")} == {"anvil", "polaris"}


async def test_bundled_discover_by_facility_key():
    c = BundledCatalog(FIX / "two_machines.yaml")
    assert {s.id for s in await c.discover("alcf")} == {"polaris"}


async def test_bundled_rejects_a_malformed_entry():
    bad = FIX / "bad.yaml"
    bad.write_text("- id: x\n")  # missing required fields
    try:
        with pytest.raises(Exception):
            BundledCatalog(bad)
    finally:
        bad.unlink()


async def test_bundled_loads_a_directory(tmp_path):
    (tmp_path / "a.yaml").write_text(
        "- {id: anvil, facility_key: purdue, facility: P, description: d, display_name: A,"
        " transfer_endpoint_uuid: '11111111-2222-3333-4444-555555555555', ssh_host: h,"
        " allocation: {command: mybalance, parser: mybalance},"
        " compute: {scheduler: slurm, interface: ib0, env_setup: x, scratch_root: s},"
        " defaults: {partition: debug}, last_validated: 2026-06-03}\n"
    )
    (tmp_path / "b.yaml").write_text(
        "- {id: polaris, facility_key: alcf, facility: A, description: d, display_name: P,"
        " transfer_endpoint_uuid: '99999999-8888-7777-6666-555555555555', ssh_host: h,"
        " allocation: {command: sbank, parser: sbank},"
        " compute: {scheduler: pbs, interface: bond0, env_setup: x, scratch_root: s},"
        " defaults: {partition: debug}, last_validated: 2026-06-03}\n"
    )
    c = BundledCatalog(tmp_path)
    assert {s.id for s in await c.discover("")} == {"anvil", "polaris"}


async def test_bundled_missing_path_is_empty_not_crash(tmp_path):
    c = BundledCatalog(tmp_path / "does_not_exist")
    assert await c.get("anything") is None
    assert await c.discover("") == []


async def test_default_bundled_catalog_has_anvil():
    c = BundledCatalog()  # default packaged seed dir
    anvil = await c.get("purdue:anvil")
    assert anvil is not None
    assert anvil.compute.interface == "ib0"
    assert anvil.compute.amqp_port == 443
    assert anvil.allocation.parser == "mybalance"
