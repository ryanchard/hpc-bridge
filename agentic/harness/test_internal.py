"""Hermetic: the `internal` profile and internal_hostnames' graders."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scenarios"))

import targets  # noqa: E402
from invariants import ToolCall, Trace, check_all  # noqa: E402


def test_internal_profile_layers_on_site():
    m = targets.load_profile("internal")
    assert m["layers"] == ["site", "internal"] and m["capabilities"]["hostnames"] == "internal"
    assert m["capabilities"]["login_hosts"] == ["login01.hpcb.test", "login02.hpcb.test"]      # the PUBLIC names stay
    assert m["capabilities"]["login_internal_hosts"] == ["login01.int.hpcb.test", "login02.int.hpcb.test"]
    import internal_hostnames as sc
    assert targets.meets(sc.REQUIRES, m["capabilities"])[0]
    assert not targets.meets(sc.REQUIRES, targets.load_profile("site")["capabilities"])[0]
    provided = {r.name for r in check_all(Trace([]))} | {fn(Trace([])).name for fn in sc.EXTRA_INVARIANTS}
    assert set(sc.EXPECT_OK) <= provided, set(sc.EXPECT_OK) - provided


def test_internal_hostnames_graders():
    import internal_hostnames as sc
    login = ToolCall.of("mcp__endpoint__run_shell", {"shape": "login", "command": "hostname -f"}, {"phase": "complete", "stdout": "login02.int.hpcb.test\n"})
    assert sc.internal_name_seen(Trace([login])).ok
    public = ToolCall.of("mcp__endpoint__run_shell", {"shape": "login", "command": "hostname -f"}, {"phase": "complete", "stdout": "login02.hpcb.test\n"})
    assert not sc.internal_name_seen(Trace([public])).ok
    by_addr = ToolCall.of("mcp__endpoint__connect_facility", {"facility": "f"}, {"phase": "provisioning", "notice": "first contact over SSH: hpcbridge-test-00@172.20.0.6 (host pinned by HPC_BRIDGE_SSH_HOST); env_setup run there"})
    by_name = ToolCall.of("mcp__endpoint__connect_facility", {"facility": "f"}, {"phase": "provisioning", "notice": "first contact over SSH: hpcbridge-test-00@login02.hpcb.test; env_setup run there"})
    assert sc.pinned_by_address(Trace([by_addr])).ok and "172.20.0.6" in sc.pinned_by_address(Trace([by_addr])).detail
    assert not sc.pinned_by_address(Trace([by_name])).ok
