"""Hermetic tests for hermes_trace: a hermes state.db -> normalised Trace (the same shape the Claude
SDK adapter produces, so the same invariants grade both operators). No hermes, no network — a synthetic
sqlite db with hermes' `messages` schema."""
from __future__ import annotations

import json
import sqlite3

from hermes_trace import result_to_dict, trace_from_hermes_db


def _make_db(tmp_path, rows):
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
        "content TEXT, tool_calls TEXT, tool_name TEXT, tool_call_id TEXT)"
    )
    for r in rows:
        con.execute(
            "INSERT INTO messages (session_id, role, content, tool_calls, tool_name, tool_call_id) "
            "VALUES (?,?,?,?,?,?)",
            (r.get("session_id", "s1"), r["role"], r.get("content"), r.get("tool_calls"),
             r.get("tool_name"), r.get("tool_call_id")),
        )
    con.commit()
    con.close()
    return db


def _call(cid, name, args="{}"):
    return json.dumps([{"id": cid, "call_id": cid, "type": "function",
                        "function": {"name": name, "arguments": args}}])


def test_pairs_calls_with_results_and_collects_text(tmp_path):
    rows = [
        {"role": "user", "content": "which facilities?"},
        {"role": "assistant", "tool_calls": _call("c1", "mcp__hpc_bridge__list_facilities", "{}")},
        {"role": "tool", "tool_call_id": "c1", "tool_name": "mcp__hpc_bridge__list_facilities",
         "content": '<untrusted_tool_result source="mcp__hpc_bridge__list_facilities">\n'
                    '{"id": "anvil", "access": "mep"}\n</untrusted_tool_result>'},
        {"role": "assistant", "content": "You can use Anvil (zero SSH) and Expanse (SSH)."},
    ]
    t = trace_from_hermes_db(_make_db(tmp_path, rows))
    assert [c.name for c in t.calls] == ["list_facilities"]          # logical name, prefix stripped
    assert t.calls[0].result == {"id": "anvil", "access": "mep"}      # envelope unwrapped, JSON parsed
    assert any("Anvil" in x for x in t.texts)                        # assistant prose reaches Trace.texts


def test_builtin_tools_appear_but_dont_masquerade(tmp_path):
    """hermes' own tools (tool_search) show up as ToolCalls, but under their own name — hpc-bridge graders
    key on connect_facility/run_shell/... and never match them."""
    rows = [
        {"role": "assistant", "tool_calls": _call("s1", "tool_search", '{"queries":["list facilities"]}')},
        {"role": "tool", "tool_call_id": "s1", "tool_name": "tool_search", "content": '{"results":[]}'},
        {"role": "assistant", "tool_calls": _call("c1", "mcp__hpc_bridge__connect_facility", '{"facility":"anvil"}')},
        {"role": "tool", "tool_call_id": "c1",
         "content": '<untrusted_tool_result source="x">{"phase":"needs_login"}</untrusted_tool_result>'},
    ]
    t = trace_from_hermes_db(_make_db(tmp_path, rows))
    assert [c.name for c in t.calls] == ["tool_search", "connect_facility"]
    assert t.calls[1].input == {"facility": "anvil"}
    assert t.calls[1].result == {"phase": "needs_login"}


def test_unwraps_hermes_tool_call_dispatcher(tmp_path):
    """gpt-oss sometimes invokes a deferred hpc-bridge tool via hermes' generic `tool_call` dispatcher
    (the real tool name + input nested in the arguments) — unwrap it so the tool reaches the trace under
    its own logical name, with its result and input intact."""
    rows = [
        {"role": "assistant", "tool_calls": _call("s1", "tool_search", '{"queries":["list facilities"]}')},
        {"role": "tool", "tool_call_id": "s1", "content": '{"results":[]}'},
        {"role": "assistant", "tool_calls": _call(
            "d1", "tool_call", json.dumps({"name": "mcp__hpc_bridge__list_facilities", "arguments": {}}))},
        {"role": "tool", "tool_call_id": "d1",
         "content": '<untrusted_tool_result source="x">{"id":"anvil"}</untrusted_tool_result>'},
        # the nested arguments can also arrive as a JSON *string*, not an object
        {"role": "assistant", "tool_calls": _call(
            "d2", "tool_call", json.dumps({"name": "mcp__hpc_bridge__connect_facility",
                                           "arguments": '{"facility":"anvil"}'}))},
        {"role": "tool", "tool_call_id": "d2",
         "content": '<untrusted_tool_result source="x">{"phase":"needs_login"}</untrusted_tool_result>'},
    ]
    t = trace_from_hermes_db(_make_db(tmp_path, rows))
    assert [c.name for c in t.calls] == ["tool_search", "list_facilities", "connect_facility"]
    assert t.calls[1].result == {"id": "anvil"}
    assert t.calls[2].input == {"facility": "anvil"}
    assert t.calls[2].result == {"phase": "needs_login"}


def test_result_to_dict_variants():
    assert result_to_dict(None) is None
    # a tool-loop warning trailing the JSON, inside the envelope, is dropped
    c = '<untrusted_tool_result source="x">{"error":"boom"}\n\n[Tool loop warning: ...]</untrusted_tool_result>'
    assert result_to_dict(c) == {"error": "boom"}
    assert result_to_dict("just words")["text"] == "just words"          # non-JSON -> wrapped text
    assert result_to_dict('{"total_available": 19}') == {"total_available": 19}  # bare JSON (no envelope)
    assert result_to_dict('{"id":"a"}\n{"id":"b"}') == {"id": "a"}       # multiple objects -> first


def test_malformed_tool_calls_are_skipped(tmp_path):
    rows = [{"role": "assistant", "tool_calls": "not json", "content": "hi"}]
    t = trace_from_hermes_db(_make_db(tmp_path, rows))
    assert t.calls == []
    assert t.texts == ["hi"]
