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
from invariants import Result, Trace, _UP_PHASES

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


def reuse_signalled(t: Trace) -> Result:
    """The reconnect must carry the explicit `reused=True` FIELD (a "reuse" substring in the notice
    is not evidence — dropped by the coverage audit), and the trace must show a genuine bring-up
    first. Structure, per the prompt: connect → `hostname` on the login shape → connect again. So:

    - a `reused=False` connect exists BEFORE the first connect that reaches the endpoint (the
      fresh bring-up: the discovery probe / the first `connect(details=…)`);
    - the RECONNECT = the last `_UP_PHASES` connect, which must come AFTER the first completed
      login-shape `run_shell` (the first connection was used before reconnecting) and read
      `reused=True`.

    Why not "first up-connect reused=False, second reused=True" (the ideal spec): issue #39's
    registration-lag race fails the first `connect(details=…)` in practically every run, and the
    agent's retry — the first connect that reaches the endpoint — ALREADY reads `reused=True`
    (find_online locates the just-registered endpoint). The `reused=False` evidence therefore lives
    on the probe/failed connect, not on an up-phase one; `first_details_connect_succeeds` (reported
    on every run) tracks the #39 rate. Tighten to the ideal spec once #39 is fixed."""
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
    reused = bool((recon.result or {}).get("reused"))
    ok = fresh and after_work and reused
    return Result(
        "reuse_signalled", ok,
        "ok: fresh bring-up, work on the login shape, then a reconnect flagged reused=True" if ok else
        f"fresh_bringup_first={fresh} (want True); reconnect (call {recon_i}) after first work="
        f"{after_work} (want True), reused={reused} (want True — the FIELD, no notice substring)",
    )


EXTRA_INVARIANTS = [reuse_signalled]

EXPECT_OK = [
    "reuse_signalled",              # the spec: reuse must be observable
    "no_raw_ssh_after_endpoint_up",
    "spend_not_unprompted",
    "ends_with_stop",               # login-only: trivially satisfied (no billed block)
]

TEARDOWN = "delete"
