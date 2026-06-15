from __future__ import annotations

import base64
import re
import shlex
from dataclasses import dataclass

_VALID_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Scheduler/runtime-injected vars that must never be carried across calls: they belong to
# the *current* allocation, so persisting them would freeze a past job/node and replay it
# (e.g. `$SLURM_JOB_ID` reporting a stale block). Expressed as a bash `case` glob over
# variable NAMES (applied in wrap() below). Belt-and-suspenders next to the ambient-diff:
# this also self-heals a `.env` already dirtied by an older build.
# NB: HOSTNAME (scheduler/compute-node injected) is filtered, but NOT bare HOST — that's a
# common user var (e.g. HOST=0.0.0.0 for a dev server) we must let the session persist.
_VOLATILE_NAME_GLOBS = "SLURM*|HOSTNAME|PBS_*|PMI_*|OMPI_*|FLUX_*"


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


_WRAP_TEMPLATE = r"""umask 077
mkdir -p @@SD@@
__hb_cwd=$(cat @@SD@@/.cwd 2>/dev/null)
if [ -n "$__hb_cwd" ]; then cd "$__hb_cwd" 2>/dev/null || cd @@SD@@; else cd @@SD@@; fi
# Fingerprint each exported var as ONE line "NAME=<base64 value>" so the diff below is
# record-safe: a line-oriented diff on raw `export -p` corrupts multi-line values (a mutated
# multi-line var leaves an orphan line that breaks the next `. .env`, silently dropping the
# whole session env). base64 keeps any value on a single line.
__hb_snap() {
  local __hb_k
  for __hb_k in $(compgen -A export); do
    printf '%s=%s\n' "$__hb_k" "$(printf %s "${!__hb_k}" | base64 | tr -d '\n')"
  done
}
__hb_base="@@SD@@/.env.base.$$"
__hb_snap > "$__hb_base"
[ -f @@SD@@/.env ] && . @@SD@@/.env 2>/dev/null
eval "$(printf %s @@B64@@ | base64 -d)"
__hb_rc=$?
pwd > @@SD@@/.cwd
# Persist only vars the command added/changed vs the ambient baseline, dropping scheduler
# runtime vars (@@VOLATILE@@). `printf %q` keeps each record single-line and re-sourceable
# even for multi-line values, so the persisted .env can never corrupt the next source.
{
  for __hb_n in $(compgen -A export); do
    case "$__hb_n" in @@VOLATILE@@) continue;; esac
    __hb_v="$(printf %s "${!__hb_n}" | base64 | tr -d '\n')"
    grep -qxF "$__hb_n=$__hb_v" "$__hb_base" && continue
    printf 'export %s=%q\n' "$__hb_n" "${!__hb_n}"
  done
} > @@SD@@/.env 2>/dev/null || true
rm -f "$__hb_base"
exit $__hb_rc
"""


def wrap(command: str, session: Session) -> str:
    """Wrap a command so cwd and env persist across calls (invisible to the model).

    The command is base64-encoded and decoded+`eval`'d in the *current* shell, so
    arbitrary shell (brace groups, quotes, ``${VAR}``) cannot textually break out of
    the wrapper, while ``cd``/``export`` inside it still affect the persisted cwd/env.

    Only env vars the command itself set/changed are persisted: we fingerprint the ambient
    (scheduler-injected) environment *before* layering the session's own vars, persist only
    what differs, and drop known runtime vars (``SLURM_*``/``HOSTNAME``/...). Otherwise the
    first command's values would freeze and be replayed into a later, *different* allocation,
    so ``echo $SLURM_JOB_ID`` would report a stale node/job. Fingerprinting per-var (not a
    line diff) and emitting with ``printf %q`` make this correct for multi-line values too.
    Runs under bash (Globus Compute ShellFunction executes via /bin/bash).
    """
    sd = shlex.quote(session.state_dir)
    b64 = shlex.quote(base64.b64encode(command.encode()).decode())
    return (
        _WRAP_TEMPLATE.replace("@@SD@@", sd)
        .replace("@@B64@@", b64)
        .replace("@@VOLATILE@@", _VOLATILE_NAME_GLOBS)
    )


def reset_command(session: Session) -> str:
    """A command that clears the session's persisted cwd/env (fresh slate)."""
    sd = shlex.quote(session.state_dir)
    # also sweep any .env.base.* snapshot a command leaked by exiting mid-wrapper
    return f"rm -f {sd}/.cwd {sd}/.env {sd}/.env.base.*"
