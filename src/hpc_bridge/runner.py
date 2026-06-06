from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class CanaryResult:
    """Outcome of a worker-registration canary: did a real worker answer, and (best-effort)
    its Python/Dill versions + host, so the caller can flag serialization skew."""

    ok: bool
    worker_python: str | None = None
    worker_dill: str | None = None
    worker_host: str | None = None
    error: str | None = None


# A worker that runs this proves the compute block is live (manager_online cannot). A result
# returning at all = liveness; the version line is parsed best-effort (`|| true` keeps a
# missing python/dill from failing the probe — the worker still answered).
_CANARY_SENTINEL = "HPCB_CANARY"
_CANARY_CMD = (
    f"echo {_CANARY_SENTINEL}; "
    'python -c "import platform,dill,socket;'
    'print(platform.python_version(),dill.__version__,socket.gethostname())" '
    "2>/dev/null || true"
)


def _parse_canary(stdout: str) -> tuple[str | None, str | None, str | None]:
    """Pull (python, dill, host) out of canary stdout; all None when the version line is absent."""
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0][:1].isdigit():  # the "3.11.7 0.3.9 <host>" line
            return parts[0], parts[1], parts[2]
    return None, None, None


def _escape_for_shellfunction(command: str) -> str:
    """ShellFunction runs cmd.format(**kwargs); double literal braces so arbitrary
    shell (brace groups, ${VAR}) survives the format pass as single braces."""
    return command.replace("{", "{{").replace("}", "}}")


class GlobusRunner:
    """Dispatches shell commands to a Globus Compute endpoint via a long-lived Executor.

    The executor is created lazily and reused across calls so the AMQP result
    subscription outlives individual dispatches. `executor_factory` is injectable
    for tests; the default builds a real `globus_compute_sdk.Executor`.
    """

    def __init__(
        self,
        endpoint_id: str,
        executor_factory=None,
        timeout: float = 120.0,
        walltime: float | None = None,
        user_endpoint_config: dict | None = None,
    ) -> None:
        self.endpoint_id = endpoint_id
        self.timeout = timeout
        # Server-side wall-clock: the worker kills the process and returns 124 at
        # `walltime`, slightly before the client stops waiting at `timeout`.
        self.walltime = walltime if walltime is not None else max(timeout - 10.0, 5.0)
        self.user_endpoint_config = user_endpoint_config
        self._ex = None
        self._factory = executor_factory or self._default_factory

    def _default_factory(self):
        from globus_compute_sdk import Executor

        return Executor(
            endpoint_id=self.endpoint_id,
            user_endpoint_config=self.user_endpoint_config,
        )

    def executor(self):
        if self._ex is None:
            self._ex = self._factory()
        return self._ex

    async def run(self, command: str):
        from globus_compute_sdk import ShellFunction

        fn = ShellFunction(_escape_for_shellfunction(command), walltime=self.walltime)
        fut = self.executor().submit(fn)
        # .result() blocks on the AMQP round-trip; run it off the event loop.
        return await asyncio.to_thread(fut.result, self.timeout)

    async def canary(self, timeout: float = 8.0) -> CanaryResult:
        """Submit a trivial task and confirm a WORKER answers within `timeout`.

        A returned result (any) proves a worker is live — the worker-registration signal
        `manager_online()` can't give. TimeoutError => no worker yet (block still cold-starting),
        reported as not-ok rather than raised. Reuses the long-lived Executor, so the same AMQP
        path real dispatches use is what gets proven (and the submit kicks a cold block)."""
        from globus_compute_sdk import ShellFunction

        fn = ShellFunction(_escape_for_shellfunction(_CANARY_CMD), walltime=max(timeout - 1.0, 2.0))
        fut = self.executor().submit(fn)
        try:
            res = await asyncio.to_thread(fut.result, timeout)
        except TimeoutError:
            return CanaryResult(ok=False, error="timeout")
        except Exception as exc:  # noqa: BLE001 - worker reachable but the dispatch path is broken
            return CanaryResult(ok=False, error=f"{type(exc).__name__}: {exc}"[:200])
        py, dill_v, host = _parse_canary(getattr(res, "stdout", "") or "")
        return CanaryResult(ok=True, worker_python=py, worker_dill=dill_v, worker_host=host)

    def close(self) -> None:
        if self._ex is not None:
            self._ex.shutdown()
            self._ex = None
