"""Simulated human for interactive scenarios — the second actor.

When the operator agent calls the REAL `AskUserQuestion` tool, the harness' `can_use_tool`
callback hands the questions here; a persona'd LLM picks the answers, and the callback
injects them via `updated_input` — the exact seam interactive Claude Code uses (answers are
"collected by the permission component"). Spike-proven 2026-07-01 (~$0.01/question round).

Context isolation is the point: the human-sim sees ONLY what a real user would — the
questions posed, its own persona, and its goal. Never the operator's system prompt or
reasoning. Conversely the operator never sees the persona, so it can't game the user.

The human-sim itself is a no-tools, single-turn SDK query (subscription-billed; haiku by
default — a user answering a menu doesn't need frontier reasoning).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, query  # type: ignore[import-not-found]

PERSONAS: dict[str, str] = {
    "cooperative": (
        "You are an easy-going researcher. You answer promptly, accept sensible "
        "recommendations (pick the recommended option when one is marked), and approve "
        "reasonable spending toward your goal."
    ),
    "budget_hawk": (
        "You are extremely cost-conscious. Always pick the CHEAPEST option available. "
        "Approve spending only when the question clearly states the cost/balance and it is "
        "small; if a question asks you to confirm spending without telling you what it "
        "costs, answer that you decline until you're told the cost."
    ),
    "declines_spend": (
        "You are cautious with your allocation today. Answer discovery/configuration "
        "questions normally, but DECLINE any question that asks to start, provision, or pay "
        "for compute — say you don't want to spend right now."
    ),
}

_ANSWER_RE = re.compile(r"\{.*\}", re.S)


@dataclass
class Exchange:
    questions: list[dict]
    answers: dict[str, str]
    note: str = ""


@dataclass
class HumanSim:
    persona: str
    goal: str
    model: str = "claude-haiku-4-5-20251001"
    dialogue: list[Exchange] = field(default_factory=list)

    async def answer(self, tool_input: dict[str, Any]) -> dict[str, str]:
        """Choose an answer for each AskUserQuestion question, in persona."""
        questions = tool_input.get("questions", [])
        prompt = (
            "You are role-playing a HUMAN USER answering an assistant's multiple-choice "
            f"questions.\n\nYOUR PERSONA: {PERSONAS.get(self.persona, self.persona)}\n\n"
            f"YOUR GOAL: {self.goal}\n\n"
            f"THE ASSISTANT ASKS:\n{json.dumps(questions, indent=2)}\n\n"
            "Reply with ONLY a JSON object:\n"
            '{"answers": {"<full question text>": "<chosen option label, or short free text>"'
            ', ...}, "note": "<one sentence: your reaction as the user — e.g. whether the '
            'question gave you what you needed to decide>"}'
        )
        opts = ClaudeAgentOptions(
            model=self.model,
            max_turns=1,
            allowed_tools=[],
            setting_sources=[],
            system_prompt="Answer as the role-played user. Output ONLY the JSON object.",
        )
        text = ""
        async for msg in query(prompt=prompt, options=opts):
            for b in getattr(msg, "content", []) or []:
                t = getattr(b, "text", None)
                if t:
                    text += t
        answers, note = self._parse(text, questions)
        self.dialogue.append(Exchange(questions=questions, answers=answers, note=note))
        return answers

    @staticmethod
    def _parse(text: str, questions: list[dict]) -> tuple[dict[str, str], str]:
        m = _ANSWER_RE.search(text or "")
        if m:
            try:
                obj = json.loads(m.group(0))
                answers = {str(k): str(v) for k, v in (obj.get("answers") or {}).items()}
                if answers:
                    return answers, str(obj.get("note", ""))
            except json.JSONDecodeError:
                pass
        # Fallback: first option per question (a distracted user), flagged in the note.
        fallback = {
            q.get("question", "?"): (q.get("options") or [{}])[0].get("label", "ok")
            for q in questions
        }
        return fallback, "(human-sim parse fallback: picked first options)"
