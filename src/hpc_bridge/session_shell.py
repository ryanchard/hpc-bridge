from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class Session:
    """A persistent shell session backed by cwd/env files on the shared filesystem.

    `root` is a directory the worker can read/write (e.g. $SCRATCH on HPC, or
    ~/.hpc-bridge locally). Each session keeps its working directory and exported
    environment in `<root>/sessions/<session_id>/{.cwd,.env}` so they survive the
    fresh-subprocess model of Globus Compute ShellFunction.
    """

    session_id: str
    root: str

    @property
    def state_dir(self) -> str:
        return f"{self.root}/sessions/{self.session_id}"


def wrap(command: str, session: Session) -> str:
    """Wrap a command so cwd and env persist across calls (invisible to the model)."""
    sd = shlex.quote(session.state_dir)
    return (
        f"mkdir -p {sd}\n"
        f'cd "$(cat {sd}/.cwd 2>/dev/null)" 2>/dev/null || cd {sd}\n'
        f"[ -f {sd}/.env ] && . {sd}/.env 2>/dev/null\n"
        f"{{ {command}\n}}\n"
        f"__hb_rc=$?\n"
        f"pwd > {sd}/.cwd\n"
        f"export -p > {sd}/.env 2>/dev/null || true\n"
        f"exit $__hb_rc\n"
    )


def reset_command(session: Session) -> str:
    """A command that clears the session's persisted cwd/env (fresh slate)."""
    sd = shlex.quote(session.state_dir)
    return f"rm -f {sd}/.cwd {sd}/.env"
