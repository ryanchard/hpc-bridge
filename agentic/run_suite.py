#!/usr/bin/env python3
"""Staggered, capped suite runner for the agentic harness.

Runs a matrix of (scenario × model × effort × persona × ablation × repeat) across the POOL of
test users, ≤N in parallel, STAGGERED so concurrent SSH bootstraps from one host don't trip
globus1's per-source new-connection rate limit. Each job is one `run_smoke.sh` invocation — a
fresh container, a DISTINCT pool user (so squeue/home/storage.db don't bleed), a unique
endpoint. Aggregates pass rates per cell (`model @ effort [persona] ~ablation`).

Run from the repo root (agentic/.env supplies the token + Globus db):

    python agentic/run_suite.py --scenarios happy_path --repeat 4
    python agentic/run_suite.py --scenarios happy_path \
        --models claude-opus-4-8,claude-sonnet-4-6 --efforts low,high,max --repeat 3
    python agentic/run_suite.py --scenarios happy_path --repeat 6 --concurrency 6   # induce contention
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SMOKE = REPO / "agentic" / "run_smoke.sh"
sys.path.insert(0, str(REPO / "agentic" / "harness"))
from pool import PoolClaims  # noqa: E402  (cross-process pool-user claims — see pool.py)

POOL = [f"hpcbridge-test-{i:02d}" for i in range(10)]
DEFAULT_MODEL = "claude-opus-5"


class Stagger:
    """Ensure launches are >= `seconds` apart, so concurrent SSH bootstraps don't burst past
    globus1's per-source new-connection rate limit."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.seconds
        if delay:
            await asyncio.sleep(delay)


async def _build_once() -> bool:
    print("building the jail image once (parallel jobs reuse it)…", flush=True)
    proc = await asyncio.create_subprocess_exec(
        "docker", "build", "--provenance=false", "-t", "hpc-bridge-agentic",
        "-f", str(REPO / "agentic" / "Dockerfile"), str(REPO),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        print(f"build FAILED:\n{err.decode(errors='replace')[-1500:]}", file=sys.stderr)
        return False
    return True


def _knobs(scenario: str) -> dict[str, str]:
    """The scenario module's host-side knobs (via harness/scenario_knobs.py, importable on the host)."""
    import subprocess
    try:
        out = subprocess.run([sys.executable, str(REPO / "agentic" / "harness" / "scenario_knobs.py"), scenario],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:  # noqa: BLE001 - unknown scenario / import error: run.py reports it; no knobs
        return {}
    return dict(ln.split("=", 1) for ln in out.splitlines() if "=" in ln)


def _is_serial(scenario: str) -> bool:
    k = _knobs(scenario)
    return k.get("HPCB_KNOB_SERIAL") == "1" or int(k.get("HPCB_KNOB_COOLDOWN_S", "0") or 0) > 0


def _cooldown_s(scenario: str) -> int:
    """Seconds run_suite waits after each cell of `scenario` before the next one (holding its serial
    lock): a scenario whose cells are deliberate failed SSH auths must stay under the cluster's
    fail2ban maxretry/findtime — six back-to-back cells got the harness' egress banned (2026-09-03)."""
    return int(_knobs(scenario).get("HPCB_KNOB_COOLDOWN_S", "0") or 0)


def _needs_node(scenario: str) -> bool:
    """NEEDS_COMPUTE_NODE scenarios bring up a real block: launched only when the partition has an idle node."""
    return _knobs(scenario).get("HPCB_KNOB_NEEDS_NODE") == "1"


def _idle_nodes() -> int | None:
    """Idle nodes on the cluster partition, probed over the operator's own ssh alias (HPCB_NODE_PROBE_SSH,
    default `globus1`; partition HPCB_NODE_PARTITION, default `main`). None = the probe itself failed."""
    import subprocess
    host = os.environ.get("HPCB_NODE_PROBE_SSH", "globus1")
    part = os.environ.get("HPCB_NODE_PARTITION", "main")
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", host,
                            f"sinfo -h -p {part} -t idle -o %D"], capture_output=True, text=True, timeout=60)
    except Exception:  # noqa: BLE001 - a transport failure just means "unknown"
        return None
    if r.returncode != 0:
        return None
    out = r.stdout.strip()
    return int(out) if out.isdigit() else 0


async def _await_idle_node(label: str, halt: asyncio.Event, max_wait_s: int) -> int | None:
    """Wait until the partition has an idle node. Returns the idle count (>=1), -1 when the probe is
    unavailable (launch unguarded rather than never), or None when `max_wait_s` passed with none idle.
    Why: a cell whose block sits PENDING (Resources) for its whole run grades as `compute_ran` FAILED —
    an environment fact dressed as agent behaviour. Waiting turns that into data worth having."""
    t0 = time.monotonic()
    last_note = -600.0
    while not halt.is_set():
        idle = await asyncio.to_thread(_idle_nodes)
        if idle is None:
            print(f"⚠ nodes  {label}: node probe unavailable (ssh/sinfo) — launching unguarded", flush=True)
            return -1
        if idle >= 1:
            return idle
        elapsed = time.monotonic() - t0
        if elapsed >= max_wait_s:
            return None
        if elapsed - last_note >= 600:
            print(f"⏳ nodes  {label}: 0 idle nodes on the partition — waiting ({int(elapsed)}s of {max_wait_s}s)",
                  flush=True)
            last_note = elapsed
        await asyncio.sleep(60)
    return None


def _short_model(m: str) -> str:
    parts = m.split("-")
    return parts[1] if len(parts) > 1 else m


def _cell(model: str, effort: str | None) -> str:
    return f"{model} @ {effort or 'default'}"


async def _run_job(scenario, model, effort, persona, ablate, claims, sem, stagger, halt) -> dict:
    # A concurrency slot (this invocation) + an EXCLUSIVE claim on a pool user (across every harness
    # process on this host — two invocations must never share one: the other's teardown would cancel
    # this run's blocks). If another invocation holds the whole pool, wait rather than collide.
    await sem.acquire()
    user = None
    while (user := claims.claim_any(POOL)) is None:
        if halt.is_set():
            sem.release()
            return {"scenario": scenario, "model": model, "effort": effort, "persona": persona,
                    "ablate": ablate, "user": None, "ok": False, "skipped": True,
                    "result": "SKIPPED (rate-limit halt)", "output": ""}
        print(f"⏳ wait   {scenario} — every pool user is claimed ({len(claims.busy(POOL))} held, some by "
              "another invocation); retrying in 20s", flush=True)
        await asyncio.sleep(20)
    label = (
        f"{scenario} · {_short_model(model)}/{effort or 'default'}"
        f"{f'/{persona}' if persona else ''}{' ~' + ablate if ablate else ''} · {user}"
    )
    try:
        if halt.is_set():   # a prior job hit the session/rate limit — don't burn the queue
            print(f"⏭ skip   {label} — rate-limit halt", flush=True)
            return {"scenario": scenario, "model": model, "effort": effort, "persona": persona,
                    "ablate": ablate, "user": user, "ok": False, "skipped": True,
                    "result": "SKIPPED (rate-limit halt)", "output": ""}
        await stagger.wait()
        print(f"▶ start  {label}", flush=True)
        env = dict(os.environ, HPCB_TEST_SSH_USER=user, HPCB_SKIP_BUILD="1", HPCB_MODEL=model)
        if effort:
            env["HPCB_EFFORT"] = effort
        if persona:
            env["HPCB_PERSONA"] = persona
        if ablate == "skill":
            env["HPCB_NO_SKILL"] = "1"
        proc = await asyncio.create_subprocess_exec(
            "bash", str(SMOKE), scenario,
            env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        text = out.decode(errors="replace")
        ok = proc.returncode == 0
        if proc.returncode == 3:  # RATE_LIMITED (run.py) — stop launching new jobs
            halt.set()
            print(f"⛔ halt   {label} hit the session/rate limit — skipping remaining jobs",
                  flush=True)
        result = next((ln for ln in text.splitlines() if ln.startswith("RESULT:")),
                      f"(no RESULT line; rc={proc.returncode})")
        print(f"{'✓' if ok else '✗'} done   {label} — {result}", flush=True)
        return {"scenario": scenario, "model": model, "effort": effort, "persona": persona,
                "ablate": ablate, "user": user, "ok": ok,
                "skipped": False, "rate_limited": proc.returncode == 3,
                "result": result, "output": text}
    finally:
        claims.release(user)
        sem.release()


async def _main(args) -> int:
    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    efforts = [e.strip() for e in args.efforts.split(",") if e.strip()] or [None]
    personas = [p.strip() for p in args.personas.split(",") if p.strip()] or [None]
    ablations = [None if a in ("", "none") else a for a in args.ablations.split(",")] if args.ablations else [None]
    jobs = [(s, m, e, pe, ab) for s in scenarios for m in models for e in efforts
            for pe in personas for ab in ablations for _ in range(args.repeat)]
    slots = min(args.concurrency, len(POOL))
    print(f"suite: {len(jobs)} jobs "
          f"({len(scenarios)} scenario × {len(models)} model × {len(efforts)} effort × "
          f"{len(personas)} persona × {len(ablations)} ablation × {args.repeat}) | "
          f"≤{slots} parallel | {args.stagger}s stagger",
          flush=True)

    if not args.no_build and not await _build_once():
        return 2

    claims = PoolClaims()
    held = claims.busy(POOL)
    if held:
        print(f"pool: {len(held)} user(s) already claimed by another harness process — {held}; "
              "this suite uses the rest", flush=True)
    sem = asyncio.Semaphore(slots)
    stagger = Stagger(args.stagger)
    halt = asyncio.Event()
    # SERIAL scenarios (one Globus identity per cell — mep_no_account, stranger_mep_walk) run one at a
    # time: two cells at once make the web service answer the second with RESOURCE_CONFLICT (first sweep).
    serial_locks = {s: asyncio.Lock() for s in scenarios if _is_serial(s)}
    cooldowns = {s: _cooldown_s(s) for s in serial_locks}
    remaining = {s: sum(1 for j in jobs if j[0] == s) for s in serial_locks}  # cooldown only BETWEEN cells
    if serial_locks:
        print("serial scenarios (one cell at a time): "
              + ", ".join(f"{s}{f' (+{cooldowns[s]}s cooldown)' if cooldowns[s] else ''}" for s in sorted(serial_locks)),
              flush=True)

    # NEEDS_COMPUTE_NODE scenarios launch only when the partition has an idle node; with exactly one idle
    # node the gate stays held for the whole cell so a second cell can't queue behind the same node.
    node_scenarios = {s for s in scenarios if _needs_node(s)} if args.node_wait_s > 0 else set()
    node_lock = asyncio.Lock()
    if node_scenarios:
        print("compute-node scenarios (launch only when a node is idle; wait up to "
              f"{args.node_wait_s}s): " + ", ".join(sorted(node_scenarios)), flush=True)

    async def serial_or_plain(s, m, e, pe, ab):
        lock = serial_locks.get(s)
        if lock is None:
            return await _run_job(s, m, e, pe, ab, claims, sem, stagger, halt)
        async with lock:
            r = await _run_job(s, m, e, pe, ab, claims, sem, stagger, halt)
            remaining[s] -= 1
            if cooldowns.get(s) and remaining[s] > 0 and not halt.is_set():  # the last cell needs no cooldown
                print(f"⏲ cooldown {s}: {cooldowns[s]}s before its next cell (fail2ban findtime)", flush=True)
                await asyncio.sleep(cooldowns[s])
            return r

    async def gated(s, m, e, pe, ab):
        if s not in node_scenarios:
            return await serial_or_plain(s, m, e, pe, ab)
        label = f"{s} · {_short_model(m)}/{e or 'default'}"
        await node_lock.acquire()
        held = True
        try:
            idle = await _await_idle_node(label, halt, args.node_wait_s)
            if idle is None:
                print(f"⏭ skip   {label} — no idle compute node within {args.node_wait_s}s", flush=True)
                return {"scenario": s, "model": m, "effort": e, "persona": pe, "ablate": ab, "user": None,
                        "ok": False, "skipped": True,
                        "result": f"SKIPPED (no idle compute node within {args.node_wait_s}s)", "output": ""}
            print(f"🖥 nodes  {label}: {idle if idle >= 0 else '?'} idle at launch", flush=True)
            if idle != 1:  # unknown or plenty: don't serialise the rest behind this cell
                node_lock.release()
                held = False
            return await serial_or_plain(s, m, e, pe, ab)
        finally:
            if held:
                node_lock.release()

    try:
        results = await asyncio.gather(*[gated(s, m, e, pe, ab) for s, m, e, pe, ab in jobs])
    finally:
        claims.release_all()

    skipped = [r for r in results if r.get("skipped")]
    limited = [r for r in results if r.get("rate_limited")]
    graded = [r for r in results if not r.get("skipped") and not r.get("rate_limited")]
    passed = sum(1 for r in graded if r["ok"])
    extra = (f" · {len(limited)} rate-limited" if limited else "") + (f" · {len(skipped)} skipped" if skipped else "")
    print(f"\n==== SUITE: {passed}/{len(graded)} passed{extra} ====")
    cells: dict[str, list[bool]] = {}
    for r in graded:
        key = (_cell(r["model"], r["effort"]) + (f" [{r['persona']}]" if r.get("persona") else "")
               + (f" ~{r['ablate']}" if r.get("ablate") else ""))
        cells.setdefault(key, []).append(r["ok"])
    for cell, oks in sorted(cells.items()):
        print(f"  {cell}: {sum(oks)}/{len(oks)} passed")   # the model × reasoning-level comparison
    for r in skipped:
        print(f"  ⏭ {r['scenario']} · {_cell(r['model'], r['effort'])} — {r['result']}")
    fails = [r for r in graded if not r["ok"]]
    if fails:
        print("\nfailures:")
        for r in fails:
            pe = (f" [{r['persona']}]" if r.get("persona") else "") + \
                 (f" ~{r['ablate']}" if r.get("ablate") else "")
            print(f"  ✗ {r['scenario']} · {_cell(r['model'], r['effort'])}{pe} · {r['user']} — {r['result']}")
    return 0 if passed == len(results) else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="Run an agentic scenario × model × effort suite, staggered + capped.")
    ap.add_argument("--scenarios", default="happy_path", help="comma-separated scenario module names")
    ap.add_argument("--models", default=DEFAULT_MODEL, help="comma-separated Anthropic model ids")
    ap.add_argument("--efforts", default="",
                    help="comma reasoning levels: low,medium,high,xhigh,max (default: the model's default)")
    ap.add_argument("--personas", default="",
                    help="comma personas for interactive scenarios: cooperative,budget_hawk,declines_spend "
                         "(default: the scenario's own PERSONA, or autonomous)")
    ap.add_argument("--ablations", default="",
                    help="comma ablation cells: none,skill — 'none,skill' runs baseline AND skill-ablated "
                         "(the pass-rate delta = the measured value of SKILL.md)")
    ap.add_argument("--repeat", type=int, default=1, help="runs per (scenario, model, effort, persona, ablation)")
    ap.add_argument("--concurrency", type=int, default=10, help="max parallel runs (capped at pool size 10)")
    # globus1 allows ~15 simultaneous new SSH connections per source (ufw fix, 2026-07-01;
    # verified 10/10 concurrent from our egress). A small stagger stays as a guard: each run
    # also opens a teardown connection, and a shared office NAT / CI runner shares the budget.
    ap.add_argument("--stagger", type=float, default=2.0, help="seconds between launches (rate-limit guard)")
    ap.add_argument("--no-build", action="store_true", help="skip the one-time image build")
    ap.add_argument("--node-wait-s", type=int, default=3600,
                    help="NEEDS_COMPUTE_NODE scenarios: wait up to this long for an idle node before skipping the "
                         "cell (0 = launch regardless). Probe: ssh $HPCB_NODE_PROBE_SSH sinfo -p $HPCB_NODE_PARTITION")
    args = ap.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
