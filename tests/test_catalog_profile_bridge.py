"""The catalog -> MachineProfile bridge (profile_from_catalog_entry)."""
import asyncio
from dataclasses import asdict

from hpc_bridge.catalog.bundled import BundledCatalog
from hpc_bridge.facility.remote import anvil_profile, profile_from_catalog_entry


def _anvil_entry():
    return asyncio.run(BundledCatalog().get("purdue:anvil"))


def test_bridge_matches_anvil_profile():
    # The catalog entry must reconstruct exactly the hardcoded profile the working path uses.
    got = profile_from_catalog_entry(_anvil_entry(), user="u1", account="ACCT-CPU")
    ref = anvil_profile(account="ACCT-CPU", user="u1")
    assert asdict(got) == asdict(ref)


def test_bridge_resolves_templates_and_overrides():
    p = profile_from_catalog_entry(
        _anvil_entry(), user="bob", account="A", partition="wholenode", venv="/opt/gce"
    )
    assert "{venv}" not in p.env_setup
    assert "/opt/gce/bin/activate" in p.env_setup
    assert p.worker_init == p.env_setup  # worker_init is the resolved env_setup
    assert "{user}" not in p.scratch_root
    assert "/anvil/scratch/bob/" in p.scratch_root
    assert p.partition == "wholenode"  # explicit override beats the entry default
    assert p.account == "A"


def test_bridge_defaults_venv_to_convention():
    p = profile_from_catalog_entry(_anvil_entry(), user="alice", account="A")
    assert "/home/alice/hpc-bridge/gce-venv/bin/activate" in p.env_setup
