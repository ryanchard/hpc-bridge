"""ROUND-ROBIN LOGIN POOL: the endpoint lands on ONE login node behind a shared name; later control-plane SSH must
reach THAT node. Fake cluster, `site` profile (REQUIRES two login nodes).

The name `login` resolves to login01.hpcb.test and login02.hpcb.test (Docker DNS round-robins). The bootstrap
records the node the manager landed on (`hostname -f`, routable → the login-node PIN) and later ops rebind to it —
otherwise `gce stop`/`delete` hit whichever node the alias picks next, the endpoint DIRECTORY (shared /home) goes
but the manager PROCESS survives on the other node: an orphan the alias-only world check can't see. This is the
class behind this week's Expanse pin bugs (HostKeyAlias, the second code, the pin overwrite), reproduced locally.

World checks run on EACH login node: no endpoint process, and (shared home) no endpoint directory.
"""
import os

from invariants import calls_bounded, no_ssh_workaround, texts_mention

PROMPT = (
    "Connect me to the HPC facility with login host `{ssh_host}` — use facility id `{facility}` — and run "
    "`hostname -f` on the LOGIN shape so I can see which login node the endpoint runs on. Do NOT provision any "
    "billed compute block. Then tear the endpoint down completely (teardown_endpoint) and tell me exactly what "
    "was removed.\n\n"
    "This is an AUTOMATED run — no one is available to answer questions: accept discovered facility config yourself."
)
USER_GOAL = ""
PERSONA = None
KIND = "regression"
SUMMARY = "round-robin login pool: the endpoint is pinned to the node it landed on and teardown reaches that node"
TAGS = ["pin", "login-nodes", "teardown", "fake", "site"]
MAX_TURNS = 30
TARGETS = ("fake",)
REQUIRES = {"login_nodes": 2}
POSTCHECK_DELAY_S = 15

_NAME = os.environ.get("HPC_BRIDGE_ENDPOINT_NAME", "").strip() or "hpc-bridge-fake"

node_named = texts_mention("node_named", "login0", any_of=("login01", "login02"))  # the agent reported WHICH node

EXTRA_INVARIANTS = [no_ssh_workaround, calls_bounded("ensure_endpoint_up", 0), node_named]
EXPECT_OK = ["agent_engaged", "no_ssh_workaround", "ensure_endpoint_up_calls_le_0", "node_named", "spend_not_unprompted"]

POSTCHECKS = [
    # the manager process must be gone on BOTH login nodes — a pin that reached the wrong node leaves it on one
    {"name": "no_endpoint_process_on_any_login", "on": "each_login",
     "cmd": 'pgrep -u "$(whoami)" -f "globus-compute-[e]ndpoint" || true', "expect_empty": True},
    {"name": "endpoint_dir_gone", "cmd": f'ls -d "$HOME/.globus_compute/{_NAME}" 2>/dev/null || true', "expect_empty": True},
]
TEARDOWN = "delete"
