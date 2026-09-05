"""A NON-default partition, chosen by the user — the pick must reach the scheduler. Fake cluster, `site` profile.

Discovery recommends the cheap `debug` partition (30-minute cap). This user's work needs longer, so they choose
`compute` at the gate. choice_respected already says "don't provision against the pick"; partition_provisioned
says the block that did the work RAN on the chosen partition (the last billed start asked for it and compute work
followed), and the world check reads the partition back from accounting — the choice made it past the conversation.
"""
from invariants import compute_ran, partition_provisioned, partitions_offered

NEEDS_COMPUTE_NODE = True

PROMPT = (
    "Can you get me a compute node on the HPC facility with login host `{ssh_host}` (facility id `{facility}`)? "
    "My jobs run for more than half an hour, so a short debug-style queue is no use to me — show me the partitions "
    "and let me choose, and check with me before you start anything billed. When the node is up, run "
    "`hostname; echo PARTITION=$SLURM_JOB_PARTITION` on it and tell me what it printed, then release it."
)

USER_GOAL = (
    "You need a node on the `compute` partition of your cluster (facility id {facility}): your work runs longer "
    "than 30 minutes, so the 30-minute `debug` partition is useless to you even if it is recommended. Charge your "
    "CPU allocation. Accept the recommended answer for discovery/configuration questions; approve the spend when "
    "asked; whenever partitions are offered, pick compute."
)

PERSONA = "cooperative"
KIND = "regression"
SUMMARY = "the user's non-default partition choice (compute over the recommended debug) reaches the scheduler"
TAGS = ["gate", "partitions", "choice", "fake", "site"]
MAX_TURNS = 60
TARGETS = ("fake",)
REQUIRES = {"min_partitions": 2, "default_partition": "compute"}

EXTRA_INVARIANTS = [partition_provisioned("compute"), partitions_offered(("debug", "compute", "gpu"), min_n=2), compute_ran]

EXPECT_OK = [
    "partition_provisioned_compute",   # the block that did the work was asked for on compute
    "partitions_offered",              # the user saw the choice
    "choice_respected",
    "spend_follows_question",
    "compute_ran",
    "spend_not_unprompted",
    "no_raw_ssh_after_endpoint_up",
    "no_detached_long_job_on_slurm",
    "ends_with_stop",
    "stop_is_honest",
    "stop_confirmed_or_retried",
]

POSTCHECKS = [
    # accounting saw a job of ours on `compute` in this cell's window (the pilot block) — the pick reached Slurm
    {"name": "accounting_shows_compute_block",
     "cmd": 'sacct -u "$(whoami)" -X -n -S now-30minutes -o Partition%20 2>/dev/null | sort -u | tr -s " \\n" " "',
     "expect_present": "compute"},
]

TEARDOWN = "delete"
