#!/usr/bin/env python3
"""Staggered, capped suite runner for the agentic harness.

Runs a matrix of (scenario × model × effort × repeat) across the POOL of test users, ≤N in
parallel, STAGGERED so concurrent SSH bootstraps from one host don't trip globus1's per-source
new-connection rate limit. Each job is one `run_smoke.sh` invocation — a fresh container, a
DISTINCT pool user (so squeue/home/storage.db don't bleed), a unique endpoint. Aggregates
pass/fail per **model @ effort**, so you can see how model *and reasoning level* affect the workflow.

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
POOL = [f"hpcbridge-test-{i:02d}" for i in range(10)]
DEFAULT_MODEL = "claude-opus-4-8"


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


def _short_model(m: str) -> str:
    parts = m.split("-")
    return parts[1] if len(parts) > 1 else m


def _cell(model: str, effort: str | None) -> str:
    return f"{model} @ {effort or 'default'}"


async def _run_job(scenario, model, effort, persona, ablate, users, stagger) -> dict:
    user = await users.get()  # a distinct pool user == a concurrency slot
    label = f"{scenario} · {_short_model(model)}/{effort or 'default'}" \
            f"{f'/{persona}' if persona else ''}{' ~' + ablate if ablate else ''} · {user}"
    try:
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
        result = next((ln for ln in text.splitlines() if ln.startswith("RESULT:")),
                      f"(no RESULT line; rc={proc.returncode})")
        print(f"{'✓' if ok else '✗'} done   {label} — {result}", flush=True)
        return {"scenario": scenario, "model": model, "effort": effort, "persona": persona,
                "ablate": ablate, "user": user, "ok": ok, "result": result, "output": text}
    finally:
        users.put_nowait(user)


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

    users: asyncio.Queue = asyncio.Queue()
    for u in POOL[:slots]:
        users.put_nowait(u)
    stagger = Stagger(args.stagger)
    results = await asyncio.gather(*[_run_job(s, m, e, pe, ab, users, stagger) for s, m, e, pe, ab in jobs])

    passed = sum(1 for r in results if r["ok"])
    print(f"\n==== SUITE: {passed}/{len(results)} passed ====")
    cells: dict[str, list[bool]] = {}
    for r in results:
        key = (_cell(r["model"], r["effort"]) + (f" [{r['persona']}]" if r.get("persona") else "")
               + (f" ~{r['ablate']}" if r.get("ablate") else ""))
        cells.setdefault(key, []).append(r["ok"])
    for cell, oks in sorted(cells.items()):
        print(f"  {cell}: {sum(oks)}/{len(oks)} passed")   # the model × reasoning-level comparison
    fails = [r for r in results if not r["ok"]]
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
    args = ap.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
