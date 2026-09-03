# agentic/fakecluster — a Docker-simulated Slurm cluster for hpc-bridge live testing

A local, deterministic Slurm cluster (`docker compose`) that stands in for **globus1** when the lab
cluster is contended: a real `slurmctld` + `slurmdbd` (MariaDB — so `sacct`/`sacctmgr` work), two
compute nodes, and a **login node running sshd** with the harness's pool users, all sharing `/home`.
hpc-bridge's real SSH bootstrap runs against it unchanged: the discover-first probe proposes the
right config, a personal Globus Compute endpoint self-provisions into a `uv` venv on the login node,
and a `SlurmProvider` block is `sbatch`'d onto a compute container whose worker phones home over the
compose network. Outbound HTTPS/AMQPS to Globus works from every container (Docker NAT).

**It complements globus1; it does not replace it.** No module system, no MFA, no site Slurm quirks,
no accounting enforcement, no GPUs, no facility MEP (see *Limitations*). What it gives you is a
cluster you can saturate, kill blocks on, and reset in seconds — and run regressions on when globus1
is busy.

Status: **spike (2026-09-03) — all three steps proven**, including a full hpc-bridge bootstrap →
compute block → `run_shell` → teardown in 96 s (see *Stretch result*). Nothing here is wired into
`run_smoke.sh` / `run_suite.py` yet (see *Pointing the harness at it*).

```
            host (macOS, Docker Desktop)                 hpcb-fake_default (compose network)
  ┌──────────────────────────────────┐        ┌──────────────────────────────────────────────────┐
  │ ssh -p 2222 hpcbridge-test-00@localhost ──┼─▶ login  (sshd; endpoint manager + UEPs live here)│
  │ ~/.ssh/hpcb-fake (test key)      │        │   │ sbatch                    ▲ workers connect  │
  │                                  │        │   ▼                           │ back (eth0)      │
  │ agentic jail container ──────────┼─▶      │ slurmctld ◀── slurmdbd ◀── mysql (mariadb:11)    │
  │   --network hpcb-fake_default    │        │   │                                              │
  │   HPC_BRIDGE_SSH_HOST=login      │        │   ├──▶ c1 (slurmd)   shared volume `home` = /home │
  └──────────────────────────────────┘        │   └──▶ c2 (slurmd)   shared volume `munge`       │
                                              └──────────────────────────────────────────────────┘
```

## Prerequisites

- Docker Desktop (Apple silicon is the tested host; images are **arm64-native** — Ubuntu 24.04 ships
  `slurm-wlm` 23.11 + `munge` for arm64, so there's no x86 emulation). ~2 GB of images.
- Nothing else. The test SSH key is generated for you **outside the repo** (`~/.ssh/hpcb-fake`).
- For the stretch driver only: a logged-in Globus Compute `~/.globus_compute/storage.db` on the host
  and the agentic jail image (`hpc-bridge-agentic`, built by `agentic/run_smoke.sh`).

## Quickstart (all commands from the repo root)

```bash
# 1. Build + start + wait until schedulable (first build ~3 min; later starts ~20 s).
agentic/fakecluster/bin/up.sh
#    → generates ~/.ssh/hpcb-fake if missing, `docker compose up -d --build`, then waits for
#      2 idle nodes in `main`, `sacct`, and sshd accepting the key. Ends with "READY".

# 2. Step-1 proof: sbatch over ssh as a pool user → COMPLETED on a compute container in sacct.
agentic/fakecluster/bin/prove-sbatch.sh
#    PROOF OK: job 1 COMPLETED on c1

# 3. Step-2 sweep: tools, NIC, outbound HTTPS/AMQPS, srun-in-a-job, shared /home, uv provisioning.
agentic/fakecluster/bin/check-login.sh

# 4. (Stretch) hpc-bridge's REAL bootstrap → login shape → compute block → run_shell → teardown,
#    driven from inside the agentic jail image joined to the cluster network.
agentic/fakecluster/bin/stretch.sh

# Poke at it by hand:
ssh -p 2222 -i ~/.ssh/hpcb-fake -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    hpcbridge-test-00@localhost 'sinfo; squeue; sacct -X'
docker compose -f agentic/fakecluster/docker-compose.yml logs -f slurmctld c1 c2 login

# Stop (keeps volumes: homes, accounting DB, munge key) / wipe everything.
agentic/fakecluster/bin/down.sh
agentic/fakecluster/bin/down.sh --wipe
```

The manual step-1 proof from the spike brief, verbatim, also works:
```bash
ssh -p 2222 -i ~/.ssh/hpcb-fake hpcbridge-test-00@localhost \
  'sbatch --wrap "hostname; sleep 5" -p main -N1 && sleep 8 && sacct -X -o JobID,State,NodeList'
```

Knobs (env): `HPCB_FAKE_SSH_PORT` (default 2222), `HPCB_FAKE_KEY` (default `~/.ssh/hpcb-fake`),
`HPCB_FAKE_USER` (default `hpcbridge-test-00`), `SLURM_DB_PASS` (the throwaway MariaDB password —
a local fixture inside the private network, not a secret).

## What's in the cluster

| Thing | Value |
|---|---|
| Slurm | 23.11.4 (Ubuntu 24.04 `slurm-wlm`), munge auth, `slurmdbd` + MariaDB 11 accounting |
| Cluster / partition | `ClusterName=fake`, one partition **`main`** (default), `MaxTime=2-00:00:00` |
| Nodes | `c1`, `c2` — `CPUs=4 RealMemory=4000` (declared; `config_overrides` so slurmd doesn't argue with the VM's real 18 cores) |
| Login node | service/hostname `login`, sshd on container port 22 → host **2222**; `hostname -f` = `login` (single-label ⇒ hpc-bridge keeps the alias, no node pin) |
| Users | `hpcbridge-test` (uid 1999) + **`hpcbridge-test-00/-01/-02`** (uids 2000–2002), group `hpcb` (2000); same on every node; key-only ssh; no password |
| Shared FS | named volume `home` mounted at **`/home`** on `login`, `c1`, `c2` (the endpoint venv `~/hpc-bridge/gce-venv`, `~/.globus_compute`, and the session scratch `~/.hpc-bridge` are all visible to workers) |
| Accounting | cluster `fake` registered; account `hpcb` with all pool users; **no enforcement** (`--account` not required — like globus1's `AccountingStorageEnforce=none`) |
| Toolchain on every node | Python **3.12.3**, **uv**, `bash base64 scancel squeue sacct sinfo sacctmgr sbatch srun ip curl git`, build-essential |
| NIC | **`eth0`** (the only non-lo interface — `address_by_interface: eth0`) |
| Network | outbound HTTPS + AMQPS 443 to Globus OK; nothing published but the ssh port |

### hpc-bridge facility config

This is what hpc-bridge's discovery probe proposes for it (verified live — you can pass it as
`connect_facility(details=…)` verbatim, or just use `connect_facility(facility="fake", ssh_host=…)`):

```yaml
ssh_host: login            # `hpcb-fake` from the host via the ssh alias; `login` from a container on the network
interface: eth0
env_setup: "[ -d {venv} ] || uv venv {venv}; . {venv}/bin/activate; command -v globus-compute-endpoint >/dev/null 2>&1 || uv pip install -q globus-compute-endpoint"
scratch_root: /home/{user}/.hpc-bridge
partition: main
scheduler: slurm
walltime: "00:30:00"
```

Two ways to reach the login node:

- **From the host** (an interactive Claude Code session, `uv run hpc-bridge`): append
  `ssh_config.example` to `~/.ssh/config` (alias `hpcb-fake` → `localhost:2222`, user
  `hpcbridge-test-00`, the test key, host-key pinning off), then `HPC_BRIDGE_SSH_HOST=hpcb-fake`. hpc-bridge's `SshTarget` has no port field — the alias is what carries the port.
- **From a container** (the agentic jail, the stretch driver): join the network
  (`docker run --network hpcb-fake_default …`) and use `HPC_BRIDGE_SSH_HOST=login` on port 22 — no
  alias, no port mapping. This is how `bin/stretch.sh` does it.

## Pointing the harness at it (not done in the spike — the small changes needed)

`run_smoke.sh` / `run_suite.py` already take the SSH target from env (`HPC_BRIDGE_SSH_HOST`,
`HPCB_TEST_SSH_USER`, `HPCB_TEST_SSH_KEY`) and hardcode the pool names `hpcbridge-test-NN` — which
this cluster provides. What's missing is small:

1. **`run_smoke.sh`:** `HPC_BRIDGE_SSH_HOST` is hardcoded to `globus1.cs.uchicago.edu`; make it
   `${HPCB_TEST_SSH_HOST:-globus1.cs.uchicago.edu}`. Add an opt-in `HPCB_DOCKER_NETWORK` that
   appends `--network "$HPCB_DOCKER_NETWORK"` to `docker run` (so the jail can reach `login:22`).
   Then a fake-cluster run is just
   `HPCB_TEST_SSH_HOST=login HPCB_DOCKER_NETWORK=hpcb-fake_default HPCB_TEST_SSH_USER=hpcbridge-test-00 HPCB_TEST_SSH_KEY=~/.ssh/hpcb-fake ./agentic/run_smoke.sh happy_path`.
2. **`run_suite.py`:** same host/network pass-through; the pool is 3 users here (`--concurrency 3`
   max, or add `-03..-09` to the Dockerfile — one line).
3. **Scenarios:** any that assume globus1 facts (3 nodes in `saturation`'s SETUP, the `enP7s7` NIC,
   `glabs`, the catalog entry) need a per-target parameter; `happy_path`, `spend_refusal`,
   `session_persistence`, `endpoint_reuse*`, `facility_cache`, `spend_gate_enforced`,
   `long_task_via_handle`, `idle_release_kill` look target-agnostic.
4. **Postchecks over SSH** use `$HOME/hpc-bridge/gce-venv/bin/globus-compute-endpoint` and
   `squeue`/`sacct`/`scancel` — all present and on the same paths here.

The agent itself is untouched: it sees an un-indexed facility, probes it, and proposes exactly the
config above (BYO discovery — the same path a real new facility takes).

## Chaos recipes (what globus1 can't give you on demand)

```bash
F=agentic/fakecluster/docker-compose.yml
# Saturate the cluster as ANOTHER pool user (the `saturation` scenario's SETUP, without waiting for a real user to do it).
ssh -p 2222 -i ~/.ssh/hpcb-fake hpcbridge-test-01@localhost 'for i in 1 2; do sbatch -p main -N1 --exclusive --wrap "sleep 1800"; done'
# Kill a running block out from under the endpoint (the #21 / poll_task-must-notice case).
docker compose -f $F exec login scancel -u hpcbridge-test-00
# Kill a compute node mid-job (NODE_FAIL), then bring it back.
docker compose -f $F kill c1 && sleep 30 && docker compose -f $F start c1
# Restart the controller (jobs survive via StateSaveLocation).
docker compose -f $F restart slurmctld
# Fresh cluster in ~20 s (also wipes the pool users' homes → no stale endpoints/venvs).
agentic/fakecluster/bin/down.sh --wipe && agentic/fakecluster/bin/up.sh
```

## Limitations (honest list)

- **Not a facility.** No module system (`module load` env_setups won't work — the uv path does), no
  MFA/Duo (the `needs_preauth` path is untestable here), no site `job_submit` plugins, no QOS/limits,
  no accounting enforcement or balances (`allocation_command` paths untestable), no GPUs/`gres`.
- **No multi-user endpoint (MEP)** — the M1/`MEPFacility` path isn't covered. Feasible later: run
  `globus-compute-endpoint configure --multi-user` as root in the `login` container with an identity
  mapping `<your Globus identity> → hpcbridge-test-00`; a root-run MEP inside a container is the
  standard deployment shape, so this is a day of work, not a research problem. Out of scope for the spike.
- **Topology:** everything shares one kernel/VM; `hostname -f` is single-label; one NIC (`eth0`);
  `srun`/`slurmstepd` work but with no cgroup confinement (`CgroupPlugin=cgroup/v1` as a no-op —
  Ubuntu's 23.11 build has no `disabled` plugin and cgroup/v2 needs systemd/dbus).
- **Globus is still real.** The endpoint registers with and dispatches through the real Globus
  Compute service (the host's `storage.db` identity), so the cluster is local but the control plane
  isn't: endpoints you create here show up in your Globus account (the drivers tear theirs down).
- **Timing is not globus1's.** Block scheduling is instant (idle nodes, no queue), so "wait for the
  queue" behaviour needs the saturation recipe above to reproduce; cold-start is ~1–2 min (UEP fork
  + Parsl strategy period + worker import), not ARM-DGX-with-NFS timing.
- **Version skew is possible:** the login node installs whatever `globus-compute-endpoint` PyPI
  serves (4.16.0 today) while the jail's SDK is lock-pinned (4.13.0). Pin it in `env_setup` if that
  ever bites (globus1's MEP seed does exactly this).

## Stretch result (2026-09-03) — PASSED, 96 s end to end

`bin/stretch.sh` drove hpc-bridge's own server seams (`_connect_facility` → `_ensure_endpoint_up` →
`_run_shell` → `_stop_endpoint` → `_teardown_endpoint`) from inside the `hpc-bridge-agentic` jail
image on the cluster network, with the host's real Globus identity. Timeline:

| t | step | result |
|---|---|---|
| 1.0 s | `connect_facility("fake", ssh_host="login")` | `proposed_facility_details` — exactly the config above (`eth0`, `main`, uv env_setup, `/home/{user}/.hpc-bridge`) |
| 15.3 s | `connect_facility(details=…)` | `provisioning` on the **first** call (seeded `storage.db`, `uv venv` + `pip install globus-compute-endpoint`, `configure`, `start`) — no #39 registration-lag race here |
| 40.1 s | `ensure_endpoint_up(shape="login")` | `up` — "worker live on login (py3.12.3)" |
| 42.1 s | `run_shell(…, shape="login")` | `login / hpcbridge-test-00 / main* idle 2` |
| 86.5 s | `ensure_endpoint_up(shape="compute", partition="main", confirm_spend=True)` | `up` — "worker live on **c1**"; Slurm job 6 `parsl.GlobusComputeEngine-…block-0` RUNNING on c1 |
| 87.5 s | `run_shell("hostname; …", shape="compute")` | **`c1`**, `SLURM_JOB_ID=6` — the worker on the compute container phoned home to the login node's interchange over `eth0` |
| 89–90 s | two more `run_shell(shape="compute")` | warm hits; session shell persisted `cd /tmp` + `export FOO=bar` across calls |
| 91.5 s | `stop_endpoint` | `down` — "compute block released over AMQP (released 6)" (confirmed, not `draining`) |
| 95.6 s | `teardown_endpoint` | `down` — manager gce-stopped + deleted; `squeue` empty; `sacct` shows job 6 `CANCELLED` after 17 s |

So the whole hpc-bridge SSH-bootstrap path — discovery, credential seeding, self-provisioned
endpoint, login shape, billed compute block on a scheduler, dispatch over AMQP, session shell,
release, teardown — runs against this cluster with **zero product changes**.

## Gotchas hit while building this (so you don't again)

- **slurmd 23.11 initialises the cgroup plugin unconditionally**, and `cgroup/v2` wants a systemd
  dbus scope → `slurmd initialization failed` in a plain container. `CgroupPlugin=disabled` is
  24.05+; on 23.11 pin `cgroup/v1` (its init is a no-op). (`slurm/cgroup.conf`.)
- **`srun` eats stdin.** A script fed to `ssh … bash` over stdin dies silently at the first `srun`
  (it forwards stdin to the task). `srun … </dev/null`.
- **DNS flaps while nodes crash-loop.** `slurmctld: Unable to resolve "c2"` just means the `c2`
  container isn't running (compose DNS only answers for live containers); it stops once slurmd stays up.
- **Locked accounts.** `useradd` without a password leaves `!` in shadow; with `UsePAM no`, sshd
  refuses pubkey login to a locked account. `useradd -p '*'`.
- **Homes on a volume.** `useradd -m` in the Dockerfile is shadowed by the `/home` volume; the login
  entrypoint creates the homes (+ `authorized_keys`) on first boot.
- **Host keys churn** on every image build — the ssh alias disables known_hosts pinning.

## Design notes

- **One image, four roles** (`entrypoint.sh <role>`): the endpoint venv on the shared `/home` has a
  python symlink into the system interpreter, so login and compute must be the *same* image; it also
  guarantees identical `slurm`/`munge`/pool uids everywhere (munge credentials carry uids).
- **The munge key is generated per `up`** by `slurmctld` (1 KiB of urandom into the `munge` volume,
  written atomically); every other role waits for it. Never a real cluster's key; nothing in the repo.
- **`auth/slurm`** (`auth_slurm.so` is in the package) would remove munge entirely — a small
  simplification for the real tier (`AuthType=auth/slurm`, `CredType=cred/slurm`, one `slurm.key`).
- **Nothing secret is committed:** the test key lives in `~/.ssh/`, the Globus `storage.db` is
  mounted from the host at run time, the DB password is a local fixture.
