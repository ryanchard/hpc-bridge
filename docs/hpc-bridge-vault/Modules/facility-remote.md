# facility-remote.py — `facility/remote.py`

> [!abstract] Role
> Everything machine-specific for a remote Slurm cluster, behind one [[facility-base|Facility]]: the SSH transport, the per-facility `MachineProfile`, the `globus-compute-endpoint` CLI driver, and `SlurmFacility` (bootstrap / provision / teardown / config template).

## The pieces

- **SSH transport** — `SshTarget` (`:37`) + `ssh_exec()` (`:55`): key-only (`BatchMode`, `IdentitiesOnly`), reaps the child on timeout/cancel (no process/FD leak). The control channel of [[Two-channel architecture]].
- **Per-facility data** — `MachineProfile` (`:86`) + `anvil_profile()` (`:109`): host, `env_setup` (module + venv), `interface`, partition, account, scratch… exactly the facts a [[Discovery today|discovery]] index is meant to supply instead of hardcoding.
- **gce driver** — `RemoteEndpointCLI` (`:151`): runs `globus-compute-endpoint` over SSH via `_gce` (`:163`); also `login_exec` (`:167`, backs the `login_shell` tool), `seed_storage_db` (`:238`, [[Credential seeding]]), `configure`/`start`/`stop`, and `cancel_blocks` (`:293`).
- **Orchestration** — `SlurmFacility` (`:355`): `bootstrap` (`:471`), `provision` (`:512`), `config_template` (`:378`, [[MEP & templated endpoints]]), `teardown` (`:545`), `manager_online` (`:562`, web), `find_online_endpoint` (`:569`, web reuse).

## How a stand-up flows

`bootstrap` (`:471`) is the entry point, and it is **reuse-or-SSH**: it first asks the Globus *web* service whether we already own an online endpoint (`find_online_endpoint`, `:569`) → reuse over AMQP, **zero SSH**. Only if none is online does it seed credentials (when needed) and call `provision` (`:512`): `configure` if absent → write the engine-free manager `config.yaml` + the UEP template → `start` (detached) → capture & **pin** the login node. See [[Standing up the endpoint]].

> [!warning] Login-node pinning
> The manager lives on ONE login node, but HPC SSH aliases round-robin. `start` (`:267`) captures the FQDN *in the same SSH connection* that launches the daemon (a separate probe could resolve a different node), records it via [[state]]'s `LoginNodeStore`, and the CLI `rebind`s straight there next session.

> [!warning] `gce list` parsing is fail-loud
> `status`/`endpoint_id` parse `gce list`'s ASCII pipe-table via `_parsed_rows` (`:201`); a gce version/format change **raises** rather than being misread as "no endpoints" (which would trigger a wrong re-provision). See [#8](https://github.com/ryanchard/hpc-bridge/issues/8).

> [!note] SSH-once
> `find_online_endpoint` reuse is the keystone that lets a reconnect session avoid SSH entirely — the load-bearing mitigation for MFA facilities ([#3](https://github.com/ryanchard/hpc-bridge/issues/3)). See [[Two-channel architecture]] and [[Discovery today]].

## See also
[[Standing up the endpoint]] · [[Credential seeding]] · [[MEP & templated endpoints]] · [[facility-base]] · [[state]] · [[credentials]]
