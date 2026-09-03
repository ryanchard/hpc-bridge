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

## Load the plugin

hpc-bridge is not on a marketplace yet. Clone the repository and load it as a plugin:

```bash
git clone https://github.com/ryanchard/hpc-bridge.git
cd hpc-bridge
uv sync --extra integration      # one-time: the server's dependencies
claude --plugin-dir .            # start Claude Code with hpc-bridge loaded
```

You can start Claude Code from any directory with `claude --plugin-dir /path/to/hpc-bridge`. The
plugin adds a slash command, `/hpc-bridge:hpc-connect`, and a skill the agent follows automatically.

## Check it works

In the session, ask:

> What HPC facilities can I use?

The agent lists the registered facilities and what each one needs. No login, no configuration, no
SSH is involved in that first answer. If the agent says it has no hpc-bridge tools, the server did
not start — see [Troubleshooting](troubleshooting.md#the-agent-has-no-hpc-bridge-tools).

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
