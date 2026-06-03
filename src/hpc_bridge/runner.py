from __future__ import annotations

import asyncio


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
    ) -> None:
        self.endpoint_id = endpoint_id
        self.timeout = timeout
        # Server-side wall-clock: the worker kills the process and returns 124 at
        # `walltime`, slightly before the client stops waiting at `timeout`.
        self.walltime = walltime if walltime is not None else max(timeout - 10.0, 5.0)
        self._ex = None
        self._factory = executor_factory or self._default_factory

    def _default_factory(self):
        from globus_compute_sdk import Executor

        return Executor(endpoint_id=self.endpoint_id)

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

    def close(self) -> None:
        if self._ex is not None:
            self._ex.shutdown()
            self._ex = None
