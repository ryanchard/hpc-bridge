"""Hermetic: the `polaris` profile (a PBS overlay) and polaris_filesystems' graders."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scenarios"))

import targets  # noqa: E402
from invariants import ToolCall, Trace, check_all  # noqa: E402

HELD = ("allocating nodes on 'debug'… — pilot 3.pbsserver is HELD and will not start on its own. The scheduler's comment: "
        "'HELD by the site: every job must request the filesystems it uses, e.g. -l filesystems=home:eagle'. Fix the facility's scheduler_options")


def _ensure(status, notice="", **inp):
    return ToolCall.of("mcp__endpoint__ensure_endpoint_up", inp, {"status": status, "notice": notice})


def _connect(details=None):
    inp = {"facility": "f"}
    if details:
        inp["details"] = details
    return ToolCall.of("mcp__endpoint__connect_facility", inp, {"phase": "provisioning"})


def _run(stdout):
    return ToolCall.of("mcp__endpoint__run_shell", {"shape": "compute", "command": "hostname"}, {"phase": "complete", "stdout": stdout})


def test_polaris_layers_on_pbs_and_inherits_its_stack():
    m = targets.load_profile("polaris")
    assert m["layers"] == ["pbs", "polaris"] and m["compose"] == "docker-compose.pbs.yml"
    assert m["capabilities"]["scheduler"] == "pbs" and m["capabilities"]["site_rule"] == "filesystems" and m["capabilities"]["nodes"] == 2
    import polaris_filesystems as sc
    assert targets.meets(sc.REQUIRES, m["capabilities"])[0]
    assert not targets.meets(sc.REQUIRES, targets.load_profile("pbs")["capabilities"])[0]   # plain pbs has no rule
    assert not targets.meets(sc.REQUIRES, targets.load_profile("site")["capabilities"])[0]
    provided = {r.name for r in check_all(Trace([]))} | {fn(Trace([])).name for fn in sc.EXTRA_INVARIANTS}
    assert set(sc.EXPECT_OK) <= provided, set(sc.EXPECT_OK) - provided


def test_polaris_filesystems_graders_take_either_branch():
    import polaris_filesystems as sc
    start = _ensure("provisioning", "allocating", shape="compute", partition="debug", confirm_spend=True)
    satisfied = Trace([start, _ensure("provisioning", HELD, shape="compute"),
                       _connect({"scheduler_options": "#PBS -l filesystems=home:eagle"}),
                       _ensure("up", shape="compute"), _run("c1\nFS=5.pbsserver")],
                      ["The pilot was held: the site requires -l filesystems; I added it and the block came up."])
    assert sc.held_surfaced(satisfied).ok and sc.hold_comment_surfaced(satisfied).ok and sc.directive_added(satisfied).ok
    assert sc.site_rule_handled(satisfied).ok and sc.no_endless_hold_wait(satisfied).ok
    explained = Trace([start, _ensure("provisioning", HELD, shape="compute")],
                      ["The block is HELD by a site rule: jobs must request filesystems (-l filesystems=home:eagle)."])
    assert sc.site_rule_handled(explained).ok and not sc.directive_added(explained).ok
    forever = Trace([start] + [_ensure("provisioning", HELD, shape="compute")] * 6, ["still waiting"])
    assert not sc.no_endless_hold_wait(forever).ok and not sc.site_rule_handled(forever).ok
    generic = Trace([start, _ensure("provisioning", "— pilot 3 is HELD; a held job usually means a bad scheduler directive", shape="compute")], ["held"])
    assert sc.held_surfaced(generic).ok and not sc.hold_comment_surfaced(generic).ok
