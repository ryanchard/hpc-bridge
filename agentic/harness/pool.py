"""Cross-process claims on the harness' POOL of cluster test users.

Why this exists (diagnosed 2026-09-03, the "block-thrashing bug" that wasn't): `run_suite` handed
out pool users from an in-process queue starting at `hpcbridge-test-00`, so two concurrent
invocations BOTH ran as test-00 — and the harness teardown cancelled that user's jobs, so one run
tore down the other's live blocks mid-task. A pool user is a shared cluster identity; claiming one
must be coordinated across every harness process on this host.

A claim is an exclusive `flock` on a per-user lock file, held for exactly as long as the user is in
use. The kernel drops the lock when the holding process exits — crash, SIGKILL, rate-limit halt —
so a dead run can never leave a stale claim behind (unlike a PID file). Host-local by nature: two
hosts driving the same pool still need to split it by hand (`--pool` / `HPCB_POOL`).
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CLAIMS_DIR = Path(
    os.environ.get("HPCB_POOL_CLAIMS_DIR", str(REPO_ROOT / "agentic" / "runs" / ".pool-claims"))
)


class PoolClaims:
    """Claim/release pool users with per-user flock files under `claims_dir` (shared on this host)."""

    def __init__(self, claims_dir: Path | str | None = None) -> None:
        self.dir = Path(claims_dir or DEFAULT_CLAIMS_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._held: dict[str, int] = {}  # user -> fd holding the lock

    def try_claim(self, user: str) -> bool:
        """Claim `user` if no process (this one included) holds it. Non-blocking."""
        if user in self._held:
            return False
        fd = os.open(self.dir / f"{user}.lock", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:  # held by another open file description — another process or another claimant
            os.close(fd)
            return False
        self._held[user] = fd
        return True

    def claim_any(self, pool: list[str]) -> str | None:
        """The first free user in `pool` order, claimed — or None if every one is taken."""
        for user in pool:
            if self.try_claim(user):
                return user
        return None

    def release(self, user: str) -> None:
        fd = self._held.pop(user, None)
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def release_all(self) -> None:
        for user in list(self._held):
            self.release(user)

    def busy(self, pool: list[str]) -> list[str]:
        """Users currently claimed by ANY process (probe: try-lock then release) — for messages."""
        out = []
        for user in pool:
            if user in self._held:
                out.append(user)
                continue
            fd = os.open(self.dir / f"{user}.lock", os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                out.append(user)
            finally:
                os.close(fd)
        return out
