"""Hermetic tests for regrade.bundle_trace: one reader per bundle shape (SDK dict-form, hermes state.db rows with
ACP re-stamping, Claude CLI transcript), sniffed from messages.jsonl — so every operator's bundles regrade."""
from __future__ import annotations

import json

from invariants import spend_follows_question
from regrade import bundle_trace

Q = "Shall I provision one node on `debug` charging `lab` (~2 SU)?"


def _bundle(tmp_path, name, lines, record=None):
    d = tmp_path / name
    d.mkdir()
    (d / "messages.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    (d / "record.json").write_text(json.dumps(record or {}))
    return d


def test_hermes_acp_bundle_restamps_exchanges_from_dialogue(tmp_path):
    rows = [
        {"role": "user", "content": "bring up a node", "tool_calls": None},
        {"role": "assistant", "content": "", "tool_calls": json.dumps([{"id": "c1", "function": {"name": "mcp__hpc_bridge__connect_facility", "arguments": "{}"}}])},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"phase": "up"}), "tool_calls": None},
        {"role": "assistant", "content": Q, "tool_calls": None},
        {"role": "user", "content": "Yes, go ahead.", "tool_calls": None},
        {"role": "assistant", "content": "", "tool_calls": json.dumps([{"id": "c2", "function": {"name": "mcp__hpc_bridge__ensure_endpoint_up",
                                                                                                 "arguments": json.dumps({"shape": "compute", "confirm_spend": True})}}])},
        {"role": "tool", "tool_call_id": "c2", "content": json.dumps({"status": "up"}), "tool_calls": None},
    ]
    rec = {"env": {"HPCB_HERMES_ACP": "1"},
           "dialogue": [{"questions": [{"question": Q}], "answers": {"reply": "Yes, go ahead."}, "kind": "answer"},
                        {"questions": [{"question": "done"}], "answers": {}, "kind": "conclude"}]}
    d = _bundle(tmp_path, "r1-gated_provision", rows, rec)
    t = bundle_trace(d, rec)
    assert [c.name for c in t.calls] == ["connect_facility", "AskUserQuestion", "ensure_endpoint_up"]
    assert spend_follows_question(t).ok
    # the same bundle from the -z transcript-replay path (no HPCB_HERMES_ACP): trace only, no re-stamping
    t2 = bundle_trace(d, {"env": {}, "dialogue": rec["dialogue"]})
    assert [c.name for c in t2.calls] == ["connect_facility", "ensure_endpoint_up"]


def test_claude_transcript_bundle(tmp_path):
    lines = [
        {"type": "user", "sessionId": "s1", "message": {"role": "user", "content": "bring up a node"}},
        {"type": "assistant", "sessionId": "s1", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "mcp__endpoint__run_shell", "input": {"command": "hostname", "shape": "compute"}}]}},
        {"type": "user", "sessionId": "s1", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": json.dumps({"status": "completed", "stdout": "c1"})}]}},
    ]
    d = _bundle(tmp_path, "r2-happy_path", lines)
    t = bundle_trace(d, {})
    assert [c.name for c in t.calls] == ["run_shell"] and t.calls[0].result["stdout"] == "c1"


def test_sniff_routes_sdk_dict_form(tmp_path):
    lines = [{"__type__": "AssistantMessage", "content": [{"__type__": "ToolUseBlock", "id": "a", "name": "mcp__endpoint__list_facilities", "input": {}}]},
             {"__type__": "UserMessage", "content": [{"__type__": "ToolResultBlock", "tool_use_id": "a", "content": "[]"}]}]
    d = _bundle(tmp_path, "r3-zero_config_list", lines)
    assert [c.name for c in bundle_trace(d, {}).calls] == ["list_facilities"]
