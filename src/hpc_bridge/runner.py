from __future__ import annotations

import asyncio


class GlobusRunner:
    """Dispatches shell commands to a Globus Compute endpoint via a long-lived Executor.

    The executor is created lazily and reused across calls so the AMQP result
    subscription outlives individual dispatches. `executor_factory` is injectable
    for tests; the default builds a real `globus_compute_sdk.Executor`.
    """

    def __init__(self, endpoint_id: str, executor_factory=None, timeout: float = 200.0) -> None:
        self.endpoint_id = endpoint_id
        self.timeout = timeout
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

        fut = self.executor().submit(ShellFunction(command))
        # .result() blocks on the AMQP round-trip; run it off the event loop.
        return await asyncio.to_thread(fut.result, self.timeout)

    def close(self) -> None:
        if self._ex is not None:
            self._ex.shutdown()
            self._ex = None
