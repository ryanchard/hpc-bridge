"""Provision a Globus Compute endpoint on a remote Slurm login node over SSH.

The facility-agnostic runtime (dispatch / session shell / cost) is unchanged;
everything machine-specific lives in a `MachineProfile` (data), so new HPC
systems are added as profiles rather than code. Anvil (Purdue/ACCESS) is the
first profile — its key-based SSH (no per-login MFA) makes the bootstrap the
easy case; OTP facilities will later route SSH through the credential broker.
"""
from __future__ import annotations

import asyncio
import re
import shlex
from dataclasses import dataclass

import yaml

from ..profile import Profile
from .base import EndpointHandle

# ---------------------------------------------------------------- SSH transport


@dataclass(frozen=True)
class SshTarget:
    host: str
    user: str
    key_path: str
    connect_timeout: int = 20

    def argv(self, remote_cmd: str) -> list[str]:
        # Key-only, never-prompt: BatchMode fails fast instead of hanging on a
        # password/MFA prompt (and proves the key-only assumption at Anvil).
        return [
            "ssh", "-i", self.key_path,
            "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={self.connect_timeout}",
            "-o", "StrictHostKeyChecking=accept-new",
            f"{self.user}@{self.host}", remote_cmd,
        ]


async def ssh_exec(
    target: SshTarget, remote_cmd: str, *, stdin: str | None = None, timeout: float = 120.0
) -> tuple[int, str, str]:
    """Run one command on the remote host over SSH; returns (rc, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *target.argv(remote_cmd),
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    payload = stdin.encode() if stdin is not None else None
    out, err = await asyncio.wait_for(proc.communicate(payload), timeout)
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


# ------------------------------------------------------------- machine profiles


@dataclass(frozen=True)
class MachineProfile:
    """Per-facility data: how to reach the endpoint binary and what Slurm to request."""

    name: str
    endpoint_name: str
    env_setup: str          # bash that puts globus-compute-endpoint on PATH (module + venv)
    interface: str          # address_by_interface ifname for the worker (e.g. ib0)
    partition: str
    account: str
    worker_init: str        # replays env_setup on the compute worker (parsl writes it into sbatch)
    walltime: str = "00:30:00"
    max_workers_per_node: int = 2
    amqp_port: int = 443    # facilities firewall the default AMQPS 5671; 443 is allowed
    scheduler_options: str | None = None
    scratch_root: str | None = None  # session-shell root on the shared filesystem


def anvil_profile(
    *,
    account: str,
    user: str,
    partition: str = "debug",
    module: str = "anaconda/2024.02-py311",
    endpoint_name: str = "hpc-bridge",
    walltime: str = "00:30:00",
) -> MachineProfile:
    """Anvil (Purdue/ACCESS) profile — validated 2026-06-03 (worker on compute node a006)."""
    venv = f"/home/{user}/hpc-bridge/gce-venv"
    env = f"module load {module} && source {venv}/bin/activate"
    return MachineProfile(
        name="anvil",
        endpoint_name=endpoint_name,
        env_setup=env,
        interface="ib0",
        partition=partition,
        account=account,
        worker_init=env,
        walltime=walltime,
        scratch_root=f"/anvil/scratch/{user}/.hpc-bridge",
    )


# ------------------------------------------------- globus-compute-endpoint / SSH


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class RemoteEndpointCLI:
    """Drive `globus-compute-endpoint` on a remote login node over SSH.

    Mirrors the local `EndpointCLI` (configure / start / stop) but runs every
    command inside the remote venv, writes config files over SSH, and reads the
    UUID from `list` (v4 MEP mode does not reliably write endpoint.json)."""

    def __init__(self, target: SshTarget, env_setup: str, *, remote_dir: str = "$HOME/.globus_compute") -> None:
        self.target = target
        self.env_setup = env_setup
        self.remote_dir = remote_dir

    async def _gce(self, *args: str) -> tuple[int, str, str]:
        inner = f"{self.env_setup} && globus-compute-endpoint " + " ".join(shlex.quote(a) for a in args)
        return await ssh_exec(self.target, f"bash -lc {shlex.quote(inner)}")

    async def login_exec(self, command: str) -> tuple[int, str, str]:
        """Run a read-only command on the login node over SSH for facility discovery
        (sinfo/sacctmgr/module). Unlike `_gce` it does NOT source the endpoint venv — base
        login-node tools are already on PATH — and it provisions nothing."""
        return await ssh_exec(self.target, f"bash -lc {shlex.quote(command)}")

    async def configure(self, name: str, multi_user: bool = False) -> None:
        # Force --multi-user false (personal endpoint): the default auto-selects from
        # POSIX caps and can silently create an identity-mapping MEP — see endpoint.py.
        rc, out, err = await self._gce("configure", "--multi-user", "true" if multi_user else "false", name)
        if rc != 0:
            msg = (err or out).strip()
            if "already" in msg.lower() or "configexists" in msg.lower():  # dir exists -> fine
                return
            raise RuntimeError(f"remote configure failed: {msg}")

    async def status(self, name: str) -> str | None:
        """'running' | 'configured' | None — drives idempotent (re)provisioning."""
        rc, out, _err = await self._gce("list")
        if rc != 0:
            return None
        for line in out.splitlines():
            if name in line:
                return "running" if "Running" in line else "configured"
        return None

    async def write_config(self, name: str, manager_yaml: str, uep_yaml: str) -> None:
        base = f"{self.remote_dir}/{name}"
        await self._write_file(f"{base}/config.yaml", manager_yaml)
        await self._write_file(f"{base}/user_config_template.yaml.j2", uep_yaml)

    async def _write_file(self, path: str, content: str) -> None:
        # Double-quote (NOT shlex.quote) so the remote shell expands $HOME; content
        # rides stdin so it needs no escaping. `path` is a controlled config path.
        rc, out, err = await ssh_exec(self.target, f'cat > "{path}"', stdin=content)
        if rc != 0:
            raise RuntimeError(f"remote write {path} failed: {(err or out).strip()}")

    async def start(self, name: str) -> str:
        rc, out, err = await self._gce("start", name, "--detach")
        if rc != 0:
            raise RuntimeError(f"remote start failed: {(err or out).strip()}")
        return await self.endpoint_id(name)

    async def stop(self, name: str) -> None:
        # Best-effort: `stop` can throw a psutil traceback yet still cancel the block.
        await self._gce("stop", name)

    async def endpoint_id(self, name: str) -> str:
        rc, out, err = await self._gce("list")
        if rc != 0:
            raise RuntimeError(f"remote list failed: {(err or out).strip()}")
        for line in out.splitlines():
            if name in line:
                m = _UUID.search(line)
                if m:
                    return m.group(0)
        raise RuntimeError(f"could not find endpoint {name!r} in `list` output")


# ---------------------------------------------------------------- the facility


class SlurmFacility:
    """A Globus Compute endpoint on a remote Slurm cluster, provisioned over SSH."""

    def __init__(self, profile: MachineProfile, cli: RemoteEndpointCLI, *, client_factory=None) -> None:
        self.profile = profile
        self.cli = cli
        self.name = profile.name
        self._client_factory = client_factory or self._default_client

    @property
    def scratch_root(self) -> str | None:
        return self.profile.scratch_root

    def config_template(self, hpc: Profile) -> dict:
        # interactive => request a block eagerly the moment the UEP starts (init_blocks=1).
        # NOTE: in the v4 MEP model the UEP only forks on the FIRST task, so this does NOT
        # pre-warm before provision() returns — the first run_shell still cold-starts. batch
        # => fully on-demand. min_blocks is ALWAYS 0 so the idle timer can release the compute
        # node: Parsl never scales below min_blocks, so min_blocks>=1 would bill the allocation
        # until walltime or an explicit teardown. The block scale-to-zero (min_blocks=0 +
        # max_idletime) is the cost net — VALIDATED live on Anvil (2026-06-04): the Slurm block
        # self-released ~1 idle window after the last task, both client-open and client-closed.
        # (gce's idle_heartbeats_soft, the UEP self-shutdown knob, is deliberately NOT set: live
        # testing showed it never fires here — the UEP never flags idle once the block is gone —
        # so it's a no-op footgun. The UEP + manager are free login-node processes, freed by
        # stop_endpoint / the canary.) CAVEAT: manager_online() sees only the manager, not worker
        # readiness — so after an idle release the endpoint manager still reads "online" while the
        # next task cold-starts (confirmed live). The fix is the worker-registration canary, now
        # IMPLEMENTED in the dispatch layer (runner.GlobusRunner.canary + server._confirm_worker):
        # warmth requires a worker to actually answer a trivial task, so ensure_endpoint_up reports
        # "provisioning" (not "up") and run_shell returns cold_start (not a 124) until one does.
        warm = hpc.mode == "interactive"
        p = self.profile
        provider: dict = {
            "type": "SlurmProvider",
            "partition": p.partition,
            "account": p.account,
            "launcher": {"type": "SrunLauncher"},
            "worker_init": p.worker_init,
            "walltime": p.walltime,
            "init_blocks": 1 if warm else 0,
            "min_blocks": 0,
            "max_blocks": 1,
        }
        if p.scheduler_options:
            provider["scheduler_options"] = p.scheduler_options
        return {
            "engine": {
                "type": "GlobusComputeEngine",
                "max_workers_per_node": p.max_workers_per_node,
                "address": {"type": "address_by_interface", "ifname": p.interface},
                # Scale the worker block to zero after max_idletime idle (needs min_blocks=0).
                "job_status_kwargs": {
                    "max_idletime": float(hpc.max_idletime_s),
                    "strategy_period": 30,
                },
                "provider": provider,
            },
        }

    async def provision(self, hpc: Profile) -> EndpointHandle:
        # Idempotent: reuse a running endpoint; configure only if it doesn't exist yet
        # (re-configuring an existing one raises ConfigExists).
        name = self.profile.endpoint_name
        st = await self.cli.status(name)
        if st == "running":
            return EndpointHandle(endpoint_id=await self.cli.endpoint_id(name), name=name)
        if st is None:
            await self.cli.configure(name)
        # config.yaml is the engine-free MANAGER config (amqp_port lives here); the
        # engine goes in the UEP template (v4 manager+template model). Rewrite both so
        # a re-provision always applies the current profile.
        manager = yaml.safe_dump(
            {"display_name": name, "amqp_port": self.profile.amqp_port}, sort_keys=False
        )
        uep = yaml.safe_dump(self.config_template(hpc), sort_keys=False)
        await self.cli.write_config(name, manager, uep)
        eid = await self.cli.start(name)
        return EndpointHandle(endpoint_id=eid, name=name)

    async def teardown(self, endpoint_id: str) -> None:
        """Stop the endpoint and cancel its Slurm block(s) — the cost-control exit."""
        await self.cli.stop(self.profile.endpoint_name)

    async def login_exec(self, command: str) -> tuple[int, str, str]:
        """Read-only login-node command for discovery — no block, no allocation (delegates
        to the SSH CLI). This is the channel that backs the `login_shell` tool."""
        return await self.cli.login_exec(command)

    async def manager_online(self, endpoint_id: str) -> bool:
        # Web-service query (runs in the MCP process, not over SSH) — works for any
        # endpoint UUID regardless of where it runs.
        client = self._client_factory()
        status = await asyncio.to_thread(client.get_endpoint_status, endpoint_id)
        return status.get("status") == "online"

    def _default_client(self):
        from globus_compute_sdk import Client

        return Client()
