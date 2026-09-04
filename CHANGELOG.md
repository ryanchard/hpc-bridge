# Changelog

All notable changes to hpc-bridge. The plugin version lives in `.claude-plugin/plugin.json` (Claude Code updates an
installed plugin only when that version changes); git tags mark releases.

## 0.1.1 — 2026-09-04 — first public beta (`v0.1.1-beta.1`)

hpc-bridge lets Claude Code work on a supercomputer for you: it finds the facility, logs you in to Globus once, starts
a one-node scheduler job (asking before it spends anything), runs your commands on that node, and releases it. This is
the first release offered to test users. Expect rough edges, and please report them at
https://github.com/ryanchard/hpc-bridge/issues. (0.1.0 was the pre-release the marketplace served during development;
the version moves so existing installs update.)

### What's in it
- **Install from Claude Code** — the repository is its own plugin marketplace: `/plugin marketplace add
  ryanchard/hpc-bridge`, `/plugin install hpc-bridge@hpc-bridge`. Eleven tools, a skill the agent follows on its own,
  and the `/hpc-bridge:hpc-connect` command. The server is launched through a small script that finds `uv` even
  from the desktop app or an IDE, whose PATH lacks the usual install locations. (#52, #74)
- **Public facility registry** — "What HPC facilities can I use?" answers from a curated Globus Search index, read
  anonymously, and each entry says how you get in. The registry wins over a local cache for any catalogued id. (#49)
- **In-terminal Globus login** — the first connect opens your browser, you approve once, the agent carries on in the
  same call and names the identity that landed; a paste fallback when no browser can reach the loopback. (#48, #75)
- **Two ways in** — an SSH-once bootstrap that installs a personal Globus Compute endpoint in your home directory on
  the login node and reconnects with no SSH afterwards; or a facility-run multi-user endpoint attached with zero SSH,
  the identity mapping done by the facility. (#41, #20)
- **Bring your own cluster** — an un-catalogued login host is probed, a configuration proposed and confirmed with you
  in this conversation, and remembered only once it has proven to work. (#50, #57, #78)
- **Cost control** — no billed block starts without your confirmation; idle blocks self-release; stopping confirms the
  release or says honestly that the block is draining. (#24, #44)
- **Honest first-contact failures** — "NO ACCOUNT at this facility" (naming the identity), "NO SSH ACCESS to <host>",
  "CANNOT REACH", "UNKNOWN HOST KEY": each terminal, explained once, with the remedy. (#49, #50, #75)
- **Long-running work** — a command that outlives the sync wait becomes a task you can poll; a task on a dead
  endpoint is reported as orphaned, never "running forever". (#44)
- **A user guide** — install, quickstart, facilities, the Globus login, costs and stopping, troubleshooting — written
  for someone who has never seen the project, and checked by an agent playing exactly that person. (#52, #73, #74)

### Security (review of 2026-09-04, record in the vault)
- **hpc-bridge trusts exactly the hosts your own `ssh` trusts.** No host-key auto-accept; an unknown or changed key is
  refused as UNKNOWN HOST KEY, and the remedy is one `ssh` from your own terminal. A pinned login node is verified
  against the alias you trusted. The host string is validated before it can reach ssh's argument list. (#75)
- **The first SSH contact is in the transcript:** the connect result names the login `user@host` and the shell line it
  will run there, before anything runs. Confirmation must come from the user in the current conversation; a
  remembered or cached confirmation is not consent, and the agent is told not to re-probe over its own ssh. (#75, #78)
- **Tokens:** a trimmed Globus token store is placed on an SSH facility's login node so the endpoint can register; the
  docs now say so, and teardown removes it — only if hpc-bridge put it there. (#75, #76, #79)
- **Registry and cache:** the offline cache is served only when the registry is unreachable and forgotten on a clean
  miss; a cached record whose host doesn't match is ignored; entry ids are allowlisted. The registry is trusted code;
  the user docs say so. Its index moves to a production, group-owned index with the org transfer. (#75)
- **Supply chain:** the server runs against its lockfile (`uv run --locked`); the endpoint installed on the login node
  is pinned to the SDK's version. (#75)

### Fixed
- The first connect after registering an endpoint no longer fails with "could not find endpoint … in list output":
  the CLI's table wrapped long names at 80 columns without a TTY. Fixed at the source, with a bounded wait for the
  UUID and adoption of an already-running manager. (#72, closes #39)
- Teardown really deletes the endpoint (directory, registration, worker directories) and reports what it did; it
  used to stop only, while claiming "deleted". (#79)
- A fresh bring-up is cached as proven and is no longer reported as "reused the already-online endpoint". (#78)
- Twenty-nine bugs from two adversarial review rounds, each pinned by a regression test. (#54, #67)

### Verified
- Live on **Purdue Anvil** and **Midway** (Slurm), **ALCF Polaris** (PBS) and Globus Labs' cluster.
- Agentic regression tier across Opus 5, Sonnet 5 and Haiku 4.5: cheap tier 29/30, block tier 14/14, plus the two
  host-key cells (`byo_teardown_clean`, `unknown_host_key`) that replace the manual fresh-user walk. (#51, #69–#71,
  #80, #81)
- 469 unit tests, mypy and ruff clean, all enforced in CI.

### Known limitations
- Facilities not in the registry need the bring-your-own path; ALCF, NCSA Delta and NeSI multi-user endpoints are
  not catalogued yet.
- Windows is untested; the SSH path relies on OpenSSH connection multiplexing.
- Interactive-login (password / Duo) facilities: the agent hands you an `ssh` command for your own terminal and never
  handles the secret.
- The agent runs as you on the login node; treat what it runs there as you would your own shell.
- The registry index is a trial index owned by one maintainer until the org transfer.
