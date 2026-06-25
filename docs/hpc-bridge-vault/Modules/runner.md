# runner.py

> [!abstract] Role
> `GlobusRunner` — the AMQP hot-path dispatcher. Submits a `ShellFunction` through a long-lived Globus Compute `Executor`, and runs the warmth **canary** through the same Executor.

## What it does

- **`GlobusRunner`** (`runner.py:46`) — built per shape with an `endpoint_id` + `user_endpoint_config`. It lazily creates a Globus Compute `Executor` (the SDK captures the `user_endpoint_config` at build time) and reuses it for all dispatch.
- **`run(command)`** — submits a `ShellFunction(command)` and returns its `{returncode, stdout, stderr}`. Commands are escaped via `_escape_for_shellfunction` (`:40`) because `ShellFunction` runs `cmd.format()` (bare `{}`/`}` would break formatting).
- **`canary(timeout)`** — submits `_CANARY_CMD` (`:23`), a trivial probe that echoes a sentinel plus the worker's host/Python/dill; `_parse_canary` (`:31`) extracts them into a `CanaryResult` (`:8`). Drives [[Warmth, the canary & cold-start]].

## Key points

> [!warning] The Executor captures `user_endpoint_config` at build time
> A shape/partition change must rebuild the runner, or the cached Executor keeps the *old* config. [[server]] tracks this with a `runner_stale` flag and rebuilds via `_runner_for`.

> [!warning] Dill skew is the real failure
> `canary` reports the worker's dill version specifically because a mismatch with the client breaks function (de)serialization — the genuine "worker up but tasks fail" hazard.

## See also
[[Two-channel architecture]] · [[Warmth, the canary & cold-start]] · [[server]] · [[dispatch]]
