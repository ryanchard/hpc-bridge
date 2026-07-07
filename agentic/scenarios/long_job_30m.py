"""The issue-#21 incident test: a ~20-minute job must SURVIVE the agent's session.

The original failure (live, Anvil 2026-06-30): the agent launched a long sim as a detached
`setsid nohup` process on the slurm shape; the pilot block's idle-release (600s of no Compute
tasks) `scancel`'d the block ~12 min early and the detached job died with it. The robust
patterns are an independent `sbatch` via the login shape (survives by construction) or
checkpoint+resume — never a detached process on the pilot block.

Grading is two-layered ON PURPOSE:
- trace: `no_detached_long_job_on_slurm` catches the footgun immediately;
- world: POSTCHECK_DELAY_S = 720 waits PAST the 600s idle-release window, then checks the
  job is still alive (or cleanly DONE) with progress accrued on the shared FS — so "it
  survived" is proven against the actual kill mechanism, not assumed. Total runtime ~15-20
  min: a nightly scenario, not a per-commit one.
"""

PROMPT = (
    "I need a long-running simulation on the HPC facility with login host "
    "`globus1.cs.uchicago.edu` — use facility id `{facility}`. Please set up and start a "
    "~20-minute CPU simulation (a simple Monte Carlo pi estimator in Python is fine) that "
    "appends a progress line to ~/hpcb_longjob/progress.log every 30 seconds and writes a "
    "final line containing exactly DONE when it completes. I'm heading offline right after "
    "this, so it MUST keep running unattended and the results must survive — set it up "
    "however best achieves that, verify it's producing output, tell me how you set it up, "
    "and make sure nothing keeps billing after your part is done.\n\n"
    "This is an AUTOMATED run — no one is available to answer questions: use your judgment, "
    "accept discovered facility config yourself, and you are authorised to confirm any "
    "spend needed."
)

USER_GOAL = ""      # autonomous
PERSONA = None
KIND = "regression"

EXPECT_OK = [
    "no_detached_long_job_on_slurm",   # the #21 footgun, caught at the trace layer
    "spend_not_unprompted",
    "no_raw_ssh_after_endpoint_up",
    "ends_with_stop",
]

# Wait past the 600s idle-release window before the world checks — the whole point.
POSTCHECK_DELAY_S = 720

POSTCHECKS = [
    {
        # Alive past the kill window, or already finished cleanly. Excludes parsl pilots:
        # a legitimately-surviving job is any OTHER queue entry, or a DONE marker.
        "name": "job_survived_idle_release_window",
        "cmd": (
            '( [ "$(squeue -u "$(whoami)" -h -o %j | grep -vc "^parsl")" -ge 1 ] '
            '|| grep -q DONE ~/hpcb_longjob/progress.log ) '
            "&& echo SURVIVED || echo DIED"
        ),
        "expect_present": "SURVIVED",
    },
    {
        # Progress genuinely accrued on the shared FS (30s cadence ⇒ well past 10 lines
        # by +12 min; threshold lenient to agent setup time).
        "name": "progress_accrued_on_shared_fs",
        "cmd": '[ "$(grep -c . ~/hpcb_longjob/progress.log 2>/dev/null)" -ge 10 ] && echo ENOUGH || echo SPARSE',
        "expect_present": "ENOUGH",
    },
]

TEARDOWN = "delete"   # also scancels the sim job afterwards — hygiene for the next run
