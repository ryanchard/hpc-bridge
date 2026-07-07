"""Adapter: Claude Agent SDK message stream -> normalised Trace (invariants.py).

Tool calls come from ``AssistantMessage`` content blocks (a ToolUseBlock carries
``name`` + ``input`` + ``id``); tool outputs come from ``UserMessage`` content
blocks (a ToolResultBlock carries ``tool_use_id`` + ``content``) and are paired
back into ``ToolCall.result``. Block types are **duck-typed** (by attribute), so
this survives SDK version/class-name drift. The runner feeds the raw message list
here; ``invariants.py`` then asserts over the returned ``Trace``.

Named ``trace_adapter`` (not ``trace``) to avoid shadowing the stdlib ``trace``.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from invariants import ToolCall, Trace


def _is_tool_use(b: Any) -> bool:
    return hasattr(b, "name") and hasattr(b, "input") and hasattr(b, "id")


def _is_tool_result(b: Any) -> bool:
    return hasattr(b, "tool_use_id")


def _result_to_dict(content: Any) -> dict | None:
    """MCP tool results arrive as a text string (usually JSON) or a list of blocks.
    Parse to a dict when we can; otherwise wrap the raw text."""
    text: str | None = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for item in content:
            t = getattr(item, "text", None)
            if t is None and isinstance(item, dict):
                t = item.get("text")
            if t:
                parts.append(t)
        text = "\n".join(parts) if parts else None
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {"value": obj}
    except (json.JSONDecodeError, TypeError):
        return {"text": text}


def build_trace(messages: Iterable[Any]) -> Trace:
    """Normalise an SDK message stream into a Trace of ToolCalls (with results paired)."""
    calls: list[ToolCall] = []
    by_id: dict[str, ToolCall] = {}
    for msg in messages:
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            continue
        for b in content:
            if _is_tool_use(b):
                tc = ToolCall.of(
                    getattr(b, "name", "") or "",
                    dict(getattr(b, "input", {}) or {}),
                )
                calls.append(tc)
                bid = getattr(b, "id", None)
                if bid:
                    by_id[bid] = tc
            elif _is_tool_result(b):
                tc = by_id.get(getattr(b, "tool_use_id", None))
                if tc is not None and tc.result is None:
                    tc.result = _result_to_dict(getattr(b, "content", None))
    return Trace(calls)
