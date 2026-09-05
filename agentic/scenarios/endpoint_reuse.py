"""RED / TDD spec — reconnecting must REUSE the online endpoint, and SAY so (issue #20).

hpc-bridge's SSH-once keystone: `find_online_endpoint` reuses a still-online endpoint (by
identity+name, over the Globus web service — zero SSH). But reuse is INVISIBLE in the tool
results: `connect_facility` returns the same phases whether it bootstrapped over SSH or
reused over the web, so neither an agent (on an MFA facility: "will this re-auth me?") nor
this harness can observe it. The spec: `connect_facility`'s result must carry a
`reused: true` signal (field or notice) when an online endpoint was reused.

In-process version (this scenario): connect twice to the SAME facility id in one session.
Each connect resets state and re-runs bootstrap, so the second one calls
`find_online_endpoint` (Globus web service, by identity+name) and reattaches to the manager
the first connect started — zero SSH — and now returns `reused=True`. The config cache
(`session_facilities`) only skips catalog re-resolution; the endpoint reuse itself is the
find-online path. The cross-restart version (a keep-chain across two containers, where even
the in-process state is gone — the full #20 cache problem) is deferred until suite chain
support; `TEARDOWN="keep"` + a stable `FACILITY_ID` are the waiting hooks.

STATUS: GREEN — the signal is IMPLEMENTED (#20 — `ConnectFacilityResult.reused` + notice) and
confirmed live. The grader (`reuse_signalled`) keys on the `reused` FIELD only and requires a fresh
bring-up → login work → reconnect structure (see its docstring for how #39 shapes the first connect).
Login-shape only (no billed block): cheap (~3 min) and fast.
"""
from invariants import _UP_PHASES, Result, Trace

PROMPT = (
    "Connect me to the HPC facility with login host `globus1.cs.uchicago.edu` — use "
    "facility id `{facility}` — and run `hostname` on the LOGIN shape (do not provision "
    "any billed compute block). Then, to test reconnection, connect to the SAME facility "
    "id a second time and tell me explicitly whether the existing endpoint was REUSED or "
    "a new one was set up. Do not tear anything down.\n\n"
    "This is an AUTOMATED run — no one is available to answer questions: accept discovered "
    "facility config yourself."
)

USER_GOAL = ""      # autonomous
PERSONA = None
KIND = "regression"
SUMMARY = "intra-agent reuse: within ONE session, a second connect_facility reattaches to the endpoint the first stood up"
TAGS = ["reuse", "zero-ssh", "intra-agent"]


# The product's exact intra-session reconnect notice (connect._reuse_note, #78). Pinned here as a CONTRACT string:
# a reword in the product flips this grader, which is the point — the harness must notice.
INTRA_SESSION_RECONNECT = "reconnected to the endpoint this session started"


def reuse_signalled(t: Trace) -> Result:
    """A genuine bring-up first, work on the login shape, then a RECONNECT that reattached instead of
    re-probing or re-bootstrapping. Structure, per the prompt: connect → `hostname` on the login shape →
    connect again.

    The reconnect's evidence, since #78 (2026-09-04) — "a session's own bootstrap is proven, not a reuse":
    - `reused=True` is the FIELD for an endpoint from a PREVIOUS session (a different process found it online);
    - an endpoint THIS session stood up reads `reused=False` with the notice INTRA_SESSION_RECONNECT — the
      intra-session case this scenario tests. Either is a reattach. The first block-tier run after #78 failed
      this grader on the field alone (2026-09-05): the product had changed by design, the grader had not.
    - Structurally, no connect AFTER the first completed login-shape work may be a discovery phase
      (`proposed_facility_details` / `needs_facility_details` / `needs_preauth`): that would be a re-probe or a
      re-bootstrap, i.e. the reconnect did not reattach.
    A `reused=False` connect must exist BEFORE the first up-phase connect (the fresh bring-up)."""
    connects = t.named("connect_facility")
    ups = [(i, c) for i, c in connects if str((c.result or {}).get("phase")) in _UP_PHASES]
    if not ups:
        return Result("reuse_signalled", False, "no connect ever reached the endpoint")
    first_up_i = ups[0][0]
    fresh = any(i < first_up_i and not bool((c.result or {}).get("reused")) for i, c in connects)
    work = [
        i for i, c in t.named("run_shell")
        if str(c.input.get("shape")) == "login" and str((c.result or {}).get("phase")) == "complete"
    ]
    if not work:
        return Result("reuse_signalled", False,
                      "no completed login-shape run_shell — the first connection was never used")
    recon_i, recon = ups[-1]
    after_work = recon_i > min(work)
    res = recon.result or {}
    reattached_field = bool(res.get("reused"))
    reattached_notice = str(res.get("notice") or "").startswith(INTRA_SESSION_RECONNECT)
    reprobed = [i for i, c in connects if i > min(work)
                and str((c.result or {}).get("phase")) in ("proposed_facility_details", "needs_facility_details", "needs_preauth")]
    ok = fresh and after_work and (reattached_field or reattached_notice) and not reprobed
    how = ("reused=True (an earlier session's endpoint)" if reattached_field
           else "the intra-session reconnect notice" if reattached_notice else "NEITHER the reused field NOR the notice")
    return Result(
        "reuse_signalled", ok,
        f"ok: fresh bring-up, work on the login shape, then a reconnect that reattached via {how}" if ok else
        f"fresh_bringup_first={fresh} (want True); reconnect (call {recon_i}) after first work={after_work} (want True); "
        f"reattached via {how} (want the field or the notice); re-probe after work at calls {reprobed} (want none)",
    )

EXTRA_INVARIANTS = [reuse_signalled]

EXPECT_OK = [
    "reuse_signalled",              # the spec: reuse must be observable
    "no_raw_ssh_after_endpoint_up",
    "spend_not_unprompted",
    "ends_with_stop",               # login-only: trivially satisfied (no billed block)
]

TEARDOWN = "delete"
