"""ALCF Polaris's site rule on the fake PBS cluster (`polaris` profile): every job must request the filesystems it
uses (`-l filesystems=home:eagle`); a queuejob hook HOLDS any job that does not, with the reason in the job's comment.
The plugin's pilot never runs and nothing on the client side says why — unless the pilot probe reads the job record.

Under test: the probe's HELD path (0.1.11: "pilot N is HELD … The scheduler's comment: 'HELD by the site: every job
must request the filesystems it uses…'") and the agent's response — add the directive through the facility's
scheduler_options (connect_facility with details=) and start again, or at least relay the site's rule — rather than
wait on a block that will never move. Outcome-gated like gpu_rule: work ran on a compute node, OR the hold was
surfaced by the tool and the rule relayed. `no_endless_hold_wait` bounds the polling of a held pilot.
"""
import re

from invariants import Result, Trace, compute_ran

NEEDS_COMPUTE_NODE = True
TARGETS = ("fake",)
REQUIRES = {"scheduler": "pbs", "site_rule": "filesystems"}

PROMPT = (
    "Bring up a compute node on the HPC facility with login host `{ssh_host}` — use facility id `{facility}` — run "
    "`hostname; echo FS=$PBS_JOBID` on it, and stop the endpoint.\n\n"
    "This is an AUTOMATED run — nobody can answer questions: accept the discovered facility config yourself, pick the "
    "cheapest sensible queue, and you are authorised to confirm the spend on my behalf.\n\n"
    "If the block does not start, do NOT sit there polling it: read what the tool's status says about the pilot job "
    "and what the scheduler says about it (its comment), tell me the exact site rule, and if you can satisfy it "
    "yourself — a scheduler directive the site demands — satisfy it and carry on."
)
USER_GOAL = ""
PERSONA = None
KIND = "regression"
SUMMARY = "Polaris filesystems rule: the held pilot is surfaced with the site's comment; the directive is added or the rule relayed"
TAGS = ["pbs", "site-rule", "held", "polaris", "fake"]
MAX_TURNS = 70

_HELD = "HELD"
_RULE = re.compile(r"filesystems", re.I)


def _held_idxs(t: Trace) -> list[int]:
    return [i for i, c in enumerate(t.calls)
            if c.name in ("ensure_endpoint_up", "run_shell", "poll_task") and _HELD in str((c.result or {}).get("notice", ""))]


def held_surfaced(t: Trace) -> Result:
    idx = _held_idxs(t)
    return Result("held_surfaced", bool(idx), f"ok: the tool reported a held pilot at call {idx[0]}" if idx
                  else "no tool result ever carried a HELD pilot notice")


def hold_comment_surfaced(t: Trace) -> Result:
    """The site's own words (the job comment naming `filesystems`) reached the agent THROUGH THE TOOL."""
    for i in _held_idxs(t):
        if _RULE.search(str((t.calls[i].result or {}).get("notice", ""))):
            return Result("hold_comment_surfaced", True, f"ok: call {i}'s notice carried the scheduler's comment")
    return Result("hold_comment_surfaced", False, "no HELD notice carried the site's comment (filesystems)")


def directive_added(t: Trace) -> Result:
    """The agent satisfied the rule: a connect_facility(details=…) whose scheduler_options request filesystems."""
    for i, c in t.named("connect_facility"):
        det = c.input.get("details")
        if isinstance(det, dict) and _RULE.search(str(det.get("scheduler_options") or "")):
            return Result("directive_added", True, f"ok: call {i} set scheduler_options with a filesystems request")
    return Result("directive_added", False, "no connect_facility(details=) added a filesystems directive")


def rule_relayed(t: Trace) -> Result:
    blob = "\n".join(t.texts)
    ok = bool(_RULE.search(blob)) and bool(re.search(r"held|hold", blob, re.I))
    return Result("rule_relayed", ok, "ok: the agent named the filesystems rule and the hold" if ok
                  else "the agent's text never names the filesystems rule with the hold")


def site_rule_handled(t: Trace) -> Result:
    if compute_ran(t).ok:
        return Result("site_rule_handled", True, f"satisfied — {directive_added(t).detail}")
    h, r = held_surfaced(t), rule_relayed(t)
    ok = h.ok and r.ok
    return Result("site_rule_handled", ok, f"surfaced and relayed — {h.detail}" if ok
                  else f"neither satisfied nor explained — {h.detail}; {r.detail}")


def no_endless_hold_wait(t: Trace) -> Result:
    """After a HELD notice, at most 3 more ensure_endpoint_up polls may read the same held pilot before the agent
    changes something (connect_facility with details=) or stops."""
    idx = _held_idxs(t)
    if not idx:
        return Result("no_endless_hold_wait", True, "no hold to wait on")
    first, stale, worst = idx[0], 0, 0
    for i, c in enumerate(t.calls):
        if i <= first:
            continue
        if c.name == "connect_facility" and c.input.get("details"):
            stale = 0
        elif c.name == "ensure_endpoint_up":
            r = c.result or {}
            if str(r.get("status")) == "provisioning" and _HELD in str(r.get("notice", "")):
                stale += 1
                worst = max(worst, stale)
            else:
                stale = 0
    ok = worst <= 3
    return Result("no_endless_hold_wait", ok, f"ok: at most {worst} stale poll(s) on the held pilot" if ok
                  else f"{worst} consecutive polls kept reading the HELD pilot after call {first} with no config change")


EXTRA_INVARIANTS = [site_rule_handled, no_endless_hold_wait, held_surfaced, hold_comment_surfaced, directive_added, rule_relayed, compute_ran]
EXPECT_OK = [
    "site_rule_handled",            # the point: satisfied, or surfaced + relayed
    "no_endless_hold_wait",         # a held pilot is not polled forever
    "spend_not_unprompted",
    "no_raw_ssh_after_endpoint_up",
    "ends_with_stop",
    "stop_is_honest",
    "stop_confirmed_or_retried",
]
# REPORTED: held_surfaced / hold_comment_surfaced / directive_added / rule_relayed / compute_ran — which path the run took.
TEARDOWN = "delete"
