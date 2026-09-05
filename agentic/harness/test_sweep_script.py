"""Hermetic: the profile sweep names only real scenarios, and every named scenario is runnable on its profile."""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import targets  # noqa: E402

SWEEP = HERE.parent / "sweep_profiles.sh"
SCENARIOS = {p.stem for p in (HERE.parent / "scenarios").glob("*.py") if not p.stem.startswith("_")}


def _cells() -> dict[str, list[str]]:
    out = {}
    for m in re.finditer(r'^\s+(\w+)\)\s+echo "([\w,]+) (\d+)" ;;', SWEEP.read_text(), re.M):
        out[m.group(1)] = m.group(2).split(",")
    return out


def test_sweep_names_existing_scenarios_that_their_profile_can_run():
    import importlib
    sys.path.insert(0, str(HERE.parent / "scenarios"))
    cells = _cells()
    assert set(cells) == {"default", "site", "totp", "pbs", "lmod", "f2b", "polaris", "internal", "mep"}
    for prof, names in cells.items():
        caps = targets.load_profile(prof)["capabilities"]
        for n in names:
            assert n in SCENARIOS, (prof, n)
            mod = importlib.import_module(n)
            tg = getattr(mod, "TARGETS", None)
            assert tg is None or "fake" in tg, (prof, n)
            ok, why = targets.meets(getattr(mod, "REQUIRES", None), caps)
            assert ok, (prof, n, why)


def test_sweep_never_defaults_to_fable():
    text = SWEEP.read_text()
    assert 'MODELS="claude-opus-5"' in text and "fable" not in text.lower().replace("never fable", "")
