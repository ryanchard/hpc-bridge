from hpc_bridge.cost import cap_output, estimate_spend


def test_estimate_spend_node_hours():
    # 1 node held 1 hour at charge_factor 1.0 => 1 node-hour
    assert estimate_spend(3600.0, nodes=1, charge_factor=1.0) == 1.0
    assert estimate_spend(1800.0, nodes=2, charge_factor=1.0) == 1.0
    assert estimate_spend(3600.0, nodes=1, charge_factor=0.0) == 0.0


def test_cap_output_passthrough_and_truncation():
    assert cap_output("short", 100) == "short"
    capped = cap_output("x" * 50, 10)
    assert capped.startswith("x" * 10)
    assert "truncated" in capped
