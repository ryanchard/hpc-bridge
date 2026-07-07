"""Per-run provenance bundle — the durable evidence a graded run leaves behind.

Every run (pass, fail, or crash) writes `agentic/runs/<runid>-<scenario>/`:
- record.json    — resolved config (prompt/persona/goal/model/effort/ablation/git SHA/pool
                   user/endpoint), grading verdicts, cost/usage, redacted env, dialogue.
- messages.jsonl — the COMPLETE SDK message stream, one message per line: assistant text,
                   thinking blocks (as the API returns them — summarized on Opus 4.7+),
                   tool_use inputs, tool_results. The raw material for re-grading and the
                   future LLM-judge — grading can be re-run WITHOUT re-running the agent.
- transcript.md  — human-readable rendering of the same.
- claude-session/ — harvested by entrypoint.sh after the run: the CLI's own native session
                   JSONLs from inside the jail, including the human-sim's separate sessions.

Design: prefer lossy-but-never-failing serialization (a provenance writer that can crash the
run it documents is worse than useless).
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Values of these env vars are secrets; names are still recorded (presence is provenance).
_REDACT = {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}


def _jsonable(obj: Any, depth: int = 0) -> Any:
    if depth > 10:
        return repr(obj)[:500]
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x, depth + 1) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in obj.items()}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        d = {"__type__": type(obj).__name__}
        for f in dataclasses.fields(obj):
            d[f.name] = _jsonable(getattr(obj, f.name), depth + 1)
        return d
    return {"__type__": type(obj).__name__, "repr": repr(obj)[:2000]}


def _safe_env() -> dict[str, str]:
    out = {}
    for k, v in os.environ.items():
        if k.startswith(("HPCB_", "HPC_BRIDGE_")) or k in _REDACT:
            out[k] = "<redacted>" if k in _REDACT else v
    return out


def _block_lines(block: Any) -> list[str]:
    t = getattr(block, "text", None)
    if t is not None:
        return [t.strip()] if t.strip() else []
    th = getattr(block, "thinking", None)
    if th is not None:
        quoted = "\n".join(f"> {ln}" for ln in th.strip().splitlines())
        return [f"> 🧠 **thinking**\n{quoted}"] if th.strip() else []
    if hasattr(block, "name") and hasattr(block, "input"):
        inp = json.dumps(_jsonable(getattr(block, "input", {})), default=str)
        return [f"`🔧 {getattr(block, 'name', '?')}({inp[:600]}{'…' if len(inp) > 600 else ''})`"]
    if hasattr(block, "tool_use_id"):
        content = getattr(block, "content", "")
        if isinstance(content, list):
            content = " ".join(str(getattr(c, "text", c)) for c in content)
        s = str(content).strip()
        return [f"```\n↩ {s[:1500]}{'…' if len(s) > 1500 else ''}\n```"] if s else []
    return []


def _transcript_md(config: dict, messages: list[Any], dialogue: list[Any],
                   grading: list[Any], rc: int | None) -> str:
    lines = [
        f"# {config.get('scenario', 'run')} — {config.get('runid', '')}",
        "",
        f"*{datetime.now(timezone.utc).isoformat(timespec='seconds')} · model "
        f"{config.get('model')} · effort {config.get('effort') or 'default'} · persona "
        f"{config.get('persona') or 'autonomous'}"
        f"{' · ABLATED: skill' if config.get('ablate_skill') else ''} · rc {rc}*",
        "",
        "## Conversation",
        "",
    ]
    for m in messages:
        role = type(m).__name__.replace("Message", "").lower()
        content = getattr(m, "content", None)
        if not isinstance(content, list):
            continue
        chunk: list[str] = []
        for b in content:
            chunk += _block_lines(b)
        if chunk:
            lines.append(f"**{role}:**")
            lines += chunk
            lines.append("")
    if dialogue:
        lines += ["## Human-sim dialogue", ""]
        for x in dialogue:
            for q in getattr(x, "questions", []):
                lines.append(f"- **asked:** {q.get('question', '?')}")
            for k, v in getattr(x, "answers", {}).items():
                lines.append(f"  - **answered:** {v}")
            if getattr(x, "note", ""):
                lines.append(f"  - *user note:* {x.note}")
        lines.append("")
    lines += ["## Grading", ""]
    for r in grading:
        lines.append(f"- [{'PASS' if r.ok else 'FAIL'}] `{r.name}` — {r.detail}")
    return "\n".join(lines) + "\n"


def write_run_record(
    runs_dir: Path,
    *,
    config: dict,
    messages: list[Any],
    dialogue: list[Any],
    grading: list[Any],
    final: Any,
    rc: int | None,
) -> Path | None:
    """Write the bundle; never raises (best-effort provenance must not fail the run)."""
    try:
        d = runs_dir / f"{config.get('runid', 'local')}-{config.get('scenario', 'run')}"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "messages.jsonl").open("w") as fh:
            for m in messages:
                fh.write(json.dumps(_jsonable(m), default=str) + "\n")
        record = {
            "schema": 1,
            "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "config": config,
            "env": _safe_env(),
            "grading": [{"name": r.name, "ok": r.ok, "detail": r.detail} for r in grading],
            "rc": rc,
            "final": {
                "is_error": getattr(final, "is_error", None),
                "total_cost_usd": getattr(final, "total_cost_usd", None),
                "num_turns": getattr(final, "num_turns", None),
                "duration_ms": getattr(final, "duration_ms", None),
                "usage": _jsonable(getattr(final, "usage", None)),
                "session_id": getattr(final, "session_id", None),
            },
            "dialogue": _jsonable(dialogue),
            "n_messages": len(messages),
        }
        (d / "record.json").write_text(json.dumps(record, indent=2, default=str))
        (d / "transcript.md").write_text(
            _transcript_md(config, messages, dialogue, grading, rc)
        )
        return d
    except Exception as exc:  # noqa: BLE001 - provenance must never break the run
        print(f"provenance: write failed (ignored) — {type(exc).__name__}: {exc}",
              file=sys.stderr, flush=True)
        return None
