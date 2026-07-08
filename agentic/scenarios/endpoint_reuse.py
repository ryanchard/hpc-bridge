"""RED / TDD spec — reconnecting must REUSE the online endpoint, and SAY so (issue #20).

hpc-bridge's SSH-once keystone: `find_online_endpoint` reuses a still-online endpoint (by
identity+name, over the Globus web service — zero SSH). But reuse is INVISIBLE in the tool
results: `connect_facility` returns the same phases whether it bootstrapped over SSH or
reused over the web, so neither an agent (on an MFA facility: "will this re-auth me?") nor
this harness can observe it. The spec: `connect_facility`'s result must carry a
`reused: true` signal (field or notice) when an online endpoint was reused.

In-process version (this scenario): connect twice to the SAME facility id in one session —
the second connect hits the session-facility cache and reuses the online endpoint
server-side; the result must SAY so. The cross-restart version (a keep-chain across two
containers, where even the session-facility cache is gone — the full #20 cache problem) is
deferred until suite chain support; `TEARDOWN="keep"` + a stable `FACILITY_ID` are the
waiting hooks.

STATUS: RED by design — the `reused` signal does not exist in ConnectFacilityResult yet.
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
KIND = "regression"  # RED until connect_facility surfaces `reused`


def reuse_signalled(t: Trace) -> Result:
    """The SECOND successful connect must carry an explicit reuse signal. Checks the result
    for a truthy `reused` field, or a notice mentioning reuse — absent today (the RED)."""
    ups = [
        (i, c) for i, c in t.named("connect_facility")
        if str((c.result or {}).get("phase")) in _UP_PHASES
    ]
    if len(ups) < 2:
        return Result("reuse_signalled", False,
                      f"needs two successful connects; saw {len(ups)}")
    _, second = ups[-1]
    r = second.result or {}
    signalled = bool(r.get("reused")) or "reus" in str(r.get("notice", "")).lower()
    return Result(
        "reuse_signalled",
        signalled,
        "ok" if signalled else "second connect carried no reuse signal (field/notice absent)",
    )


EXTRA_INVARIANTS = [reuse_signalled]

EXPECT_OK = [
    "reuse_signalled",              # the spec: reuse must be observable
    "no_raw_ssh_after_endpoint_up",
    "spend_not_unprompted",
    "ends_with_stop",               # login-only: trivially satisfied (no billed block)
]

TEARDOWN = "delete"
