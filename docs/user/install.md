# Install

## Prerequisites

- **Claude Code** (the CLI, desktop app, or IDE extension).
- **Python 3.11 or newer** and [`uv`](https://docs.astral.sh/uv/). The plugin launches its server
  with `uv run`, so `uv` must be on your `PATH`.
- **A Globus account.** Any identity works — institutional, ACCESS, Google, ORCID — but see
  [Facilities](facilities.md): a facility only accepts the identity it knows you by.
- For **SSH-bootstrap facilities** (e.g. Anvil): an account there and key-based SSH to its login
  node, ideally as a `Host` block in `~/.ssh/config` with `User` and `IdentityFile` set. hpc-bridge
  reads that live; it never asks for a password.
- For **facility-run endpoints** (e.g. `globus1`): an account at the facility with your Globus
  identity mapped to it. Nothing to install on the machine.

## Install from Claude Code

The repository is its own plugin marketplace, so installing is two commands inside Claude Code:

```
/plugin marketplace add ryanchard/hpc-bridge
/plugin install hpc-bridge@hpc-bridge
```

The same works from a shell with `claude plugin marketplace add ryanchard/hpc-bridge` and
`claude plugin install hpc-bridge@hpc-bridge`. By default the plugin is installed for you across all
projects; add `--scope project` to share it with a repository's collaborators. Claude Code keeps a
versioned copy under its plugin cache and starts the plugin's server with `uv run`, which resolves
the server's Python dependencies on first start.

Adding a marketplace is a trust decision: a plugin runs with your privileges. This one is the
repository you can read.

The plugin adds a slash command, `/hpc-bridge:hpc-connect`, and a skill the agent follows on its own,
so plain requests work too. To pick up a new release later, run `/plugin marketplace update hpc-bridge`
followed by `/plugin update hpc-bridge@hpc-bridge`, then `/reload-plugins`.

### From a clone (developers)

```bash
git clone https://github.com/ryanchard/hpc-bridge.git
cd hpc-bridge
claude --plugin-dir .            # start Claude Code with this checkout loaded as the plugin
```

## Check it works

In the session, ask:

> What HPC facilities can I use?

The agent lists the registered facilities and what each one needs. No login, no configuration, no
SSH is involved in that first answer. If the agent says it has no hpc-bridge tools, the server did
not start: check `/plugin` and its **Errors** tab, and see
[Troubleshooting](troubleshooting.md#the-agent-has-no-hpc-bridge-tools). The usual cause is `uv`
missing from the `PATH` of the shell that started Claude Code.

## Where things live on your machine

| What | Where |
|---|---|
| Globus tokens | Globus Compute's standard token store, `~/.globus_compute/storage.db` (mode 600) |
| hpc-bridge state: known facilities, endpoint pins, SSH multiplexing sockets | `~/.hpc-bridge/` |
| the plugin's catalog cache | Claude Code's plugin data directory |

Nothing else is written. Deleting `~/.hpc-bridge` makes hpc-bridge forget the clusters it has
discovered (registry facilities are unaffected); deleting the token store logs you out of Globus.

## Optional environment variables

The defaults are right for almost everyone. If you set these, set them in the shell that starts
Claude Code.

| Variable | Use |
|---|---|
| `HPC_BRIDGE_SSH_USER`, `HPC_BRIDGE_SSH_KEY` | SSH login name and key when `~/.ssh/config` can't provide them |
| `HPC_BRIDGE_CHARGE_FACTOR` | service-unit multiplier for the session's spend estimate (default `0`, i.e. unmetered) |
| `HPC_BRIDGE_SYNC_WAIT_S` | how long a command blocks before it becomes a background task you can poll (default `120`) |
| `HPC_BRIDGE_MAX_TASK_S` | a hard cap on one task's runtime (default: the block's walltime) |
| `HPC_BRIDGE_LOGIN_WAIT_S` | how long the login step waits for your browser before handing you the link (default `90`) |
| `HPC_BRIDGE_STATE_DIR` | relocate hpc-bridge's local state (keep the path short: SSH sockets live there) |
| `HPC_BRIDGE_SEARCH_INDEX` | point at a different facility registry (the public one is built in) |

The full reference, including developer and testing knobs, is in the vault's
[Configuration](../hpc-bridge-vault/Reference/Configuration.md) page.

## Platform notes

- **macOS and Linux** are supported for every path above; the user guide was written from a Mac.
- **Windows** is untested. The SSH-bootstrap path relies on OpenSSH connection multiplexing, which
  Windows OpenSSH does not support; the zero-SSH path may work.
- Running an endpoint *on your own machine* (rather than on a facility) needs Linux, because the
  Globus Compute endpoint daemon is Linux-only. Normal use never needs this.
