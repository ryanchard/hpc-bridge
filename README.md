# hpc-bridge

**Drive a supercomputer from Claude Code.** Ask for a compute node in plain language; the agent finds the
facility, logs you in to Globus once, brings up a block (asking before it spends anything), runs your
commands on the node, and releases it when you are done.

## Install

Two commands inside Claude Code; the repository is its own plugin marketplace.

```
/plugin marketplace add ryanchard/hpc-bridge
/plugin install hpc-bridge@hpc-bridge
```

Then ask *"What HPC facilities can I use?"* — that first answer needs no login and no configuration.

## What you bring, what's included

| You bring | Included |
|---|---|
| **Claude Code** | the plugin: eleven tools, a skill the agent follows on its own, `/hpc-bridge:hpc-connect` |
| **Python 3.11+ and [`uv`](https://docs.astral.sh/uv/)**, the only local prerequisites | the **public facility registry**: which machines exist and what each needs from you |
| **A Globus account** (institutional, ACCESS, Google, ORCID) | the **Globus login in your terminal**: the browser opens once, the token is remembered |
| **Access to a facility**: an account whose Globus identity it has mapped (zero SSH), or an account plus key-based SSH to its login node in `~/.ssh/config` | the **endpoint bootstrap**: on an SSH facility hpc-bridge installs Globus Compute in your home directory there, then reconnects with no SSH — nothing for you to install on the machine |
| For an un-catalogued cluster, a minute to confirm the settings the agent discovers | **discovery** of that cluster's configuration, the **spend gate** before any billed block, and **idle self-release** |

Nothing is configured by hand. Details: **[Install](docs/user/install.md)** · **[Facilities](docs/user/facilities.md)**.

## Your first session

1. *What HPC facilities can I use?*
2. *Connect me to globus1*
3. *Run `hostname` on a compute node*
4. *Tear it down*

The connect opens your browser for Globus; approve once. Before the first command the agent shows the
partition and account it will charge and asks you to confirm. Allocating a block takes a couple of minutes
on a quiet cluster; after that, commands run immediately and your working directory persists between them.
The **[Quickstart](docs/user/quickstart.md)** shows what the agent actually says at each step.

## How it works

SSH is a one-time bootstrap; everything after rides a Globus Compute endpoint. `connect_facility` resolves
a machine from the registry, from a cluster you used before, or by probing a new login node, and both
discovery and compute then flow through the endpoint over Globus. A facility that runs its own multi-user
endpoint needs no SSH at all. Design, tool reference and module notes: the **[vault](docs/hpc-bridge-vault/Home.md)**.

## Docs

- **[User guide](docs/user/README.md)** — install, quickstart, facilities, the Globus login, costs and stopping, troubleshooting.
- **[Adding a facility to the registry](docs/adding-a-facility.md)** — for facility staff and contributors.
- **[Vault](docs/hpc-bridge-vault/Home.md)** — how it is built.

## Status and security

Pre-release (v0.1.0), proven live on **Purdue Anvil** and **Midway** (Slurm), **ALCF Polaris** (PBS) and a
lab Globus cluster; unit tests, lint and type checks run in CI. The hot path carries a scoped Globus token,
never SSH material; SSH is key-only and used once to bootstrap. A password or Duo passcode is never handled
by the agent: it hands you an `ssh` command for your own terminal and shares that session. The agent runs
as you on the login node, so treat what it runs there as you would your own shell.
