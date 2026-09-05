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

STATUS: GREEN with #20's `reused` signal live — the spec is that phase 1 reports `reused=False`
(genuine fresh bootstrap) and phase 2 reports `reused=True` (reattached). Graded per phase via
`ToolCall.phase` (see `reuse_across_restart`); phase 1's own first up-connect reading `reused=True` is
the #39 registration-lag retry, tolerated there and reported by `first_details_connect_succeeds`.
Login-shape only, no billed block: ~6 min for the pair.
"""
from invariants import _UP_PHASES, Result, Trace

# Each phase is its own agent session (fresh server). Both phases connect to the SAME ssh_host, so the
# server computes the same endpoint name (hpc-bridge-<ssh_host> since #27) and phase 2 reattaches to
# phase 1's still-online manager. Sharing {facility} keeps them one session facility across the restart.
PHASES = [
    (
        "Connect me to the HPC facility with login host `{ssh_host}` — use facility "
        "id `{facility}` — and run `hostname` on the LOGIN shape to confirm it's up. Do NOT "
        "provision any billed compute block, and do NOT tear anything down.\n\n"
        "This is an AUTOMATED run — no one is available to answer questions: accept discovered "
        "facility config yourself."
    ),
    (
        "Reconnect me to the SAME HPC facility — facility id `{facility}`, login host "
        "`{ssh_host}` — and tell me explicitly whether the existing endpoint was "
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


def _phase_of(c) -> str:
    return str((c.result or {}).get("phase"))


def reuse_across_restart(t: Trace) -> Result:
    """The chain spec, keyed on `ToolCall.phase` (run.py `_combine` stamps each session's calls;
    phase 0 = the bring-up session, phase 1 = the restarted one):

    - phase 1 BUILT the endpoint: some phase-1 connect is `reused=False` (a genuine fresh bring-up)
      and some phase-1 connect reached the endpoint (an `_UP_PHASES` result);
    - phase 2 REATTACHED CLEANLY: its FIRST connect that reaches the endpoint reads `reused=True`
      (the FIELD — a "reuse" substring in the notice is not evidence), with NO non-up connect
      (`failed` / `proposed_facility_details` / `needs_facility_details` …) before it within phase
      2 — a cold server that re-probes or trips #39 before finding the endpoint did not reattach.

    Phase 1 is judged loosely on purpose: its first UP-phase connect routinely ALREADY reads
    `reused=True` — issue #39's registration-lag race fails the first `connect(details=…)` and the
    retry finds the just-registered endpoint online — so "first up-connect was fresh" is a false
    negative (seen live, run 1783608805). `reused=False` on the probe/failed connect is sufficient
    proof the endpoint was built by this chain (per-run unique facility ids + TEARDOWN=delete rule
    out a leftover). Phase 2 gets the strict form: the reattach must be the first thing that works.
    Pre-phase-attribution this grader keyed on the LAST connect, which passed on phase 1's #39 retry
    alone even when phase 2 never connected (coverage audit)."""
    if t.n_phases < 2:
        return Result("reuse_across_restart", False,
                      f"needs a two-phase chain trace; saw {t.n_phases} phase(s)")
    p1 = t.named("connect_facility", phase=0)
    built_fresh = any(not bool((c.result or {}).get("reused")) for _, c in p1)
    p1_up = any(_phase_of(c) in _UP_PHASES for _, c in p1)
    p2 = t.named("connect_facility", phase=1)
    p2_ups = [(i, c) for i, c in p2 if _phase_of(c) in _UP_PHASES]
    if not p2_ups:
        return Result("reuse_across_restart", False,
                      f"phase 2 never reached the endpoint ({len(p2)} connect(s), none up-phase)")
    first_i, first = p2_ups[0]
    reattached = bool((first.result or {}).get("reused"))
    dirty = [(i, _phase_of(c)) for i, c in p2 if i < first_i]   # non-up connects before the reattach
    ok = built_fresh and p1_up and reattached and not dirty
    detail = ("ok: phase 1 built fresh, phase 2's first connect reattached cleanly" if ok else
              f"built_fresh={built_fresh} p1_up={p1_up} (want True); phase-2 first up-connect "
              f"(call {first_i}) reused={reattached} (want True); pre-reattach connects in phase 2: "
              f"{dirty} (want none)")
    return Result("reuse_across_restart", ok, detail)


EXTRA_INVARIANTS = [reuse_across_restart]

EXPECT_OK = [
    "reuse_across_restart",          # the spec: fresh bootstrap, then reattach across the restart
    "no_raw_ssh_after_endpoint_up",  # the reattach rides the web service, not a fresh SSH
    "spend_not_unprompted",
    "ends_with_stop",                # login-only: trivially satisfied (no billed block)
]

TEARDOWN = "delete"  # same-container chain: reclaim the shared endpoint once, after phase 2
