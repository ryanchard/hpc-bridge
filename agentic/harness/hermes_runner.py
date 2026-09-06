"""Operator: drive a hermes-agent run over hpc-bridge (autonomous), capture the Trace from its state.db.

Signature-compatible with ``runner.run_scenario`` so ``run.py`` can dispatch to either operator with the
same downstream grading. hermes runs the scenario prompt one-shot (``hermes -z``) against the hpc-bridge
MCP server, using an ALCF-hosted model (OpenAI-compatible). Guidance is delivered over MCP (the server's
instructions pointer + the ``hpcbridge://guidance/operations`` resource) — NOT injected as a system
prompt — so this is a faithful cross-harness test of the guidance-over-MCP design; ``ablate_skill``
suppresses that guidance (HPC_BRIDGE_OMIT_INSTRUCTIONS) to measure its value.

Interactive personas and mid-run chaos hooks are not supported here yet (autonomous scenarios only);
run.py refuses those combinations for this operator rather than grading them vacuously. The trace comes
from hermes' own ``state.db`` (see hermes_trace) — a hermes-specific tap; the harness-agnostic MCP-boundary
tap is a later refinement.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import hermes_setup
from hermes_trace import load_messages, trace_from_messages
from runner import HPC_BRIDGE_TOOLS, RunResult  # shared dataclass + the logical hpc-bridge tool names

_HERMES_BIN = "hermes"
# Harness plumbing the agent's shell has no business seeing (parity with runner._SCRUB). ALCF_INFERENCE_TOKEN is
# KEPT: hermes interpolates it for model auth, exactly as the Claude runner keeps CLAUDE_CODE_OAUTH_TOKEN in the env.
_SCRUB_PREFIXES = ("HPCB_",)
_SCRUB_KEYS = ("PYTHONPATH",)
_HPC_BRIDGE_LOGICAL = frozenset(HPC_BRIDGE_TOOLS)


@dataclass
class HermesFinal:
    """Stand-in `final` (the SDK ResultMessage's role) for a hermes run — run.py reads is_error / result /
    total_cost_usd via getattr(default=None). is_error means the RUN didn't complete (non-zero exit or a
    timeout); whether it did the RIGHT thing is the graders' job (agent_engaged, the scenario invariants)."""
    result: str
    is_error: bool = False
    total_cost_usd: float | None = None
    session_id: str | None = None
    api_error_status: int | None = None


def _lead(ablate_skill: bool) -> str:
    """The autonomous framing prepended to the scenario prompt — mirrors runner._system_prompt's lead. The
    skill itself is NOT injected (hermes reads it over MCP); we only nudge the model to consult it."""
    lead = (
        "You are driving real HPC through the hpc-bridge tools in an automated test. "
        "There is no human to answer follow-up questions — act on the instructions you are given. "
    )
    if not ablate_skill:
        lead += (
            "Operational guidance for using these tools well is offered by the hpc-bridge server as an MCP "
            "resource — read it before you connect, provision or spend. "
        )
    return lead


async def run_scenario(
    prompt: str,
    *,
    repo_root: Path,
    model: str = "openai/gpt-oss-120b",
    effort: str | None = None,
    persona: str | None = None,
    user_goal: str = "",
    ablate_skill: bool = False,
    max_turns: int = 40,
    max_budget_usd: float = 2.0,
    extra_env: dict[str, str] | None = None,
    midrun_hooks: list[dict] | None = None,
    hook_runner=None,
) -> RunResult:
    if persona is not None:
        raise NotImplementedError("the hermes operator is autonomous-only for now (no interactive personas)")
    if midrun_hooks:
        raise NotImplementedError("the hermes operator does not support mid-run chaos hooks yet")
    if not os.environ.get("ALCF_INFERENCE_TOKEN"):
        raise RuntimeError("hermes operator: ALCF_INFERENCE_TOKEN is unset — run_smoke.sh must mint + pass it")

    # Per-run hermes home (a fresh state.db, co-located under the run dir so the bundle path is predictable).
    home = Path(os.environ.get("HERMES_HOME", "").strip()
                or (Path(os.environ.get("HPC_BRIDGE_USER_DIR", str(Path.home()))) / "hermes"))
    os.environ["HERMES_HOME"] = str(home)   # hermes_setup.hermes_home() + the child both read this
    server_extra = dict(extra_env or {})
    if ablate_skill:
        server_extra["HPC_BRIDGE_OMIT_INSTRUCTIONS"] = "1"   # suppress the guidance pointer for the ablation
    cfg_path = hermes_setup.write_config(server_extra)

    full_prompt = _lead(ablate_skill) + "\n\n" + prompt

    # Scrub harness plumbing from the child's env; keep the ALCF token (model auth), HOME, PATH, HERMES_HOME.
    child_env = {k: v for k, v in os.environ.items()
                 if not k.startswith(_SCRUB_PREFIXES) and k not in _SCRUB_KEYS}
    child_env["HERMES_HOME"] = str(home)
    if "ALCF_INFERENCE_TOKEN" in os.environ:
        child_env["ALCF_INFERENCE_TOKEN"] = os.environ["ALCF_INFERENCE_TOKEN"]

    print(f"  hermes: model={os.environ.get('HPCB_ALCF_MODEL', model)} home={home} config={cfg_path}",
          file=sys.stderr, flush=True)
    timeout_s = max(180, min(900, max_turns * 25))
    t0 = time.time()
    rc, timed_out, out, err = 1, False, "", ""
    proc = await asyncio.create_subprocess_exec(
        _HERMES_BIN, "-z", full_prompt, env=child_env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        rc, out, err = proc.returncode, out_b.decode(errors="replace"), err_b.decode(errors="replace")
    except TimeoutError:
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        rc, timed_out = 124, True
    except asyncio.CancelledError:
        # `docker stop` -> SIGTERM -> run.py cancels the task: kill hermes so run.py's finally (teardown+bundle) runs.
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise
    print(f"  hermes: rc={rc} ({time.time() - t0:.0f}s){' TIMEOUT' if timed_out else ''}", file=sys.stderr, flush=True)

    db = home / "state.db"
    rows = load_messages(db) if db.is_file() else []
    trace = trace_from_messages(rows)
    engaged = any(c.name in _HPC_BRIDGE_LOGICAL for c in trace.calls)
    answer = trace.texts[-1] if trace.texts else (out or err or "").strip()[-2000:]
    if not engaged and not answer:
        answer = "[hermes operator: no hpc-bridge tool called and no assistant text]"
    final = HermesFinal(
        result=answer,
        is_error=(rc != 0) or timed_out,
        session_id=(rows[0].get("session_id") if rows else None),
    )
    return RunResult(trace=trace, final=final, messages=rows)
