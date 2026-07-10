"""LIVE probe of the #21 per-task walltime cap (the cheap half, ~5 min).

A single run_shell task on the compute shape is bounded at ~110s (runner walltime = timeout − 10,
`runner.py:66`), so a longer blocking task is CUT — it never returns `complete`, and the caller gets
no warning. This scenario is DETERMINISTIC on purpose: the prompt dictates the exact command + shape
so we OBSERVE the bound bite — the inverse of `long_job_30m`, which tests the agent AVOIDING it.

Run with **--no-skill** (`HPCB_NO_SKILL=1`): the driving-hpc skill steers away from long single tasks,
so ablation is what lets the agent follow the literal instruction. One short billed block.
See issue #21 (https://github.com/ryanchard/hpc-bridge/issues/21).
"""
from invariants import Result, Trace

_MARK = "HPCB_TASKCAP"

PROMPT = (
    "I'm probing the compute block's PER-TASK time limit on the HPC facility with login host "
    "`globus1.cs.uchicago.edu` — facility id `{facility}`. Bring up a compute node, then run EXACTLY "
    "this as ONE `run_shell` on the **compute** shape — do NOT split it, do NOT use sbatch, do NOT "
    "use the login shape:\n\n"
    "    python3 -c \"import time; print('" + _MARK + " start', flush=True); time.sleep(180); "
    "print('" + _MARK + " DONE')\"\n\n"
    "I EXPECT it may be cut short — that is exactly what I'm testing. Report the exact result you get "
    "back (phase, exit_code, any notice), then stop the endpoint to release the block.\n\n"
    "This is an AUTOMATED run — accept discovered facility config yourself and you are authorised to "
    "confirm the spend."
)

USER_GOAL = ""
PERSONA = None
KIND = "diagnostic"
SUMMARY = "#21 live: a single >110s run_shell on the compute shape is CUT by the per-task walltime cap"
TAGS = ["issue-21", "bounds", "diagnostic", "ablation"]
MAX_TURNS = 55


def per_task_cap_bit(t: Trace) -> Result:
    """#21 spec: the long probe task, run on the compute shape, did NOT complete — the ~110s per-task
    walltime cut it (no DONE marker, or a non-`complete` phase)."""
    probes = [
        (i, c) for i, c in t.named("run_shell")
        if c.input.get("shape") in (None, "compute") and _MARK in str(c.input.get("command", ""))
    ]
    if not probes:
        return Result("per_task_cap_bit", False,
                      "agent never ran the probe on the compute shape (rerouted — needs --no-skill / a firmer prompt)")
    cut = [
        i for i, c in probes
        if str((c.result or {}).get("phase")) != "complete"
        or (_MARK + " DONE") not in str((c.result or {}).get("stdout", ""))
    ]
    ok = bool(cut)
    return Result("per_task_cap_bit", ok,
                  f"ok: the >110s compute task was CUT at the cap (calls {cut}) — #21 observed" if ok else
                  "the probe task COMPLETED — the ~110s cap did NOT bite (did it truly run >110s on compute?)")


EXTRA_INVARIANTS = [per_task_cap_bit]

EXPECT_OK = [
    "per_task_cap_bit",   # the point: the bound bit, observed live
    "ends_with_stop",     # released the block after
]

POSTCHECK_DELAY_S = 10
TEARDOWN = "delete"
