# Two-channel architecture

> [!abstract] In one line
> SSH is a **one-time control channel** (bootstrap · teardown · read-only discovery before an endpoint exists); all *work* rides **Globus Compute over AMQP** — a scoped Globus token, never SSH material.

## What & why

hpc-bridge keeps two strictly separate paths to a facility:

- **Control plane — SSH (key-only).** Used *only* for the irreducible: the one-time bootstrap, `gce stop` at teardown (you can't stop the daemon through itself), and `login_shell` discovery before an endpoint exists. Minimised because every fresh SSH risks an interactive re-auth on an MFA facility — and a fresh SSH to a *loaded* login node is also just slow.
- **Hot path — Globus Compute / AMQP.** Every `run_shell` and the warmth *canary* ride Globus Compute's AMQP path (the warm worker), carrying a scoped Globus Auth token. No SSH credential ever touches the work path. **AMQP is the first port of call for *runtime* cluster comms** — discovery and compute. (Teardown — `scancel` + `gce stop` — is SSH: the endpoint is being destroyed and the login worker is ephemeral, so AMQP isn't the right tool there.)

```mermaid
flowchart LR
  A[Claude Code laptop] -- MCP stdio --> B[hpc-bridge server]
  B -- "SSH key-only<br/>bootstrap · gce stop · login_shell" --> C[Login node]
  B == "AMQP<br/>ShellFunction + canary" ==> D[Globus Compute web]
  D == AMQP ==> E[Endpoint manager on login node]
  E -- sbatch --> F[Worker on compute node]
  F == "result via AMQP" ==> B
```

## How it shows up in the code

- **SSH transport:** `ssh_exec()` ([[facility-remote]]) — key-only, `BatchMode`, reaps the child on timeout. Drives `bootstrap`, teardown (`gce stop` + `cancel_blocks`/`scancel`, each bounded by `_TEARDOWN_SSH_S`=30 s), and `login_exec` (the `login_shell` tool).
- **AMQP hot path:** `GlobusRunner` ([[runner]]) submits a `ShellFunction` through a long-lived Globus Compute `Executor`; the same Executor runs the canary ([[Warmth, the canary & cold-start]]). Reached from `run_shell` via [[server]] → `_run_shell`.

> [!warning] The load-bearing invariant
> The hot path carries a **scoped Globus Auth token, never SSH material**. SSH is key-only (`BatchMode`, `IdentitiesOnly`) and used only for bootstrap + teardown. Routing *discovery* through the login-*shape* (AMQP) rather than `login_shell`/SSH is what makes a warm session's runtime SSH-free — see [[Discovery today]].

## See also
[[Standing up the endpoint]] · [[MEP & templated endpoints]] · [[Credential seeding]] · [[server]] · [[facility-remote]] · [[runner]]
