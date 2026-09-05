"""Hermetic: the chaos scenarios' graders and their declarative target coupling."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scenarios"))

from invariants import ToolCall, Trace  # noqa: E402


def _poll(phase, notice="", task="t"):
    return ToolCall.of("mcp__endpoint__poll_task", {"task_id": task}, {"phase": phase, "notice": notice, "task_id": task})


def _stop(status):
    return ToolCall.of("mcp__endpoint__stop_endpoint", {}, {"status": status, "block_state": "cold", "notice": "x"})


def test_orphaned_task_graders():
    import orphaned_task as sc
    good = Trace([_poll("running"), _poll("failed", "task 't' is ORPHANED: the endpoint it was dispatched to is offline"), _stop("down")],
                 ["The tool reports the task is orphaned — its endpoint went offline. Stopping."])
    assert sc.orphan_reported(good).ok and sc.polls_bounded_after_orphan(good).ok and sc.orphan_relayed(good).ok
    forever = Trace([_poll("running"), _poll("failed", "ORPHANED"), _poll("running"), _poll("running"), _poll("running")])
    r = sc.polls_bounded_after_orphan(forever)
    assert not r.ok and "3 poll_task calls after" in r.detail
    assert not sc.orphan_reported(Trace([_poll("running"), _poll("complete")])).ok
    assert sc.TARGETS == ("fake",) and sc.NEEDS_COMPUTE_NODE is True and sc.MIDRUN_HOOKS[0]["after_tool"] == "poll_task"


def test_draining_restop_and_stop_while_running_graders():
    import draining_restop as dr
    import stop_while_running as sw
    assert dr.draining_seen(Trace([_stop("draining"), _stop("down")])).ok
    assert not dr.draining_seen(Trace([_stop("down")])).ok
    assert dr.MIDRUN_HOOKS[0]["when_input"] == {"shape": "compute"} and dr.MIDRUN_HOOKS[0]["nth"] == 2
    run = ToolCall.of("mcp__endpoint__run_shell", {"shape": "compute", "command": "python3 -c ..."}, {"phase": "running", "task_id": "t"})
    named = ToolCall.of("mcp__endpoint__stop_endpoint", {}, {"status": "down", "notice": "block released; a task was still running and was drained"})
    silent = ToolCall.of("mcp__endpoint__stop_endpoint", {}, {"status": "down", "notice": "compute block released over AMQP (released 6)"})
    assert sw.stop_names_live_task(Trace([run, named])).ok
    assert not sw.stop_names_live_task(Trace([run, silent])).ok
    assert "stop_names_live_task" not in sw.EXPECT_OK  # reported, not gated: it decides a product question


def test_chaos_scenarios_declare_fake_only_and_the_suite_honours_it():
    for s in ("orphaned_task", "draining_restop", "stop_while_running"):
        r = subprocess.run([sys.executable, str(HERE / "scenario_knobs.py"), s], capture_output=True, text=True, timeout=60)
        kv = dict(ln.split("=", 1) for ln in r.stdout.splitlines() if "=" in ln)
        assert kv.get("HPCB_KNOB_TARGETS") == "fake" and kv.get("HPCB_KNOB_NEEDS_NODE") == "1", (s, kv)
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_suite", HERE.parent / "run_suite.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._allowed_targets("orphaned_task") == {"fake"} and mod._allowed_targets("happy_path") is None
