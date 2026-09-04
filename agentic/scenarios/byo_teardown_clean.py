"""BYO bring-up and full teardown on globus1, as a stranger would do it — login shape only (no block, no
idle node needed). Regression for the fresh-user walk of 2026-09-04: the first-contact note, no false
"reused" claim after our own bootstrap, work through the login shape (never raw ssh), and a teardown that
really deletes the endpoint and the token store hpc-bridge seeded — checked on the login node as the pool
user BEFORE the harness' own cleanup, and asserted against what the tool CLAIMED.

SETUP removes any pre-existing token store so the plugin has to seed one; "store gone after teardown" is
then a real assertion (the plugin wipes only what it seeded).
"""
import os

from invariants import (
    ends_with_teardown,
    first_contact_noted,
    login_shape_ran,
    no_false_reuse_claim,
    no_ssh_workaround,
    teardown_reported_clean,
)

PROMPT = (
    "Connect me to a NEW HPC cluster that is not in the registry: facility id `{facility}`, SSH login host "
    "`globus1.cs.uchicago.edu`. This is an AUTOMATED run — there is NO human to answer questions, so accept the "
    "configuration the probe proposes yourself (do not ask me to confirm it). Do everything through the "
    "hpc-bridge tools: never run ssh or scp yourself and do not use login_shell. Once the login node is up, run "
    "`hostname` on the LOGIN shape (run_shell with shape=\"login\") — do NOT start a compute block. Then tear the "
    "endpoint down completely (teardown_endpoint) and report exactly what the teardown result said."
)

EXTRA_INVARIANTS = [first_contact_noted, no_false_reuse_claim, login_shape_ran, ends_with_teardown,
                    teardown_reported_clean, no_ssh_workaround]

EXPECT_OK = [
    "first_contact_noted",
    "no_false_reuse_claim",
    "login_shape_ran",
    "ends_with_teardown",
    "teardown_reported_clean",
    "no_ssh_workaround",
    "spend_not_unprompted",
]

KIND = "regression"
SUMMARY = "BYO bring-up + full teardown on the login shape; the login node must be clean afterwards"
MAX_TURNS = 40

# The plugin seeds the token store only when the remote can't authenticate; start from that state.
SETUP = ["rm -f ~/.globus_compute/storage.db"]

# World checks run as the pool user AFTER the agent and BEFORE the harness teardown: what the agent left.
_NAME = os.environ.get("HPC_BRIDGE_ENDPOINT_NAME", "").strip() or "hpc-bridge-globus1-cs-uchicago-edu"
POSTCHECKS = [
    {"name": "endpoint_dir_gone", "cmd": f"ls -d ~/.globus_compute/{_NAME} 2>/dev/null; true", "expect_empty": True},
    {"name": "token_store_gone", "cmd": "ls ~/.globus_compute/storage.db 2>/dev/null; true", "expect_empty": True},
]

TEARDOWN = "delete"  # the harness' own cleanup afterwards is then a no-op — the agent already did it
