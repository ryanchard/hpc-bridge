from __future__ import annotations

import base64
import re
import shlex
from dataclasses import dataclass

_VALID_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Scheduler/runtime-injected vars that must never be carried across calls: they belong to
# the *current* allocation, so persisting them would freeze a past job/node and replay it
# (e.g. `$SLURM_JOB_ID` reporting a stale block). Matched against `export -p` output in both
# bash (`declare -x NAME=`) and POSIX-sh (`export NAME=`) forms. Belt-and-suspenders next to
# the ambient-diff below: this also self-heals a `.env` already dirtied by older builds.
# NB: HOSTNAME (scheduler/compute-node injected) is filtered, but NOT bare HOST — that's a
# common user var (e.g. HOST=0.0.0.0 for a dev server) we must let the session persist.
_VOLATILE_EXPORT = (
    r"^(declare -x |export )"
    r"(SLURM[A-Z_]*|HOSTNAME|PBS_[A-Z_]*|PMI_[A-Z_]*|OMPI_[A-Z_]*|FLUX_[A-Z_]*)="
)


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

    Only env vars the command itself set/changed are persisted: we snapshot the
    ambient (scheduler-injected) environment *before* layering the session's own vars,
    persist the diff, then drop known runtime vars (``SLURM_*``/``HOSTNAME``/...) outright.
    Otherwise the first command's values would freeze and be replayed into a later,
    *different* allocation, so ``echo $SLURM_JOB_ID`` would report a stale node/job. The
    explicit filter also self-heals a ``.env`` already dirtied by an older build (where the
    ambient diff alone would re-persist the reintroduced stale value).
    """
    sd = shlex.quote(session.state_dir)
    b64 = shlex.quote(base64.b64encode(command.encode()).decode())
    volatile = shlex.quote(_VOLATILE_EXPORT)
    return (
        "umask 077\n"
        f"mkdir -p {sd}\n"
        f"__hb_cwd=$(cat {sd}/.cwd 2>/dev/null)\n"
        f'if [ -n "$__hb_cwd" ]; then cd "$__hb_cwd" 2>/dev/null || cd {sd}; else cd {sd}; fi\n'
        # Ambient baseline BEFORE the session env is layered on (PID-suffixed so
        # concurrent commands in one session don't clobber each other's snapshot).
        f'__hb_base="{sd}/.env.base.$$"\n'
        f'export -p > "$__hb_base"\n'
        f"[ -f {sd}/.env ] && . {sd}/.env 2>/dev/null\n"
        f"eval \"$(printf %s {b64} | base64 -d)\"\n"
        f"__hb_rc=$?\n"
        f"pwd > {sd}/.cwd\n"
        # Persist only exports that DIFFER from the ambient baseline (what the command
        # added/changed), then drop scheduler runtime vars outright so a stale .env can't
        # replay them into a new allocation.
        f'export -p | grep -vxF -f "$__hb_base" | grep -vE {volatile} > {sd}/.env 2>/dev/null || true\n'
        f'rm -f "$__hb_base"\n'
        f"exit $__hb_rc\n"
    )


def reset_command(session: Session) -> str:
    """A command that clears the session's persisted cwd/env (fresh slate)."""
    sd = shlex.quote(session.state_dir)
    # also sweep any .env.base.* snapshot a command leaked by exiting mid-wrapper
    return f"rm -f {sd}/.cwd {sd}/.env {sd}/.env.base.*"
