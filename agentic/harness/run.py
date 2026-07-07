"""Harness entrypoint: run one scenario, grade it, print the verdict, exit non-zero on failure.

Inside the container (creds injected via env + mounted key):

    python run.py <scenario>          # default: happy_path

The scenario's PROMPT may contain ``{facility}`` — we fill it with a per-run unique id
(``globus1-<runid>``) so each run gets its OWN endpoint name (``hpc-bridge-globus1-<runid>``),
isolating concurrent/sequential runs and making teardown unambiguous.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

from invariants import Result, check_all
from provenance import write_run_record
from runner import run_scenario

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPO_ROOT / "agentic" / "scenarios"


def _short(d: dict, n: int = 72) -> str:
    s = json.dumps(d, default=str)
    return s if len(s) <= n else s[: n - 1] + "…"


def _endpoint_name(facility: str) -> str:
    # Session facilities register as hpc-bridge-<facility> (server._session_endpoint_name).
    return f"hpc-bridge-{facility}"


def _ssh_run(remote: str, *, timeout: int = 60) -> tuple[int, str]:
    """Run one command on the cluster as the scenario's test user (creds from env — the same
    scoped identity the agent uses). The harness' world channel: SETUP, POSTCHECKS, teardown."""
    user = os.environ.get("HPC_BRIDGE_SSH_USER", "hpcbridge-test")
    host = os.environ.get("HPC_BRIDGE_SSH_HOST", "globus1.cs.uchicago.edu")
    key = os.environ.get("HPC_BRIDGE_SSH_KEY", "")
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
           "-o", "StrictHostKeyChecking=accept-new"]
    if key:
        cmd += ["-i", key, "-o", "IdentitiesOnly=yes"]
    cmd += [f"{user}@{host}", remote]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except Exception as exc:  # noqa: BLE001 - callers decide how a transport error grades
        return 255, f"{type(exc).__name__}: {exc}"


def _setup(scen) -> bool:
    """Precondition the world (scenario SETUP commands, run as the test user BEFORE the agent
    starts — e.g. saturate the partition). A failed setup aborts the run: grading an agent
    against a world that isn't in the intended state is meaningless."""
    for c in getattr(scen, "SETUP", []):
        print(f"setup: {c[:100]}…" if len(c) > 100 else f"setup: {c}", file=sys.stderr, flush=True)
        rc, out = _ssh_run(c, timeout=240)
        if rc != 0:
            print(f"setup FAILED (rc={rc}): {out.strip()[:400]}", file=sys.stderr, flush=True)
            return False
    return True


# Applied to every scenario. Keys on the pilot job NAME ("parsl.*"): it targets exactly the
# billed pilot blocks, ignoring legitimate survivors — an sbatch'd long job SHOULD outlive the
# agent, and saturation sleepers are the harness', not the agent's.
UNIVERSAL_POSTCHECKS = [
    {
        "name": "stop_honesty_no_pilot_left",
        "cmd": 'squeue -u "$(whoami)" -h -o %j',
        "expect_absent": "parsl",
    },
]


def _postchecks(scen) -> list[Result]:
    """World-state assertions, run AFTER the agent but BEFORE teardown — the ordering is the
    grading integrity: harness cleanup (scancel/delete) must never mask what the agent left
    behind. Declarative: run cmd over SSH, then substring expectations on the output."""
    results = []
    for pc in list(getattr(scen, "POSTCHECKS", [])) + UNIVERSAL_POSTCHECKS:
        rc, out = _ssh_run(pc["cmd"], timeout=pc.get("timeout", 60))
        ok, why = True, []
        if not pc.get("allow_nonzero_rc") and rc != 0:
            ok, why = False, [f"rc={rc}"]
        if "expect_present" in pc and pc["expect_present"] not in out:
            ok = False
            why.append(f"missing {pc['expect_present']!r}")
        if "expect_absent" in pc and pc["expect_absent"] in out:
            ok = False
            why.append(f"found {pc['expect_absent']!r}")
        if "expect_empty" in pc and out.strip():
            ok = False
            why.append("output not empty")
        detail = "ok" if ok else f"{'; '.join(why)} — output: {out.strip()[:200]!r}"
        results.append(Result(f"world:{pc['name']}", ok, detail))
    return results


def _teardown(facility: str, scen) -> None:
    """Fully delete the run's endpoint (stop + delete — not just release the block) AND scancel
    the test user's remaining jobs (harness sleepers, finished experiments), unless the scenario
    keeps state for a reuse chain (TEARDOWN='keep' skips both). Runs AFTER postchecks so cleanup
    can't mask agent failures. Best-effort: never fails the run."""
    name = _endpoint_name(facility)
    if getattr(scen, "TEARDOWN", "delete") != "delete":
        print(f"teardown: KEEP — leaving {name} (and any jobs) for the chain", file=sys.stderr, flush=True)
        return
    gce = "$HOME/hpc-bridge/gce-venv/bin/globus-compute-endpoint"
    remote = (
        f"{gce} stop {name} >/dev/null 2>&1; {gce} delete {name} --yes 2>&1; "
        'scancel -u "$(whoami)" 2>/dev/null; true'
    )
    print(f"teardown: deleting {name} + scancel'ing leftover jobs …", file=sys.stderr, flush=True)
    rc, out = _ssh_run(remote, timeout=60)
    tag = "ok" if rc == 0 else f"rc={rc}"
    print(f"teardown: {tag} — {out.strip().replace(chr(10), ' ')[:200]}", file=sys.stderr, flush=True)


def _resolve_scenario(name: str) -> str:
    """Forgive tab-completion and path forms: 'saturation.', 'saturation.py', and
    'agentic/scenarios/saturation.py' all resolve to 'saturation'."""
    n = Path(name.strip()).name
    if n.endswith(".py"):
        n = n[:-3]
    return n.rstrip(".")


async def _run(scenario: str, model: str, effort: str | None, persona: str | None,
               no_skill: bool) -> int:
    sys.path.insert(0, str(SCENARIOS_DIR))
    scenario = _resolve_scenario(scenario)
    try:
        scen = importlib.import_module(scenario)
    except ModuleNotFoundError:
        avail = sorted(p.stem for p in SCENARIOS_DIR.glob("*.py") if not p.stem.startswith("_"))
        print(f"unknown scenario {scenario!r}. Available: {', '.join(avail)}")
        return 2

    runid = os.environ.get("HPCB_RUNID", "local")
    # A scenario may pin a STABLE facility id (reuse chains); else it's per-run unique.
    facility = getattr(scen, "FACILITY_ID", None) or f"globus1-{runid}"
    prompt = scen.PROMPT.format(facility=facility)
    # Interactive mode: persona from the CLI override, else the scenario's default.
    persona = persona or getattr(scen, "PERSONA", None)
    user_goal = getattr(scen, "USER_GOAL", "").format(facility=facility)

    # Resolved-config snapshot for the provenance record (what actually ran, not defaults).
    config = {
        "runid": runid,
        "scenario": scenario,
        "kind": getattr(scen, "KIND", "regression"),
        "facility": facility,
        "endpoint_name": _endpoint_name(facility),
        "prompt": prompt,
        "persona": persona,
        "user_goal": user_goal,
        "model": model,
        "effort": effort,
        "ablate_skill": no_skill,
        "expect_ok": list(getattr(scen, "EXPECT_OK", [])),
        "teardown": getattr(scen, "TEARDOWN", "delete"),
        "setup": list(getattr(scen, "SETUP", [])),
        "postcheck_delay_s": getattr(scen, "POSTCHECK_DELAY_S", 10),
        "git_sha": os.environ.get("HPCB_GIT_SHA", "unknown"),
        "pool_user": os.environ.get("HPC_BRIDGE_SSH_USER", "hpcbridge-test"),
    }
    runs_dir = Path(os.environ.get("HPCB_RUNS_DIR", str(REPO_ROOT / "agentic" / "runs")))

    rc = 1
    res = None
    all_results: list[Result] = []
    try:
        if not _setup(scen):
            print("RESULT: SETUP FAILED — scenario not run (world precondition unmet)")
            rc = 2
            return rc
        res = await run_scenario(prompt, repo_root=REPO_ROOT, model=model, effort=effort,
                                 persona=persona, user_goal=user_goal, ablate_skill=no_skill)

        print(f"\n=== TRACE: {len(res.trace.calls)} tool calls ===")
        for i, c in enumerate(res.trace.calls):
            print(f"  {i:2d}  {c.name}({_short(c.input)})")

        if res.dialogue:
            print(f"\n=== DIALOGUE (persona: {persona}) ===")
            for x in res.dialogue:
                for q in x.questions:
                    print(f"  agent asked: {q.get('question')}")
                for k, v in x.answers.items():
                    print(f"  human chose: {v}   ({k[:60]}…)" if len(k) > 60 else f"  human chose: {v}   ({k})")
                if x.note:
                    print(f"  human note:  {x.note}")

        print("\n=== INVARIANTS ===")
        # Universal trace invariants + the scenario's own bespoke graders (EXTRA_INVARIANTS).
        results = check_all(res.trace)
        results += [fn(res.trace) for fn in getattr(scen, "EXTRA_INVARIANTS", [])]
        critical = set(getattr(scen, "EXPECT_OK", [r.name for r in results]))
        failed = []
        for r in results:
            tag = "PASS" if r.ok else "FAIL"
            gate = " *critical*" if r.name in critical else ""
            print(f"  [{tag}] {r.name}{gate}: {r.detail}")
            if not r.ok and r.name in critical:
                failed.append(r.name)

        # World postchecks — AFTER the agent, BEFORE teardown (cleanup must not mask
        # failures). The settle delay lets async releases land; long_job stretches it past
        # the 600s idle-release window so "survived" is actually proven.
        delay = max(10, int(getattr(scen, "POSTCHECK_DELAY_S", 10)))
        print(f"\n=== WORLD CHECKS (settling {delay}s first) ===")
        await asyncio.sleep(delay)
        world = _postchecks(scen)
        for r in world:
            print(f"  [{'PASS' if r.ok else 'FAIL'}] {r.name} *critical*: {r.detail}")
            if not r.ok:
                failed.append(r.name)  # all world postchecks gate — they are deliberate
        all_results = results + world

        cost = getattr(res.final, "total_cost_usd", None)
        is_error = getattr(res.final, "is_error", None)
        print(f"\nfinal: is_error={is_error}  cost=${cost}  ({len(res.trace.calls)} calls)")
        if failed:
            print(f"RESULT: FAILED — critical checks broke: {failed}")
        else:
            print("RESULT: OK")
            rc = 0
    finally:
        _teardown(facility, scen)
        # Provenance is written LAST and unconditionally — a crashed run still leaves its
        # evidence (partial messages, whatever grading completed, the resolved config).
        rec = write_run_record(
            runs_dir,
            config=config,
            messages=(res.messages if res else []),
            dialogue=(res.dialogue if res else []),
            grading=all_results,
            final=(res.final if res else None),
            rc=rc,
        )
        if rec is not None:
            try:
                shown = rec.relative_to(REPO_ROOT)
            except ValueError:
                shown = rec
            print(f"record: {shown}", flush=True)
    return rc


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one hpc-bridge agentic scenario.")
    ap.add_argument("scenario", nargs="?", default="happy_path")
    ap.add_argument("--model", default="claude-opus-4-8")
    ap.add_argument("--effort", default=None,
                    help="reasoning level: low|medium|high|xhigh|max (default: the model's default)")
    ap.add_argument("--persona", default=None,
                    help="interactive mode: simulated-human persona (cooperative|budget_hawk|declines_spend); "
                         "overrides the scenario's PERSONA")
    ap.add_argument("--no-skill", action="store_true",
                    help="ablation: withhold SKILL.md from the system prompt (measure the guidance's value)")
    args = ap.parse_args()
    sys.exit(asyncio.run(_run(args.scenario, args.model, args.effort, args.persona, args.no_skill)))


if __name__ == "__main__":
    main()
