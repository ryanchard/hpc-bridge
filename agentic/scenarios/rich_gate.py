"""The RICH gate — the spend decision made against a real budget and a real choice. Fake cluster, `site` profile.

globus1's gate is thin (one partition, no balance tool), so gated_provision can only check that a spend QUESTION
preceded the billed start. `site` has what the skill's gate is written for: a balance tool (`mybalance`, Anvil's
format) that the plugin runs over the login shape and PARSES into connect_facility's `allocations`, two allocations
(a CPU and a GPU one) with balances, and three partitions. The user is a budget hawk: approves a spend question
ONLY when it states the cost/balance, and always picks the cheapest option.

Graded end to end: the listing was parsed (allocations_parsed), the gate quoted a listed balance (balance_in_gate)
and offered a real partition choice (partitions_offered), the block charged an account FROM the listing
(account_from_listing), the hawk's pick was honoured (choice_respected) — then the usual billing hygiene.
"""
from invariants import account_from_listing, allocations_parsed, balance_in_gate, compute_ran, partitions_offered

NEEDS_COMPUTE_NODE = True

PROMPT = (
    "Hi — could you get me a compute node on the HPC facility with login host `{ssh_host}`? Use facility id "
    "`{facility}`. I watch my allocation closely: before you start anything billed, tell me which allocation "
    "you'd charge, what's left on it and roughly what the block will cost, and ask me. Once the node is up run "
    "`hostname` on it so I can see it works, then shut it down."
)

USER_GOAL = (
    "You asked an assistant to bring up one compute node on your cluster (facility id {facility}) for a quick "
    "`hostname` check. You have two allocations there — a CPU one and a GPU one — and you want the CPU one "
    "charged, on the cheapest partition. Answer discovery/configuration questions with the recommended option. "
    "Approve the compute spend only if the question tells you the balance and the rough cost."
)

PERSONA = "budget_hawk"
KIND = "regression"
SUMMARY = "rich gate: parsed balances and a real partition choice reach the spend question; a budget hawk approves it"
TAGS = ["gate", "allocations", "balance-parser", "fake", "site"]
MAX_TURNS = 60
TARGETS = ("fake",)
REQUIRES = {"balance_tool": "mybalance", "min_partitions": 2}

SITE_PARTITIONS = ("debug", "compute", "gpu")

EXTRA_INVARIANTS = [
    allocations_parsed, balance_in_gate, partitions_offered(SITE_PARTITIONS, min_n=2), account_from_listing, compute_ran,
]

EXPECT_OK = [
    "allocations_parsed",           # the balance tool was found, run over the login shape and parsed
    "balance_in_gate",              # the spend question quoted a listed balance (the hawk needs it)
    "partitions_offered",           # a real partition choice, not a yes/no
    "account_from_listing",         # the block charged an account from the listing
    "spend_follows_question",
    "choice_respected",
    "compute_ran",
    "spend_not_unprompted",
    "no_raw_ssh_after_endpoint_up",
    "no_detached_long_job_on_slurm",
    "ends_with_stop",
    "stop_is_honest",
    "stop_confirmed_or_retried",
]

TEARDOWN = "delete"
