"""Hermetic: the `lmod` profile's manifest and the lmod_bootstrap scenario's graders."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scenarios"))

import targets  # noqa: E402
from invariants import ToolCall, Trace, check_all  # noqa: E402

MODULE_SETUP = ("type module >/dev/null 2>&1 || . /etc/profile.d/lmod.sh; module load uv/0.12.9; [ -d {venv} ] || uv venv {venv} "
                "--python 3.11; . {venv}/bin/activate; command -v globus-compute-endpoint >/dev/null 2>&1 || uv pip install -q x")
CURL_SETUP = 'export PATH="$HOME/.local/bin:$PATH"; command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh; uv venv {venv}'


def _proposal(env_setup):
    return ToolCall.of("mcp__endpoint__connect_facility", {"facility": "f", "ssh_host": "login"},
                       {"phase": "proposed_facility_details", "proposed_details": {"env_setup": env_setup, "partition": "debug"}})


def _accept(env_setup):
    return ToolCall.of("mcp__endpoint__connect_facility", {"facility": "f", "details": {"env_setup": env_setup, "ssh_host": "login"}},
                       {"phase": "provisioning"})


def test_lmod_layers_on_site_and_declares_the_module_system(monkeypatch):
    m = targets.load_profile("lmod")
    assert m["layers"] == ["site", "lmod"] and m["capabilities"]["module_system"] == "lmod" and m["capabilities"]["nodes"] == 3
    assert "uv/0.12.9" in m["capabilities"]["modules"] and "python/3.11" in m["capabilities"]["modules"]
    import lmod_bootstrap as sc
    assert targets.meets(sc.REQUIRES, m["capabilities"])[0]
    assert not targets.meets(sc.REQUIRES, targets.load_profile("site")["capabilities"])[0]
    assert not targets.meets(sc.REQUIRES, targets.GLOBUS1_CAPABILITIES)[0]
    assert sc.TARGETS == ("fake",) and sc.NEEDS_COMPUTE_NODE is True
    provided = {r.name for r in check_all(Trace([]))} | {fn(Trace([])).name for fn in sc.EXTRA_INVARIANTS}
    assert set(sc.EXPECT_OK) <= provided, set(sc.EXPECT_OK) - provided


def test_lmod_bootstrap_graders_read_the_plugins_proposal_not_the_agents_fix():
    import lmod_bootstrap as sc
    good = Trace([_proposal(MODULE_SETUP), _accept(MODULE_SETUP)])
    assert sc.proposal_uses_modules(good).ok and sc.no_curl_bootstrap(good).ok and sc.module_reinit_in_env_setup(good).ok
    # the plugin proposed the curl bootstrap; the agent fixed it by hand — the PROPOSAL still fails
    fixed_by_agent = Trace([_proposal(CURL_SETUP), _accept(MODULE_SETUP)])
    assert not sc.proposal_uses_modules(fixed_by_agent).ok and not sc.no_curl_bootstrap(fixed_by_agent).ok
    # a module proposal that assumes `module` exists (breaks in a batch script)
    naive = Trace([_proposal("module load uv/0.12.9; uv venv {venv}"), _accept("module load uv/0.12.9; uv venv {venv}")])
    assert sc.proposal_uses_modules(naive).ok and not sc.module_reinit_in_env_setup(naive).ok
    # no proposal at all (the facility was catalogued): the module graders fail loudly rather than pass vacuously
    assert not sc.proposal_uses_modules(Trace([_accept(MODULE_SETUP)])).ok
