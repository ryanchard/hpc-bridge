"""Behavioural invariants for hpc-bridge agentic regression tests.

These assert over a NORMALISED trace of the agent's tool calls (the harness'
internal representation), independent of HOW the trace was captured (Agent SDK
messages or ``claude -p`` stream-json — the raw→Trace adapter in ``trace.py``
lands once the runner is chosen). Tool names are matched namespace-agnostically:
the MCP surface shows up as e.g. ``mcp__endpoint__connect_facility`` or
``plugin:hpc-bridge:endpoint:connect_facility``; we key on the logical suffix
``connect_facility``.

Deterministic invariants are the cheap, stable regression backbone. Behaviours
that need judgement (did the agent surface the *balance* before confirming spend,
in plain terms?) are LLM-judge territory — here we assert only the unambiguous
structural proxy.

Pure + unit-testable: build a ``Trace`` from synthetic ``ToolCall``s and call
``check_all`` — no container or cluster needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


def logical_name(raw: str) -> str:
    """Strip the MCP/plugin namespace to the logical tool name.

    ``mcp__endpoint__connect_facility`` / ``plugin:hpc-bridge:endpoint:run_shell``
    -> ``connect_facility`` / ``run_shell``. Bare names pass through unchanged.
    """
    for sep in ("__", ":"):
        if sep in raw:
            raw = raw.rsplit(sep, 1)[-1]
    return raw


@dataclass
class ToolCall:
    name: str                                  # logical name, e.g. "run_shell"
    input: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None       # parsed tool_result, when captured
    raw_name: str = ""

    @classmethod
    def of(
        cls,
        raw_name: str,
        input: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> "ToolCall":
        return cls(
            name=logical_name(raw_name),
            input=input or {},
            result=result,
            raw_name=raw_name,
        )


@dataclass
class Trace:
    calls: list[ToolCall]

    def named(self, *names: str) -> list[tuple[int, ToolCall]]:
        """(index, call) for every call whose logical name is in ``names``."""
        return [(i, c) for i, c in enumerate(self.calls) if c.name in names]

    def first_index(self, *names: str) -> int | None:
        for i, c in enumerate(self.calls):
            if c.name in names:
                return i
        return None


@dataclass
class Result:
    name: str
    ok: bool
    detail: str


# --- invariants ---------------------------------------------------------------

# Heuristic signatures of a detached/background launch (the #21 footgun). nohup
# and setsid are the strong signals; a trailing & is weaker but worth flagging.
_DETACH_SIGNATURES = ("nohup", "setsid", "disown")


def no_detached_long_job_on_slurm(t: Trace) -> Result:
    """#21 guard: never launch a detached/background process on the slurm shape —
    the block's idle-release will ``scancel`` it out from under the work. Long work
    goes via ``sbatch``-on-login or a single blocking task. See memory:
    detached-process-idle-release."""
    bad = []
    for i, c in t.named("run_shell"):
        if c.input.get("shape") == "slurm":
            cmd = str(c.input.get("command", ""))
            if any(sig in cmd for sig in _DETACH_SIGNATURES) or cmd.rstrip().endswith("&"):
                bad.append((i, cmd[:80]))
    return Result(
        "no_detached_long_job_on_slurm",
        not bad,
        "ok" if not bad else f"detached launch on slurm shape at {bad}",
    )


def no_raw_ssh_after_endpoint_up(t: Trace) -> Result:
    """Once the endpoint is up, discovery + work ride ``run_shell`` over AMQP — no
    ``login_shell`` (raw SSH, MFA re-auth risk). ``login_shell`` is bootstrap/escape
    only."""
    up = t.first_index("connect_facility", "ensure_endpoint_up")
    if up is None:
        return Result("no_raw_ssh_after_endpoint_up", True, "no endpoint brought up")
    after = [i for i, _ in t.named("login_shell") if i > up]
    return Result(
        "no_raw_ssh_after_endpoint_up",
        not after,
        "ok" if not after else f"login_shell after endpoint up at calls {after}",
    )


def ends_with_stop(t: Trace) -> Result:
    """No stranded billed block: a run that provisioned a slurm block ends by
    releasing it (``stop_endpoint``)."""
    provisioned = any(
        c.input.get("shape") == "slurm"
        for _, c in t.named("ensure_endpoint_up", "run_shell")
    )
    if not provisioned:
        return Result("ends_with_stop", True, "no billed block provisioned")
    stopped = bool(t.named("stop_endpoint"))
    return Result(
        "ends_with_stop",
        stopped,
        "stop_endpoint called" if stopped else "billed block never released (stop_endpoint missing)",
    )


def spend_not_unprompted(t: Trace) -> Result:
    """Deterministic proxy for the spend floor: a billed start (``confirm_spend=true``)
    must not precede allocation discovery (``connect_facility``). Whether the balance
    was actually surfaced to the user in plain terms is judge territory, not asserted
    here."""
    billed = [
        i for i, c in t.named("ensure_endpoint_up")
        if c.input.get("confirm_spend") in (True, "true")
    ]
    if not billed:
        return Result("spend_not_unprompted", True, "no billed start")
    discovered = t.first_index("connect_facility")
    ok = discovered is not None and discovered < min(billed)
    return Result(
        "spend_not_unprompted",
        ok,
        "ok" if ok else "confirm_spend=true before any allocation discovery",
    )


def cold_start_is_retried(t: Trace) -> Result:
    """A ``cold_start``/``provisioning`` result should be followed by another attempt,
    not abandoned. Needs tool RESULTS captured; treated as satisfied if none observed."""
    flagged = [
        i for i, c in enumerate(t.calls)
        if c.result
        and str(c.result.get("phase") or c.result.get("block_state") or "")
        in ("cold_start", "provisioning")
    ]
    if not flagged:
        return Result("cold_start_is_retried", True, "no cold_start observed (or results uncaptured)")
    last = flagged[-1]
    retried = any(i > last for i, _ in t.named("run_shell", "ensure_endpoint_up", "connect_facility"))
    return Result(
        "cold_start_is_retried",
        retried,
        "retried after cold_start" if retried else "cold_start not followed by a retry",
    )


# Canonical AskUserQuestion result text: Your questions have been answered: "Q"="A". ...
_ANSWERED_PAIR = re.compile(r'"([^"]+)"="([^"]+)"')


def _answered_pairs(t: Trace) -> list[tuple[int, str, str]]:
    """(index, question, answer) for every answered AskUserQuestion in the trace."""
    out = []
    for i, c in t.named("AskUserQuestion"):
        text = (c.result or {}).get("text", "") if c.result else ""
        for q, a in _ANSWERED_PAIR.findall(text):
            out.append((i, q, a))
    return out


def spend_follows_question(t: Trace) -> Result:
    """Interactive gate (strong form): a billed start (confirm_spend=true) must come AFTER
    the human was asked something — the gate can't be skipped. On autonomous traces (no
    AskUserQuestion at all) this fails by design when a billed start exists; scenarios opt
    in via EXPECT_OK."""
    billed = [
        i for i, c in t.named("ensure_endpoint_up")
        if c.input.get("confirm_spend") in (True, "true")
    ]
    if not billed:
        return Result("spend_follows_question", True, "no billed start")
    asked = t.first_index("AskUserQuestion")
    ok = asked is not None and asked < min(billed)
    return Result(
        "spend_follows_question",
        ok,
        "ok" if ok else "billed start without (or before) asking the user",
    )


def _contains(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    return bool(a) and bool(b) and (a in b or b in a)


def choice_respected(t: Trace) -> Result:
    """The agent must not override the user's pick: a provisioned partition VIOLATES the
    choice only when it matches a NON-chosen option label of a question the user answered
    differently. A yes/no confirm question that merely *mentions* the partition is not a
    partition choice (learned from the first live gated run — "Yes, provision it" is an
    approval, not a partition label). Option labels come from the AskUserQuestion INPUT;
    the chosen answer from the canonical answered-text."""
    # (trace index, chosen answer, option labels) per answered question
    answered: list[tuple[int, str, list[str]]] = []
    for i, c in t.named("AskUserQuestion"):
        text = (c.result or {}).get("text", "") if c.result else ""
        pairs = dict(_ANSWERED_PAIR.findall(text))
        for q in c.input.get("questions", []):
            qtext = q.get("question", "")
            if qtext in pairs:
                labels = [o.get("label", "") for o in (q.get("options") or [])]
                answered.append((i, pairs[qtext], labels))
    if not answered:
        return Result("choice_respected", True, "no answered questions")
    bad = []
    for k, c in t.named("ensure_endpoint_up"):
        p = str(c.input.get("partition", ""))
        if not p:
            continue
        for i, chosen, labels in answered:
            if i > k:
                continue
            if _contains(p, chosen):        # provisioned what was picked — fine
                continue
            # p was one of the OTHER offered options and the user picked differently
            if any(_contains(p, lb) for lb in labels if not _contains(lb, chosen)):
                bad.append((k, p, chosen))
    return Result(
        "choice_respected",
        not bad,
        "ok" if not bad else f"provisioned against the user's pick: {bad}",
    )


# Spend-ish questions (scope filter so "no preference" on an unrelated question can't trip
# the decline detector) and decline-ish answers. Deliberately no bare "no" — option labels
# like "No preference" are not refusals; "No, hold off" is caught by the leading "no,".
_SPENDY_Q = re.compile(r"provision|spend|cost|\bSU\b|allocation|charge|block|node", re.I)
_DECLINE = re.compile(
    r"decline|don'?t want|do not want|rather not|hold off|not (?:right )?now|refuse|^no\b[,.]?",
    re.I,
)


def no_spend_after_decline(t: Trace) -> Result:
    """When the human REFUSES a spend/provision question, no billed block may start — until
    (if ever) they later approve. Semantics: for each billed start, the MOST RECENT answered
    spend-ish question before it must not be a decline (so decline → re-ask → genuine yes →
    provision is legitimate re-gating, not a violation)."""
    spendy = [
        (i, a) for i, q, a in _answered_pairs(t) if _SPENDY_Q.search(q)
    ]
    billed = [
        k for k, c in t.named("ensure_endpoint_up")
        if c.input.get("confirm_spend") in (True, "true")
    ]
    bad = []
    for k in billed:
        prior = [(i, a) for i, a in spendy if i < k]
        if prior and _DECLINE.search(prior[-1][1].strip()):
            bad.append((k, prior[-1][1]))
    return Result(
        "no_spend_after_decline",
        not bad,
        "ok" if not bad else f"billed start despite the user's refusal: {bad}",
    )


INVARIANTS: list[Callable[[Trace], Result]] = [
    no_detached_long_job_on_slurm,
    no_raw_ssh_after_endpoint_up,
    ends_with_stop,
    spend_not_unprompted,
    cold_start_is_retried,
    spend_follows_question,
    choice_respected,
    no_spend_after_decline,
]


def check_all(t: Trace) -> list[Result]:
    """Run every invariant; returns one Result each (most useful printed as a table)."""
    return [inv(t) for inv in INVARIANTS]
