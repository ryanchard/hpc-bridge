"""Host-side knobs: scenario_knobs.py output and run_suite's idle-node gate (hermetic)."""
from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KNOBS = HERE / "scenario_knobs.py"


def _knobs(scenario: str) -> tuple[int, dict[str, str]]:
    r = subprocess.run([sys.executable, str(KNOBS), scenario], capture_output=True, text=True, timeout=60, check=False)
    return r.returncode, dict(ln.split("=", 1) for ln in r.stdout.splitlines() if "=" in ln)


def test_compute_scenarios_declare_needs_node():
    for s in ("happy_path", "gated_provision"):
        rc, k = _knobs(s)
        assert rc == 0 and k.get("HPCB_KNOB_NEEDS_NODE") == "1", (s, k)
        assert "HPCB_KNOB_SERIAL" not in k


def test_ssh_failure_scenario_is_serial_not_node_gated():
    rc, k = _knobs("no_ssh_access")
    assert rc == 0 and k["HPCB_KNOB_SERIAL"] == "1" and k["HPCB_KNOB_COOLDOWN_S"] == "660"
    assert "HPCB_KNOB_NEEDS_NODE" not in k


def test_unknown_scenario_exits_nonzero():
    rc, _ = _knobs("no_such_scenario")
    assert rc == 2


def _run_suite():
    spec = importlib.util.spec_from_file_location("run_suite", HERE.parent / "run_suite.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _drive(mod, probes: list[int | None], max_wait_s: int, monkeypatch):
    seq = iter(probes)
    clock = {"t": 0.0}
    monkeypatch.setattr(mod, "_idle_nodes", lambda: next(seq))
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])

    async def fake_sleep(s):
        clock["t"] += s

    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
    return asyncio.run(mod._await_idle_node("cell", asyncio.Event(), max_wait_s))


def test_gate_waits_until_a_node_is_idle(monkeypatch):
    mod = _run_suite()
    assert _drive(mod, [0, 0, 2], 3600, monkeypatch) == 2


def test_gate_gives_up_after_max_wait(monkeypatch):
    mod = _run_suite()
    assert _drive(mod, [0] * 10, 120, monkeypatch) is None


def test_gate_launches_unguarded_when_probe_fails(monkeypatch):
    mod = _run_suite()
    assert _drive(mod, [None], 3600, monkeypatch) == -1
