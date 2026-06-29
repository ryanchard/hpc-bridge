# Persistent SSH session

> [!warning] Planned · built (this branch), pending live validation
> One authenticated SSH connection, reused for the whole bootstrap **and** the discovery sweep, so an MFA facility prompts **once** — not ~10×. The "**authenticate once**" generalization of [[Two-channel architecture|SSH-once]] (reuse, not avoidance). Tracking: [#7](https://github.com/ryanchard/hpc-bridge/issues/7). Graduates into [[facility-remote]] + [[Two-channel architecture]] once live-validated.

## What it is

OpenSSH **ControlMaster** multiplexing, added at the single SSH chokepoint — all SSH funnels through `ssh_exec` → `SshTarget.argv` ([[facility-remote]]), so three `-o` options buy connection reuse with **zero call-site changes**. The first connection authenticates and forks a background master socket; every later `ssh` rides it — no TCP, no re-auth.

## How it works

- `SshTarget` gains `control_dir` / `control_persist` (`remote.py:50`). When `control_dir` is set, `argv` (`:59`) appends `ControlMaster=auto` + a **`%C`-keyed** `ControlPath` + `ControlPersist`; **byte-identical** to before when it's `None`. `%C` hashes localhost/user/host/port → a short path (fits macOS' ~104-char socket limit) and a *distinct* socket per host, so a `rebind` to the pinned node simply opens its own master.
- `control_argv` (`:80`) + `RemoteEndpointCLI.close()` (`:399`) send `ssh -O exit` to drop the master; wired into `SlurmFacility.teardown`. `ControlPersist` self-reaps an idle master, so `close()` is belt-and-suspenders, not load-bearing.
- `_control_settings()` (`server.py:87`) is the shared policy: socket dir `~/.hpc-bridge/cm` (`0700`), `HPC_BRIDGE_SSH_CONTROL_PERSIST` seconds (`0` disables → `control_dir=None`). Used by **both** `_slurm_facility` (`:103`) and the discovery probe (`_propose_or_ask`, `:764`) — same `user@host` ⇒ same `%C` socket ⇒ probing **warms the master the bootstrap then rides** (discovery adds no authentication).

## MFA: the server never prompts

`BatchMode=yes` stays. On a **key-only** facility (Anvil, globus1) `ControlMaster=auto` opens the master non-interactively on the first call. On an **MFA** facility the user pre-opens it once in their own terminal (`ssh -fN <host>` → one Duo) and the server's BatchMode connections multiplex over the existing socket. No server-side interactive auth in this slice.

> [!warning] Control plane only — never the hot path
> Persistent SSH carries **bootstrap + discovery**, never dispatch: the work path stays AMQP + a scoped Globus token ([[Two-channel architecture]]). And the control socket is **credential-equivalent** — whoever can reach it runs commands on the master without re-auth — so its dir is `0700`.

> [!note] Honest nuance — 2 auths, not 1
> The post-`start` `rebind` to the pinned node means a cold bootstrap ends with two masters (round-robin alias + pinned node) ⇒ **2 authentications, not 1** — still decisive vs ~10. Literally one auth would need pinning the host *before* the bulk of the work (deferred).

## Deferred

Server-side interactive MFA auth; the single-auth-for-the-whole-bootstrap optimization (pin the host first); persisting the socket across MCP-server restarts.

## See also
[[Two-channel architecture]] · [[facility-remote]] · [[Standing up the endpoint]] · [[Globus index discovery channel]] · [[Home]]
