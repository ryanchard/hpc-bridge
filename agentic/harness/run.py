"""Harness entrypoint: run one scenario, grade it, print the verdict, exit non-zero on failure.

Inside the container (creds injected via env + mounted key):

    python run.py <scenario>          # default: happy_path

The scenario's PROMPT may contain ``{facility}`` — we fill it with a per-run unique id
(``globus1-<runid>``) so each run is a distinct SESSION FACILITY. NB: since #27 the server keys the
endpoint NAME on the ssh_host (not the facility id), so concurrent runs isolate by their distinct
pool USER, and teardown deletes this user's ``hpc-bridge-*`` endpoints by enumeration (name-agnostic).
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

from cluster_ops import (
    capture_logs_cmd,
    delete_endpoint_cmd,
    endpoint_uuid_cmd,
    scoped_cancel_cmd,
    uep_dirs_cleanup_cmd,
)
from invariants import Result, Trace, check_all
from provenance import write_run_record
from runner import RunResult, run_scenario
from targets import fill_prompt

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = REPO_ROOT / "agentic" / "scenarios"


def _short(d: dict, n: int = 72) -> str:
    s = json.dumps(d, default=str)
    return s if len(s) <= n else s[: n - 1] + "…"


def _endpoint_name(facility: str) -> str:
    # The record's expected-name field (teardown no longer relies on it — it enumerates; see _teardown).
    # Mirrors the server's resolution: HPC_BRIDGE_ENDPOINT_NAME — the per-run isolation override the
    # harness sets so concurrent runs don't share one registration — wins; else hpc-bridge-<ssh_host>
    # (server._session_endpoint_name). Falls back to the facility id if no ssh host is set.
    override = os.environ.get("HPC_BRIDGE_ENDPOINT_NAME", "").strip()
    if override:
        return override
    ssh_host = os.environ.get("HPC_BRIDGE_SSH_HOST", "").strip()
    key = re.sub(r"[^a-z0-9]+", "-", (ssh_host or facility).lower()).strip("-") or "session"
    return f"hpc-bridge-{key}"


def _combine(results: list[RunResult]) -> RunResult:
    """Merge a chain's per-phase RunResults into one for grading: concatenated calls / messages /
    dialogue, and a `final` that surfaces the FIRST errored phase — so an early-phase failure
    can't hide behind a healthy last phase's result. Each call is stamped with its 0-based
    phase index (`ToolCall.phase`) so chain graders can key on "phase 2's first connect" rather
    than infer the boundary from call order (`trace_adapter.trace_from_bundle` recovers the same
    stamp offline from the per-session `init` messages)."""
    calls = []
    for k, r in enumerate(results):
        for c in r.trace.calls:
            c.phase = k
            calls.append(c)
    texts = [x for r in results for x in getattr(r.trace, "texts", [])]  # the agent's words survive a chain too
    messages = [m for r in results for m in r.messages]
    dialogue = [d for r in results for d in (r.dialogue or [])]
    errored = [r for r in results if r.final is None or getattr(r.final, "is_error", False)]
    final = (errored[0] if errored else results[-1]).final
    return RunResult(trace=Trace(calls, texts), final=final, messages=messages, dialogue=dialogue)


async def _run_chain(phase_prompts, scen, *, model, effort, persona, user_goal, no_skill):
    """A cross-restart reuse CHAIN: each phase is a SEPARATE agent session (a fresh MCP server —
    the "restart"), sharing this run's facility id + pool user with NO teardown between. So a
    later phase's cold server must reattach (find_online_endpoint) to the endpoint an earlier
    phase stood up — the inter-agent reuse the intra-agent scenario can't reach. Between phases we
    settle so the just-started endpoint registers 'online' in the web service (else the next phase
    re-bootstraps over SSH instead of reattaching over the web)."""
    delay = max(0, int(getattr(scen, "INTERPHASE_DELAY_S", 25)))
    results: list[RunResult] = []
    for i, pp in enumerate(phase_prompts):
        print(f"\n=== CHAIN PHASE {i + 1}/{len(phase_prompts)} (fresh MCP server) ===")
        r = await run_scenario(pp, repo_root=REPO_ROOT, model=model, effort=effort,
                               persona=persona, user_goal=user_goal, ablate_skill=no_skill,
                               max_turns=getattr(scen, "MAX_TURNS", 40),
                               extra_env=getattr(scen, "EXTRA_ENV", None) or None,
                               midrun_hooks=(getattr(scen, "MIDRUN_HOOKS", None) if i == 0 else None), hook_runner=_run_hook)
        results.append(r)
        print(f"  phase {i + 1}: {len(r.trace.calls)} calls · is_error={getattr(r.final, 'is_error', None)}")
        if i + 1 < len(phase_prompts):
            if not _interphase_setup(scen):
                print("  chain ABORTED: interphase setup failed (later phases would be graded against the wrong world)")
                break
            if delay:
                print(f"  … settling {delay}s so the endpoint registers online for the next phase")
                await asyncio.sleep(delay)
    total = sum((getattr(r.final, "total_cost_usd", 0) or 0) for r in results)
    print(f"chain total cost ≈ ${total:.4f} across {len(results)} phases")
    return _combine(results)


def _capabilities() -> dict:
    """The target cluster's capabilities (HPCB_TARGET_CAPS, JSON from targets.py via run_smoke.sh)."""
    try:
        return json.loads(os.environ.get("HPCB_TARGET_CAPS") or "{}")
    except json.JSONDecodeError:
        return {}


def _ssh_run(remote: str, *, timeout: int = 60, host: str | None = None) -> tuple[int, str]:
    """Run one command on the cluster as the scenario's test user (creds from env — the same
    scoped identity the agent uses). The harness' world channel: SETUP, POSTCHECKS, teardown.
    `host` overrides the login host — a postcheck can target a SPECIFIC login node of a round-robin pool."""
    user = os.environ.get("HPC_BRIDGE_SSH_USER", "hpcbridge-test")
    host = host or os.environ.get("HPC_BRIDGE_SSH_HOST", "globus1.cs.uchicago.edu")
    key = os.environ.get("HPC_BRIDGE_SSH_KEY", "")
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
           # LogLevel=ERROR so the ssh client's "Warning: Permanently added <host> to known hosts" does not land in
           # the command output — an `expect_empty` postcheck counted that warning as content (site profile, the
           # round-robin login pool whose per-node keys are new; 2026-09-05). Real errors still come through.
           "-o", "StrictHostKeyChecking=accept-new", "-o", "LogLevel=ERROR"]
    if key:
        cmd += ["-i", key, "-o", "IdentitiesOnly=yes"]
    cmd += [f"{user}@{host}", remote]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except Exception as exc:  # noqa: BLE001 - callers decide how a transport error grades
        return 255, f"{type(exc).__name__}: {exc}"


def _seed_facility_cache(scen) -> None:
    """SEED_FACILITY_CACHE = {facility_id: FacilityDetails-shaped dict}: pre-populate the server's
    local BYO cache (~/.hpc-bridge/facilities.json in this container) BEFORE the agent starts — e.g. a
    stale SSH-era config for a facility the registry now serves as a MEP (registry must win)."""
    seed = getattr(scen, "SEED_FACILITY_CACHE", None)
    if not seed:
        return
    import json
    state = Path(os.environ.get("HPC_BRIDGE_STATE_DIR") or (Path.home() / ".hpc-bridge"))
    state.mkdir(parents=True, exist_ok=True)
    p = state / "facilities.json"
    p.write_text(json.dumps(dict(seed), indent=2, sort_keys=True))
    p.chmod(0o600)
    print(f"seeded facility cache: {sorted(seed)} -> {p}", file=sys.stderr, flush=True)


def _interphase_setup(scen) -> bool:
    """World changes BETWEEN chain phases (scenario INTERPHASE_SETUP commands, run as the test user
    through the harness' own channel) — e.g. trust the cluster's host key the way a user does from
    their own terminal, so phase 2 can succeed where phase 1 was refused (unknown_host_key)."""
    for c in getattr(scen, "INTERPHASE_SETUP", []):
        print(f"interphase setup: {c[:100]}", file=sys.stderr, flush=True)
        rc, out = _ssh_run(c, timeout=240)
        if rc != 0:
            print(f"interphase setup FAILED (rc={rc}): {out.strip()[:400]}", file=sys.stderr, flush=True)
            return False
    return True


def _trust_host_key(scen) -> bool:
    """Seed the jail's known_hosts with the cluster's key BEFORE the agent starts — the harness' own channel connects
    once with `accept-new`, exactly what a user's first `ssh host` did on their own machine. Since the host-key
    boundary (#75) the plugin trusts only what the user's ssh already trusts, so a fresh jail (empty known_hosts)
    refuses first contact as UNKNOWN HOST KEY — every SSH scenario without a SETUP step failed that way on the
    first block-tier run after #75 (2026-09-05). `TRUST_HOST_KEY = False` keeps the jail cold: `unknown_host_key`
    tests that refusal. Returns whether the key is trusted (False also when the ssh failed)."""
    if not getattr(scen, "TRUST_HOST_KEY", True):
        print("host key: NOT pre-trusted (scenario tests the unknown-key refusal)", file=sys.stderr, flush=True)
        return False
    rc, out = _ssh_run("true", timeout=45)
    ok = rc == 0
    print(f"host key: {'trusted via the harness channel' if ok else f'pre-trust ssh failed rc={rc}: {out.strip()[:120]}'}",
          file=sys.stderr, flush=True)
    return ok


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
# agent, and saturation sleepers are the harness', not the agent's. Scheduler-specific: Slurm's
# `squeue` vs PBS's `qstat` (a scenario declares `SCHEDULER = "pbs"`; default slurm).
def _universal_postchecks(scheduler: str) -> list[dict]:
    if scheduler == "pbs":  # noqa: SIM108
        # -w (wide) so the pilot job name isn't truncated; `|| true` -> empty output reads as "no leak".
        cmd = 'qstat -u "$(whoami)" -w 2>/dev/null || true'
    else:
        cmd = 'squeue -u "$(whoami)" -h -o %j'
    return [{"name": "stop_honesty_no_pilot_left", "cmd": cmd, "expect_absent": "parsl"}]


def _postchecks(scen) -> list[Result]:
    """World-state assertions, run AFTER the agent but BEFORE teardown — the ordering is the
    grading integrity: harness cleanup (scancel/delete) must never mask what the agent left
    behind. Declarative: run cmd over SSH, then substring expectations on the output."""
    results = []
    universal = _universal_postchecks(getattr(scen, "SCHEDULER", "slurm"))
    login_hosts = list(_capabilities().get("login_hosts") or []) or [os.environ.get("HPC_BRIDGE_SSH_HOST", "")]
    for pc in list(getattr(scen, "POSTCHECKS", [])) + universal:
        # "on": "each_login" runs the check on EVERY login node (a round-robin pool: a manager process left on the
        # node the alias did not pick is exactly the leak a pin bug produces) and joins the outputs
        hosts = login_hosts if pc.get("on") == "each_login" else [None]
        rc, raw, shown = 0, [], []
        for h in hosts:
            rc_h, out_h = _ssh_run(pc["cmd"], timeout=pc.get("timeout", 60), host=h)
            rc = rc or rc_h
            raw.append(out_h)                                       # what the expectations are judged on
            shown.append(f"[{h}] {out_h.strip()}" if h else out_h.strip())  # what the detail shows
        out = "\n".join(raw)
        ok, why = True, []
        if not pc.get("allow_nonzero_rc") and rc != 0:
            # rc=255 is ssh itself failing (host down, sshd refused): the world could not be checked at
            # all — still a FAIL (conservative), but labelled so a sweep can separate an outage from a leak.
            ok, why = False, [f"UNVERIFIABLE — ssh rc={rc}" if rc == 255 else f"rc={rc}"]
        if "expect_present" in pc and pc["expect_present"] not in out:
            ok = False
            why.append(f"missing {pc['expect_present']!r}")
        if "expect_absent" in pc and pc["expect_absent"] in out:
            ok = False
            why.append(f"found {pc['expect_absent']!r}")
        if "expect_empty" in pc and any(o.strip() for o in raw):   # per host: the host labels are not output
            ok = False
            why.append("output not empty")
        detail = "ok" if ok else f"{'; '.join(why)} — output: {' | '.join(shown)[:200]!r}"
        results.append(Result(f"world:{pc['name']}", ok, detail))
    return results


_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _run_endpoint_ids(res) -> list[str]:
    """The endpoint uuid(s) THIS run dispatched to, read off its own tool results (`endpoint_id`)."""
    out: list[str] = []
    for c in (res.trace.calls if res is not None else []):
        eid = str((c.result or {}).get("endpoint_id") or "")
        if _UUID_RE.fullmatch(eid) and eid not in out:
            out.append(eid)
    return out


async def _run_hook(hook: dict) -> dict:
    """Execute one chaos hook on the cluster as the test user (the harness' world channel), mid-run. `{endpoint_name}`
    is this run's endpoint; `{eid}` the latest endpoint uuid the agent's tool results have shown."""
    cmd = str(hook["cmd"]).replace("{endpoint_name}", os.environ.get("HPC_BRIDGE_ENDPOINT_NAME", ""))
    eids = hook.get("endpoint_ids") or []
    cmd = cmd.replace("{eid}", eids[-1] if eids else "")
    rc, out = await asyncio.to_thread(_ssh_run, cmd, timeout=int(hook.get("timeout", 120)))
    print(f"  💥 hook {hook.get('name', hook['after_tool'])}: rc={rc} — {out.strip()[:160]}", file=sys.stderr, flush=True)
    return {"rc": rc, "out": out.strip()[:400], "cmd": cmd[:300]}


def _cleanup(scen) -> None:
    """Scenario-declared CLEANUP commands (the mirror of SETUP), run as the test user AFTER postchecks and
    the run-scoped teardown, whatever the outcome — for the world state a scenario itself created (saturation's
    sleepers), which the run-scoped teardown deliberately never touches. Best-effort."""
    for c in getattr(scen, "CLEANUP", []):
        rc, out = _ssh_run(c, timeout=120)
        print(f"cleanup: {'ok' if rc == 0 else f'rc={rc}'} — {out.strip()[:160]}", file=sys.stderr, flush=True)


def _build_info() -> str:
    """What the jail image was built from (`git describe --always --dirty`, stamped at build time), else unknown."""
    try:
        return (REPO_ROOT / "BUILD_INFO").read_text().strip() or "unknown"
    except OSError:
        return "unknown"


def _sdk_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("claude-agent-sdk")
    except Exception:  # noqa: BLE001 - not installed (hermetic host): fine
        return None


def _teardown(scen, res=None) -> str:
    """Tear down THIS RUN's endpoint and blocks — and nothing else the pool user owns — unless the
    scenario keeps state for a reuse chain (TEARDOWN != 'delete'). Runs AFTER postchecks so cleanup
    can't mask agent failures. Best-effort: never fails the run. Returns the captured endpoint logs
    (manager + UEPs + block stdout/stderr), which the caller files into the provenance bundle.

    Scoped on purpose (2026-09-03). The old form deleted every `hpc-bridge-*` endpoint the user had
    and ran `scancel -u $(whoami)`, assuming concurrent runs use DISTINCT users. Two `run_suite`
    invocations both allocated test-00 (see pool.py), and one run's teardown killed the other's live
    blocks mid-task — the "block-thrashing bug" that wasn't. Now: the endpoint is deleted BY NAME
    (this run's HPC_BRIDGE_ENDPOINT_NAME) and blocks are cancelled by the `uep.<eid>` StdOut marker
    for this run's uuid(s) only (the server's own scope); with no uuid known, nothing is cancelled.
    This run's per-UEP dirs are removed too (after their logs are captured) — `gce delete` leaves them
    behind. Stranded leftovers from a crashed run are swept by hand: agentic/sweep_pool_user.sh."""
    if getattr(scen, "TEARDOWN", "delete") != "delete":
        print("teardown: KEEP — leaving endpoint(s) + jobs for the chain", file=sys.stderr, flush=True)
        return ""
    scheduler = getattr(scen, "SCHEDULER", "slurm")
    name = os.environ.get("HPC_BRIDGE_ENDPOINT_NAME", "").strip()
    eids = _run_endpoint_ids(res)
    if name:  # the registered uuid, from the endpoint's own endpoint.json (works even if the agent never saw it)
        _rc, out = _ssh_run(endpoint_uuid_cmd(name), timeout=30)
        m = _UUID_RE.search(out)
        if m and m.group(0) not in eids:
            eids.append(m.group(0))
    logs = ""
    if name or eids:  # capture BEFORE delete erases the evidence
        _rc, logs = _ssh_run(capture_logs_cmd(name or "no-endpoint-name", eids), timeout=90)
    delete = (
        delete_endpoint_cmd(name) if name
        else 'echo "no HPC_BRIDGE_ENDPOINT_NAME: endpoint left in place (never enumerate-delete; see agentic/sweep_pool_user.sh)"'
    )
    print(f"teardown: endpoint {name or '<unknown>'}; cancelling blocks of uuid(s) {eids or 'none'} …",
          file=sys.stderr, flush=True)
    rc, out = _ssh_run(f"{delete}; {scoped_cancel_cmd(scheduler, eids)}; {uep_dirs_cleanup_cmd(eids)}", timeout=90)
    tag = "ok" if rc == 0 else f"rc={rc}"
    print(f"teardown: {tag} — {out.strip().replace(chr(10), ' ')[:200]}", file=sys.stderr, flush=True)
    return logs


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
    target = os.environ.get("HPCB_TARGET", "globus1")
    ssh_host = os.environ.get("HPC_BRIDGE_SSH_HOST", "globus1.cs.uchicago.edu")
    # A scenario may pin a STABLE facility id (reuse chains); else it's per-run unique (named for the target).
    facility = getattr(scen, "FACILITY_ID", None) or f"{target}-{runid}"
    # PHASES => a cross-restart CHAIN: each phase is a separate agent session (fresh MCP server),
    # sharing this run's facility id so a later phase reattaches to an earlier phase's endpoint. A
    # single PROMPT is just the one-phase case.
    # Literal {facility} substitution, NOT str.format: a prompt may embed a code block with other
    # braces (e.g. a #21 probe's `f'{x}'`), which str.format would choke on with a KeyError.
    fill = lambda s: fill_prompt(s, facility=facility, ssh_host=ssh_host)  # noqa: E731 - two tokens, one place
    phases = [fill(p) for p in (getattr(scen, "PHASES", []) or [])]
    prompt = phases[0] if phases else fill(scen.PROMPT)
    # Interactive mode: persona from the CLI override, else the scenario's default.
    persona = persona or getattr(scen, "PERSONA", None)
    user_goal = fill(getattr(scen, "USER_GOAL", ""))

    # Resolved-config snapshot for the provenance record (what actually ran, not defaults).
    config = {
        "runid": runid,
        "scenario": scenario,
        "kind": getattr(scen, "KIND", "regression"),
        "tags": list(getattr(scen, "TAGS", [])),
        "summary": getattr(scen, "SUMMARY", ""),
        "facility": facility,
        "target": target,
        "ssh_host": ssh_host,
        "endpoint_name": _endpoint_name(facility),
        "prompt": prompt,
        "phases": phases or None,
        "persona": persona,
        "user_goal": user_goal,
        "model": model,
        "effort": effort,
        "ablate_skill": no_skill,
        "expect_ok": list(getattr(scen, "EXPECT_OK", [])),
        "teardown": getattr(scen, "TEARDOWN", "delete"),
        "setup": list(getattr(scen, "SETUP", [])),
        "trust_host_key": bool(getattr(scen, "TRUST_HOST_KEY", True)),
        "extra_env": dict(getattr(scen, "EXTRA_ENV", {}) or {}),
        "seed_facility_cache": sorted(getattr(scen, "SEED_FACILITY_CACHE", {}) or {}),
        "no_globus_db": bool(getattr(scen, "NO_GLOBUS_DB", False)),
        "globus_db_secret": getattr(scen, "GLOBUS_DB_SECRET", None),
        "postcheck_delay_s": getattr(scen, "POSTCHECK_DELAY_S", 10),
        "cleanup": list(getattr(scen, "CLEANUP", [])),
        "midrun_hooks": [{k: v for k, v in h.items() if k != "cmd"} | {"cmd": str(h.get("cmd", ""))[:200]}
                         for h in (getattr(scen, "MIDRUN_HOOKS", None) or [])],
        "targets": list(getattr(scen, "TARGETS", []) or []),
        "requires": dict(getattr(scen, "REQUIRES", {}) or {}),
        "admin_setup": list(getattr(scen, "ADMIN_SETUP", []) or []),      # applied by run_smoke.sh via the admin channel
        "admin_cleanup": list(getattr(scen, "ADMIN_CLEANUP", []) or []),
        "profile": os.environ.get("HPCB_FAKE_PROFILE") or None,
        "capabilities": _capabilities(),
        # Code provenance (review 2026-09-05, 2.3): `build` pins what this image was built from; `git_sha` is the
        # host's HEAD when the cell was LAUNCHED (kept as `host_head` too, so drift between the two is visible).
        "build": _build_info(),
        "image_id": os.environ.get("HPCB_IMAGE_ID", "unknown"),
        "git_sha": os.environ.get("HPCB_GIT_SHA", "unknown"),
        "host_head": os.environ.get("HPCB_GIT_SHA", "unknown"),
        "sdk_version": _sdk_version(),
        "human_sim_model": None,  # filled once the run has a HumanSim (interactive runs)
        "pool_user": os.environ.get("HPC_BRIDGE_SSH_USER", "hpcbridge-test"),
    }
    runs_dir = Path(os.environ.get("HPCB_RUNS_DIR", str(REPO_ROOT / "agentic" / "runs")))

    rc = 1
    res = None
    all_results: list[Result] = []
    gating: set[str] = set()
    failed: list[str] = []
    result_label = "CRASHED"  # replaced below; a bundle written from an exception path says so (INTERRUPTED on a signal)
    try:
        _seed_facility_cache(scen)
        _trust_host_key(scen)
        if not _setup(scen):
            print("RESULT: SETUP FAILED — scenario not run (world precondition unmet)")
            rc = 2
            result_label = "SETUP FAILED"
            return rc
        if phases:
            res = await _run_chain(phases, scen, model=model, effort=effort,
                                   persona=persona, user_goal=user_goal, no_skill=no_skill)
        else:
            res = await run_scenario(prompt, repo_root=REPO_ROOT, model=model, effort=effort,
                                     persona=persona, user_goal=user_goal, ablate_skill=no_skill,
                                     max_turns=getattr(scen, "MAX_TURNS", 40),
                                     extra_env=getattr(scen, "EXTRA_ENV", None) or None,
                                     midrun_hooks=getattr(scen, "MIDRUN_HOOKS", None), hook_runner=_run_hook)

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

        config["human_sim_model"] = getattr(res, "human_sim_model", None)

        print("\n=== INVARIANTS ===")
        # Universal trace invariants + the scenario's own bespoke graders (EXTRA_INVARIANTS).
        results = check_all(res.trace)
        results += [fn(res.trace) for fn in getattr(scen, "EXTRA_INVARIANTS", [])]
        # Harness-side observations as ROWS, so a bundle explains a failure by itself (review 2026-09-05, 2.5/3.3):
        # the completion gate (an errored/cut-off agent run can't pass on vacuous invariants) and the human-sim's
        # prose follow-ups (a run that ended because the agent kept asking in prose is not a clean pass).
        completed = res.final is not None and not getattr(res.final, "is_error", False)
        results.append(Result("run_completed", completed,
                              "ok" if completed else "the agent run errored or never returned a result: "
                              + str(getattr(res.final, "result", "") or "")[:160]))
        if persona:
            n = getattr(res, "prose_followups", 0)
            capped = getattr(res, "followups_capped", False)
            results.append(Result("harness:prose_followups", not capped,
                                  f"{n} prose question(s) answered by the human-sim"
                                  + ("; the run ENDED at the cap — the agent kept asking in prose" if capped else "")))
        hooks = list(getattr(res, "hooks_fired", None) or [])
        if getattr(scen, "MIDRUN_HOOKS", None):
            # a chaos scenario's premise is that its fault was injected: a hook that never fired means the agent
            # never reached the trigger, and every grader below would pass vacuously
            unfired = [h["name"] for h in hooks if h.get("call_index") is None]
            failed_hooks = [h["name"] for h in hooks if h.get("rc") not in (None, 0)]
            results.append(Result("harness:midrun_hooks", not unfired and not failed_hooks,
                                  ", ".join(f"{h['name']}@call{h['call_index']} rc={h.get('rc')}" for h in hooks if h.get("call_index") is not None)
                                  + (f"; NEVER FIRED: {unfired}" if unfired else "")
                                  + (f"; FAILED: {failed_hooks}" if failed_hooks else "")))
        # agent_engaged + run_completed always gate: a do-nothing or truncated run must never grade OK.
        critical = set(getattr(scen, "EXPECT_OK", [r.name for r in results])) | {"agent_engaged", "run_completed"}
        if persona:
            critical.add("harness:prose_followups")
        if getattr(scen, "MIDRUN_HOOKS", None):
            critical.add("harness:midrun_hooks")
        gating = critical
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
            gating.add(r.name)
            if not r.ok:
                failed.append(r.name)  # all world postchecks gate — they are deliberate
        all_results = results + world

        cost = getattr(res.final, "total_cost_usd", None)
        is_error = getattr(res.final, "is_error", None)
        print(f"\nfinal: is_error={is_error}  cost=${cost}  ({len(res.trace.calls)} calls)")
        # Rate-limit deaths are infrastructure, not behaviour: distinct rc so the suite can
        # HALT instead of burning the remaining queue (11 wasted launches in sweep 2).
        rate_limited = (
            getattr(res.final, "api_error_status", None) == 429
            or "session limit" in str(getattr(res.final, "result", "")).lower()
        )
        all_results.append(Result("rate_limited", not rate_limited,
                                  "the run hit the session/rate limit (not graded)" if rate_limited else "ok"))
        if rate_limited:
            rc = 3
            result_label = "RATE_LIMITED"
            print("RESULT: RATE_LIMITED — session/rate limit; run not graded, suite should halt")
        elif failed:
            result_label = "FAILED"
            print(f"RESULT: FAILED — critical checks broke: {failed}")
        else:
            result_label = "OK"
            print("RESULT: OK")
            rc = 0
    except asyncio.CancelledError:
        result_label = "INTERRUPTED"
        rc = 130  # the bundle's rc says "stopped", not "graded FAIL" (live 2026-09-05: it read 1)
        print("RESULT: INTERRUPTED — the run was stopped (signal); tearing down and writing the bundle", flush=True)
        raise
    finally:
        endpoint_logs = _teardown(scen, res)
        _cleanup(scen)
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
            gating=sorted(gating),
            failed=failed,
            result=result_label,
            events=list(getattr(res, "hooks_fired", None) or []) if res else [],
        )
        if rec is not None and endpoint_logs:
            # The evidence a post-mortem needs (manager + UEP logs, block stdout/stderr) — deleted on
            # the cluster by teardown, so this file is the only copy. See cluster_ops.capture_logs_cmd.
            (rec / "endpoint-logs.txt").write_text(endpoint_logs)
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
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--effort", default=None,
                    help="reasoning level: low|medium|high|xhigh|max (default: the model's default)")
    ap.add_argument("--persona", default=None,
                    help="interactive mode: simulated-human persona (cooperative|budget_hawk|declines_spend); "
                         "overrides the scenario's PERSONA")
    ap.add_argument("--no-skill", action="store_true",
                    help="ablation: withhold SKILL.md from the system prompt (measure the guidance's value)")
    args = ap.parse_args()
    sys.exit(asyncio.run(_main(args)))


async def _main(args) -> int:
    """SIGTERM (`docker stop` -> entrypoint.sh) CANCELS the run task through asyncio's own signal handling, so
    `_run`'s `finally` (teardown + bundle) executes on the loop. A plain `signal.signal` handler that raised
    KeyboardInterrupt from inside the loop's select() tore the process down instead — no teardown, no bundle (live
    2026-09-05, twice). Returns rc 130 when interrupted, after the cleanup."""
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    state = {"signalled": False}

    def _cancel(signum: int) -> None:
        state["signalled"] = True
        print(f"\n⛔ signal {signum}: stopping the run — teardown + bundle follow", file=sys.stderr, flush=True)
        if task is not None:
            task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _cancel, sig)
    try:
        return await _run(args.scenario, args.model, args.effort, args.persona, args.no_skill)
    except asyncio.CancelledError:
        if state["signalled"]:
            return 130
        raise


if __name__ == "__main__":
    main()
