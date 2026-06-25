# session_shell.py

> [!abstract] Role
> The cwd/env persistence shim. Wraps each command so `cd`/exports survive `ShellFunction`'s fresh-subprocess model, persisting state in `<root>/sessions/<id>/{.cwd,.env}` on the shared filesystem.

## What it does

- **`Session`** (`session_shell.py:21`) — `(session_id, root)`. `session_id` is an untrusted MCP param, so it's validated against `_VALID_SESSION_ID` (`:8`, strict allowlist — no `../`, no metacharacters → no path traversal).
- **`wrap(command, session)`** (`:83`) — renders `_WRAP_TEMPLATE` (`:47`): `cd` into the saved `.cwd`, source `.env`, run the **base64-carried** command via `eval` in the current shell, then persist the new cwd + changed env. Runs under `/bin/bash` (`ShellFunction` execs `shell=True, executable=/bin/bash`).
- **`reset_command(session)`** (`:107`) — clears `.cwd`/`.env` (and any leaked snapshot).

This is the mechanism behind [[Session continuity]]; both invariants (volatile-var filtering, record-safe multi-line env) are documented there.

> [!warning] Base64-carry the command
> The user command is base64-encoded and decoded+`eval`'d, so arbitrary shell (brace groups, quotes, `${VAR}`) can't textually break out of the wrapper while `cd`/`export` inside it still affect the persisted state.

## See also
[[Session continuity]] · [[server]]
