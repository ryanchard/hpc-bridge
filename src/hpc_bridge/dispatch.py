from __future__ import annotations

from typing import Protocol

from .models import ShellOutcome


class ShellLike(Protocol):
    returncode: int
    stdout: str
    stderr: str


class Runner(Protocol):
    async def run(self, command: str) -> ShellLike: ...


async def execute(command: str, runner: Runner, *, block_state: str = "warm") -> ShellOutcome:
    """Dispatch a shell command through a Runner and shape the structured result."""
    res = await runner.run(command)
    return ShellOutcome(
        phase="complete",
        exit_code=res.returncode,
        stdout=res.stdout,
        stderr_snippet=res.stderr,
        block_state=block_state,
    )
