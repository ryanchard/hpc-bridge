# hpc-bridge — user guide

hpc-bridge lets Claude Code work on a supercomputer for you. You ask in plain language; the agent
finds the facility, logs you in to Globus from the terminal, brings up a compute block (asking before
it spends anything), runs your commands on a node, and releases the block when you are done.

| Page | Read it when |
|---|---|
| [Install](install.md) | you are setting it up: prerequisites, loading the plugin, platform notes |
| [Quickstart](quickstart.md) | your first session, step by step, with what the agent will say |
| [Facilities](facilities.md) | choosing a machine: what each facility needs from you |
| [The Globus login](login.md) | the one credential hpc-bridge needs, and why your browser opens |
| [Costs and stopping](costs-and-stopping.md) | how spend is gated, what "stop" means, long-running work |
| [Troubleshooting](troubleshooting.md) | what a refusal or a stuck state means and what to do |

**Three ways a facility is reached.** Which one applies is decided for you, but it helps to know:

- **Zero-SSH, facility-run endpoint.** Some facilities run a Globus Compute *multi-user endpoint*.
  You need an account there with your Globus identity mapped to it, and nothing else — no SSH keys,
  no setup on the machine. The lab cluster `globus1` works this way.
- **SSH bootstrap.** For other catalogued facilities, hpc-bridge uses SSH *once* to stand up a
  personal endpoint on the login node, then never again: later sessions reconnect over the network.
  You need an account and key-based SSH to the login node. Purdue Anvil works this way.
- **Bring your own cluster.** Give the agent an SSH login host for a cluster that is not in the
  registry; it probes the machine, proposes a configuration, and asks you to confirm.

The developer documentation (design, internals, the tool reference) lives in the
[vault](../hpc-bridge-vault/Home.md).
