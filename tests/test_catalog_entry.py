# tests/test_catalog_entry.py
import datetime

import pytest
from pydantic import ValidationError

from hpc_bridge.catalog.entry import CatalogEntry, CatalogSummary

VALID_UUID = "11111111-2222-3333-4444-555555555555"


def _entry(**over):
    base = {
        "id": "anvil",
        "facility_key": "purdue",
        "facility": "Purdue / ACCESS",
        "description": "Anvil CPU cluster",
        "display_name": "HPC-Bridge Anvil",
        "transfer_endpoint_uuid": VALID_UUID,
        "ssh_host": "anvil.rcac.purdue.edu",
        "allocation": {"command": "mybalance", "parser": "mybalance"},
        "compute": {
            "scheduler": "slurm",
            "interface": "ib0",
            "env_setup": "module load x && source {venv}/bin/activate",
            "scratch_root": "/anvil/scratch/{user}/.hpc-bridge",
        },
        "defaults": {"partition": "debug"},
        "last_validated": "2026-06-03",
    }
    base.update(over)
    return base


def test_valid_entry_parses_and_applies_defaults():
    e = CatalogEntry.model_validate(_entry())
    assert e.id == "anvil"
    assert e.compute.amqp_port == 443             # defaulted
    assert e.compute.endpoint_name == "hpc-bridge"  # defaulted
    assert e.defaults.walltime == "00:30:00"      # defaulted
    assert e.auth_method == "ssh-key"             # defaulted
    assert e.provenance == "curated"              # defaulted
    assert e.compute_mep_uuid is None             # optional
    assert e.last_validated == datetime.date(2026, 6, 3)


def test_subject_is_facility_key_colon_id():
    assert CatalogEntry.model_validate(_entry()).subject == "purdue:anvil"


def test_summary_is_agent_safe_subset():
    s = CatalogEntry.model_validate(_entry()).summary()
    assert s.subject == "purdue:anvil"
    assert s.display_name == "HPC-Bridge Anvil"
    # summary must NOT leak executable config
    assert not hasattr(s, "env_setup")
    assert set(CatalogSummary.model_fields) == {
        "subject", "id", "facility", "description", "display_name",
        "provenance", "last_validated",
    }


def test_bad_uuid_rejected():
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(_entry(transfer_endpoint_uuid="not-a-uuid"))


def test_unknown_parser_rejected():
    with pytest.raises(ValidationError):
        CatalogEntry.model_validate(
            _entry(allocation={"command": "x", "parser": "bogus"})
        )


def test_uuid_is_normalized_to_canonical_form():
    e = CatalogEntry.model_validate(_entry(
        transfer_endpoint_uuid="11111111222233334444555555555555"  # no dashes
    ))
    assert e.transfer_endpoint_uuid == "11111111-2222-3333-4444-555555555555"


def test_profile_kwargs_maps_every_machineprofile_field():
    kw = CatalogEntry.model_validate(_entry()).profile_kwargs()
    # superset-of-MachineProfile contract; account/worker_init are intentionally absent
    expected = {
        "name", "endpoint_name", "display_name", "env_setup", "interface",
        "partition", "walltime", "max_workers_per_node", "nodes_per_block",
        "max_blocks", "available_accelerators", "amqp_port", "scheduler_options",
        "scratch_root",
    }
    assert set(kw) == expected
    assert "account" not in kw
    assert "worker_init" not in kw
    assert kw["interface"] == "ib0"
    assert kw["name"] == "anvil"
