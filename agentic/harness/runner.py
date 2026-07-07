"""Per-scenario runner: drive a headless agent over hpc-bridge, capture the Trace.

Registers the hpc-bridge MCP stdio server via the Agent SDK under the key
``endpoint`` (tools surface as ``mcp__endpoint__*``, matching the .mcp.json rename),
injecting **scoped** test credentials via the server's own ``env`` — never the admin
key. Runs the scripted prompt non-interactively (``bypassPermissions`` — the
disposable container IS the sandbox), then returns the normalised Trace + the
ResultMessage for grading by ``invariants.py`` (+ the judge).

Credentials come from THIS process's environment, which in the container holds only
the scoped test key + test Globus identity. See Plan B (runtime sandbox).

Requires ``claude-agent-sdk`` (not a hpc-bridge dependency — installed only in the
agentic harness image). The hermetic ``pytest -q`` never imports this module
(testpaths = ["tests"]).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import (  # type: ignore[import-not-found]
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    query,
)

from human_sim import HumanSim
from invariants import Trace, logical_name
from trace_adapter import build_trace

# Logical hpc-bridge tool names (see Modules/server.md). Registered under SDK key
# "endpoint" -> the agent sees them as mcp__endpoint__<tool>.
HPC_BRIDGE_TOOLS = (
    "list_facilities",
    "connect_facility",
    "ensure_endpoint_up",
    "run_shell",
    "reset_session",
    "stop_endpoint",
    "login_shell",
)

# Scoped creds the harness injects into the MCP server's env. The first three are
# required (fail fast if the container didn't inject them); the rest are optional.
_REQUIRED_ENV = ("HPC_BRIDGE_USER_DIR", "HPC_BRIDGE_SSH_USER", "HPC_BRIDGE_SSH_KEY")
_OPTIONAL_ENV = ("HPC_BRIDGE_SSH_HOST", "HPC_BRIDGE_MACHINE", "HPC_BRIDGE_SEARCH_INDEX")


@dataclass
class RunResult:
    trace: Trace
    final: Any            # the SDK ResultMessage (cost, is_error, session_id, ...)
    messages: list[Any]   # raw messages, kept for the LLM-judge / debugging
    dialogue: list[Any] = None  # interactive mode: the human-sim's Q&A Exchanges


def _server_env() -> dict[str, str]:
    missing = [k for k in _REQUIRED_ENV if k not in os.environ]
    if missing:
        raise RuntimeError(
            f"agentic harness: missing scoped credential env {missing} — the container "
            "must inject the test SSH user/key + a fresh HPC_BRIDGE_USER_DIR (never the admin key)."
        )
    env = {k: os.environ[k] for k in _REQUIRED_ENV}
    env.update({k: os.environ[k] for k in _OPTIONAL_ENV if k in os.environ})
    return env


def _mcp_servers(repo_root: Path) -> dict:
    # Direct registration (not as a plugin) — matches the .mcp.json launch command
    # (the trailing "hpc-bridge" is the console script, unchanged by the server rename).
    return {
        "endpoint": {
            "type": "stdio",
            "command": "uv",
            "args": ["run", "--directory", str(repo_root), "--extra", "integration", "hpc-bridge"],
            "env": _server_env(),
        }
    }


def _system_prompt(repo_root: Path, interactive: bool, include_skill: bool = True) -> str:
    # First cut: inject the driving-hpc skill as standing guidance. (Faithful on-demand
    # Skill-tool loading is a later refinement; here we test whether the guidance CONTENT
    # drives the right behaviour.)
    if interactive:
        lead = (
            "You are driving real HPC through the hpc-bridge tools on behalf of a user. "
            "The user is present and answers questions via the AskUserQuestion tool — use it "
            "for the consequential choices"
        )
        lead += " exactly as the guidance instructs. " if include_skill else ". "
    else:
        lead = (
            "You are driving real HPC through the hpc-bridge tools in an automated test. "
            "There is no human to answer follow-up questions — act on the instructions you "
            "are given. "
        )
    if not include_skill:
        # Skill ablation: the agent gets only the tools' own descriptions — the measured
        # pass-rate delta vs baseline IS the causal value of SKILL.md.
        return lead + "Use the tools' own descriptions to decide how to proceed."
    skill = (repo_root / "skills" / "driving-hpc" / "SKILL.md").read_text()
    return lead + "Follow this operational guidance:\n\n" + skill


def _live(msg: Any) -> None:
    """Stream a one-line marker per tool call as the agent works — to stderr, so progress
    interleaves with container logs while the final structured trace goes to stdout."""
    content = getattr(msg, "content", None)
    if not isinstance(content, list):
        return
    for b in content:
        if hasattr(b, "name") and hasattr(b, "input") and hasattr(b, "id"):  # ToolUseBlock
            inp = json.dumps(getattr(b, "input", {}) or {}, default=str)
            if len(inp) > 90:
                inp = inp[:89] + "…"
            print(f"  → {logical_name(getattr(b, 'name', '') or '')}({inp})", file=sys.stderr, flush=True)


async def run_scenario(
    prompt: str,
    *,
    repo_root: Path,
    model: str = "claude-opus-4-8",
    effort: str | None = None,
    persona: str | None = None,
    user_goal: str = "",
    ablate_skill: bool = False,
    max_turns: int = 40,
    max_budget_usd: float = 2.0,
) -> RunResult:
    """Run one scripted scenario end-to-end and return the captured Trace + result.

    Two modes:
    - autonomous (persona=None): one-shot `query()` under bypassPermissions — the prompt
      pre-authorises everything (no human exists).
    - interactive (persona set): a simulated human ([[human_sim]]) answers the agent's REAL
      AskUserQuestion calls. Mechanics (spike-proven): `permission_mode="default"` with
      everything pre-allowed EXCEPT AskUserQuestion, so it — and only it — falls through to
      `can_use_tool`, which injects the human-sim's answers via `updated_input`. The
      callback needs the streaming control channel, hence ClaudeSDKClient.
    """
    interactive = persona is not None
    opts: dict[str, Any] = dict(
        model=model,
        allowed_tools=[f"mcp__endpoint__{t}" for t in HPC_BRIDGE_TOOLS] + ["Bash", "Read", "Write"],
        mcp_servers=_mcp_servers(repo_root),
        system_prompt=_system_prompt(repo_root, interactive, include_skill=not ablate_skill),
        setting_sources=[],  # SDK isolation: ignore host ~/.claude + project settings
        cwd=str(repo_root),
        max_turns=max_turns,            # safety rail: bound a runaway agent
        max_budget_usd=max_budget_usd,  # safety rail: bound cost per scenario
    )
    if effort:
        # Reasoning level (low..max): effort guides adaptive thinking DEPTH, so pair it with
        # adaptive thinking to make the level bite. Unset ⇒ the model's default effort.
        opts["effort"] = effort
        opts["thinking"] = {"type": "adaptive"}

    human: HumanSim | None = None
    injected_answers: dict[str, dict[str, str]] = {}  # tool_use_id -> answers (structural record)
    if interactive:
        human = HumanSim(persona=persona, goal=user_goal)

        async def _gatekeeper(tool_name: str, tool_input: dict, ctx: Any):
            if tool_name == "AskUserQuestion":
                print(f"  ? gate: {[q.get('question') for q in tool_input.get('questions', [])]}",
                      file=sys.stderr, flush=True)
                answers = await human.answer(tool_input)
                print(f"  ! human({persona}): {answers}", file=sys.stderr, flush=True)
                # Stamp the answers structurally by tool_use_id so grading never depends on
                # how the CLI renders them into result text (format drift => vacuous passes).
                tid = getattr(ctx, "tool_use_id", None)
                if tid:
                    injected_answers[tid] = answers
                return PermissionResultAllow(updated_input={**tool_input, "answers": answers})
            # Anything else that falls through (not in allowed_tools) is fine in the jail.
            return PermissionResultAllow()

        opts["permission_mode"] = "default"   # bypass would skip can_use_tool entirely
        opts["can_use_tool"] = _gatekeeper
    else:
        opts["permission_mode"] = "bypassPermissions"  # the disposable container is the sandbox

    options = ClaudeAgentOptions(**opts)
    messages: list[Any] = []
    final: Any = None
    if interactive:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for msg in client.receive_response():
                messages.append(msg)
                _live(msg)
                if type(msg).__name__ == "ResultMessage":
                    final = msg
    else:
        async for msg in query(prompt=prompt, options=options):
            messages.append(msg)
            _live(msg)
            if type(msg).__name__ == "ResultMessage":
                final = msg
    return RunResult(
        trace=build_trace(messages, injected_answers=injected_answers),
        final=final, messages=messages,
        dialogue=(human.dialogue if human else []),
    )
