"""Provision a Globus Compute endpoint on a remote Slurm login node over SSH.

The facility-agnostic runtime (dispatch / session shell / cost) is unchanged;
everything machine-specific lives in a `MachineProfile` (data), so new HPC
systems are added as profiles rather than code. Anvil (Purdue/ACCESS) is the
first profile — its key-based SSH (no per-login MFA) makes the bootstrap the
easy case; OTP facilities will later route SSH through the credential broker.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import shlex
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import yaml
from globus_compute_sdk.sdk.auth.token_storage import (
    _get_storage_filepath,
    _resolve_namespace,
)

from ..credentials import build_minimal_storage_db
from ..profile import Profile
from ..state import EndpointRecord, LoginNodeStore
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

    @staticmethod
    def _list_rows(out: str) -> list[list[str]]:
        """Parse `gce list`'s table into [uuid, status, name] cells per data row.

        Matching the Endpoint Name column EXACTLY (not as a substring of the line) is
        load-bearing: a `hpc-bridge-login` row contains the substring `hpc-bridge`, so a
        naive `name in line` would mis-identify a *different* endpoint as ours and skip
        configure — caught live on Anvil."""
        rows = []
        for line in out.splitlines():
            if "|" not in line:
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) >= 3:
                rows.append(cells)
        return rows

    async def status(self, name: str) -> str | None:
        """'running' | 'configured' | None — drives idempotent (re)provisioning."""
        rc, out, _err = await self._gce("list")
        if rc != 0:
            return None
        for cells in self._list_rows(out):
            if cells[-1] == name:  # exact Endpoint Name match, not a line substring
                return "running" if "Running" in cells[1] else "configured"
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

    async def seed_storage_db(self, local_db: Path) -> None:
        """Ship a (trimmed) storage.db to the remote ~/.globus_compute/storage.db.

        The db is binary SQLite, so it rides stdin base64-encoded and is decoded
        remotely. The directory is created 0700 and the file chmod'd 0600 — this is a
        bearer credential. Raises RuntimeError on any remote step failure."""
        payload = base64.b64encode(Path(local_db).read_bytes()).decode("ascii")
        db_path = f"{self.remote_dir}/storage.db"
        rc, out, err = await ssh_exec(
            self.target,
            f'mkdir -p "{self.remote_dir}" && chmod 700 "{self.remote_dir}"',
        )
        if rc != 0:
            raise RuntimeError(
                f"seed storage.db (mkdir) failed: {(err or out).strip()}"
            )
        rc, out, err = await ssh_exec(
            self.target, f'base64 -d > "{db_path}"', stdin=payload
        )
        if rc != 0:
            raise RuntimeError(
                f"seed storage.db (write) failed: {(err or out).strip()}"
            )
        rc, out, err = await ssh_exec(self.target, f'chmod 600 "{db_path}"')
        if rc != 0:
            raise RuntimeError(
                f"seed storage.db (chmod) failed: {(err or out).strip()}"
            )

    async def start(self, name: str) -> tuple[str, str | None]:
        # Start the daemon AND capture the login node it landed on in the SAME ssh
        # connection: the alias round-robins, so a separate hostname probe could resolve
        # a different node than the one now hosting the manager daemon. The sentinel
        # isolates the FQDN from gce's own stdout.
        inner = (
            f"{self.env_setup} && globus-compute-endpoint start {shlex.quote(name)} "
            f"--detach && echo HPCB_HOST=$(hostname -f)"
        )
        rc, out, err = await ssh_exec(self.target, f"bash -lc {shlex.quote(inner)}")
        if rc != 0:
            raise RuntimeError(f"remote start failed: {(err or out).strip()}")
        host = None
        for line in out.splitlines():
            if line.startswith("HPCB_HOST="):
                host = line[len("HPCB_HOST=") :].strip() or None
        return await self.endpoint_id(name), host

    async def stop(self, name: str) -> None:
        # Best-effort: `stop` can throw a psutil traceback yet still cancel the block.
        await self._gce("stop", name)

    async def wipe_storage_db(self) -> None:
        """Remove the seeded credential from the remote host (best-effort)."""
        await ssh_exec(self.target, f'rm -f "{self.remote_dir}/storage.db"')

    async def endpoint_id(self, name: str) -> str:
        rc, out, err = await self._gce("list")
        if rc != 0:
            raise RuntimeError(f"remote list failed: {(err or out).strip()}")
        for cells in self._list_rows(out):
            if cells[-1] == name:  # exact Endpoint Name match, not a line substring
                m = _UUID.search(cells[0])
                if m:
                    return m.group(0)
        raise RuntimeError(f"could not find endpoint {name!r} in `list` output")

    async def whoami(self) -> bool:
        """True if the remote endpoint can authenticate (storage.db usable)."""
        rc, _out, _err = await self._gce("whoami")
        return rc == 0

    async def hostname_fqdn(self) -> str:
        """The fully-qualified hostname of the login node this SSH connection landed on.

        Anvil-style aliases round-robin; this is how we learn the *specific* node the
        manager daemon will run on, so we can return to it later."""
        rc, out, err = await ssh_exec(self.target, "hostname -f")
        if rc != 0 or not out.strip():
            raise RuntimeError(f"hostname -f failed: {(err or out).strip()}")
        return out.strip().splitlines()[0]

    def rebind(self, host: str) -> None:
        """Re-point this CLI at a specific host (the pinned FQDN) for reconnect."""
        self.target = replace(self.target, host=host)


# ---------------------------------------------------------------- the facility


class SlurmFacility:
    """A Globus Compute endpoint on a remote Slurm cluster, provisioned over SSH."""

    def __init__(
        self,
        profile: MachineProfile,
        cli: RemoteEndpointCLI,
        *,
        client_factory=None,
        store: LoginNodeStore | None = None,
        alias: str | None = None,
    ) -> None:
        self.profile = profile
        self.cli = cli
        self.name = profile.name
        self._client_factory = client_factory or self._default_client
        self.store = store
        self.alias = alias

    @property
    def scratch_root(self) -> str | None:
        return self.profile.scratch_root

    def config_template(self, hpc: Profile) -> tuple[str, dict]:
        """Return (jinja_template_str, default_user_opts) for the UEP template.

        ONE template serves every shape: provider.type and resources are Jinja
        variables rendered per task from user_endpoint_config (see shapes.py). Defaults
        come from the MachineProfile so a bare submit still resolves. min_blocks is
        always 0 (+ max_idletime) so an idle slurm block self-releases — the cost net,
        validated live on Anvil. LocalProvider ignores the slurm keys.

        Profile defaults are injected as json.dumps'd literals via sentinel
        substitution (not %-formatting) so values containing quotes/%/braces can't
        break Jinja compilation."""
        p = self.profile
        eager = 1 if hpc.mode == "interactive" else 0
        template = """\
engine:
  type: GlobusComputeEngine
  max_workers_per_node: {{ max_workers_per_node | default(@@MAXW@@) }}
  address:
    type: address_by_interface
    ifname: {{ interface | default(@@IFACE@@) }}
  job_status_kwargs:
    max_idletime: @@IDLE@@
    strategy_period: 30
  provider:
    type: {{ provider_type | default('SlurmProvider') }}
{% if (provider_type | default('SlurmProvider')) == 'SlurmProvider' %}
    partition: {{ partition | default(@@PARTITION@@) }}
    account: {{ account | default(@@ACCOUNT@@) }}
    walltime: {{ walltime | default(@@WALLTIME@@) }}
    worker_init: {{ worker_init | default(@@WORKER_INIT@@) | tojson }}
    launcher:
      type: SrunLauncher
    init_blocks: {{ init_blocks | default(@@EAGER@@) }}
    min_blocks: 0
    max_blocks: 1
{% if scheduler_options is defined and scheduler_options %}
    scheduler_options: {{ scheduler_options | tojson }}
{% endif %}
{% else %}
    init_blocks: 1
    min_blocks: 0
    max_blocks: 1
{% endif %}
"""
        subs = {
            "@@MAXW@@": str(p.max_workers_per_node),
            "@@IFACE@@": json.dumps(p.interface),
            "@@IDLE@@": repr(float(hpc.max_idletime_s)),
            "@@PARTITION@@": json.dumps(p.partition),
            "@@ACCOUNT@@": json.dumps(p.account),
            "@@WALLTIME@@": json.dumps(p.walltime),
            "@@WORKER_INIT@@": json.dumps(p.worker_init),
            "@@EAGER@@": str(eager),
        }
        for token, value in subs.items():
            template = template.replace(token, value)
        defaults: dict = {
            "interface": p.interface,
            "partition": p.partition,
            "account": p.account,
            "walltime": p.walltime,
            "worker_init": p.worker_init,
            "max_workers_per_node": p.max_workers_per_node,
        }
        if p.scheduler_options is not None:
            defaults["scheduler_options"] = p.scheduler_options
        return template, defaults

    async def bootstrap(self, hpc: Profile) -> EndpointHandle:
        """Full first-run bootstrap: ensure remote creds, provision, and record the
        login-node FQDN so later sessions reconnect direct-to-node.

        The FQDN is captured in the same SSH connection that starts the daemon (the
        alias round-robins, so a separate probe could name the wrong node). A reused
        already-running endpoint keeps its prior recorded node; reconciling a stale or
        dead pin is deferred. Idempotent: seeds storage.db only if the remote can't
        already authenticate, and reuses a running endpoint."""
        if not await self.cli.whoami():
            with tempfile.TemporaryDirectory() as tmp:
                trimmed = build_minimal_storage_db(
                    src_path=Path(_get_storage_filepath()),
                    dst_path=Path(tmp) / "storage.db",
                    namespace=_resolve_namespace(),
                )
                await self.cli.seed_storage_db(trimmed)
        handle = await self.provision(hpc)
        if handle.login_host is not None and self.store is not None and self.alias is not None:
            self.store.put(
                EndpointRecord(
                    endpoint_id=handle.endpoint_id,
                    login_host=handle.login_host,
                    alias=self.alias,
                    user=self.cli.target.user,
                    key_path=self.cli.target.key_path,
                    name=handle.name,
                    provisioned_at=datetime.now(timezone.utc).isoformat(),
                )
            )
        return handle

    async def provision(self, hpc: Profile) -> EndpointHandle:
        # Idempotent: reuse a running endpoint; configure only if it doesn't exist yet
        # (re-configuring an existing one raises ConfigExists).
        name = self.profile.endpoint_name
        st = await self.cli.status(name)
        if st == "running":
            # REUSE: we did NOT launch it, so its node is unknown from a fresh
            # round-robin probe — leave login_host None and keep any prior record.
            return EndpointHandle(endpoint_id=await self.cli.endpoint_id(name), name=name)
        if st is None:
            await self.cli.configure(name)
        # config.yaml is the engine-free MANAGER config (amqp_port lives here); the
        # engine goes in the UEP template (v4 manager+template model). Rewrite both so
        # a re-provision always applies the current profile.
        manager = yaml.safe_dump(
            {"display_name": name, "amqp_port": self.profile.amqp_port}, sort_keys=False
        )
        uep, _defaults = self.config_template(hpc)
        await self.cli.write_config(name, manager, uep)
        eid, host = await self.cli.start(name)
        return EndpointHandle(endpoint_id=eid, name=name, login_host=host)

    async def teardown(self, endpoint_id: str, *, wipe_credentials: bool = False) -> None:
        """Stop the endpoint and cancel its Slurm block(s) — the cost-control exit.
        Credentials are kept by default so a later session can reconnect; pass
        wipe_credentials=True to also remove the remote storage.db."""
        await self.cli.stop(self.profile.endpoint_name)
        if wipe_credentials:
            await self.cli.wipe_storage_db()

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
