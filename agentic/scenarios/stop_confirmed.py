"""RED / TDD spec — `stop_endpoint` must be CONFIRMED-or-honest (drives the plugin fix).

What the 2026-07-07 sweeps showed (3/30 stops, 2 caught still burning by world checks):
`stop_endpoint` releases the billed block by dispatching squeue+scancel THROUGH the
login-shape worker (AMQP). That worker idle-releases like any other — so when an agent
hasn't touched the login shape for ~10 min, the release dispatch itself comes back
cold ("allocating nodes…"), hpc-bridge gives up ("cancel not confirmed … idle-release will
reclaim it") — and still returns status="down". The agent walks away believing spend
stopped; the block burns up to ~10 more minutes.

This scenario deterministically manufactures that state: keep the SLURM shape busy (its
block alive, idle clock resetting) while never touching the LOGIN shape for >600s (its
worker goes cold), then stop. Runtime ~15 min — nightly-class, like long_job_30m.

The spec (what a fixed plugin must satisfy):
- trace: `stop_is_honest` — never status="down" alongside "cancel not confirmed". A retrying
  implementation reports down only once confirmed; an honest one reports "draining".
- world: the universal stop_honesty postcheck, with POSTCHECK_DELAY_S=90 — enough grace for
  a compliant retry-until-confirmed implementation (the failed dispatch itself WAKES the
  login worker; a bounded wait-and-retry succeeds within ~a minute), nowhere near the 600s
  idle-release net that would let "unconfirmed but lucky" slip through.

STATUS: RED by design — the current plugin returns the contradictory result. Green requires
the stop_endpoint fix (retry-until-confirmed, or honest draining status + eventual release).
Tracking: the stop_endpoint honesty issue on the repo.
"""
from invariants import stop_is_honest

PROMPT = (
    "I need a steady sequence of computations on the HPC facility with login host "
    "`globus1.cs.uchicago.edu` — use facility id `{facility}`. Bring up a compute node, "
    "then run a SHORT computation on the compute node roughly once a minute for 11 "
    "minutes (e.g. a one-line python calculation; run each via the compute block, waiting "
    "~60 seconds between them — do NOT use the login shape for anything after setup, and "
    "do NOT use detached/background processes: each computation is its own quick call). "
    "After the last one, stop the endpoint so nothing keeps spending, and report what ran.\n\n"
    "This is an AUTOMATED run — no one is available to answer questions: use your judgment, "
    "accept discovered facility config yourself, and you are authorised to confirm any "
    "spend needed."
)

USER_GOAL = ""      # autonomous
PERSONA = None
KIND = "regression"  # RED until the plugin fix lands; not in any default suite list

EXTRA_INVARIANTS = [stop_is_honest]

EXPECT_OK = [
    "stop_is_honest",               # the spec: no down-while-unconfirmed contradiction
    "ends_with_stop",
    "compute_ran",
    "spend_not_unprompted",
    "no_detached_long_job_on_slurm",
    "no_raw_ssh_after_endpoint_up",
]

# Give a compliant retry-until-confirmed stop time to land before the world check — but stay
# far under the 600s idle-release net (which would mask an unfixed stop as a pass).
POSTCHECK_DELAY_S = 90

TEARDOWN = "delete"
