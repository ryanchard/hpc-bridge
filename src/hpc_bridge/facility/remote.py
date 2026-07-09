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
import sys
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import yaml
from globus_compute_sdk.sdk.auth.token_storage import (
    _get_storage_filepath,
    _resolve_namespace,
)

from typing import TYPE_CHECKING

from ..credentials import build_minimal_storage_db
from ..profile import Profile
from ..state import EndpointRecord, LoginNodeStore

if TYPE_CHECKING:
    from ..catalog.entry import CatalogEntry
from .base import EndpointHandle

# ---------------------------------------------------------------- SSH transport


@dataclass(frozen=True)
class SshTarget:
    host: str
    # user/key_path optional: when None, defer to the user's ~/.ssh/config (User/IdentityFile), which
    # OpenSSH reads live — so access needs no boot-env var the already-running server can't see.
    user: str | None = None
    key_path: str | None = None
    connect_timeout: int = 20
    # When set, multiplex every connection over one ControlMaster socket under this (0700) dir, so
    # the whole bootstrap+discovery authenticates ONCE instead of per-call — the MFA-once win, and
    # what makes a multi-command discovery sweep cheap. None ⇒ no multiplexing (argv unchanged).
    control_dir: str | None = None
    control_persist: int = 60

    def _control_path(self) -> str:
        # %C hashes localhost/user/host/port: short (fits macOS' ~104-char socket-path limit) and
        # auto-distinct after `rebind` swaps host, so the alias master and the pinned-node master
        # land on separate sockets with no bookkeeping.
        return f"{self.control_dir}/%C"

    def argv(self, remote_cmd: str) -> list[str]:
        # Never-prompt (BatchMode fails fast instead of hanging on a password/MFA prompt). With an
        # explicit key we pin it (IdentitiesOnly); with none we DEFER to ~/.ssh/config's IdentityFile.
        # A pre-opened master (e.g. one Duo on an MFA facility) is reused regardless of BatchMode;
        # ControlMaster=auto opens one non-interactively on a key host.
        opts = ["ssh"]
        if self.key_path:
            opts += ["-i", self.key_path, "-o", "IdentitiesOnly=yes"]
        opts += [
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={self.connect_timeout}",
            "-o", "StrictHostKeyChecking=accept-new",
        ]
        if self.control_dir:
            opts += [
                "-o", "ControlMaster=auto",
                "-o", f"ControlPath={self._control_path()}",
                "-o", f"ControlPersist={self.control_persist}",
            ]
        opts += [f"{self.user}@{self.host}" if self.user else self.host, remote_cmd]
        return opts

    def control_argv(self, request: str) -> list[str]:
        """argv for an `ssh -O <request>` control command (e.g. 'exit', 'check') against this
        target's master socket. Only meaningful when control_dir is set."""
        return [
            "ssh", "-O", request,
            "-o", f"ControlPath={self._control_path()}",
            f"{self.user}@{self.host}" if self.user else self.host,
        ]


# Teardown SSH ops (gce stop, squeue, scancel) hit a possibly-loaded login node with *fresh*
# connections; bound them tighter than the 120s default so stop_endpoint releases the allocation
# promptly instead of dragging on a slow sshd.
_TEARDOWN_SSH_S = 30.0


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
    try:
        out, err = await asyncio.wait_for(proc.communicate(payload), timeout)
    except BaseException:  # noqa: BLE001 - timeout OR cancellation: wait_for abandons the
        # ssh child still running; kill + reap it so we don't leak the process and its 3 pipe
        # FDs (bites when a *connected* session wedges mid-command past `timeout`). Then re-raise.
        if proc.returncode is None:
            proc.kill()
            try:
                await proc.wait()
            except Exception:  # noqa: BLE001 - best-effort reap
                pass
        raise
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


# ------------------------------------------------------------- machine profiles


@dataclass(frozen=True)
class MachineProfile:
    """Per-facility data: how to reach the endpoint binary and what Slurm to request."""

    name: str
    endpoint_name: str      # registration / on-disk dir name (e.g. "hpc-bridge")
    env_setup: str          # bash that puts globus-compute-endpoint on PATH (module + venv)
    interface: str          # address_by_interface ifname for the worker (e.g. ib0)
    partition: str
    account: str
    worker_init: str        # replays env_setup on the compute worker (parsl writes it into sbatch)
    # Human-readable label shown in the Globus web UI / `gce list` (distinct from the
    # endpoint_name used for registration). Defaults to endpoint_name when unset.
    display_name: str | None = None
    walltime: str = "00:30:00"
    max_workers_per_node: int = 2     # parsl workers per node (the engine's slots)
    nodes_per_block: int = 1          # nodes requested per Slurm block
    max_blocks: int = 1               # ceiling on concurrent Slurm blocks Parsl may hold
    available_accelerators: int | list[str] | None = None  # GPU count or device IDs
    amqp_port: int = 443    # facilities firewall the default AMQPS 5671; 443 is allowed
    scheduler_options: str | None = None
    scratch_root: str | None = None  # session-shell root on the shared filesystem


def profile_from_catalog_entry(
    entry: CatalogEntry,
    *,
    user: str,
    account: str,
    partition: str | None = None,
    venv: str | None = None,
) -> MachineProfile:
    """Build a `MachineProfile` from a catalog entry plus per-user runtime values.

    The catalog stores user-agnostic templates; this resolves them at provision time:
    ``{user}`` is the SSH login user and ``{venv}`` is the remote globus-compute-endpoint venv
    (defaults to the ``/home/{user}/hpc-bridge/gce-venv`` convention). ``account`` and the derived
    ``worker_init`` (= the resolved ``env_setup``, replayed on the compute worker) are supplied
    here, never stored in the catalog. ``partition`` overrides the entry's default when given.
    Substitution uses ``str.replace`` (not ``str.format``) so literal shell braces in ``env_setup``
    are left untouched.
    """
    venv = venv or f"/home/{user}/hpc-bridge/gce-venv"

    def _resolve(template: str) -> str:
        # Accept the plugin template ({user}/{venv}) AND the natural shell idiom ($USER/${USER}) —
        # a BYO facility's scratch_root is embedded in the session shim *single-quoted*, so the
        # remote shell never expands $USER; we must resolve it here or `mkdir` hits a literal $USER.
        for tok in ("{user}", "${USER}", "$USER"):
            template = template.replace(tok, user)
        return template.replace("{venv}", venv)

    kw = entry.profile_kwargs()
    # Naming convention: never the bare "hpc-bridge" — endpoints are keyed by identity+name, so a
    # shared name collides (stale-reuse → stuck "provisioning"). A seed that omits endpoint_name
    # derives hpc-bridge-<id> here; session entries already set it explicitly.
    kw["endpoint_name"] = kw["endpoint_name"] or f"hpc-bridge-{entry.id}"
    kw["env_setup"] = _resolve(kw["env_setup"])
    kw["scratch_root"] = _resolve(kw["scratch_root"])
    if partition:
        kw["partition"] = partition
    return MachineProfile(**kw, account=account, worker_init=kw["env_setup"])


# ------------------------------------------------- globus-compute-endpoint / SSH


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_JOBID = re.compile(r"^\d+(_\d+)?$")  # plain or array slurm job id (guards what we scancel)


class RemoteEndpointCLI:
    """Drive `globus-compute-endpoint` on a remote login node over SSH.

    Mirrors the local `EndpointCLI` (configure / start / stop) but runs every
    command inside the remote venv, writes config files over SSH, and reads the
    UUID from `list` (v4 MEP mode does not reliably write endpoint.json)."""

    def __init__(self, target: SshTarget, env_setup: str, *, remote_dir: str = "$HOME/.globus_compute") -> None:
        self.target = target
        self.env_setup = env_setup
        self.remote_dir = remote_dir

    async def _gce(self, *args: str, timeout: float = 120.0) -> tuple[int, str, str]:
        inner = f"{self.env_setup} && globus-compute-endpoint " + " ".join(shlex.quote(a) for a in args)
        return await ssh_exec(self.target, f"bash -lc {shlex.quote(inner)}", timeout=timeout)

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

    @classmethod
    def _parsed_rows(cls, out: str) -> list[list[str]]:
        """`_list_rows`, but fail LOUD when `list` clearly emitted an endpoint table we could
        NOT parse (a gce version/format/locale change away from the pipe table) — otherwise an
        unparsed listing reads as "no endpoints" and the caller silently mis-provisions or
        can't find a live endpoint. The legitimate empty case ("No endpoints configured")
        still returns []. See issue #8 (the robust fix is the SDK's get_endpoints())."""
        rows = cls._list_rows(out)
        low = out.lower()
        if not rows and "endpoint" in low and "no endpoint" not in low:
            raise RuntimeError(
                "could not parse `globus-compute-endpoint list` output "
                f"(gce version/format change?); raw output:\n{out.strip()[:500]}"
            )
        return rows

    async def status(self, name: str) -> str | None:
        """'running' | 'configured' | None — drives idempotent (re)provisioning."""
        rc, out, _err = await self._gce("list")
        if rc != 0:
            return None
        for cells in self._parsed_rows(out):
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
        # Best-effort + bounded: `stop` can throw a psutil traceback yet still cancel the block, and
        # a fresh SSH to a loaded login node is slow — don't let it hold teardown hostage.
        await self._gce("stop", name, timeout=_TEARDOWN_SSH_S)

    async def wipe_storage_db(self) -> None:
        """Remove the seeded credential from the remote host (best-effort)."""
        await ssh_exec(self.target, f'rm -f "{self.remote_dir}/storage.db"')

    async def cancel_blocks(self, endpoint_id: str) -> list[str]:
        """Best-effort `scancel` of THIS endpoint's Slurm blocks; returns the cancelled IDs.

        An ungraceful `stop` (it can die on a psutil traceback) won't scale Parsl's block in,
        so the compute keeps its allocation until walltime. We find our blocks precisely by
        their StdOut path, which Parsl writes under the endpoint's UEP dir
        (`uep.<endpoint_id>.*`) — so we never touch another GlobusComputeEngine endpoint's
        jobs. Never raises: teardown must not crash on a flaky scheduler query."""
        marker = f"uep.{endpoint_id}"
        try:
            squeue = 'squeue -u "$USER" -h -O "JobID:30,StdOut:1024" 2>/dev/null'
            rc, out, _err = await ssh_exec(
                self.target, f"bash -lc {shlex.quote(squeue)}", timeout=_TEARDOWN_SSH_S
            )
        except Exception:  # noqa: BLE001 - scheduler unreachable -> nothing to cancel
            return []
        if rc != 0:
            return []
        ids = [
            line.split()[0]
            for line in out.splitlines()
            if marker in line and line.split() and _JOBID.match(line.split()[0])
        ]
        if ids:
            try:
                await ssh_exec(
                    self.target,
                    f"bash -lc {shlex.quote('scancel ' + ' '.join(ids))}",
                    timeout=_TEARDOWN_SSH_S,
                )
            except Exception:  # noqa: BLE001 - best-effort
                pass
        return ids

    async def endpoint_id(self, name: str) -> str:
        rc, out, err = await self._gce("list")
        if rc != 0:
            raise RuntimeError(f"remote list failed: {(err or out).strip()}")
        for cells in self._parsed_rows(out):
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

    async def close(self) -> None:
        """Close the SSH ControlMaster for this target (best-effort, bounded).

        `ControlPersist` self-reaps an idle master, so this is just a prompt, explicit teardown of
        the shared connection at endpoint end — not load-bearing. No-op when multiplexing is off."""
        if self.target.control_dir is None:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.target.control_argv("exit"),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), _TEARDOWN_SSH_S)
        except Exception:  # noqa: BLE001 - no master / already gone / slow sshd: ControlPersist reaps it
            pass


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
        break Jinja compilation.

        Two render-time invariants imposed by the endpoint manager (`render_config_user_template`
        → `_sanitize_user_json`), both learned by live debugging:
        - It json.dumps's every *string* user_opt, so `"SlurmProvider"` arrives as
          `'"SlurmProvider"'`. Branch on the BOOLEAN `is_slurm` (bools pass through the
          sanitizer unchanged), never a string equality — a string compare silently fails
          and drops the whole provider block.
        - Because the sanitizer already quotes strings, the template must NOT also `| tojson`
          a string value (worker_init, scheduler_options) — that double-encodes it (embedded
          quotes) and breaks the worker. The json.dumps'd defaults are pre-quoted to match."""
        p = self.profile
        eager = 1 if hpc.mode == "interactive" else 0
        template = """\
engine:
  type: GlobusComputeEngine
  run_in_sandbox: true
  max_workers_per_node: {{ max_workers_per_node | default(@@MAXW@@) }}
  address:
    type: address_by_interface
    ifname: {{ interface | default(@@IFACE@@) }}
{% if available_accelerators is defined and available_accelerators %}
{% if available_accelerators is iterable and available_accelerators is not string %}
  available_accelerators: [{{ available_accelerators | join(', ') }}]
{% else %}
  available_accelerators: {{ available_accelerators }}
{% endif %}
{% endif %}
  job_status_kwargs:
    max_idletime: @@IDLE@@
    strategy_period: 30
  provider:
    type: {{ provider_type | default('SlurmProvider') }}
{% if is_slurm | default(true) %}
    partition: {{ partition | default(@@PARTITION@@) }}
    account: {{ account | default(@@ACCOUNT@@) }}
    walltime: {{ walltime | default(@@WALLTIME@@) }}
    nodes_per_block: {{ nodes_per_block | default(@@NODES@@) }}
    worker_init: {{ worker_init | default(@@WORKER_INIT@@) }}
    launcher:
      type: SrunLauncher
    init_blocks: {{ init_blocks | default(@@EAGER@@) }}
    min_blocks: 0
    max_blocks: {{ max_blocks | default(@@MAXBLK@@) }}
{% if scheduler_options is defined and scheduler_options %}
    scheduler_options: {{ scheduler_options }}
{% endif %}
{% else %}
    init_blocks: 1
    min_blocks: 0
    max_blocks: {{ max_blocks | default(@@MAXBLK@@) }}
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
            "@@NODES@@": str(p.nodes_per_block),
            "@@MAXBLK@@": str(p.max_blocks),
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
            "nodes_per_block": p.nodes_per_block,
            "max_blocks": p.max_blocks,
        }
        if p.scheduler_options is not None:
            defaults["scheduler_options"] = p.scheduler_options
        if p.available_accelerators is not None:
            defaults["available_accelerators"] = p.available_accelerators
        return template, defaults

    async def bootstrap(self, hpc: Profile) -> EndpointHandle:
        """Full first-run bootstrap: ensure remote creds, provision, and record the
        login-node FQDN so later sessions reconnect direct-to-node.

        The FQDN is captured in the same SSH connection that starts the daemon (the
        alias round-robins, so a separate probe could name the wrong node). A reused
        already-running endpoint keeps its prior recorded node; reconciling a stale or
        dead pin is deferred. Idempotent: seeds storage.db only if the remote can't
        already authenticate, and reuses a running endpoint.

        SSH-once: before any SSH, ask the Globus web service whether an endpoint we own is
        already online and reuse it over AMQP — zero SSH, so an MFA facility isn't re-auth'd.
        Only when none is online do we fall through to the one allowed SSH bootstrap below.
        (Caveat: a web 'online' that's actually a stale registration is reused as-is; the
        canary then can't warm it — re-bootstrap-on-stale is a deferred follow-up.)"""
        reused = await self.find_online_endpoint(self.profile.endpoint_name)
        if reused is not None:
            return EndpointHandle(endpoint_id=reused, name=self.profile.endpoint_name, reused=True)
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
            return EndpointHandle(endpoint_id=await self.cli.endpoint_id(name), name=name, reused=True)
        if st is None:
            await self.cli.configure(name)
        # config.yaml is the engine-free MANAGER config (amqp_port lives here); the
        # engine goes in the UEP template (v4 manager+template model). Rewrite both so
        # a re-provision always applies the current profile.
        manager = yaml.safe_dump(
            {
                "display_name": self.profile.display_name or name,
                "amqp_port": self.profile.amqp_port,
            },
            sort_keys=False,
        )
        uep, _defaults = self.config_template(hpc)
        await self.cli.write_config(name, manager, uep)
        eid, host = await self.cli.start(name)
        if host:
            # Pin the live session to the node the manager daemon actually landed on, so
            # later control-plane ops — above all teardown — reach THIS node instead of
            # the round-robin alias. Without this, `stop` hits a different login node,
            # the daemon survives, and the endpoint is orphaned (the very failure the
            # login-node pin exists to prevent).
            self.cli.rebind(host)
        return EndpointHandle(endpoint_id=eid, name=name, login_host=host)

    async def teardown(self, endpoint_id: str, *, wipe_credentials: bool = False) -> None:
        """Stop the endpoint and cancel its Slurm block(s) — the cost-control exit. Both ops run
        over SSH, each bounded by `_TEARDOWN_SSH_S`. Credentials are kept by default so a later
        session can reconnect; pass wipe_credentials=True to also remove the remote storage.db."""
        await self.cli.stop(self.profile.endpoint_name)
        # `stop` kills the manager, but an ungraceful stop leaves Parsl's block holding the
        # allocation until walltime (no manager left to scale it in). Explicitly cancel this
        # endpoint's blocks so "teardown released the compute" actually holds.
        await self.cli.cancel_blocks(endpoint_id)
        if wipe_credentials:
            await self.cli.wipe_storage_db()
        await self.cli.close()  # drop the shared SSH master; the endpoint is gone

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

    async def find_online_endpoint(self, name: str) -> str | None:
        """UUID of an *online* endpoint we own named `name`, else None — via the Globus web
        service, NO SSH. This is the SSH-once keystone: a fresh session reuses a still-running
        endpoint from a prior session over AMQP, instead of re-SSHing the login node (which on
        an MFA facility could force a re-auth). Bootstrap (the one allowed SSH) is the fallback.

        Web errors (e.g. the local Globus identity isn't logged in) are swallowed to None so we
        fall through to the SSH path, which surfaces the auth problem with a clearer error."""
        client = self._client_factory()
        try:
            endpoints = await asyncio.to_thread(client.get_endpoints, "owner")
        except Exception as exc:  # noqa: BLE001 - web/auth failure -> can't reuse, fall back to SSH
            print(f"hpc-bridge: endpoint reuse check failed ({type(exc).__name__}: {exc}); "
                  "falling back to SSH bootstrap", file=sys.stderr)
            return None
        for ep in endpoints or []:
            ep_name = ep.get("name") or ep.get("display_name")
            ep_id = ep.get("uuid") or ep.get("endpoint_uuid") or ep.get("id")
            if ep_name != name or not ep_id:
                continue
            if await self.manager_online(ep_id):  # confirm it's actually online, not just registered
                return ep_id
        return None

    def _default_client(self):
        from globus_compute_sdk import Client

        return Client()
