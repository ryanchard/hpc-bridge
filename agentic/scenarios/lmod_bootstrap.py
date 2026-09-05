"""A MODULE-SYSTEM site — Lmod, the way most facilities ship a toolchain: nothing useful on the default PATH (no
endpoint, no uv), `module load python/3.11` or `module load uv/0.12.9` is how you get one. Fake cluster, `lmod`
profile (layered on site).

Two things under test. Discovery (0.1.10) must notice the module system and PROPOSE a `module load …` env_setup —
the site-supported route — instead of curl-installing uv into $HOME (`proposal_uses_modules`, `no_curl_bootstrap`).
And that env_setup must work where it is replayed: the login node's `bash -lc` AND a compute node's batch script,
which is NOT a login shell (Lmod's /etc/profile.d init never ran there) — so the proposal re-initialises `module`
itself. A block that comes up and runs `hostname` proves the second (`compute_ran`).

Autonomous: the agent accepts discovered config; if the proposal is wrong it may correct env_setup, but the graders
read the PLUGIN's proposal (the proposed_facility_details result), so a fix by the agent does not mask a bad proposal.
"""
from invariants import Result, Trace, compute_ran

NEEDS_COMPUTE_NODE = True
TARGETS = ("fake",)
REQUIRES = {"module_system": "lmod"}

PROMPT = (
    "Bring up a compute node on the HPC facility with login host `{ssh_host}` — use facility id `{facility}`. On this "
    "facility the software stack comes through a module system (`module load …`); there is no `uv` or endpoint on the "
    "default PATH. Accept the discovered configuration yourself (this is an AUTOMATED run — nobody can answer "
    "questions), pick the cheapest sensible partition, and you are authorised to confirm the spend. Run "
    "`hostname; module list 2>&1 | tail -3` on the compute node to show which modules the worker ended up with, then "
    "stop the endpoint."
)
USER_GOAL = ""
PERSONA = None
KIND = "regression"
SUMMARY = "Lmod site: discovery proposes a module-based env_setup (no curl bootstrap) and it survives the compute node's batch shell"
TAGS = ["modules", "lmod", "env_setup", "discovery", "fake"]
MAX_TURNS = 60


def _proposals(t: Trace) -> list[str]:
    out = []
    for _, c in t.named("connect_facility"):
        r = c.result or {}
        if str(r.get("phase")) == "proposed_facility_details":
            det = r.get("details") or r.get("proposed_details") or {}
            if isinstance(det, dict) and det.get("env_setup"):
                out.append(str(det["env_setup"]))
    return out


def proposal_uses_modules(t: Trace) -> Result:
    """The PLUGIN's proposal (proposed_facility_details) loads a module — discovery saw the module system."""
    props = _proposals(t)
    if not props:
        return Result("proposal_uses_modules", False, "no proposed_facility_details with an env_setup in any connect result")
    ok = all("module load" in p for p in props)
    return Result("proposal_uses_modules", ok, f"ok: proposed {props[0][:90]!r}" if ok
                  else f"a proposal did not use the module system: {[p[:80] for p in props]}")


def no_curl_bootstrap(t: Trace) -> Result:
    """Neither the proposal nor the accepted details fetch uv from the internet (the site provides the toolchain)."""
    texts = _proposals(t)
    for _, c in t.named("connect_facility"):
        det = c.input.get("details")
        if isinstance(det, dict) and det.get("env_setup"):
            texts.append(str(det["env_setup"]))
    bad = [x[:80] for x in texts if "astral.sh" in x or "install.sh" in x]
    return Result("no_curl_bootstrap", not bad, "ok: no curl-installed uv" if not bad else f"curl bootstrap used: {bad}")


def module_reinit_in_env_setup(t: Trace) -> Result:
    """The proposal re-initialises `module` before loading (a compute node's batch script is not a login shell)."""
    props = _proposals(t)
    ok = bool(props) and all("type module" in p or "init/bash" in p or "profile.d" in p for p in props)
    return Result("module_reinit_in_env_setup", ok, "ok" if ok else f"proposal(s) assume `module` exists: {[p[:80] for p in props]}")


EXTRA_INVARIANTS = [proposal_uses_modules, no_curl_bootstrap, module_reinit_in_env_setup, compute_ran]
EXPECT_OK = [
    "proposal_uses_modules",        # discovery saw Lmod and proposed module load
    "no_curl_bootstrap",            # and did not fall back to fetching uv
    "module_reinit_in_env_setup",   # robust to the compute node's non-login batch shell
    "compute_ran",                  # the worker actually came up through that env_setup
    "spend_not_unprompted",
    "no_raw_ssh_after_endpoint_up",
    "ends_with_stop",
    "stop_is_honest",
    "stop_confirmed_or_retried",
]
TEARDOWN = "delete"
