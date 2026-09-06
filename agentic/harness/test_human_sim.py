"""Human-sim mechanics (hermetic): answer re-keying and prose-question detection."""
from __future__ import annotations

from human_sim import EXCHANGE_KINDS, HumanSim, ends_with_question, rekey_answers

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


def test_parse_reply_extracts_reply_and_kind():
    reply, kind, reason = HumanSim._parse_reply(
        '{"reply": "Yes, go ahead on debug.", "kind": "answer", "reason": "reasonable question"}')
    assert reply == "Yes, go ahead on debug."
    assert kind == "answer" and kind in EXCHANGE_KINDS
    assert reason == "reasonable question"


def test_parse_reply_classifies_correction():
    reply, kind, _ = HumanSim._parse_reply(
        '{"reply": "No — I asked for the compute partition, not debug.", "kind": "correction", "reason": "wrong partition"}')
    assert kind == "correction"
    assert "compute partition" in reply


def test_parse_reply_unknown_kind_falls_back_to_answer():
    _, kind, _ = HumanSim._parse_reply('{"reply": "sure", "kind": "banana"}')
    assert kind == "answer"   # unknown label normalised, never invented


def test_parse_reply_unparseable_is_safe_unclear():
    reply, kind, _ = HumanSim._parse_reply("the model rambled with no json")
    assert kind == "unclear"
    assert "can't tell" in reply.lower()   # a neutral nudge, never a fabricated approval


def test_confirmation_request_anywhere_is_a_question():
    # a lead-in confirmation ask followed by a config block (ends on bullets, not "?") — must be detected
    msg = ("This facility isn't in the catalog, so hpc-bridge proposed a config. Before I proceed I need to "
           "confirm this with you:\n- interface: eth0\n- scratch_root: /home/u/.hpc-bridge")
    assert ends_with_question(msg)
    assert ends_with_question("Which partition should I request the block on?")
    assert ends_with_question("Awaiting your approval before I start the billed block.")


def test_completion_summaries_still_not_questions():
    assert not ends_with_question("Done. Ran hostname on node c1; released the block. All finished.")
    assert not ends_with_question("Perfect! Compute block successfully shut down.")
