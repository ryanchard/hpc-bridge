# runner.py

> [!abstract] Role
> `GlobusRunner` — the AMQP hot-path dispatcher. Submits a `ShellFunction` through a long-lived Globus Compute `Executor`, and runs the warmth **canary** through the same Executor.

## What it does

- **`GlobusRunner`** (`runner.py:46`) — built per shape with an `endpoint_id` + `user_endpoint_config`. It lazily creates a Globus Compute `Executor` (the SDK captures the `user_endpoint_config` at build time) and reuses it for all dispatch.
- **`run(command)`** — submits a `ShellFunction(command)` and returns its `{returncode, stdout, stderr}`. Commands are escaped via `_escape_for_shellfunction` (`:40`) because `ShellFunction` runs `cmd.format()` (bare `{}`/`}` would break formatting).
- **`canary(timeout)`** — submits `_CANARY_CMD` (`:23`), a trivial probe that echoes a sentinel plus the worker's host/Python/dill; `_parse_canary` (`:31`) extracts them into a `CanaryResult` (`:8`). Drives [[Warmth, the canary & cold-start]].
- **`close()`** — shuts down the Executor with `wait=False, cancel_futures=True` (see the warning below). Called on teardown, runner-swap, and machine switch.

## Key points

> [!warning] The Executor captures `user_endpoint_config` at build time
> A shape/partition change must rebuild the runner, or the cached Executor keeps the *old* config. [[server]] tracks this with a `runner_stale` flag and rebuilds via `_runner_for`.

> [!warning] Dill skew is the real failure
> `canary` reports the worker's dill version specifically because a mismatch with the client breaks function (de)serialization — the genuine "worker up but tasks fail" hazard.

> [!warning] `close()` must `shutdown(wait=False)` — the real stop hang
> `Executor.shutdown()` **defaults to `wait=True`**, which "will not return until all pending futures have received results" — it **blocks on the AMQP connection drain**. Since `_stop_endpoint` / `_runner_for` / `connect_facility` call `close()` synchronously on the event loop, the default blocked the whole tool for *minutes* **after** the work was done (the multi-minute "stop hang": the `scancel` ran fast, then `close()` hung). Fix: `shutdown(wait=False, cancel_futures=True)` — we never need pending results at teardown ([#17]).

> [!warning] Bound dispatch at `fut.result(timeout)`, NOT `asyncio.wait_for`
> A related gotcha: `run` does `await asyncio.to_thread(fut.result, self.timeout)`. A **running thread can't be cancelled**, so an `asyncio.wait_for` *around* a dispatch does **nothing** — it waits for the thread anyway. Dispatch is bounded only by `self.timeout` (the arg to `fut.result`, which `concurrent.futures` honors). Don't reach for `wait_for` to shorten a dispatch; it won't.

## See also
[[Two-channel architecture]] · [[Warmth, the canary & cold-start]] · [[server]] · [[dispatch]]
