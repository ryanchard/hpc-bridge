# Security review 2026-09-04

> [!info] What this is
> The pre-beta security review: three threat-model-driven review agents, one per surface — A: everything that becomes
> a command on a remote login node and the SSH control plane; B: credentials, tokens and what leaves the machine or
> lands in transcripts; C: untrusted network inputs (the public registry, the local cache) and the install/supply
> chain. Findings were traced to code and, where feasible, reproduced hermetically. Raw reports: [[Security review
> 2026-09-04 — raw A]], [[Security review 2026-09-04 — raw B]], [[Security review 2026-09-04 — raw C]]. Fixes landed
> in one PR with `tests/test_security_review.py` as the regression guard.

## The one design gap, and the decision

All three surfaces converged on it: **the facility host was trusted as the destination of the user's Globus tokens.**
On an SSH facility the bootstrap seeds a trimmed token store (refresh tokens included) to the login node so the endpoint
can register — the accepted design. What was not gated was *which host*: it came from a registry entry or from the
agent's `details=`, SSH ran with `StrictHostKeyChecking=accept-new`, and nothing showed the user the host or the
`env_setup` line first (A1, A2, A5, B-06, C-1). Two latent items ride along: the host string was never validated and
reached ssh's argv without a `--` guard, blocked only by an OpenSSH ≥ 9.6 quirk (A3, C-2); and the registry's offline
cache was served even after a curator's retraction (C-3).

**Decision 1 (2026-09-04, with the maintainer): the host key is the boundary, not a flag.** A `confirm_host` flag would
mirror `confirm_spend`, but the agent sets it — a prompt-injected agent sets it too; it is an audit trail, not a control.
So: hpc-bridge trusts exactly what the user's own `ssh` trusts. No `StrictHostKeyChecking` override (OpenSSH's default
refuses an unknown host under `BatchMode`; a user's own relaxation in `~/.ssh/config` applies); an unknown or changed
key is explained as `UNKNOWN HOST KEY` with the remedy in the user's own terminal (`ssh user@host` once pins the key
through the user's own action — the same shape as the MFA handoff); a login-node pin verifies against the trusted
alias's key (`HostKeyAlias`, resolved via `ssh -G` because known_hosts is keyed by the resolved HostName — found live:
the alias `globus1` stores its key under `globus1.cs.uchicago.edu`); the host string is allowlisted at the model
boundary and in `SshTarget` itself; `--` precedes every destination; and the first contact (`user@host` + the
`env_setup` line) is written into the connect result so it is in the transcript.

**Decision 2 — registry governance.** The live index is a Globus Search *trial* index (1 MB, no subscription, display
name `hpc-bridge-test`) owned by one maintainer identity; its writers cannot be enumerated by users (C-10). Kept for the
beta because decision 1 removes the worst consequence of a bad entry (token exfiltration), the seeds in this repo are
the public record of the index's content (both live entries match), and moving the index means a new baked-in id, i.e. a
plugin release. Plan: create the production index once, at the org move, under the Globus Labs subscription, owned by a
maintainers group (two admins), and switch `PUBLIC_REGISTRY_INDEX` in that same release. Now: the user docs say an entry
is trusted code; an env-pinned host is marked in the first-contact note; a curator diff of seeds vs index is a follow-up.

## Findings and what was done

| id | sev | verdict | claim | done |
|---|---|---|---|---|
| A1 / B-06 / C-1 | high | confirmed | attacker-chosen `ssh_host` + `accept-new` ⇒ seeded refresh tokens land on the attacker's host | no host-key override; UNKNOWN HOST KEY; first-contact note |
| A2 | high | confirmed | no enforced gate before the SSH bootstrap from `details=`/registry | first-contact note in the transcript; host-key boundary (decision 1) |
| A3 / C-2 | medium (latent) | confirmed | `ssh_host` unvalidated; no `--` in argv ⇒ `-oProxyCommand=…` parses as an option | `SAFE_HOST` at `FacilityDetails`, `CatalogEntry`, `connect_facility(ssh_host=)`, the env pin; `SshTarget.__post_init__`; `--` |
| A4 / C-9 | low | plausible | `preauth_command` unquoted | `shlex.quote` every token |
| A5 | accepted | design | `env_setup`/`worker_init` are shell by design | documented as trusted code; shown on first contact |
| A10 | info | amplifier | a pinned FQDN from attacker stdout re-seeds every session | `HostKeyAlias` on pins |
| B-01 | low | confirmed | paste-mode tokens bypass the SDK's validating storage | `app.token_storage` first |
| B-02 | medium | plausible | the login URL holder becomes the identity; never named | identity named in the logged-in notices; doc caveat |
| B-03 | medium | confirmed | the remote token copy is never wiped; docs misstate logout | `teardown_endpoint` → `teardown(wipe_credentials=True)`; docs |
| B-04 | low | confirmed | `/tmp` control-dir guard follows symlinks | `lstat`, symlink rejected |
| B-05 | low | confirmed | any local GET demotes the login to paste mode | code-less GETs answered 404, queue untouched |
| C-3 | medium | confirmed | offline cache served on a clean 404; a planted file impersonates a curated entry | cache only on transport failure; 404 forgets; files 0600 |
| C-4 | medium | confirmed | env overrides of registry/host leave no trace | the first-contact note marks an env-pinned host (registry half: follow-up) |
| C-5 | low | confirmed | a `facilities.json` record's host need not equal its key | an explicit `ssh_host` that mismatches the record is ignored |
| C-6 | low | confirmed | `uv run` without `--locked`; remote install unpinned; hatchling unlocked | `--locked`; endpoint pinned to the SDK's version; hatchling: follow-up |
| C-8 | info | confirmed | `id`/`facility_key` unvalidated | slug allowlist on `CatalogEntry` |
| C-7, C-10 | low / accepted | plausible / design | bare-id shadowing with >1 writer; trial index governance | decision 2 |

Verified as not issues (all three reports): the loopback binds 127.0.0.1 only; no token reaches a log or notice; the
one-time paste code is PKCE-bound and single-use; endpoint names, session ids, scratch roots and job ids are allowlisted
or quoted; UUIDs gate every remote glob; the registry wins over the cache for a catalogued id; HTTPS is verified with no
custom base URL; dependencies are hashed and lock-checked in CI; SKILL.md forbids password/passcode handling
consistently with `notices.py`.

## Live verification (globus1, as a pool user)
Known host through the new argv: ok. Pinned FQDN with `HostKeyAlias=<resolved alias>`: ok (with the alias as typed it
failed — hence `ssh -G`). Second `start` on a running manager: adopted. OpenSSH's refusal text for an unknown key
(`Host key verification failed.`) is what `_HOST_KEY_UNKNOWN` classifies.

## Follow-ups (not blocking the beta)
- Registry override signal in `list_facilities` results (C-4, the registry half) — needs a notice field on the summary.
- A `logout` path that revokes tokens (SDK `GlobusApp.logout()`) as well as wiping the remote copy (B-03 second half).
- A `state` parameter on the loopback (B-02's origin binding) — the SDK's flow manager owns the authorize URL.
- Pin `hatchling` for the build (`[tool.uv] build-constraint-dependencies`) once the version is chosen (C-6).
- Curator tooling: diff the seeds against the live index (`hpc-bridge-catalog verify`).
- The production registry index at the org move (decision 2).

## Regression cells (2026-09-04, both passed live)
- `byo_teardown_clean` — the SSH bring-up as a stranger does it, login shape only; world-checks the login node is clean after the agent's own teardown. Also confirms #39's fix (`first_details_connect_succeeds` passes).
- `unknown_host_key` — the boundary itself: phase 1 refused + explained on an empty known_hosts (no retry, no raw ssh); the harness trusts the key between phases as the user would from their terminal; phase 2 succeeds and tears down clean.

