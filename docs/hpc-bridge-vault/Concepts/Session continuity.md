# Session continuity

> [!abstract] In one line
> `ShellFunction` runs every command in a fresh subprocess, so the server **wraps** each command to rehydrate and persist `cwd`/env in `<scratch>/sessions/<id>/{.cwd,.env}` on the shared filesystem — a bare `cd build` then `make` just works.

## How

`wrap()` ([[session_shell]], `session_shell.py:83`) rewrites a command so that, under `/bin/bash`:

1. `cd` into the session's saved `.cwd` (or the session dir on first call);
2. source the saved `.env`;
3. run the user's command — **base64-carried and `eval`'d in the current shell**, so arbitrary shell (braces, quotes, `${VAR}`) can't textually break out of the wrapper;
4. persist the new `cwd` and any env the command changed.

`session_id` comes from an untrusted MCP parameter, so `Session` (`session_shell.py:21`) validates it against a strict allowlist (no `../`, no metacharacters). `reset_session` clears the state files.

> [!warning] Don't freeze scheduler runtime vars
> The env diff drops volatile, scheduler-injected names (`SLURM*`, `HOSTNAME`, `PBS_*`, … — `_VOLATILE_NAME_GLOBS`, `session_shell.py:17`). Otherwise the first command's `$SLURM_JOB_ID` would be frozen into `.env` and replayed into a *later, different* allocation.

> [!warning] Record-safe env persistence
> The env is diffed per-variable via base64 fingerprints and re-emitted with `printf %q`, so a multi-line value can't leave an orphan line that breaks the next `. .env` (which would silently drop the whole session env).

> [!note] `<scratch>` follows the bound facility
> The session root is the *facility's* remote scratch (e.g. Anvil `$SCRATCH`) — set at startup and **re-set by `connect_facility`** when a machine is bound at runtime. Without that, the shim runs at the local `~/.hpc-bridge` path on the remote node ([[Facility catalog]]).

## See also
[[session_shell]] · [[server]] · [[The MCP tools]]
