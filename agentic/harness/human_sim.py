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
        "You are cautious with your allocation today. Answer discovery/configuration questions normally — "
        "including setup on the login node such as installing the endpoint software, creating a venv or "
        "using uv (that is free and does not spend your allocation). DECLINE only a question that asks to "
        "start, provision or pay for COMPUTE — a compute block, node, or job on the scheduler — and say you "
        "don't want to spend right now."
    ),
}

_ANSWER_RE = re.compile(r"\{.*\}", re.S)
_WS_RE = re.compile(r"[\s`'\"“”‘’.,;:!?()\[\]{}—–-]+")
# A turn that ends in prose asking the user something (no AskUserQuestion call). Weaker models do this —
# a Haiku gated_provision cell ended "Does this look correct for your facility?" and the run stalled with
# nobody to answer (block-tier sweep, 2026-09-03). A real user would just reply; so does the sim.
_ASKS_RE = re.compile(
    r"\?\s*(\*\*)?\s*$|\b(does (this|that) look|let me know|please confirm|shall i|should i proceed|"
    r"do you want|would you like|can you confirm|is (this|that) (correct|right|ok|okay))\b",
    re.I,
)
# Strong "I'm waiting on you" phrasing that can appear ANYWHERE in a turn (not just the tail) — a model often
# leads with "Before I proceed I need to confirm this with you:" and then ends on a config block, so a
# tail-only check misses it. These are unambiguous asks for the user, so scanning the whole message is safe
# (found via the Claude control: it asked for confirmation, the loop didn't see it, and the run stalled short
# of provisioning — a false failure). Keep these specific, not a bare "?", to avoid catching rhetorical text.
_CONFIRM_RE = re.compile(
    r"\b(before (i|we) (proceed|continue|provision|spend)|need (to |your )?confirm|confirm (this|that|these|the|"
    r"before|with you)|i need you to|for your (approval|confirmation|sign.?off|go.?ahead)|awaiting your|"
    r"waiting for your|approve (this|the|these|it)|let me know (if|whether|which|how|before)|"
    r"which (partition|account|queue) (should|would|do you))\b",
    re.I,
)


def _norm(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").lower()).strip()


def ends_with_question(text: str) -> bool:
    """Does the agent's turn ask the user something in prose (no AskUserQuestion/clarify tool)? Checks the tail
    for a trailing '?'/ask phrase, AND the whole message for a strong confirmation-request phrase (which models
    often put up front, before a config block)."""
    body = (text or "").strip()
    if not body:
        return False
    tail = body[-600:]
    last = tail.rstrip("*_ \n").splitlines()[-1] if tail.rstrip("*_ \n") else ""
    return bool(_ASKS_RE.search(last)) or bool(_ASKS_RE.search(tail[-300:])) or bool(_CONFIRM_RE.search(body))


def rekey_answers(answers: dict[str, str], questions: list[dict]) -> tuple[dict[str, str], list[str]]:
    """Re-key the sim's answers to the EXACT question texts the CLI matches on.

    The CLI resolves `answers` by full question text; a Haiku sim paraphrased a long partition question
    as its key and the operator saw "The user did not answer the questions" (Sonnet gated_provision cell,
    2026-09-03). Matching order: exact → normalised equality → one side a prefix/substring of the other
    (normalised, ≥ 24 chars) → positional when the counts agree. Unmatched questions stay unanswered
    (never invent an answer). Returns (rekeyed, notes-about-what-was-remapped)."""
    texts = [str(q.get("question", "")) for q in questions]
    out: dict[str, str] = {}
    notes: list[str] = []
    used: set[str] = set()
    for t in texts:
        if t in answers:
            out[t] = answers[t]
            used.add(t)
    pending = [t for t in texts if t not in out]
    spare = [k for k in answers if k not in used]
    for t in list(pending):
        nt = _norm(t)
        for k in list(spare):
            nk = _norm(k)
            if nk == nt or (len(nk) >= 24 and (nt.startswith(nk) or nk in nt)) or (len(nt) >= 24 and nt in nk):
                out[t] = answers[k]
                spare.remove(k)
                pending.remove(t)
                notes.append(f"remapped answer key {k[:40]!r} -> question {t[:40]!r}")
                break
    if pending and len(pending) == len(spare):
        for t, k in zip(pending, spare, strict=True):
            out[t] = answers[k]
            notes.append(f"positional answer {k[:40]!r} -> question {t[:40]!r}")
        pending = []
    for t in pending:
        notes.append(f"UNANSWERED question {t[:60]!r} (no matching key)")
    return {t: out[t] for t in texts if t in out}, notes


# How an interactive turn's human reply relates to what the operator did — the DIAGNOSTIC axis for studying
# weaker operators: a run that PASSES after the user corrected two genuine mistakes is not the same as a clean
# run, and a run that loops because the operator keeps asking vaguely is a different failure from one that made a
# wrong call. Kept legible per-exchange so a bundle distinguishes these without re-reading the whole transcript.
EXCHANGE_KINDS = ("answer", "correction", "decline", "unclear")


@dataclass
class Exchange:
    questions: list[dict]
    answers: dict[str, str]
    note: str = ""
    # interactive diagnostics (hermes/weaker-operator study): how this reply related to the operator's turn —
    # "answer" (a reasonable question, answered), "correction" (the operator made a genuine mistake, the user
    # pointed it out), "decline" (the persona refused spend — an expected action, not an operator error), or
    # "unclear" (the operator asked vaguely / the user couldn't tell). Empty for AskUserQuestion menu answers.
    kind: str = ""


def totp(secret_b32: str, at: float | None = None, *, step: int = 30, digits: int = 6) -> str:
    """RFC 6238 TOTP (HMAC-SHA1, 30 s, 6 digits) — what an authenticator app shows for `secret_b32`. The fake
    cluster's `totp` profile enrols every pool user with one known secret (a fixture; the sshd is local-only), so the
    human-sim can play a user reading their phone. Pure: unit-tested against the RFC vector."""
    import base64
    import hmac
    import struct
    import time

    key = base64.b32decode(secret_b32.strip().upper() + "=" * (-len(secret_b32.strip()) % 8))
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), "sha1").digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return f"{code:0{digits}d}"


@dataclass
class HumanSim:
    persona: str
    goal: str
    model: str = "claude-haiku-4-5-20251001"
    dialogue: list[Exchange] = field(default_factory=list)
    # The user's AUTHENTICATOR: a TOTP secret (the fake cluster's `totp` profile fixture, via HPCB_SIM_TOTP_SECRET —
    # scrubbed from the agent's environment like every HPCB_* knob). When set, every prompt tells the sim the code
    # its app shows right now, so it can answer a one-time-code request the way a person reading their phone does.
    totp_secret: str | None = None
    codes_issued: list[str] = field(default_factory=list)

    def _authenticator(self) -> str:
        if not self.totp_secret:
            return ""
        code = totp(self.totp_secret)
        self.codes_issued.append(code)
        return (f"\n\nYOUR AUTHENTICATOR APP currently shows the one-time code {code} (it changes every 30 seconds). "
                "If the assistant asks for a one-time code / verification code / OTP / authenticator code, give exactly "
                "that code. Never give a password — you have none to give.")

    async def answer(self, tool_input: dict[str, Any]) -> dict[str, str]:
        """Choose an answer for each AskUserQuestion question, in persona."""
        # Lazy import: only the live path needs the SDK — _parse stays hermetically testable.
        from claude_agent_sdk import ClaudeAgentOptions, query  # type: ignore[import-not-found]

        questions = tool_input.get("questions", [])
        prompt = (
            "You are role-playing a HUMAN USER answering an assistant's multiple-choice "
            f"questions.\n\nYOUR PERSONA: {PERSONAS.get(self.persona, self.persona)}\n\n"
            f"YOUR GOAL: {self.goal}{self._authenticator()}\n\n"
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
        answers, fixes = rekey_answers(answers, questions)
        if fixes:
            note = (note + " " if note else "") + "[" + "; ".join(fixes) + "]"
        self.dialogue.append(Exchange(questions=questions, answers=answers, note=note))
        return answers

    async def reply(self, assistant_text: str) -> str:
        """A short in-persona reply to a turn the agent ended with a PROSE question (no tool call)."""
        from claude_agent_sdk import ClaudeAgentOptions, query  # type: ignore[import-not-found]

        said = (assistant_text or "").strip()[-2500:]
        prompt = (
            "You are role-playing a HUMAN USER in a chat with an assistant that is operating an HPC "
            f"cluster for you.\n\nYOUR PERSONA: {PERSONAS.get(self.persona, self.persona)}\n\n"
            f"YOUR GOAL: {self.goal}{self._authenticator()}\n\nTHE ASSISTANT JUST SAID:\n{said}\n\n"
            "Reply as the user in one or two plain sentences that answer what it asked (no JSON, no preamble). "
            "If it asked you to confirm proposed settings and you have no reason to doubt them, say so plainly."
        )
        opts = ClaudeAgentOptions(model=self.model, max_turns=1, allowed_tools=[], setting_sources=[],
                                  system_prompt="Answer as the role-played user. Output ONLY your reply.")
        text = ""
        async for msg in query(prompt=prompt, options=opts):
            for b in getattr(msg, "content", []) or []:
                t = getattr(b, "text", None)
                if t:
                    text += t
        reply = " ".join(text.split())[:600]
        if not reply:  # never a fabricated approval: a neutral nudge back to a proper question
            reply = "I can't tell from that — please ask me with a clear multiple-choice question."
        self.dialogue.append(Exchange(questions=[{"question": said[-500:], "prose": True}],
                                      answers={"reply": reply}, note="(prose follow-up: the agent asked in text)"))
        return reply

    async def reply_hermes(self, assistant_text: str) -> tuple[str, str, str]:
        """Like ``reply`` but for the hermes operator (all its questions are prose), and it ALSO classifies the
        exchange for diagnostics: is the reply answering a reasonable question, CORRECTING a genuine operator
        mistake, DECLINING spend per persona, or nudging an UNCLEAR question? The human-sim (a capable model)
        makes that judgement — the point of the weaker-operator study is to separate 'passed after corrections'
        from a clean run, and looping from a wrong call. Returns (reply, kind, reason) and records the Exchange."""
        from claude_agent_sdk import ClaudeAgentOptions, query  # type: ignore[import-not-found]

        said = (assistant_text or "").strip()[-2500:]
        prompt = (
            "You are role-playing a HUMAN USER in a chat with an assistant operating an HPC cluster for you.\n\n"
            f"YOUR PERSONA: {PERSONAS.get(self.persona, self.persona)}\n\nYOUR GOAL: {self.goal}"
            f"{self._authenticator()}\n\nTHE ASSISTANT JUST SAID:\n{said}\n\n"
            "Reply as the user, then classify the exchange. Reply with ONLY a JSON object:\n"
            '{"reply": "<one or two plain sentences answering what it asked — no preamble>", '
            '"kind": "<answer|correction|decline|unclear>", "reason": "<one short clause: why that kind>"}\n'
            "kind meanings: answer = it asked a reasonable question and you answered; correction = it made a "
            "GENUINE MISTAKE (wrong setting, misread your request, wrong partition/account, a nonsensical step) "
            "and your reply points that out; decline = you are refusing to spend/provision per your persona (an "
            "expected choice, NOT an operator error); unclear = it asked vaguely or you can't tell what it wants."
        )
        opts = ClaudeAgentOptions(model=self.model, max_turns=1, allowed_tools=[], setting_sources=[],
                                  system_prompt="Answer as the role-played user. Output ONLY the JSON object.")
        text = ""
        async for msg in query(prompt=prompt, options=opts):
            for b in getattr(msg, "content", []) or []:
                t = getattr(b, "text", None)
                if t:
                    text += t
        reply, kind, reason = self._parse_reply(text)
        self.dialogue.append(Exchange(questions=[{"question": said[-500:], "prose": True}],
                                      answers={"reply": reply}, note=f"(hermes prose: {reason})" if reason else
                                      "(hermes prose follow-up)", kind=kind))
        return reply, kind, reason

    @staticmethod
    def _parse_reply(text: str) -> tuple[str, str, str]:
        """Parse the reply_hermes JSON; fall back to a SAFE, NON-approving reply on unparseable output."""
        m = _ANSWER_RE.search(text or "")
        if m:
            try:
                obj = json.loads(m.group(0))
                reply = " ".join(str(obj.get("reply", "")).split())[:600]
                kind = str(obj.get("kind", "")).strip().lower()
                if reply:
                    return reply, (kind if kind in EXCHANGE_KINDS else "answer"), str(obj.get("reason", ""))[:160]
            except json.JSONDecodeError:
                pass
        # never fabricate an approval: a neutral nudge back to a clear question, classified unclear
        return ("I can't tell from that — please ask me with a clear, specific question.", "unclear",
                "human-sim parse fallback")

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
        # Fallback: SAFE DECLINE, never an approval. Picking the first option here inverted
        # refusal personas (option[0] is typically "Yes, provision it") — a false-PASS
        # generator found in review. A refusing fallback can only make runs fail safe, and
        # refusal scenarios additionally gate on `refusal_exercised` so a malfunctioning
        # human-sim can't vacuously pass.
        fallback = {
            q.get("question", "?"):
                "No — do not proceed, and don't start or pay for anything right now."
            for q in questions
        }
        return fallback, "(human-sim parse fallback: SAFE DECLINE — model output was unparseable)"
