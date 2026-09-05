"""Hermetic tests for runner.py against a STUB claude_agent_sdk (the real one is only in the jail image).

The stub drives `run_scenario` through the exact shapes that matter for the completion gate (review
2026-09-05, 2.4): a turn whose stream ends without a ResultMessage, a stream that raises mid-turn, a clean
run, and a prose-question loop that hits its cap. No SDK, no cluster, no network.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import pytest

HERE = Path(__file__).resolve().parent


@dataclass
class TextBlock:
    text: str


@dataclass
class AssistantMessage:
    content: list


@dataclass
class ResultMessage:
    is_error: bool = False
    result: str | None = None
    total_cost_usd: float = 0.01
    num_turns: int = 1
    duration_ms: int = 1
    session_id: str = "s"
    usage: dict = field(default_factory=dict)


class _Client:
    """Each `receive_response()` call yields the next scripted TURN: a list of messages, or an exception."""

    turns: ClassVar[list] = []
    queries: ClassVar[list[str]] = []

    def __init__(self, options=None):
        self._i = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def query(self, prompt):
        _Client.queries.append(prompt)

    async def receive_response(self):
        turn = _Client.turns[self._i] if self._i < len(_Client.turns) else []
        self._i += 1
        for m in turn:
            if isinstance(m, Exception):
                raise m
            yield m


async def _query(prompt, options=None):
    for m in _Client.turns[0]:
        if isinstance(m, Exception):
            raise m
        yield m


@pytest.fixture
def runner(monkeypatch):
    stub = types.ModuleType("claude_agent_sdk")
    stub.ClaudeAgentOptions = lambda **kw: kw
    stub.ClaudeSDKClient = _Client
    stub.PermissionResultAllow = lambda **kw: kw
    stub.query = _query
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", stub)
    monkeypatch.syspath_prepend(str(HERE))
    for k in ("HPC_BRIDGE_USER_DIR", "HPC_BRIDGE_SSH_USER", "HPC_BRIDGE_SSH_KEY"):
        monkeypatch.setenv(k, "x")
    sys.modules.pop("runner", None)
    import runner as mod

    # the human-sim would call the real SDK: script its replies
    async def fake_reply(self, text):
        return "Yes, that is fine."

    monkeypatch.setattr(mod.HumanSim, "reply", fake_reply)
    _Client.queries = []
    return mod


def _run(mod, persona="cooperative"):
    return asyncio.run(mod.run_scenario("do the thing", repo_root=HERE.parents[1], persona=persona))


def test_clean_interactive_run_keeps_its_result(runner):
    _Client.turns = [[AssistantMessage([TextBlock("done.")]), ResultMessage()]]
    r = _run(runner)
    assert r.final.is_error is False and r.prose_followups == 0 and not r.followups_capped
    assert r.human_sim_model == "claude-haiku-4-5-20251001"


def test_turn_without_a_result_message_is_a_truncated_run(runner):
    # turn 1 asks in prose (gets a result), turn 2's stream ends with NO ResultMessage (CLI died / EOF):
    # the run must NOT pass the completion gate on turn 1's stale result
    _Client.turns = [[AssistantMessage([TextBlock("Does this look correct?")]), ResultMessage()],
                     [AssistantMessage([TextBlock("working…")])]]
    r = _run(runner)
    assert r.prose_followups == 1
    assert getattr(r.final, "is_error", None) is True and "without a ResultMessage" in r.final.result


def test_stream_death_mid_followup_is_an_aborted_run(runner):
    _Client.turns = [[AssistantMessage([TextBlock("Shall I proceed?")]), ResultMessage()],
                     [AssistantMessage([TextBlock("…")]), RuntimeError("CLI process exited")]]
    r = _run(runner)
    assert getattr(r.final, "is_error", None) is True and "CLI process exited" in r.final.result


def test_prose_follow_up_cap_is_recorded_not_hidden(runner):
    asks = [AssistantMessage([TextBlock("Would you like me to continue?")]), ResultMessage()]
    _Client.turns = [list(asks) for _ in range(runner.MAX_PROSE_FOLLOWUPS + 2)]
    r = _run(runner)
    assert r.prose_followups == runner.MAX_PROSE_FOLLOWUPS and r.followups_capped is True
    assert r.final.is_error is False  # the last turn did complete; the cap is a separate (gating) row in run.py


def test_autonomous_run_and_max_turns_raise(runner):
    _Client.turns = [[AssistantMessage([TextBlock("ok")]), ResultMessage()]]
    r = _run(runner, persona=None)
    assert r.final.is_error is False and r.human_sim_model is None
    _Client.turns = [[AssistantMessage([TextBlock("ok")]), RuntimeError("Reached maximum number of turns")]]
    r = _run(runner, persona=None)
    assert r.final.is_error is True and "maximum number of turns" in r.final.result


def test_agent_env_is_scrubbed_during_the_run_and_restored_after(runner, monkeypatch):
    monkeypatch.setenv("HPCB_RUNID", "42-1")
    monkeypatch.setenv("PYTHONPATH", "/work/hpc-bridge/agentic/harness")
    seen = {}

    async def spy_query(prompt, options=None):
        seen["HPCB_RUNID"] = os.environ.get("HPCB_RUNID")
        seen["PYTHONPATH"] = os.environ.get("PYTHONPATH")
        seen["HPC_BRIDGE_SSH_USER"] = os.environ.get("HPC_BRIDGE_SSH_USER")
        yield ResultMessage()

    monkeypatch.setattr(runner, "query", spy_query)
    _run(runner, persona=None)
    assert seen == {"HPCB_RUNID": None, "PYTHONPATH": None, "HPC_BRIDGE_SSH_USER": "x"}  # scrubbed while the CLI lives
    assert os.environ["HPCB_RUNID"] == "42-1" and os.environ["PYTHONPATH"].endswith("harness")  # restored after
