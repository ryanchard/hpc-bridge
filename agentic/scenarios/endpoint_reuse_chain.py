"""INTER-agent reuse across an MCP-server restart (issue #20, the cross-restart half).

The intra-agent scenario ([[endpoint_reuse]]) proves reuse WITHIN one session — the same server
process connects twice. This proves the stronger, real-world case: a **fresh** hpc-bridge server
(a new agent session, a cold process) reattaches to an endpoint a *previous* session stood up —
zero SSH, over the Globus web service. That's the "SSH-once across restarts" keystone.

Mechanism (a harness CHAIN — see run.py `_run_chain`): two PHASES, each a SEPARATE agent session
(hence a fresh MCP server = the "restart"), sharing this run's facility id + pool user with NO
teardown between. Phase 1 stands the endpoint up FRESH; phase 2's cold server must find it online
and reattach. Both phases run on the same container/cluster, so the endpoint persists between them;
the harness tears down once, after phase 2.

STATUS: expected GREEN once #20's `reused` signal is live (it is) — the spec is that phase 1 reports
`reused=False` (genuine fresh bootstrap) and phase 2 reports `reused=True` (reattached). Login-shape
only, no billed block: ~6 min for the pair.
"""
from invariants import Result, Trace, _UP_PHASES

# Each phase is its own agent session (fresh server). {facility} is shared across phases so the
# endpoint name (hpc-bridge-<facility>) is stable and phase 2 can reattach to phase 1's manager.
PHASES = [
    (
        "Connect me to the HPC facility with login host `globus1.cs.uchicago.edu` — use facility "
        "id `{facility}` — and run `hostname` on the LOGIN shape to confirm it's up. Do NOT "
        "provision any billed compute block, and do NOT tear anything down.\n\n"
        "This is an AUTOMATED run — no one is available to answer questions: accept discovered "
        "facility config yourself."
    ),
    (
        "Reconnect me to the SAME HPC facility — facility id `{facility}`, login host "
        "`globus1.cs.uchicago.edu` — and tell me explicitly whether the existing endpoint was "
        "REUSED or a brand-new one was set up. Do NOT provision a billed block and do NOT tear "
        "anything down.\n\n"
        "This is an AUTOMATED run — no one is available to answer questions: accept discovered "
        "facility config yourself."
    ),
]

USER_GOAL = ""       # autonomous
PERSONA = None
KIND = "regression"
SUMMARY = "inter-agent reuse: a fresh server (new session) reattaches to an endpoint a prior session stood up — cross-restart, zero-SSH"
TAGS = ["reuse", "zero-ssh", "inter-agent", "chain"]
INTERPHASE_DELAY_S = 30  # let phase 1's endpoint register 'online' before phase 2 tries to reattach


def reuse_across_restart(t: Trace) -> Result:
    """The chain spec, over the combined two-phase trace (phase 2's calls come LAST): a fresh
    bring-up happened THIS run (some connect is `reused=False`) AND the last successful connect —
    phase 2's cold server — REATTACHED (`reused=True`).

    We check "some connect was fresh" rather than "the FIRST up-phase connect was fresh": a fresh
    bootstrap can surface as a `proposed`/`failed` connect (registration lag) followed by connects
    that already read `reused=True` (find_online located the just-started endpoint). Anchoring on
    the first up-phase connect is therefore a false negative — seen live (run 1783608805: phase-1's
    first up-phase connect was already `reused=True`, yet phase 2 genuinely reattached). Per-run
    unique facility ids + TEARDOWN=delete already rule out a leftover endpoint, so a `reused=False`
    connect anywhere is sufficient proof the endpoint was built by this chain.

    KNOWN GAP: the combined trace has no phase boundary, so a phase-2 that runs clean but never
    connects (leaving phase-1's chatty reuse as the last connect) could slip through; the completion
    gate catches a phase-2 *crash*, and phase-index attribution is the follow-up hardening."""
    connects = t.named("connect_facility")
    ups = [(i, c) for i, c in connects if str((c.result or {}).get("phase")) in _UP_PHASES]
    if len(ups) < 2:
        return Result("reuse_across_restart", False,
                      f"needs a successful connect in each phase; saw {len(ups)}")
    last = ups[-1][1].result or {}
    reattached = bool(last.get("reused")) or "reus" in str(last.get("notice", "")).lower()
    built_fresh = any(not bool((c.result or {}).get("reused")) for _, c in connects)
    ok = reattached and built_fresh
    detail = ("ok: fresh bring-up, then reattached across the restart" if ok else
              f"reattached={reattached} (want True), built_fresh={built_fresh} (want True)")
    return Result("reuse_across_restart", ok, detail)


EXTRA_INVARIANTS = [reuse_across_restart]

EXPECT_OK = [
    "reuse_across_restart",          # the spec: fresh bootstrap, then reattach across the restart
    "no_raw_ssh_after_endpoint_up",  # the reattach rides the web service, not a fresh SSH
    "spend_not_unprompted",
    "ends_with_stop",                # login-only: trivially satisfied (no billed block)
]

TEARDOWN = "delete"  # same-container chain: reclaim the shared endpoint once, after phase 2
