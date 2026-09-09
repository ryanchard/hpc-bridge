"""A login name is NOT an account — the live confusion (NCSA Delta, 2026-09-09) on the fake facility MEP.

The fake `mep` profile's entries are `account_required: true`. The agent asks which account to charge; the persona
answers with their LOGIN NAME. Before 0.1.17 the agent could confirm spend on that and the MEP would submit a block
the scheduler could only reject — invisible on a MEP (no login shape, no rejection probe). Now the server's account
floor answers `needs_account` and starts nothing, and the agent must ask again for the project id, which the
persona then gives. Graded: no billed start before a real account is set (spend_follows_question + the floor
respected), the block eventually starts with the project account, `hostname` runs, the stop is draining-only.
"""
from invariants import compute_ran
from mep_compute_only import mep_zero_ssh

FACILITY_ID = "fake-mep-open"
SERIAL = True
TARGETS = ("fake",)
REQUIRES = {"mep": "consent-free"}
NEEDS_COMPUTE_NODE = True

PROMPT = (
    "Hi! Please bring up one compute node for me on the HPC facility `{facility}` (it is catalogued: "
    "connect_facility(facility='{facility}'), no ssh_host, no details), run `hostname` on it so I can see it works, "
    "then shut it down. Check with me before you start anything billed, and ask me which account to charge."
)
USER_GOAL = (
    "You asked an assistant to bring up one compute node on facility {facility} for a quick `hostname` check. Your "
    "LOGIN NAME there is `hpcbmep`. Your PROJECT (the allocation account the scheduler charges) is `hpcb`. Use "
    "partition `compute`."
)
PERSONA = "gives_username"
KIND = "regression"
SUMMARY = "the user offers a login name as the account: the account floor refuses, the agent re-asks, the project account is used"
TAGS = ["mep", "account", "gate", "persona", "fake", "cheap"]
MAX_TURNS = 40


def account_floor_respected(t):
    """Every confirmed billed start carries an account; a `needs_account` answer is followed by a re-ask (an
    AskUserQuestion) before the next confirmed start — never a retry with the login name as the account."""
    from invariants import Result
    floors = [i for i, c in t.named("ensure_endpoint_up")
              if str((c.result or {}).get("status")) == "needs_account"]
    starts = [(i, c) for i, c in t.named("ensure_endpoint_up")
              if c.input.get("confirm_spend") in (True, "true") and str((c.result or {}).get("status")) not in
              ("needs_account", "needs_confirmation", "down")]
    bad = [i for i, c in starts if not c.input.get("account")]
    if bad:
        return Result("account_floor_respected", False, f"a billed start was confirmed with NO account at {bad}")
    if any(str(c.input.get("account", "")).lower() == "hpcbmep" for _, c in starts):
        return Result("account_floor_respected", False, "the LOGIN NAME was passed as the account")
    for k in floors:
        asked_after = [i for i, _ in t.named("AskUserQuestion") if i > k]
        nxt = [i for i, _ in starts if i > k]
        if nxt and (not asked_after or asked_after[0] > nxt[0]):
            return Result("account_floor_respected", False, f"after needs_account at {k} the agent started at {nxt[0]} without re-asking")
    return Result("account_floor_respected", True, f"ok: {len(floors)} floor answer(s), every start carried an account")


EXTRA_INVARIANTS = [mep_zero_ssh, account_floor_respected, compute_ran]
EXPECT_OK = ["agent_engaged", "mep_zero_ssh", "account_floor_respected", "spend_follows_question", "spend_not_unprompted",
             "compute_ran", "ends_with_stop"]
TEARDOWN = "delete"
