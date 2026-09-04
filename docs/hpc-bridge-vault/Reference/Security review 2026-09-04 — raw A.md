# Security review 2026-09-04 — raw report A

> [!note] Surface A (remote command construction + the SSH control plane), as delivered by the review agent; the consolidated record and decisions are in [[Security review 2026-09-04]]. Line numbers refer to the pre-fix code.

Counts: 2 CONFIRMED high (A1, A2); 1 PLAUSIBLE medium/latent (A3); 1 PLAUSIBLE low (A4); 1 accepted-design high-in-model (A5); 3 not-an-issue/informational (A6, A7, A8) + 1 amplifier (A10).

| id | severity | verdict | file:line (pre-fix) | claim |
|---|---|---|---|---|
| A1 | high | CONFIRMED | facility/remote.py:82,758-765; connect.py:101-190 | Attacker-chosen `ssh_host` + `accept-new` + no host confirmation ⇒ ships the Globus storage.db (refresh tokens) to the attacker host via `seed_storage_db`. |
| A2 | high | CONFIRMED | connect.py:82-190; server.py:418-448 | No enforced confirmation gate before SSH bootstrap; `details=`/registry config runs `env_setup` on `ssh_host` immediately. "Confirm with user" is prose, not a gate. |
| A3 | medium | PLAUSIBLE (latent) | facility/remote.py:71-91 | `SshTarget.argv` has no `--` guard / no leading-`-` rejection ⇒ host parsed as ssh options. Neutralised only incidentally (every remote command contains spaces; OpenSSH ≥ 9.6 rejects such a destination first). PoC: `-oProxyCommand=touch …` fires with a single-word destination. |
| A4 | low | PLAUSIBLE | facility/remote.py:102-113; notices.py:174-191 | `preauth_command()` builds the user's paste-and-run line unquoted. |
| A5 | high-in-model | ACCEPTED DESIGN | remote.py:299-302,406-415; templates | `env_setup`/`worker_init`/`scheduler_options` spliced unquoted into remote `bash -lc`/submit script — arbitrary shell by design; a compromised registry entry ran unconfirmed. Docs did not state the registry as a trust root. |
| A6 | low | NOT AN ISSUE | scheduler_ops.py:29-44,79-101; remote.py:504-533 | PBS release runs bare `qstat -f` (all users); the `uep.<eid>` marker is spoofable by a co-user, but ids are numeric and a foreign `qdel` fails; no injection. |
| A7 | info | NOT AN ISSUE | catalog/entry.py:23-47; remote.py:48,363-375; session_shell.py | Endpoint name, session_id, scratch_root, job ids all allowlisted or `shlex.quote`d. |
| A8 | info | NOT AN ISSUE | remote.py:447-466; config.py:93-101 | UUID gate before remote globs; the ControlMaster `/tmp` fallback is on the trusted client machine and ownership-guarded. |
| A10 | info | amplifier | remote.py:406-428,181-195; binding.py:63-69 | `HPCB_HOST=$(hostname -f)` from attacker stdout becomes a persisted pin, re-seeding on every future connect. |

Cross-cutting observation: A1/A2/A5 reduce to a single missing control — the destination host and its config were
trusted without a user-confirmation gate or host-key pinning. Fixing that (plus the cheap `--` guard and quoting)
closes the highest-impact paths.
