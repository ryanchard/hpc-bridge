# facility-remote.py — `facility/remote.py`

> [!abstract] Role
> Everything machine-specific for a remote **Slurm or PBS** cluster, behind one [[facility-base|Facility]]: the SSH transport, the per-facility `MachineProfile`, the `globus-compute-endpoint` CLI driver, and `SlurmFacility` (bootstrap / provision / teardown / config template). Despite the name, `SlurmFacility` drives **both** schedulers — the template split, not the class, is scheduler-specific.

## The pieces

- **SSH transport** — `SshTarget` (`:42`) + `ssh_exec()` (`:112`): `BatchMode` (never prompts), reaps the child on timeout/cancel (no process/FD leak). **Identity defers to `~/.ssh/config`** — `user`/`key_path` are optional; absent ⇒ a bare `host` so OpenSSH resolves `User`/`IdentityFile` **and `ProxyJump`** itself (`-i`/`IdentitiesOnly` only with an explicit key). That `ProxyJump` deferral is why a **bastion two-hop** (ALCF Aurora: `bastion.alcf.anl.gov` → login node) is transparent — no new code ([[Aurora (PBS + bastion) bring-up]]). Also drives the un-indexed [[discovery]] probe (`ssh_exec` on a bare target, pre-endpoint). The control channel of [[Two-channel architecture]]; see *Persistent SSH* below.
- **Per-facility data** — `MachineProfile` (`:182`): host, `env_setup` (module + venv), `interface`, partition, account, scratch, plus **`scheduler`** (`slurm`/`pbs`) and **`cpus_per_node`** (PBS) — supplied by the [[Facility catalog|catalog]] (`profile_from_catalog_entry`, `:207`, which derives `endpoint_name` = `hpc-bridge-<id>` when a seed omits it), no longer hardcoded per machine.
- **gce driver** — `RemoteEndpointCLI` (`:255`): runs `globus-compute-endpoint` over SSH via `_gce` (`:267`); also `login_exec` (`:271`, backs the `login_shell` tool), `seed_storage_db` (`:342`, [[Credential seeding]]), `configure`/`start`/`stop`, `cancel_blocks` (`:398`, scheduler-aware — `scancel`, or `qdel` via `_cancel_blocks_pbs` (`:434`)), and `close` (`:495`, drops the ControlMaster).
- **Orchestration** — `SlurmFacility` (`:602`): `bootstrap` (`:682`), `provision` (`:723`), `config_template` (`:625`, picks `_SLURM_TEMPLATE` (`:513`) / `_PBS_TEMPLATE` (`:554`) by `profile.scheduler` — [[MEP & templated endpoints]]), `teardown` (`:757`), `manager_online` (`:775`, web), `find_online_endpoint` (`:782`, web reuse).

## How a stand-up flows

`bootstrap` (`:682`) is the entry point, and it is **reuse-or-SSH**: it first asks the Globus *web* service whether we already own an online endpoint (`find_online_endpoint`, `:782`) → reuse over AMQP, **zero SSH**. Only if none is online does it seed credentials (when needed) and call `provision` (`:723`): `configure` if absent → write the engine-free manager `config.yaml` + the scheduler's UEP template → `start` (detached) → capture & **pin** the login node. See [[Standing up the endpoint]].

> [!warning] Login-node pinning
> The manager lives on ONE login node, but HPC SSH aliases round-robin. `start` (`:371`) captures the FQDN *in the same SSH connection* that launches the daemon (a separate probe could resolve a different node), records it via [[state]]'s `LoginNodeStore`, and the CLI `rebind`s (`:491`) straight there next session. **`_routable_pin` (`:164`) first drops a FQDN that isn't reachable from the client** — an internal suffix (`.local`/`.internal`), a single label, or a **management-plane** name (`hostmgmt`/`cm`/`mgmt`/`ipmi`/`bmc` labels, e.g. Aurora's `aurora-uan-0009.hostmgmt.cm.aurora.alcf.anl.gov`) — falling back to the alias, so a non-routable pin can't break teardown/reconnect ([#33](https://github.com/ryanchard/hpc-bridge/pull/33)).

> [!warning] PBS cancel reads bare `qstat -f`, never `-u`
> Slurm block-release matches `squeue -u`, but PBS Pro's `-u` filter suppresses full-format output entirely — so `_cancel_blocks_pbs` (`:434`) uses bare `qstat -f` (unwrapping its 80-col line continuations) scoped by the endpoint-unique `uep.<eid>` marker → `qdel`. A `-u` filter would silently no-op and let the block burn to walltime (caught in live Polaris validation, [#28](https://github.com/ryanchard/hpc-bridge/issues/28)). The marker scoping means it never touches another endpoint's jobs, same as the Slurm path.

> [!warning] `gce list` parsing is fail-loud
> `status`/`endpoint_id` parse `gce list`'s ASCII pipe-table via `_parsed_rows` (`:305`); a gce version/format change **raises** rather than being misread as "no endpoints" (which would trigger a wrong re-provision). See [#8](https://github.com/ryanchard/hpc-bridge/issues/8).

> [!note] Persistent SSH (ControlMaster) — authenticate once
> `SshTarget.argv` (`:61`) appends `ControlMaster=auto` + a `%C`-keyed `ControlPath` + `ControlPersist` (configured by `_control_settings`, [[server]]) when a socket dir is set, so all of a facility's SSH — the ~10-call cold bootstrap *and* the [[discovery]] probe — rides **one authenticated connection**. On a key facility the master opens non-interactively; on an MFA facility the user pre-opens it once (one Duo) and the server's `BatchMode` calls multiplex over it. `close` (`:495`) tears it down (`ssh -O exit`); `ControlPersist` self-reaps regardless. *(Honest nuance: the post-`start` `rebind` to the pinned node means a cold bootstrap ends with two masters — alias + node — so 2 auths, not 1; still decisive vs ~10.)*

> [!note] Endpoint reuse (zero-SSH reconnect)
> `find_online_endpoint` reuse is the keystone that lets a reconnect session avoid SSH **entirely** — one of two MFA mitigations (the other is persistent SSH, above) ([#3](https://github.com/ryanchard/hpc-bridge/issues/3)). See [[Two-channel architecture]] and [[Discovery today]].

## See also
[[Standing up the endpoint]] · [[Credential seeding]] · [[MEP & templated endpoints]] · [[facility-base]] · [[state]] · [[credentials]]
