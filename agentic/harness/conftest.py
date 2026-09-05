"""Shared fixtures for the harness' hermetic tests."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.fixture
def harness_run(monkeypatch):
    """run.py imports runner, which imports the agent SDK (jail-only): stub the SDK the way test_runner.py does."""
    stub = types.ModuleType("claude_agent_sdk")
    for name in ("ClaudeAgentOptions", "PermissionResultAllow", "PermissionResultDeny", "AssistantMessage", "UserMessage",
                 "ResultMessage", "SystemMessage", "ToolUseBlock", "ToolResultBlock", "TextBlock", "HookMatcher", "ClaudeSDKClient"):
        setattr(stub, name, type(name, (), {}))
    stub.query = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", stub)
    monkeypatch.syspath_prepend(str(HERE))
    sys.modules.pop("runner", None)
    sys.modules.pop("run", None)
    import run as mod
    return mod
