"""Human-sim mechanics (hermetic): answer re-keying and prose-question detection."""
from __future__ import annotations

from human_sim import HumanSim, ends_with_question, rekey_answers

Q_IFACE = "Does the proposed network interface `enP7s7` look right for this machine, or should I use something else?"
Q_PART = ("Login node is up. This is a small Slurm cluster with a single account ('lab'). Two partitions are "
          "available: 'main' (default) and 'backfill'. Which partition should I request the compute block on?")


def _qs(*texts):
    return [{"question": t, "header": "h", "options": [{"label": "a"}, {"label": "b"}]} for t in texts]


def test_exact_keys_pass_through_unchanged():
    out, notes = rekey_answers({Q_IFACE: "enP7s7"}, _qs(Q_IFACE))
    assert out == {Q_IFACE: "enP7s7"} and notes == []


def test_paraphrased_key_is_remapped_to_the_exact_question_text():
    # the 2026-09-03 miss: the sim keyed its answer by a shortened question and the CLI saw no answer
    short = "Which partition should I request the compute block on?"
    out, notes = rekey_answers({short: "main (Recommended)"}, _qs(Q_PART))
    assert out == {Q_PART: "main (Recommended)"}
    assert notes and "remapped" in notes[0]


def test_positional_fallback_when_counts_agree_and_keys_are_garbled():
    out, notes = rekey_answers({"q1": "enP7s7", "q2": "main (Recommended)"}, _qs(Q_IFACE, Q_PART))
    assert out == {Q_IFACE: "enP7s7", Q_PART: "main (Recommended)"}
    assert all("positional" in n for n in notes)


def test_unmatched_question_is_left_unanswered_never_invented():
    out, notes = rekey_answers({"something unrelated": "yes"}, _qs(Q_IFACE, Q_PART))
    assert out == {}
    assert sum("UNANSWERED" in n for n in notes) == 2


def test_parse_then_rekey_keeps_the_safe_decline_fallback():
    answers, note = HumanSim._parse("garbage", _qs(Q_IFACE))
    out, _ = rekey_answers(answers, _qs(Q_IFACE))
    assert out[Q_IFACE].startswith("No")
    assert "SAFE DECLINE" in note


def test_prose_question_detected():
    haiku = ("**Proposed facility configuration**\n- Interface: `enP7s7`\n\n"
             "The most important thing to confirm is the **interface `enP7s7`**. Does this look correct for your facility?")
    assert ends_with_question(haiku)
    assert ends_with_question("Ready to proceed — shall I bring up the block on `main`?")
    assert ends_with_question("Please confirm the interface and scratch path before I continue.")


def test_statements_are_not_questions():
    assert not ends_with_question("Perfect! Compute block successfully shut down.")
    assert not ends_with_question("`hostname` came back as **globus2** — the compute node is up. Now shutting it down as requested.")
    assert not ends_with_question("")
