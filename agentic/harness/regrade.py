"""Offline re-grader: replay stored provenance bundles through the CURRENT invariants.

The bundles' messages.jsonl is the grading substrate, so invariant changes can be validated
against every past run without re-running any agent:

    uv run python agentic/harness/regrade.py [runs_dir]

For each bundle: rebuild the Trace, run the universal registry + the scenario's
EXTRA_INVARIANTS, and diff per-invariant verdicts against what record.json stored (world:
postchecks are skipped — they measured the live cluster, which is gone). Reports verdict
flips and fresh critical failures, then a summary.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[0] / "scenarios"))

from invariants import check_all  # noqa: E402
from trace_adapter import trace_from_bundle  # noqa: E402


def regrade(runs_dir: Path) -> int:
    bundles = flips = now_failing = 0
    for d in sorted(runs_dir.iterdir()):
        if not (d / "record.json").exists() or not (d / "messages.jsonl").exists():
            continue
        bundles += 1
        rec = json.loads((d / "record.json").read_text())
        cfg = rec.get("config", {})
        scen_name = cfg.get("scenario", "?")
        old = {g["name"]: g["ok"] for g in rec.get("grading", [])
               if not g["name"].startswith("world:")}

        t = trace_from_bundle(d)
        results = check_all(t)
        critical = {"agent_engaged"}
        try:
            scen = importlib.import_module(scen_name)
            results += [fn(t) for fn in getattr(scen, "EXTRA_INVARIANTS", [])]
            critical |= set(getattr(scen, "EXPECT_OK", []))
        except ModuleNotFoundError:
            pass
        new = {r.name: r for r in results}

        changed = [(n, old[n], new[n].ok) for n in old if n in new and old[n] != new[n].ok]
        fresh_crit_fails = [r for r in results
                            if not r.ok and r.name in critical and r.name not in old]
        crit_fail_names = [r.name for r in results if not r.ok and r.name in critical]
        label = (f"{cfg.get('persona') or 'auto'}"
                 f"{', ~skill' if cfg.get('ablate_skill') else ''}")
        tag = "FAIL" if crit_fail_names else "OK  "
        if crit_fail_names:
            now_failing += 1
        mark = "  <-- verdict change" if (changed or fresh_crit_fails) else ""
        print(f"[{tag}] {d.name}  ({label}){mark}")
        for n, o, w in changed:
            flips += 1
            print(f"        flip {n}: {'PASS' if o else 'FAIL'} -> {'PASS' if w else 'FAIL'}"
                  f" — {new[n].detail[:110]}")
        for r in fresh_crit_fails:
            print(f"        new critical FAIL {r.name} — {r.detail[:110]}")
    print(f"\n{bundles} bundles re-graded · {flips} per-invariant verdict flips · "
          f"{now_failing} would now grade FAIL")
    return 0


if __name__ == "__main__":
    sys.exit(regrade(Path(sys.argv[1]) if len(sys.argv) > 1
                     else _HERE.parents[0] / "runs"))
