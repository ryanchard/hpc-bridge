# session_shell.py

> [!abstract] Role
> The cwd/env persistence shim. Wraps each command so `cd`/exports survive `ShellFunction`'s fresh-subprocess model, persisting state in `<root>/sessions/<id>/{.cwd,.env}` on the shared filesystem.

## What it does

- **`Session`** (`session_shell.py:21`) — `(session_id, root)`. `session_id` is an untrusted MCP param, so it's validated against `_VALID_SESSION_ID` (`:8`, strict allowlist — no `../`, no metacharacters → no path traversal). **`quoted_state_dir()`** (`:46`) is the state dir as a shell word: a root written `$HOME/…` / `${HOME}/…` / `~/…` is emitted as `"$HOME"'/rest'` so the variable expands **on the worker** — what lets a [[facility-mep|multi-user endpoint]], whose local username we can't know client-side, use a home-relative scratch; anything else is quoted whole.
- **`wrap(command, session)`** (`:102`) — renders `_WRAP_TEMPLATE` (`:63`): `cd` into the saved `.cwd`, source `.env`, run the **base64-carried** command via `eval` in the current shell, then persist the new cwd + changed env. Runs under `/bin/bash` (`ShellFunction` execs `shell=True, executable=/bin/bash`). The state-dir splice has **no outer quotes** (an assignment RHS doesn't word-split), so the mixed quoting above survives.
- **`reset_command(session)`** (`:126`) — clears `.cwd`/`.env` (and any leaked snapshot).

This is the mechanism behind [[Session continuity]]; both invariants (volatile-var filtering, record-safe multi-line env) are documented there.

> [!warning] Base64-carry the command
> The user command is base64-encoded and decoded+`eval`'d, so arbitrary shell (brace groups, quotes, `${VAR}`) can't textually break out of the wrapper while `cd`/`export` inside it still affect the persisted state.

## See also
[[Session continuity]] · [[server]]
