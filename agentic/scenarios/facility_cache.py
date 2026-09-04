"""Local-discovery cache across a restart (the `facilities.json` half of #20 / the BYO-reconnect story).

`endpoint_reuse_chain` proves phase 2 REATTACHES to phase 1's endpoint (over the web). This proves
the stronger, cheaper thing: phase 2's fresh server resolves the facility's CONFIG from the local
cache (`~/.hpc-bridge/facilities.json`) **without re-probing over SSH** — so a known BYO facility
reconnects with no discovery round-trip at all, then reuses the online endpoint.

Mechanism (a harness CHAIN — run.py `_run_chain`): two PHASES, each a SEPARATE agent session (a
fresh MCP server), sharing this run's container (so `facilities.json` persists between them). Phase 1
DISCOVERS the un-catalogued facility (a `proposed_facility_details` probe) → confirms → brings up the
login endpoint → the confirmed config is cached. Phase 2's cold server must reconnect from that cache
— no probe.

The tell that the CACHE (not a re-probe) served phase 2: once the endpoint is up (first
`reused=true`), there is NO further `proposed_facility_details`. Without the cache, phase 2 would
re-probe (a second `proposed_facility_details`).

STATUS: GREEN with the local-discovery cache live (d61c068). Login-shape only, no billed block:
~6 min for the pair.
"""
from invariants import _UP_PHASES, Result, Trace

# Each phase is its own agent session. {facility} is this run's unique id; the cache is keyed by the
# SSH host, so phase 2 must reconnect with the same login host for a hit — the prompt supplies it.
PHASES = [
    (
        "Connect me to the HPC facility with login host `globus1.cs.uchicago.edu` — use facility id "
        "`{facility}` — and run `hostname` on the LOGIN shape to confirm it's up. Do NOT provision "
        "any billed compute block, and do NOT tear anything down.\n\n"
        "This is an AUTOMATED run — no one is available to answer questions: accept discovered "
        "facility config yourself."
    ),
    (
        "Reconnect me to the SAME HPC facility — facility id `{facility}`, login host "
        "`globus1.cs.uchicago.edu` — and tell me explicitly whether it REUSED an existing endpoint "
        "or set one up fresh. Do NOT provision a billed block and do NOT tear anything down.\n\n"
        "This is an AUTOMATED run — no one is available to answer questions: accept discovered "
        "facility config yourself."
    ),
]

USER_GOAL = ""       # autonomous
PERSONA = None
KIND = "regression"
SUMMARY = "local-discovery cache: a fresh session reconnects to a known BYO facility from facilities.json — no SSH re-probe"
TAGS = ["reuse", "cache", "local-discovery", "inter-agent", "chain"]
INTERPHASE_DELAY_S = 30  # let phase 1's endpoint register 'online' before phase 2 reconnects


# connect_facility phases that mean the cache did NOT serve the config: the server either probed the
# login node again (proposed) or asked for details outright (needs_facility_details).
_DISCOVERY_PHASES = {"proposed_facility_details", "needs_facility_details"}


def cache_served_reconnect(t: Trace) -> Result:
    """The cache spec, keyed on `ToolCall.phase` (phase 0 = the discovering session, phase 1 = the
    restarted one): phase 1 DISCOVERS the BYO facility (a `proposed_facility_details` probe in phase
    0), and phase 2's fresh server RECONNECTS from the local cache — its FIRST connect that reaches
    the endpoint reads `reused=True` (the field) and NO phase-2 connect is a discovery phase. A
    probe / details request in phase 2 means a cache MISS (it re-discovered instead of reading
    `facilities.json`). Pre-phase-attribution this keyed on "no probe after the first reused=True"
    over the flat trace, which phase 1's own #39 retry could satisfy without phase 2 connecting."""
    if t.n_phases < 2:
        return Result("cache_served_reconnect", False,
                      f"needs a two-phase chain trace; saw {t.n_phases} phase(s)")
    p1 = t.named("connect_facility", phase=0)
    discovered = any(str((c.result or {}).get("phase")) == "proposed_facility_details" for _, c in p1)
    p2 = t.named("connect_facility", phase=1)
    p2_ups = [(i, c) for i, c in p2 if str((c.result or {}).get("phase")) in _UP_PHASES]
    if not discovered or not p2_ups:
        return Result("cache_served_reconnect", False,
                      f"needs a phase-1 discovery then a phase-2 reconnect "
                      f"(discovered={discovered}, phase-2 up-connects={len(p2_ups)})")
    reattached = bool((p2_ups[0][1].result or {}).get("reused"))
    reprobed = [i for i, c in p2 if str((c.result or {}).get("phase")) in _DISCOVERY_PHASES]
    ok = reattached and not reprobed
    detail = ("ok: phase-2 reconnect served from the local cache (no re-probe), endpoint reused" if ok else
              f"phase-2 first up-connect reused={reattached} (want True), "
              f"reprobed_in_phase2={bool(reprobed)} (want False; calls {reprobed})")
    return Result("cache_served_reconnect", ok, detail)


EXTRA_INVARIANTS = [cache_served_reconnect]

EXPECT_OK = [
    "cache_served_reconnect",         # the spec: discover once, reconnect from cache (no re-probe)
    "no_raw_ssh_after_endpoint_up",   # the reconnect rides the cache + web, not a fresh SSH
    "spend_not_unprompted",
    "ends_with_stop",                 # login-only: trivially satisfied (no billed block)
]

TEARDOWN = "delete"  # same-container chain: reclaim the shared endpoint once, after phase 2
