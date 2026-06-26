"""The catalog -> MachineProfile bridge (profile_from_catalog_entry)."""
import asyncio

from hpc_bridge.catalog.bundled import BundledCatalog
from hpc_bridge.facility.remote import profile_from_catalog_entry


def _anvil_entry():
    return asyncio.run(BundledCatalog().get("purdue:anvil"))


def test_bridge_reconstructs_the_anvil_config():
    # The bundled anvil entry resolves to the known-good profile we've stood up live. With the
    # hardcoded anvil_profile() removed, this is the anvil-config oracle.
    p = profile_from_catalog_entry(_anvil_entry(), user="u1", account="ACCT-CPU")
    assert p.name == "anvil"
    assert p.endpoint_name == "hpc-bridge" and p.display_name == "HPC-Bridge Anvil"
    assert p.interface == "ib0" and p.partition == "debug" and p.account == "ACCT-CPU"
    assert p.env_setup == (
        "module load anaconda/2024.02-py311 && source /home/u1/hpc-bridge/gce-venv/bin/activate"
    )
    assert p.worker_init == p.env_setup
    assert p.scratch_root == "/anvil/scratch/u1/.hpc-bridge"
    assert p.amqp_port == 443
    assert (p.max_workers_per_node, p.nodes_per_block, p.max_blocks) == (2, 1, 1)


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
