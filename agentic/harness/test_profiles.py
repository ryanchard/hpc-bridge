"""Hermetic: fake-cluster profile INHERITANCE (bin/profile.py) and what targets.py derives from a layered profile."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import targets  # noqa: E402

FC = HERE.parent / "fakecluster"
spec = importlib.util.spec_from_file_location("hpcb_profile", FC / "bin" / "profile.py")
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def test_mep_layers_on_site_and_merges_capabilities():
    m = profile.manifest("mep")
    assert m["layers"] == ["site", "mep"] and m["name"] == "mep"
    caps = m["capabilities"]
    assert caps["login_hosts"] == ["login01.hpcb.test", "login02.hpcb.test"] and caps["nodes"] == 3   # from site
    assert caps["mep"] == "consent-free" and caps["mep_schemas"] == ["strict", "open"]                    # from mep
    assert m["catalog"]["cmd"].startswith("docker exec hpcb-fake-login-1 ")
    assert "base" not in m
    assert profile.manifest("site")["layers"] == ["site"] and "catalog" not in profile.manifest("site")


def test_build_materialises_base_files_plus_overlay_and_a_flat_manifest(tmp_path):
    out = tmp_path / "mep"
    m = profile.build("mep", out)
    assert (out / "slurm.conf").is_file() and (out / "job_submit.lua").is_file()          # site's
    assert (out / "setup.d" / "login.sh").is_file() and (out / "setup.d" / "login-mep.sh").is_file()   # both layers
    assert (out / "mep" / "schema-strict.json").is_file() and (out / "mep" / "hpcb-mep-catalog").is_file()
    assert (out / "deregister.sh").is_file() and (out / "mep" / "tools.sh").is_file()   # down.sh --wipe runs the former
    import os
    assert os.access(out / "deregister.sh", os.X_OK)
    assert not (out / "compose.override.yml").exists()   # overlays go to compose -f, never into the mounted dir
    import tomllib
    flat = tomllib.loads((out / "profile.toml").read_text())
    assert flat["capabilities"]["mep"] == "consent-free" and flat["capabilities"]["nodes"] == 3 and "base" not in flat
    assert flat["catalog"]["cmd"] == m["catalog"]["cmd"]
    # rebuilding replaces (no stale files)
    (out / "stale").write_text("x")
    profile.build("mep", out)
    assert not (out / "stale").exists()


def test_unknown_profile_and_cycles_are_refused():
    with pytest.raises(SystemExit):
        profile.manifest("moon")


def test_targets_reads_the_layered_profile_and_the_catalog_command(monkeypatch):
    monkeypatch.setenv("HPCB_FAKE_PROFILE", "mep")
    t = targets.get("fake")
    assert t.profile == "mep" and t.nodes == 3 and t.capabilities["mep"] == "consent-free"
    assert t.catalog_cmd == "docker exec hpcb-fake-login-1 /usr/local/bin/hpcb-mep-catalog"
    assert targets.meets({"mep": "consent-free", "accounting": "enforce", "login_nodes": 2}, t.capabilities)[0]
    monkeypatch.setenv("HPCB_FAKE_PROFILE", "site")
    assert targets.get("fake").catalog_cmd is None
    assert targets.get("globus1").catalog_cmd is None
    import subprocess
    out = subprocess.run([sys.executable, str(HERE / "targets.py"), "fake"], capture_output=True, text=True, timeout=60,
                         env={**__import__("os").environ, "HPCB_FAKE_PROFILE": "mep"}).stdout
    assert "HPCB_T_CATALOG_CMD='docker exec hpcb-fake-login-1 /usr/local/bin/hpcb-mep-catalog'" in out
