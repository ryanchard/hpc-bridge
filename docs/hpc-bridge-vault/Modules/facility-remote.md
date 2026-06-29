# facility-remote.py — `facility/remote.py`

> [!abstract] Role
> Everything machine-specific for a remote Slurm cluster, behind one [[facility-base|Facility]]: the SSH transport, the per-facility `MachineProfile`, the `globus-compute-endpoint` CLI driver, and `SlurmFacility` (bootstrap / provision / teardown / config template).

## The pieces

- **SSH transport** — `SshTarget` (`:42`) + `ssh_exec()` (`:99`): `BatchMode` (never prompts), reaps the child on timeout/cancel (no process/FD leak). **Identity defers to `~/.ssh/config`** — `user`/`key_path` are optional; absent ⇒ a bare `host` so OpenSSH resolves `User`/`IdentityFile` itself (`-i`/`IdentitiesOnly` only with an explicit key). Also drives the un-indexed [[discovery]] probe (`ssh_exec` on a bare target, pre-endpoint). The control channel of [[Two-channel architecture]]; see *Persistent SSH* below.
- **Per-facility data** — `MachineProfile` (`:129`): host, `env_setup` (module + venv), `interface`, partition, account, scratch… now supplied by the [[Facility catalog|catalog]] (`profile_from_catalog_entry`, which derives `endpoint_name` = `hpc-bridge-<id>` when a seed omits it), no longer hardcoded per machine.
- **gce driver** — `RemoteEndpointCLI` (`:199`): runs `globus-compute-endpoint` over SSH via `_gce` (`:211`); also `login_exec` (`:215`, backs the `login_shell` tool), `seed_storage_db` (`:286`, [[Credential seeding]]), `configure`/`start`/`stop`, `cancel_blocks` (`:342`), and `close` (`:406`, drops the ControlMaster).
- **Orchestration** — `SlurmFacility` (`:427`): `bootstrap` (`:544`), `provision` (`:585`), `config_template` (`:450`, [[MEP & templated endpoints]]), `teardown` (`:618`), `manager_online` (`:636`, web), `find_online_endpoint` (`:643`, web reuse).

## How a stand-up flows

`bootstrap` (`:544`) is the entry point, and it is **reuse-or-SSH**: it first asks the Globus *web* service whether we already own an online endpoint (`find_online_endpoint`, `:643`) → reuse over AMQP, **zero SSH**. Only if none is online does it seed credentials (when needed) and call `provision` (`:585`): `configure` if absent → write the engine-free manager `config.yaml` + the UEP template → `start` (detached) → capture & **pin** the login node. See [[Standing up the endpoint]].

> [!warning] Login-node pinning
> The manager lives on ONE login node, but HPC SSH aliases round-robin. `start` (`:315`) captures the FQDN *in the same SSH connection* that launches the daemon (a separate probe could resolve a different node), records it via [[state]]'s `LoginNodeStore`, and the CLI `rebind`s (`:402`) straight there next session.

> [!warning] `gce list` parsing is fail-loud
> `status`/`endpoint_id` parse `gce list`'s ASCII pipe-table via `_parsed_rows` (`:249`); a gce version/format change **raises** rather than being misread as "no endpoints" (which would trigger a wrong re-provision). See [#8](https://github.com/ryanchard/hpc-bridge/issues/8).

> [!note] Persistent SSH (ControlMaster) — authenticate once
> `SshTarget.argv` (`:61`) appends `ControlMaster=auto` + a `%C`-keyed `ControlPath` + `ControlPersist` (configured by `_control_settings`, [[server]]) when a socket dir is set, so all of a facility's SSH — the ~10-call cold bootstrap *and* the [[discovery]] probe — rides **one authenticated connection**. On a key facility the master opens non-interactively; on an MFA facility the user pre-opens it once (one Duo) and the server's `BatchMode` calls multiplex over it. `control_argv`/`close` (`:83`/`:406`) tear it down (`ssh -O exit`); `ControlPersist` self-reaps regardless. *(Honest nuance: the post-`start` `rebind` to the pinned node means a cold bootstrap ends with two masters — alias + node — so 2 auths, not 1; still decisive vs ~10.)*

> [!note] Endpoint reuse (zero-SSH reconnect)
> `find_online_endpoint` reuse is the keystone that lets a reconnect session avoid SSH **entirely** — one of two MFA mitigations (the other is persistent SSH, above) ([#3](https://github.com/ryanchard/hpc-bridge/issues/3)). See [[Two-channel architecture]] and [[Discovery today]].

## See also
[[Standing up the endpoint]] · [[Credential seeding]] · [[MEP & templated endpoints]] · [[facility-base]] · [[state]] · [[credentials]]
