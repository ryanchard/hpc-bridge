# hpc-bridge — user guide

hpc-bridge lets Claude Code work on a supercomputer for you. You ask in plain language; the agent
finds the facility, logs you in to Globus from the terminal, brings up a compute block (asking before
it spends anything), runs your commands on a node, and releases the block when you are done.

| Page | Read it when |
|---|---|
| [Install](install.md) | you are setting it up: the two commands, prerequisites, updating |
| [Quickstart](quickstart.md) | your first session, step by step, with what the agent will say |
| [Facilities](facilities.md) | choosing a machine: what each facility needs from you |
| [The Globus login](login.md) | the one credential hpc-bridge needs, and why your browser opens |
| [Costs and stopping](costs-and-stopping.md) | how spend is gated, what "stop" means, long-running work |
| [Troubleshooting](troubleshooting.md) | what a refusal or a stuck state means and what to do |

## What you bring, by how the facility is reached

Which row applies is decided for you from the registry. Everything in the right column is included.

| Facility | You bring | hpc-bridge does |
|---|---|---|
| **Facility-run endpoint**, zero SSH (e.g. `globus1`) | an account there with your Globus identity mapped to it | attaches to the facility's endpoint; nothing to set up on the machine |
| **SSH bootstrap** (e.g. Purdue Anvil) | an account and key-based SSH to the login node, as a `Host` block in `~/.ssh/config` | one SSH to install a personal endpoint in your home directory, then reconnects with no SSH |
| **Your own cluster**, not in the registry | the same as SSH bootstrap, plus a minute to confirm the settings it discovers | probes the login node, proposes a configuration, caches it once you confirm |

In every case you also bring Claude Code, Python 3.11 with `uv`, and a Globus account; the plugin
brings the registry, the terminal Globus login, the spend gate and idle self-release.

The developer documentation (design, internals, the tool reference) lives in the
[vault](../hpc-bridge-vault/Home.md).
