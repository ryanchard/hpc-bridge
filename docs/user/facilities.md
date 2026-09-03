# Facilities

`list_facilities` answers from a public registry. Each entry says how it is reached, and that
decides what you need before you connect.

## What each access kind needs from you

| Access | You need | hpc-bridge does |
|---|---|---|
| **Facility-run endpoint** (zero SSH) | an account at the facility with your Globus identity mapped to it | attaches to the facility's endpoint; the first block start tests your mapping |
| **SSH bootstrap** | an account and key-based SSH to the login node (`~/.ssh/config` with `User` and `IdentityFile`) | one SSH to stand up a personal endpoint in your home directory, then reuses it with no further SSH |
| **Bring your own cluster** | the same as SSH bootstrap, plus a minute to confirm the discovered configuration | probes the login node, proposes a configuration, caches it once you confirm |

## Registered facilities

| Facility | Access | Scheduler | Notes |
|---|---|---|---|
| **Purdue Anvil** (ACCESS) | SSH bootstrap on `anvil.rcac.purdue.edu` | Slurm | your allocations come from `mybalance`; default partition `debug`, 30-minute walltime; the endpoint lives under `~/hpc-bridge/` in your Anvil home; scratch under `/anvil/scratch/<you>/.hpc-bridge` |
| **Globus Labs cluster** (`globus1`) | facility-run endpoint, zero SSH | Slurm | compute-only: there is no login node, every command runs on a billed block on partition `main` that stays warm between commands; no allocation account needed; unmetered lab hardware (3 ARM DGX Spark nodes) |

More facilities operate Globus Compute multi-user endpoints (ALCF Polaris and Crux, NCSA Delta,
NeSI). They are not in the registry yet because their endpoint templates need per-facility settings
hpc-bridge does not model yet; see the vault's
[MEP facilities survey](../hpc-bridge-vault/Reference/MEP%20facilities%20survey.md).

## Which Globus identity to use

A facility-run endpoint maps a specific identity, usually your institutional one, to your local
account. Globus tries every identity linked to your account, so a login through any linked identity
works, but an unlinked personal identity, a Google login for instance, will be refused with
"NO ACCOUNT at this facility". Log in with the identity the facility knows, or link it.

## Compute-only facilities

A facility-run endpoint is often compute-only: the facility refuses a free login-node worker, so
everything, including cheap discovery commands like `sinfo`, runs on the billed block. The block
stays warm between commands and the facility's idle timeout reclaims it, so the cost of a session is
one block for as long as you are active plus that timeout, not one block per command.

## Bringing your own cluster

> Connect me to the cluster at `login.example.edu`

The agent probes the login node over SSH, proposes the facility settings it discovered (scheduler,
the network interface the endpoint should bind, scratch space, an environment setup line) and asks
you to confirm or correct them. On a multi-factor facility the probe cannot log in by itself: the
agent gives you an `ssh` command to run in your own terminal, which opens a session hpc-bridge then
shares, so you authenticate once. The confirmed configuration is cached locally, and later sessions
reconnect with no probe and no SSH.

To make a cluster available to everyone, add it to the registry: see "Add your facility" in the
project README.
