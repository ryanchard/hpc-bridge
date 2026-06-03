from __future__ import annotations

import base64
import re
import shlex
from dataclasses import dataclass

_VALID_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class Session:
    """A persistent shell session backed by cwd/env files on the shared filesystem.

    `root` is a directory the worker can read/write (e.g. $SCRATCH on HPC, or
    ~/.hpc-bridge locally). Each session keeps its working directory and exported
    environment in `<root>/sessions/<session_id>/{.cwd,.env}` so they survive the
    fresh-subprocess model of Globus Compute ShellFunction.

    `session_id` comes from an untrusted MCP parameter, so it is validated against a
    strict allowlist to prevent path traversal (`../`) and shell-metacharacter abuse.
    """

    session_id: str
    root: str

    def __post_init__(self) -> None:
        if not _VALID_SESSION_ID.match(self.session_id):
            raise ValueError(
                f"invalid session_id {self.session_id!r}: must match [A-Za-z0-9_-]{{1,64}}"
            )

    @property
    def state_dir(self) -> str:
        return f"{self.root}/sessions/{self.session_id}"


def wrap(command: str, session: Session) -> str:
    """Wrap a command so cwd and env persist across calls (invisible to the model).

    The command is base64-encoded and decoded+`eval`'d in the *current* shell, so
    arbitrary shell (brace groups, quotes, ``${VAR}``) cannot textually break out of
    the wrapper, while ``cd``/``export`` inside it still affect the persisted cwd/env.
    """
    sd = shlex.quote(session.state_dir)
    b64 = shlex.quote(base64.b64encode(command.encode()).decode())
    return (
        "umask 077\n"
        f"mkdir -p {sd}\n"
        f"__hb_cwd=$(cat {sd}/.cwd 2>/dev/null)\n"
        f'if [ -n "$__hb_cwd" ]; then cd "$__hb_cwd" 2>/dev/null || cd {sd}; else cd {sd}; fi\n'
        f"[ -f {sd}/.env ] && . {sd}/.env 2>/dev/null\n"
        f"eval \"$(printf %s {b64} | base64 -d)\"\n"
        f"__hb_rc=$?\n"
        f"pwd > {sd}/.cwd\n"
        f"export -p > {sd}/.env 2>/dev/null || true\n"
        f"exit $__hb_rc\n"
    )


def reset_command(session: Session) -> str:
    """A command that clears the session's persisted cwd/env (fresh slate)."""
    sd = shlex.quote(session.state_dir)
    return f"rm -f {sd}/.cwd {sd}/.env"
