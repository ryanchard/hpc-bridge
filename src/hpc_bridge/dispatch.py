from __future__ import annotations

from typing import Protocol

from .cost import cap_output
from .models import ShellOutcome


class ShellLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    async def run(self, command: str) -> ShellLike: ...


async def execute(
    command: str,
    runner: Runner,
    *,
    block_state: str = "warm",
    max_output_chars: int = 1_000_000,
) -> ShellOutcome:
    """Dispatch a shell command through a Runner and shape the structured result."""
    res = await runner.run(command)
    return ShellOutcome(
        phase="complete",
        exit_code=res.returncode,
        stdout=cap_output(res.stdout, max_output_chars),
        stderr_snippet=cap_output(res.stderr, max_output_chars),
        block_state=block_state,
    )
