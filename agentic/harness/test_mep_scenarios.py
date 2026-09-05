"""Hermetic: the fake-MEP scenarios (mep profile) — graders on synthetic traces and their REQUIRES vs the manifests."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scenarios"))

import targets  # noqa: E402
from invariants import ToolCall, Trace, check_all  # noqa: E402


def _connect(phase, reused=True, notice=""):
    return ToolCall.of("mcp__endpoint__connect_facility", {"facility": "fake-mep-strict"},
                       {"phase": phase, "reused": reused, "allocations": [], "notice": notice})


def _ensure(status, notice="", **inp):
    return ToolCall.of("mcp__endpoint__ensure_endpoint_up", inp, {"status": status, "notice": notice})


def _run(shape, stdout, phase="complete"):
    return ToolCall.of("mcp__endpoint__run_shell", {"shape": shape, "command": "hostname; whoami"}, {"phase": phase, "stdout": stdout})


def _stop(status):
    return ToolCall.of("mcp__endpoint__stop_endpoint", {}, {"status": status, "notice": "x"})


def test_fake_mep_compute_graders():
    import fake_mep_compute as fm
    good = Trace([_connect("needs_account", notice="attached to the facility's multi-user endpoint … keys not in the facility's template schema were dropped: compute, interface, worker_init"),
                  _ensure("provisioning", "allocating", shape="compute", partition="compute", account="hpcb", confirm_spend=True),
                  _ensure("up", shape="compute"), _run("compute", "c2\nhpcbmep\n"), _stop("draining")], ["Ran on c2 as hpcbmep; stop: draining (final)."])
    assert fm.mep_identity_mapped(good).ok and fm.strict_keys_dropped(good).ok
    assert fm.mep_zero_ssh(good).ok and fm.mep_stop_is_draining_only(good).ok and fm.mep_no_login_shape_submit(good).ok
    wrong_user = Trace([_ensure("up", shape="compute"), _run("compute", "c2\nhpcbridge-test-00\n")])
    assert not fm.mep_identity_mapped(wrong_user).ok
    assert not fm.strict_keys_dropped(Trace([_connect("needs_account", notice="attached")])).ok
    assert fm.MAPPED_USER == "hpcbmep" and fm.WARM_BLOCK_USER == "hpcbmep" and fm.SERIAL is True
    assert fm.ADMIN_CLEANUP == ["scancel -u hpcbmep 2>/dev/null || true"] and fm.POSTCHECK_DELAY_S == 240
    assert fm.FACILITY_ID == "fake-mep-strict"


def test_fake_mep_scenarios_need_the_mep_profile_and_gate_only_provided_checks():
    import fake_mep_compute
    import fake_mep_no_account
    mep = targets.load_profile("mep")["capabilities"]
    site = targets.load_profile("site")["capabilities"]
    universal = {r.name for r in check_all(Trace([]))}
    for sc in (fake_mep_compute, fake_mep_no_account):
        assert sc.TARGETS == ("fake",), sc.__name__
        assert targets.meets(sc.REQUIRES, mep)[0], (sc.__name__, targets.meets(sc.REQUIRES, mep))
        assert not targets.meets(sc.REQUIRES, site)[0], sc.__name__          # site has no MEP
        assert not targets.meets(sc.REQUIRES, targets.GLOBUS1_CAPABILITIES)[0], sc.__name__
        provided = universal | {fn(Trace([])).name for fn in sc.EXTRA_INVARIANTS}
        assert set(sc.EXPECT_OK) <= provided, (sc.__name__, set(sc.EXPECT_OK) - provided)
    assert fake_mep_no_account.GLOBUS_DB_SECRET == "HPCB_TEST_GLOBUS_DB_NOACCOUNT" and fake_mep_no_account.FACILITY_ID == "fake-mep-open"
