import pytest

from hpc_bridge.profile import Profile


def test_profile_defaults_to_batch():
    assert Profile().mode == "batch"


def test_profile_rejects_invalid_mode():
    with pytest.raises(ValueError):
        Profile(mode="garbage")  # type: ignore[arg-type]


def test_profile_rejects_nonpositive_idletime():
    with pytest.raises(ValueError):
        Profile(max_idletime_s=0)


def test_profile_carries_scheduler_and_cpus_per_node():
    from hpc_bridge.catalog.entry import CatalogEntry, Compute, Defaults
    from hpc_bridge.facility.remote import profile_from_catalog_entry
    import datetime

    entry = CatalogEntry(
        id="polaris", facility_key="alcf", facility="ALCF",
        description="d", display_name="Polaris", ssh_host="polaris",
        compute=Compute(
            scheduler="pbs", interface="hsn0",
            env_setup="source {venv}/bin/activate",
            scratch_root="/home/{user}/.hpc-bridge",
        ),
        defaults=Defaults(partition="debug", cpus_per_node=32),
        last_validated=datetime.date(2026, 7, 10),
    )
    prof = profile_from_catalog_entry(entry, user="rchard", account="acct")
    assert prof.scheduler == "pbs"
    assert prof.cpus_per_node == 32
