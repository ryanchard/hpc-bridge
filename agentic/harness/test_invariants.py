"""Unit tests for the invariants grading core — pure + hermetic (no SDK, no cluster).

These prove the *graders* are correct against synthetic traces, so a real run's
verdict can be trusted. Run explicitly (not collected by the repo's `pytest -q`,
whose testpaths = ["tests"]):

    uv run pytest agentic/harness/test_invariants.py -q
"""
from invariants import ToolCall, Trace, check_all, logical_name


def _by_name(trace: Trace) -> dict:
    return {r.name: r for r in check_all(trace)}


def test_logical_name_strips_namespace():
    assert logical_name("mcp__endpoint__connect_facility") == "connect_facility"
    assert logical_name("mcp__plugin_hpc-bridge_endpoint__run_shell") == "run_shell"
    assert logical_name("Bash") == "Bash"


def _happy_trace() -> Trace:
    return Trace([
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "globus"},
                    {"phase": "needs_account", "allocations": [{"account": "lab", "balance": 100}]}),
        ToolCall.of("mcp__endpoint__run_shell", {"command": "sinfo", "shape": "login"},
                    {"phase": "complete"}),
        ToolCall.of("mcp__endpoint__ensure_endpoint_up",
                    {"shape": "compute", "account": "lab", "partition": "main", "confirm_spend": True},
                    {"status": "up"}),
        ToolCall.of("mcp__endpoint__run_shell", {"command": "hostname", "shape": "compute"},
                    {"phase": "complete"}),
        ToolCall.of("mcp__endpoint__stop_endpoint", {}, {"status": "stopped"}),
    ])


def test_happy_path_passes_every_autonomous_invariant():
    # spend_follows_question is the interactive-mode gate: an autonomous trace (billed start,
    # no AskUserQuestion) fails it BY DESIGN — scenarios opt in via EXPECT_OK.
    res = _by_name(_happy_trace())
    failed = {k: v.detail for k, v in res.items() if not v.ok and k != "spend_follows_question"}
    assert not failed, failed
    assert res["spend_follows_question"].ok is False  # autonomous: billed start, never asked


# --- inter-agent (cross-restart) reuse chain: endpoint_reuse_chain.reuse_across_restart ---------


def _reuse_across_restart():
    import sys
    from pathlib import Path
    sdir = str(Path(__file__).resolve().parents[1] / "scenarios")
    if sdir not in sys.path:
        sys.path.insert(0, sdir)
    import endpoint_reuse_chain
    return endpoint_reuse_chain.reuse_across_restart


def _chain_trace(*, p1_up_reused: bool, p2_reused: bool, p2_connects: bool = True,
                 p1_fresh: bool = True) -> Trace:
    """Synthesize the COMBINED two-phase trace run.py `_combine` produces, mirroring the real live
    shape: phase-1 does a fresh discovery connect (`proposed_facility_details`, reused=False) then
    an up-phase connect that — via the find-online refind — may ALREADY read reused=True; phase-2
    (appended last) reattaches."""
    def conn(reused, rp):
        return ToolCall.of("mcp__endpoint__connect_facility", {"facility": "g"},
                           {"phase": rp, "reused": reused})
    calls = []
    if p1_fresh:
        calls.append(conn(False, "proposed_facility_details"))  # genuine fresh bring-up (phase 1)
    calls.append(conn(p1_up_reused, "needs_account"))           # phase-1 up-phase connect
    calls.append(ToolCall.of("mcp__endpoint__run_shell", {"command": "hostname", "shape": "login"},
                             {"phase": "complete"}))
    if p2_connects:
        calls.append(conn(p2_reused, "needs_account"))          # phase-2 reattach (last)
    return Trace(calls)


def test_reuse_across_restart_passes_despite_messy_phase1_bootstrap():
    # THE regression for run 1783608805: phase-1's first UP-phase connect already read reused=True
    # (list-lag refind), yet a fresh connect exists and phase 2 reattached -> must PASS.
    assert _reuse_across_restart()(_chain_trace(p1_up_reused=True, p2_reused=True)).ok


def test_reuse_across_restart_passes_clean_fresh_then_reused():
    assert _reuse_across_restart()(_chain_trace(p1_up_reused=False, p2_reused=True)).ok


def test_reuse_across_restart_fails_when_phase2_not_reused():
    r = _reuse_across_restart()(_chain_trace(p1_up_reused=False, p2_reused=False))
    assert not r.ok and "reattached=False" in r.detail


def test_reuse_across_restart_fails_when_phase2_absent():
    r = _reuse_across_restart()(_chain_trace(p1_up_reused=True, p2_reused=True, p2_connects=False))
    assert not r.ok and "saw 1" in r.detail


def test_reuse_across_restart_fails_without_a_fresh_bringup():
    # no reused=False connect anywhere => looks like a leftover endpoint, not a chain bring-up.
    r = _reuse_across_restart()(_chain_trace(p1_fresh=False, p1_up_reused=True, p2_reused=True))
    assert not r.ok and "built_fresh=False" in r.detail


# --- local-discovery cache: facility_cache.cache_served_reconnect ------------------------------


def _cache_served_reconnect():
    import sys
    from pathlib import Path
    sdir = str(Path(__file__).resolve().parents[1] / "scenarios")
    if sdir not in sys.path:
        sys.path.insert(0, sdir)
    import facility_cache
    return facility_cache.cache_served_reconnect


def _cache_trace(*, phase2_reused: bool = True, phase2_reprobe: bool = False,
                 phase1_discovers: bool = True) -> Trace:
    """Combined two-phase trace: phase 1 discovers (proposed -> provisioning -> reused=true), phase 2
    reconnects (reused, and by default WITHOUT a re-probe = served from the cache)."""
    def conn(reused, rp):
        return ToolCall.of("mcp__endpoint__connect_facility", {"facility": "g"}, {"phase": rp, "reused": reused})
    calls = []
    if phase1_discovers:
        calls.append(conn(False, "proposed_facility_details"))  # phase-1 probe (real BYO discovery)
    calls.append(conn(False, "provisioning"))                   # phase-1 bring-up
    calls.append(conn(True, "needs_account"))                   # phase-1 endpoint online (first reuse=true)
    if phase2_reprobe:
        calls.append(conn(False, "proposed_facility_details"))  # phase-2 RE-PROBED = cache MISS
    calls.append(conn(phase2_reused, "needs_account"))          # phase-2 reconnect
    return Trace(calls)


def test_cache_served_reconnect_passes_when_no_reprobe():
    assert _cache_served_reconnect()(_cache_trace()).ok


def test_cache_served_reconnect_fails_on_reprobe():
    r = _cache_served_reconnect()(_cache_trace(phase2_reprobe=True))
    assert not r.ok and "reprobed_after_reuse=True" in r.detail


def test_cache_served_reconnect_fails_if_reconnect_not_reused():
    r = _cache_served_reconnect()(_cache_trace(phase2_reused=False))
    assert not r.ok and "last_reused=False" in r.detail


def test_cache_served_reconnect_needs_a_discovery():
    r = _cache_served_reconnect()(_cache_trace(phase1_discovers=False))
    assert not r.ok and "discovered=False" in r.detail


def _interactive_trace(picked: str = "cheap", provisioned: str = "cheap") -> Trace:
    answered = (
        'Your questions have been answered: "Which partition should I provision on? '
        f'(balance 100 SU)"="{picked}". You can now continue with these answers in mind.'
    )
    return Trace([
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "globus"},
                    {"phase": "needs_account"}),
        ToolCall.of("AskUserQuestion",
                    {"questions": [{"question": "Which partition should I provision on? (balance 100 SU)",
                                    "options": [{"label": "cheap"}, {"label": "fast"}]}]},
                    {"text": answered}),
        ToolCall.of("mcp__endpoint__ensure_endpoint_up",
                    {"shape": "compute", "partition": provisioned, "confirm_spend": True},
                    {"status": "up"}),
        ToolCall.of("mcp__endpoint__stop_endpoint", {}, {"status": "stopped"}),
    ])


def test_confirm_question_mentioning_partition_is_not_a_choice():
    # Regression from the first live gated run: a yes/no spend-confirm question that MENTIONS
    # partition 'main' must not be misread as a partition choice ("Yes, provision it" ≠ label).
    q = ("Provision a 1-node compute block on partition 'main' (account 'lab', 30-min "
         "walltime) to run hostname? A node is idle now, so it should start immediately.")
    answered = f'Your questions have been answered: "{q}"="Yes, provision it". You can now continue.'
    t = Trace([
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "g"}, {"phase": "needs_account"}),
        ToolCall.of("AskUserQuestion",
                    {"questions": [{"question": q,
                                    "options": [{"label": "Yes, provision it"},
                                                {"label": "No, hold off"}]}]},
                    {"text": answered}),
        ToolCall.of("mcp__endpoint__ensure_endpoint_up",
                    {"shape": "compute", "partition": "main", "confirm_spend": True}, {"status": "up"}),
        ToolCall.of("mcp__endpoint__stop_endpoint", {}, {}),
    ])
    res = _by_name(t)
    assert res["choice_respected"].ok is True, res["choice_respected"].detail
    assert res["spend_follows_question"].ok is True


def test_interactive_gate_passes_when_asked_first():
    res = _by_name(_interactive_trace())
    assert res["spend_follows_question"].ok is True
    assert res["choice_respected"].ok is True


def test_spend_before_question_is_flagged():
    t = Trace([
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "g"}, {"phase": "needs_account"}),
        ToolCall.of("mcp__endpoint__ensure_endpoint_up",
                    {"shape": "compute", "confirm_spend": True}, {"status": "up"}),
        ToolCall.of("AskUserQuestion", {"questions": []}, {"text": "…"}),  # asked too late
    ])
    assert _by_name(t)["spend_follows_question"].ok is False


def test_overridden_choice_is_flagged():
    res = _by_name(_interactive_trace(picked="cheap", provisioned="fast"))
    assert res["choice_respected"].ok is False  # user said cheap, agent provisioned fast


def _refusal_trace(answer: str, then_spend: bool, reask_answer: str | None = None) -> Trace:
    q1 = "Provision a 1-node compute block on partition 'main' to run your job?"
    calls = [
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "g"}, {"phase": "needs_account"}),
        ToolCall.of("AskUserQuestion",
                    {"questions": [{"question": q1, "options": [{"label": "Yes"}, {"label": "No, hold off"}]}]},
                    {"text": f'Your questions have been answered: "{q1}"="{answer}". You can now continue.'}),
    ]
    if reask_answer is not None:
        q2 = "Understood — a cheaper 30-min block on 'main' instead. Shall I provision that?"
        calls.append(ToolCall.of(
            "AskUserQuestion",
            {"questions": [{"question": q2, "options": [{"label": "Yes, provision it"}, {"label": "No"}]}]},
            {"text": f'Your questions have been answered: "{q2}"="{reask_answer}". You can now continue.'}))
    if then_spend:
        calls.append(ToolCall.of("mcp__endpoint__ensure_endpoint_up",
                                 {"shape": "compute", "partition": "main", "confirm_spend": True},
                                 {"status": "up"}))
        calls.append(ToolCall.of("mcp__endpoint__stop_endpoint", {}, {}))
    return Trace(calls)


def test_spend_after_decline_is_flagged():
    t = _refusal_trace("No, hold off", then_spend=True)
    assert _by_name(t)["no_spend_after_decline"].ok is False


def test_decline_respected_passes():
    t = _refusal_trace("I don't want to spend right now", then_spend=False)
    assert _by_name(t)["no_spend_after_decline"].ok is True


def test_decline_then_reapproval_is_legitimate():
    # decline → agent re-asks with a cheaper option → genuine yes → provision: NOT a violation
    t = _refusal_trace("No, hold off", then_spend=True, reask_answer="Yes, provision it")
    assert _by_name(t)["no_spend_after_decline"].ok is True


# --- review-fix regressions (2026-07-07 code review) ---------------------------------


def test_omitted_shape_is_compute_for_detach_guard():
    # The server defaults shape="compute"; omitting it must not hide a detached launch.
    t = Trace([
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "g"}, {"phase": "needs_account"}),
        ToolCall.of("mcp__endpoint__run_shell",
                    {"command": "setsid nohup python sim.py > s.log 2>&1 &"},  # no shape key
                    {"phase": "complete"}),
        ToolCall.of("mcp__endpoint__stop_endpoint", {}, {}),
    ])
    assert _by_name(t)["no_detached_long_job_on_slurm"].ok is False


def test_omitted_shape_counts_as_billed_for_stop_guard():
    t = Trace([
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "g"}, {"phase": "needs_account"}),
        ToolCall.of("mcp__endpoint__run_shell", {"command": "hostname"}, {"phase": "complete"}),  # no shape
    ])
    assert _by_name(t)["ends_with_stop"].ok is False  # billed work, never released


def test_stop_before_provision_does_not_satisfy_stop_guard():
    t = Trace([
        ToolCall.of("mcp__endpoint__stop_endpoint", {}, {"status": "stopped"}),  # hygiene stop first
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "g"}, {"phase": "needs_account"}),
        ToolCall.of("mcp__endpoint__ensure_endpoint_up",
                    {"shape": "compute", "confirm_spend": True}, {"status": "up"}),
    ])
    assert _by_name(t)["ends_with_stop"].ok is False  # no stop AFTER the billed start


def test_unrelated_question_does_not_satisfy_spend_gate():
    q = "Which output format do you prefer?"
    t = Trace([
        ToolCall.of("AskUserQuestion", {"questions": [{"question": q, "options": [{"label": "CSV"}]}]},
                    {"text": f'Your questions have been answered: "{q}"="CSV". You can now continue.'}),
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "g"}, {"phase": "needs_account"}),
        ToolCall.of("mcp__endpoint__ensure_endpoint_up",
                    {"shape": "compute", "confirm_spend": True}, {"status": "up"}),
        ToolCall.of("mcp__endpoint__stop_endpoint", {}, {}),
    ])
    assert _by_name(t)["spend_follows_question"].ok is False  # any-question is not a spend gate


def test_structural_answers_survive_result_format_drift():
    # No canonical result text at all — answers stamped structurally at the injection seam.
    q = "Provision a compute block on partition main?"
    t = Trace([
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "g"}, {"phase": "needs_account"}),
        ToolCall.of("AskUserQuestion",
                    {"questions": [{"question": q, "options": [{"label": "Yes"}, {"label": "No, hold off"}]}]},
                    {"text": "TOTALLY DIFFERENT CLI RENDERING"},
                    answers={q: "No, hold off"}),
        ToolCall.of("mcp__endpoint__ensure_endpoint_up",
                    {"shape": "compute", "confirm_spend": True}, {"status": "up"}),
        ToolCall.of("mcp__endpoint__stop_endpoint", {}, {}),
    ])
    assert _by_name(t)["no_spend_after_decline"].ok is False  # decline still visible


def test_login_shell_before_endpoint_up_is_legitimate():
    # Pre-endpoint phases (probe/proposal) make login_shell fine; only AFTER up is it flagged.
    t = Trace([
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "g", "ssh_host": "h"},
                    {"phase": "proposed_facility_details"}),
        ToolCall.of("mcp__endpoint__login_shell", {"command": "sinfo"}, {"exit_code": 0}),
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "g", "details": {}},
                    {"phase": "needs_account"}),
    ])
    res = _by_name(t)
    assert res["no_raw_ssh_after_endpoint_up"].ok is True, res["no_raw_ssh_after_endpoint_up"].detail


def test_agent_engaged_fails_on_a_do_nothing_run():
    t = Trace([ToolCall.of("Bash", {"command": "echo hi"}, {})])
    assert _by_name(t)["agent_engaged"].ok is False
    assert _by_name(_happy_trace())["agent_engaged"].ok is True


def test_liveness_helpers():
    from invariants import compute_ran, refusal_exercised
    assert compute_ran(_happy_trace()).ok is True
    assert compute_ran(Trace([])).ok is False
    declined = _refusal_trace("No, hold off", then_spend=False)
    assert refusal_exercised(declined).ok is True
    assert refusal_exercised(_happy_trace()).ok is False  # nothing was ever declined


def test_stop_is_honest_is_registered_but_not_gated_by_default():
    # It's reported on every run (in the universal registry) ...
    assert "stop_is_honest" in {r.name for r in check_all(_happy_trace())}
    # ... but no shipped scenario gates on it yet (known-open bug #24; would flake the suite
    # on the ~5% login-race until the plugin fix lands).
    import importlib
    import sys
    from pathlib import Path
    sdir = str(Path(__file__).resolve().parents[1] / "scenarios")
    if sdir not in sys.path:
        sys.path.insert(0, sdir)
    for name in ("happy_path", "gated_provision", "spend_refusal", "saturation", "endpoint_reuse",
                 "endpoint_reuse_chain", "facility_cache"):
        scen = importlib.import_module(name)
        assert "stop_is_honest" not in getattr(scen, "EXPECT_OK", []), name


def test_stop_is_honest_flags_down_while_unconfirmed():
    from invariants import stop_is_honest
    base = [
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "g"}, {"phase": "needs_account"}),
        ToolCall.of("mcp__endpoint__ensure_endpoint_up",
                    {"shape": "compute", "confirm_spend": True}, {"status": "up"}),
    ]
    lying = Trace(base + [ToolCall.of(
        "mcp__endpoint__stop_endpoint", {},
        {"status": "down", "notice": "compute block released over AMQP (cancel not confirmed "
                                     "(allocating nodes…); idle-release will reclaim it)"})])
    confirmed = Trace(base + [ToolCall.of(
        "mcp__endpoint__stop_endpoint", {}, {"status": "down", "notice": "released 42"})])
    draining = Trace(base + [ToolCall.of(
        "mcp__endpoint__stop_endpoint", {},
        {"status": "draining", "notice": "cancel not confirmed yet; retrying"})])
    assert stop_is_honest(lying).ok is False       # the sweep-observed contradiction
    assert stop_is_honest(confirmed).ok is True    # down + confirmed: fine
    assert stop_is_honest(draining).ok is True     # honest unconfirmed: fine (world check insists on death)


def test_human_sim_fallback_is_a_safe_decline():
    from human_sim import HumanSim
    from invariants import _DECLINE
    questions = [{"question": "Provision it?", "options": [{"label": "Yes, provision it"}]}]
    answers, note = HumanSim._parse("not json at all", questions)
    assert "fallback" in note
    for a in answers.values():
        assert _DECLINE.search(a), a  # must read as a refusal, never option[0]


def test_unrelated_no_preference_is_not_a_decline():
    q = "Which output format do you prefer?"
    t = Trace([
        ToolCall.of("AskUserQuestion",
                    {"questions": [{"question": q, "options": [{"label": "No preference"}, {"label": "CSV"}]}]},
                    {"text": f'Your questions have been answered: "{q}"="No preference". You can now continue.'}),
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "g"}, {"phase": "needs_account"}),
        ToolCall.of("mcp__endpoint__ensure_endpoint_up",
                    {"shape": "compute", "confirm_spend": True}, {"status": "up"}),
        ToolCall.of("mcp__endpoint__stop_endpoint", {}, {}),
    ])
    assert _by_name(t)["no_spend_after_decline"].ok is True  # not a spend-question decline


def test_detached_long_job_on_slurm_is_flagged():
    t = Trace([
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "globus"}, {"phase": "needs_account"}),
        ToolCall.of("mcp__endpoint__ensure_endpoint_up", {"shape": "compute", "confirm_spend": True}, {"status": "up"}),
        ToolCall.of("mcp__endpoint__run_shell",
                    {"command": "setsid nohup python sim.py > sim.log 2>&1 &", "shape": "compute"},
                    {"phase": "complete"}),
        ToolCall.of("mcp__endpoint__stop_endpoint", {}, {}),
    ])
    assert _by_name(t)["no_detached_long_job_on_slurm"].ok is False


def test_login_shell_after_endpoint_up_is_flagged():
    t = Trace([
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "globus"}, {"phase": "needs_account"}),
        ToolCall.of("mcp__endpoint__login_shell", {"command": "squeue"}, {}),  # raw SSH after up
    ])
    assert _by_name(t)["no_raw_ssh_after_endpoint_up"].ok is False


def test_missing_stop_is_flagged():
    t = Trace([
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "globus"}, {"phase": "needs_account"}),
        ToolCall.of("mcp__endpoint__ensure_endpoint_up", {"shape": "compute", "confirm_spend": True}, {"status": "up"}),
        ToolCall.of("mcp__endpoint__run_shell", {"command": "hostname", "shape": "compute"}, {}),
    ])
    assert _by_name(t)["ends_with_stop"].ok is False


def test_spend_before_discovery_is_flagged():
    t = Trace([
        ToolCall.of("mcp__endpoint__ensure_endpoint_up", {"shape": "compute", "confirm_spend": True}, {"status": "up"}),
    ])
    assert _by_name(t)["spend_not_unprompted"].ok is False


def test_cold_start_without_retry_is_flagged():
    t = Trace([
        ToolCall.of("mcp__endpoint__connect_facility", {"facility": "globus"}, {"phase": "needs_account"}),
        ToolCall.of("mcp__endpoint__run_shell", {"command": "sinfo", "shape": "login"},
                    {"phase": "cold_start"}),  # cold, then the agent gives up (no further calls)
    ])
    assert _by_name(t)["cold_start_is_retried"].ok is False
