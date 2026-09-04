from __future__ import annotations

import asyncio
import datetime
import os
import re
import shlex
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import Context, FastMCP

from . import config, dispatch, session_shell
from .catalog.entry import Allocation, CatalogEntry, CatalogSummary, Compute, Defaults
from .catalog.parsers import PARSERS
from .config import (  # noqa: F401 - re-exported: tests patch/import these on server
    CANARY_TIMEOUT_S,
    CANARY_TTL_S,
    PROVISION_GRACE_S,
    SYNC_WAIT_S,
    TASK_CEILING_MARGIN_S,
    TRANSIENT_CONFLICT_LIMIT,
    _control_settings,
    _env_endpoint_id,
    _env_float,
    _env_mode,
    _require_env,
    _short_control_dir,
)
from .context import (  # noqa: F401 - re-exported: tools + tests import them from here
    DEFAULT_SHAPE,
    AppCtx,
    ShapeRuntime,
    TaskHandle,
)
from .cost import cap_output, estimate_spend
from .discovery import discover_facility_details
from .endpoint import EndpointCLI
from .facility.base import Facility
from .facility.local import LocalFacility
from .lifecycle import EndpointState, ensure_warm
from .login import LoginFlow, LoginStart
from .models import (
    ConnectFacilityResult,
    EndpointStatus,
    FacilityDetails,
    LoginShellResult,
    LoginStatus,
    ShellOutcome,
)
from .profile import Profile
from .runner import CanaryResult, GlobusRunner
from .session_shell import Session
from .shapes import SHAPES, shape_config


def _ssh_config_user(host: str) -> str:
    """The login name OpenSSH would use for `host`, honoring ~/.ssh/config — via a local, no-connect
    `ssh -G`. Sources the user from the config the user already maintains, not a boot-env var the
    already-running server can't see. Falls back to the local username if `ssh -G` is unavailable."""
    import getpass
    import subprocess

    try:
        out = subprocess.run(["ssh", "-G", host], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            k, _, v = line.strip().partition(" ")
            if k.lower() == "user" and v.strip():
                return v.strip()
    except Exception:  # noqa: BLE001 - no ssh binary / odd host -> local username
        pass
    return getpass.getuser()


# sshd's own denial lines — the method list in parentheses is the tell (a bare "Permission denied" is
# usually the remote filesystem: `mkdir … : Permission denied` on an over-quota home, found in review).
_SSH_AUTH_DENIED = re.compile(
    r"permission denied \((?:publickey|password|keyboard-interactive|gssapi|hostbased)[^)]*\)"
    r"|permission denied, please try again|authentication failed",
    re.IGNORECASE,
)


def _explain_provision_error(exc: BaseException, fac=None, *, host: str | None = None,
                             user: str | None = None, fallback: str | None = None) -> str:
    """Turn a bootstrap failure into what a newcomer can act on. The raw text names an internal step
    ('seed storage.db (mkdir) failed: u@host: Permission denied (publickey,…)') — a stranger with no
    account or key on the facility must instead hear WHICH host and login name were tried, where the
    name came from, and the two remedies (found on the stranger's walk, 2026-09-03)."""
    raw = str(exc)
    low = raw.lower()
    ssh_line = raw.rsplit("failed: ", 1)[-1].strip() if "failed: " in raw else raw
    # ssh prefixes the verdict with warnings ("Identity file … not accessible") — quote the verdict line
    verdict = [ln for ln in ssh_line.splitlines() if "permission denied" in ln.lower() or "denied" in ln.lower()]
    if verdict:
        ssh_line = verdict[-1].strip()
    cli = getattr(fac, "cli", None)
    target = getattr(cli, "target", None) or getattr(cli, "_target", None)
    host = host or getattr(target, "host", None) or getattr(fac, "alias", None) or "the login host"
    if user is None:
        user = getattr(target, "user", None)
    if _SSH_AUTH_DENIED.search(raw) or "too many authentication failures" in low:
        who = f"as {user!r}" if user else "as your local username (no login name is configured for this host)"
        src = ("HPC_BRIDGE_SSH_USER" if (config.ssh_user() or "")
               else "~/.ssh/config" if user else "nowhere")
        return (
            f"NO SSH ACCESS to {host}: the login-node SSH {who} was refused ({ssh_line[:200]}). hpc-bridge "
            "needs an account on this facility and key-based SSH to its login node. Put the host's User and "
            "IdentityFile in ~/.ssh/config (or set HPC_BRIDGE_SSH_USER / HPC_BRIDGE_SSH_KEY) and call "
            "connect_facility again; on a multi-factor facility, pre-open a session in your own terminal "
            f"first. The login name came from {src}. Nothing was started or billed."
        )
    if "permission denied" in low:  # a denial from the remote FILESYSTEM (quota, read-only home), not from sshd
        return (f"REMOTE FILESYSTEM refused a write on {host}: {ssh_line[:200]}. The login worked; the home "
                "directory is over quota, read-only, or not writable. Free space or fix permissions there, then "
                "call connect_facility again. Nothing was started or billed.")
    if any(k in low for k in ("could not resolve hostname", "connection timed out", "connection refused",
                              "no route to host", "network is unreachable")):
        return (f"CANNOT REACH {host}: {ssh_line[:200]}. Check the login host name and your network/VPN, "
                "then call connect_facility again. Nothing was started or billed.")
    if "controlpath too long" in low:
        return (f"hpc-bridge error: the SSH ControlMaster socket path is too long ({ssh_line[:160]}); set "
                "HPC_BRIDGE_STATE_DIR to a short path (e.g. ~/.hpc-bridge). Nothing was started.")
    return (fallback or f"hpc-bridge error: {type(exc).__name__}: {raw}")[:500]


def _slurm_facility(profile, *, alias: str, user: str) -> Facility:
    """Wire a Slurm `MachineProfile` into a `SlurmFacility` over SSH — shared by the catalog
    and the hardcoded-Anvil paths."""
    from .facility.remote import RemoteEndpointCLI, SlurmFacility, SshTarget, _routable_pin
    from .state import LoginNodeStore

    control_dir, persist = _control_settings()  # multiplex all SSH over one ControlMaster (MFA-once)
    key = (config.ssh_key() or "")  # else defer to ~/.ssh/config IdentityFile
    target = SshTarget(
        host=alias,
        user=user,
        key_path=os.path.expanduser(key) if key else None,
        control_dir=control_dir,
        control_persist=persist,
    )
    cli = RemoteEndpointCLI(target, profile.env_setup)
    store = LoginNodeStore()
    rec = store.get(alias=alias, name=profile.endpoint_name)
    pin = _routable_pin(rec.login_host) if rec is not None else None
    if pin is not None:  # reconnect direct-to-node (routable pins only) instead of the round-robin alias
        # An internal-only pin (e.g. Midway's beagle3-tbd1.rcc.local) is dropped -> stay on the alias.
        # A routable-but-dead pin fails fast (BatchMode) -> CANNOT REACH, and connect_facility then DROPS
        # the pin (_drop_dead_pin) so the next attempt resolves the canonical host again.
        cli.rebind(pin)
    return SlurmFacility(profile, cli, store=store, alias=alias)


def _unsupported_entry_reason(entry) -> str | None:
    """Why this catalog entry can't drive a stand-up, or None.

    A `compute_mep_uuid` entry is a facility-run multi-user endpoint: consumed with zero SSH, and
    with NO login shape — so an allocation LISTING (which runs over the free login node) has no
    channel on it. Such an entry must omit `allocation`; the account (if any) is supplied directly."""
    if entry.compute_mep_uuid:
        if entry.allocation is not None:
            return (
                "a multi-user-endpoint entry cannot list allocations (no login-node channel on a "
                "MEP) — drop `allocation` and let ensure_endpoint_up(account=…) take the account"
            )
        return None
    if entry.compute.scheduler not in ("slurm", "pbs"):
        return f"scheduler {entry.compute.scheduler!r} not supported yet (slurm/pbs only)"
    return None


def _facility_from_entry(entry, *, account: str, pinned_host: str | None = None) -> Facility:
    """Build the facility for a catalog entry + per-user runtime values — shared by the startup
    path (make_facility) and the runtime path (connect_facility). `account` may be empty for the
    agentic flow; ensure_endpoint_up(account=…) overrides it per scheduler block.

    A `compute_mep_uuid` entry builds a **MEPFacility** (zero SSH: no login name, no key, no host —
    the facility's identity mapping is the access) and MEP wins if an entry somehow carries both
    reaches. Otherwise a SlurmFacility over SSH.

    `pinned_host` overrides the entry's `ssh_host` and is passed ONLY on the env-pinned startup path
    (HPC_BRIDGE_MACHINE + HPC_BRIDGE_SSH_HOST). The agentic connect path leaves it None so the BOUND
    facility's own `ssh_host` is authoritative — a process-wide env must never silently redirect an
    agent-chosen facility to a different host (the "globus1 is Aurora" trap, [#35])."""
    if entry.compute_mep_uuid:
        from .facility.mep import MEPFacility

        return MEPFacility.from_entry(entry, account=account or None)

    from .facility.remote import profile_from_catalog_entry

    alias = pinned_host or entry.ssh_host
    # Login name: optional env override, else read live from ~/.ssh/config (`ssh -G`) — never a
    # *required* boot-env var. The key is deferred to the config's IdentityFile in _slurm_facility.
    user = (config.ssh_user() or "") or _ssh_config_user(alias)
    profile = profile_from_catalog_entry(
        entry,
        user=user,
        account=account,
        partition=config.partition(),
        venv=config.remote_venv(),
    )
    return _slurm_facility(profile, alias=alias, user=user)


async def _catalog_facility(machine: str) -> Facility:
    """Build a facility from a catalog entry (HPC_BRIDGE_MACHINE), sourcing the machine config
    from `make_catalog()` (the live Globus Search index — HPC_BRIDGE_SEARCH_INDEX; no bundled
    fallback). v1 slice: SSH-bootstrap Slurm/PBS machines only."""
    entry = await make_catalog().get(machine)
    if entry is None:
        raise RuntimeError(f"HPC_BRIDGE_MACHINE={machine!r} not found in the catalog")
    reason = _unsupported_entry_reason(entry)
    if reason:
        raise RuntimeError(f"{machine}: {reason}")
    if entry.compute_mep_uuid:
        # A MEP entry IS the endpoint: a stray HPC_BRIDGE_ENDPOINT_ID (the BYO-UUID hatch this entry
        # supersedes) would otherwise seed app.state and silently win over the entry's UUID while
        # the entry's UEC defaults were applied to it. Refuse the ambiguity rather than guess.
        env_eid = _env_endpoint_id()
        if env_eid and env_eid != entry.compute_mep_uuid:
            raise RuntimeError(
                f"{machine}: HPC_BRIDGE_ENDPOINT_ID={env_eid} conflicts with the entry's "
                f"compute_mep_uuid={entry.compute_mep_uuid}; unset it (the entry is the endpoint)"
            )
        account = (config.account() or "")
        if entry.account_required and not account:
            account = _require_env("HPC_BRIDGE_ACCOUNT")  # raises with the standard message
        return _facility_from_entry(entry, account=account)
    # Startup pin only: HPC_BRIDGE_SSH_HOST may override the catalog's canonical ssh_host (your own
    # alias / a login node, or the FQDN the container needs). The agentic connect path does NOT (#35).
    return _facility_from_entry(
        entry,
        account=_require_env("HPC_BRIDGE_ACCOUNT"),
        pinned_host=config.ssh_host(),
    )


async def make_facility() -> Facility:
    """Select the facility: a catalog-described machine (HPC_BRIDGE_MACHINE — sourced from the
    Globus Search index), or local dev. Machines are catalog *data*, never hardcoded; the agent
    can also bind one at runtime via connect_facility. (lifespan boots resiliently if this raises.)"""
    machine = (config.machine() or "")
    if not machine and (config.env("HPC_BRIDGE_FACILITY") or ""):
        raise RuntimeError(
            "HPC_BRIDGE_FACILITY was removed — machines are catalog data now. Use "
            "HPC_BRIDGE_MACHINE=<id> (e.g. anvil), or let the agent pick via connect_facility."
        )
    if machine:
        return await _catalog_facility(machine)
    user_dir = config.user_dir()
    return LocalFacility(EndpointCLI(user_dir=user_dir))


def _resolve_scratch_root(facility) -> str:
    """The session-shell root for `facility`: explicit env wins, else the facility's shared-FS
    scratch (e.g. Anvil $SCRATCH), else a home-relative default.

    `~` is expanded CLIENT-side only for a LocalFacility, whose worker IS this machine. A remote
    facility's root is kept verbatim: expanding `~/.hpc-bridge` here would bake the CLIENT's home
    (e.g. /Users/me/.hpc-bridge) into a command that runs as a different user on a different host —
    the exact bug a bound facility's scratch is meant to avoid. A `~/`/`$HOME/` root is instead
    expanded on the WORKER by `Session.quoted_state_dir()`, which is also what lets a multi-user
    endpoint (whose local username we can't know client-side) use a `$HOME`-relative scratch."""
    root = config.scratch() or getattr(facility, "scratch_root", None) or "~/.hpc-bridge"
    return os.path.expanduser(root) if isinstance(facility, LocalFacility) else root


def _make_search_client(_app_factory=None):
    """Build the Globus SearchClient for the facility registry.

    The registry's entries are `visible_to: public`, and Globus Search requires authentication
    ONLY for non-public entries (docs, verified 2026-09-03) — so the default is an **anonymous**
    client: a fresh install can `list_facilities` with zero setup and no consent. If the user's
    Compute identity already holds the Search scope (a curator who ran `hpc-bridge-catalog`, or a
    later registry with `visible_to`-restricted entries), we use it — the authenticated client also
    sees restricted entries. We never trigger a login here (a server runs non-interactively); a
    restricted registry that needs one is the job of the `needs_login` flow, lazily. Isolated so
    tests can substitute it.
    """
    from globus_sdk import SearchClient

    try:
        from globus_compute_sdk import Client

        # do_version_check=False: the check is an AUTHENTICATED call — on a fresh install it would
        # trigger the SDK's command-line login on the MCP transport (see the gate in _connect_facility).
        app = (_app_factory or (lambda: Client(do_version_check=False).app))()
        client = SearchClient(app=app)  # registers the search scope requirement on this app instance
        if not app.login_required():  # non-prompting: the scope is already granted -> authenticated
            return client
    except Exception:  # noqa: BLE001 - no Compute login at all, unreadable storage, SDK trouble -> anonymous
        pass
    return SearchClient()  # anonymous: public entries only (the registry's default)


def make_catalog():
    """The runtime catalog is the PUBLIC REGISTRY — a Globus Search index, read anonymously. The
    plugin ships its id (`PUBLIC_REGISTRY_INDEX`), so `list_facilities()` works out of the box with no
    login and no configuration; HPC_BRIDGE_SEARCH_INDEX overrides it (a private/staging registry).
    There is **no bundled fallback**: a machine the registry can't resolve is a hard failure (the
    soft agent-discovery fallback is a later slice). The bundled seed is the curator's ingest source
    (see `hpc-bridge-catalog`), never a runtime catalog.
    """
    from .catalog.search import SearchCatalog

    index = config.search_index()
    client = _make_search_client()  # anonymous unless a Search-scoped login is already held
    cache_dir = (
        config.plugin_data_dir()
        / "catalog-cache"
    )
    return SearchCatalog(index_id=index, client=client, cache_dir=cache_dir)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppCtx]:
    try:
        facility = await make_facility()
    except Exception as exc:  # noqa: BLE001 - a config error must NOT brick the MCP server at boot
        # (a startup crash = the agent silently sees no tools). Start unbound/local and let the
        # catalog tools surface/bind: list_facilities / connect_facility.
        print(
            f"hpc-bridge: facility setup failed at startup ({type(exc).__name__}: {exc}); starting "
            "unbound — use list_facilities / connect_facility to bind a machine.",
            file=sys.stderr,
        )
        user_dir = config.user_dir()
        facility = LocalFacility(EndpointCLI(user_dir=user_dir))
    scratch = _resolve_scratch_root(facility)
    app = AppCtx(
        facility=facility,
        profile=Profile(mode=_env_mode()),  # type: ignore[arg-type]
        state=EndpointState(endpoint_id=_env_endpoint_id()),
        scratch_root=scratch,
        charge_factor=config.charge_factor(),
        login_flow=LoginFlow(),
    )
    try:
        yield app
    finally:
        app.tasks.clear()  # drop any live poll handles — their blocks are going away with the process
        for rt in app.shapes.values():
            if rt.runner is not None:
                rt.runner.close()


# Named "endpoint", not "hpc-bridge" (the plugin/CLI name): Claude Code namespaces a plugin's MCP
# tools as plugin:<plugin>:<server>, so matching names would read the doubled plugin:hpc-bridge:hpc-bridge.
# Keep in sync with the mcpServers key in .mcp.json — CC namespaces by that key, this name just mirrors it.
mcp = FastMCP("endpoint", lifespan=lifespan)


def _supported_shapes(app: AppCtx) -> tuple[str, ...]:
    """The shapes the bound facility can serve. Default: every shape (a personal endpoint renders
    our own template, which has both). A facility-run multi-user endpoint declares
    `supported_shapes=("compute",)` — its schema REJECTS the LocalProvider login shape — and the
    server derives the rest from that single fact: no login shape ⇒ no free channel for the
    allocation listing / the pilot query / the scancel release ⇒ stop is draining-only, teardown is
    a no-op, every shape is billed."""
    return tuple(getattr(app.facility, "supported_shapes", None) or SHAPES)


def _has_login_shape(app: AppCtx) -> bool:
    return "login" in _supported_shapes(app)


def _shape_reject(app: AppCtx, shape: str) -> str | None:
    """A notice if `shape` isn't served by the bound facility, else None. Checked BEFORE any
    _shape_runtime(app, shape) so an unsupported shape never gets a ShapeRuntime/runner (a submit
    with it would be refused server-side and would shut the Executor down — see _confirm_worker)."""
    if shape in SHAPES and shape not in _supported_shapes(app):
        return (
            f"shape {shape!r} isn't available on this facility (a compute-only multi-user endpoint: "
            "no free login node; its schema refuses a LocalProvider block). Use shape='compute' — a "
            "block stays warm between calls (init_blocks=1 + the facility's idle-release), so cheap "
            "follow-up commands don't re-queue."
        )
    return None


def _shape_runtime(app: AppCtx, shape: str) -> ShapeRuntime:
    """Resolve (and lazily build) the per-shape runtime, seeding its user_endpoint_config
    from facility defaults (SlurmFacility) merged with the shape's template vars."""
    if shape not in SHAPES:
        raise ValueError(f"unknown shape {shape!r}")
    rt = app.shapes.get(shape)
    if rt is None:
        defaults: dict = {}
        ct = getattr(app.facility, "config_template", None)
        if ct is not None:
            result = ct(app.profile)
            if isinstance(result, tuple):  # SlurmFacility -> (template_str, defaults)
                defaults = result[1]
            # LocalFacility/FakeFacility return a plain dict (rendered engine) -> no UEP defaults
        if not isinstance(defaults, dict):
            defaults = {}
        uec = {**defaults, **shape_config(shape)}
        rt = ShapeRuntime(user_endpoint_config=uec)
        app.shapes[shape] = rt
    return rt


def _parse_hhmmss(s: str | None) -> int:
    """HH:MM:SS (also H:MM:SS / MM:SS / SS) -> seconds. Deterministic and total: returns 0 on anything
    missing or malformed so callers fall back rather than crash; never negative."""
    if not s:
        return 0
    text = str(s).strip()
    days = 0
    if "-" in text:  # Slurm's "days-hours[:minutes[:seconds]]" (a 2-day walltime parsed as 0 -> a 300 s ceiling)
        d, _, text = text.partition("-")
        if not d.isdigit() or not text:
            return 0
        days = int(d)
        parts = text.split(":")
        if not 1 <= len(parts) <= 3 or not all(p.strip().isdigit() for p in parts):
            return 0
        parts = parts + ["0"] * (3 - len(parts))  # after a day count the first field is HOURS
    else:
        parts = text.split(":")
        if not 1 <= len(parts) <= 3 or not all(p.strip().isdigit() for p in parts):
            return 0
    secs = 0
    for p in parts:
        secs = secs * 60 + int(p)
    return days * 86400 + secs


def _task_ceiling_s(uec: dict) -> float:
    """The per-task kill ceiling (seconds) passed to the runner as the ShellFunction walltime: the block
    walltime minus a margin (so a task dies with a 124 result just BEFORE the scheduler reclaims the
    block), optionally capped by HPC_BRIDGE_MAX_TASK_S (unset = the full block walltime — the
    deterministic default). Falls back to a safe non-zero value when the block walltime is absent."""
    block_s = _parse_hhmmss(uec.get("walltime"))
    ceiling = block_s - TASK_CEILING_MARGIN_S
    if ceiling <= 0:  # missing/tiny walltime (e.g. LocalFacility has none) -> a safe default
        ceiling = max(SYNC_WAIT_S + TASK_CEILING_MARGIN_S, 300.0)
    cap = config.max_task_s()
    if cap > 0:
        ceiling = min(ceiling, cap)
    return float(ceiling)


def _live_task_handles(app: AppCtx, shape: str) -> list[tuple[str, TaskHandle]]:
    """(task_id, handle) for this shape whose task is still RUNNING (future not yet done) — i.e. still
    holding the block. The warmth signal and the swap/session-busy guards all key off this."""
    return [(tid, h) for tid, h in app.tasks.items() if h.shape == shape and not h.future.done()]


def _drain_shape_tasks(app: AppCtx, shape: str) -> None:
    """Drop a shape's still-RUNNING task handles — its block is going away (endpoint swap/stop/
    connect/teardown), so those futures are moot; poll_task on a drained id reports it ended rather
    than polling a dead future. A FINISHED task's handle is kept: its result is already delivered and
    stays retrievable via poll_task whatever happens to the Executor — dropping it would lose a
    completed result the agent simply hadn't polled yet."""
    for tid in [tid for tid, h in app.tasks.items() if h.shape == shape and not h.future.done()]:
        app.tasks.pop(tid, None)


def _runner_for(app: AppCtx, shape: str) -> GlobusRunner:
    """Reuse the shape's runner if it's bound to the current endpoint, else (re)create it. A
    new endpoint voids the prior worker confirmation and banks the old endpoint's spend."""
    rt = _shape_runtime(app, shape)
    eid = app.state.endpoint_id
    if rt.runner is None or rt.runner.endpoint_id != eid or rt.runner_stale:
        if rt.runner is not None and rt.runner.endpoint_id == eid and _live_task_handles(app, shape):
            # A credential/config-only swap (runner_stale, e.g. a new Globus login) must WAIT: closing
            # this Executor would drop the live task's future and poll_task would report "no task"
            # (found in review). The rebuild happens once the task has been polled.
            return rt.runner
        if rt.runner is not None:
            # A config-only swap (runner_stale) is barred while a task runs (see _apply_partition/
            # _apply_account), so reaching here with a live task means the ENDPOINT changed — that
            # block (and its tasks) is gone; drop the handles before closing so no dead future is polled.
            _drain_shape_tasks(app, shape)
            rt.runner.close()
            _bank_warm_interval(rt, app)
        ceiling_s = _task_ceiling_s(rt.user_endpoint_config)
        sync_wait_s = max(min(SYNC_WAIT_S, ceiling_s - TASK_CEILING_MARGIN_S), 5.0)
        rt.runner = app.runner_factory(
            eid, user_endpoint_config=rt.user_endpoint_config, walltime=ceiling_s, timeout=sync_wait_s
        )
        rt.runner_stale = False
        rt.warm_confirmed_at = None
    return rt.runner


async def _confirm_worker(app: AppCtx, shape: str, *, force: bool) -> str:
    """Upgrade a manager-online endpoint to truly 'warm' by confirming a worker answers a
    canary. Returns 'warm' if a worker is live, else 'provisioning' — the manager is up but the
    compute block is still cold-starting (the gap manager_online cannot see; the canary submit
    also kicks that block). Within CANARY_TTL_S of the last success we trust warmth and skip
    the round-trip so an interactive burst doesn't pay it on every call."""
    rt = _shape_runtime(app, shape)
    if rt.no_account:  # terminal for this identity: no canary, no runner rebuild — keep last_canary as the evidence
        return "provisioning"
    runner = _runner_for(app, shape)
    now = time.monotonic()
    # A task still running on this shape IS liveness — the worker is demonstrably executing our work.
    # Trust it and skip the canary, which would otherwise queue behind the sole worker and (on timeout)
    # flip us to 'not warm', banking the spend clock while the block is still burning (#21).
    if _live_task_handles(app, shape):
        rt.warm_confirmed_at = now
        rt.provisioning_since = None
        return "warm"
    if not force and rt.warm_confirmed_at is not None and now - rt.warm_confirmed_at < CANARY_TTL_S:
        rt.provisioning_since = None
        return "warm"
    result = await runner.canary(timeout=CANARY_TIMEOUT_S)
    rt.last_canary = result  # keep failures too: the error text is the diagnosis the caller needs
    if result.ok:
        rt.warm_confirmed_at = now
        rt.transient_conflicts = 0
        rt.provisioning_since = None  # warm by any route: a later cold start must not inherit a stale clock
        return "warm"
    rt.warm_confirmed_at = None
    if result.error and result.error != "timeout":
        # A NON-timeout failure means the dispatch path itself broke — e.g. the web service rejected
        # the submit (a user_endpoint_config the endpoint's schema refuses, a bad partition), after
        # which the SDK Executor shuts ITSELF down and every later submit raises `Executor is
        # shutdown`. Left alone, the runner is bricked while the caller sees "allocating nodes…"
        # forever (the #37 dead-end in a new guise). Rebuild it on the next call; the failure text
        # rides `last_canary` into the provisioning notice so the cause is visible, not buried.
        rt.runner_stale = True
        if _no_account_failure(result.error):
            rt.no_account = result.error
        rt.transient_conflicts = rt.transient_conflicts + 1 if _transient_dispatch_failure(result.error) else 0
    return "provisioning"


def _drop_all_shapes(app: AppCtx, *, bank: bool) -> float:
    """Forget every task handle, close every shape's runner, and unbind the endpoint. With `bank`, fold
    each shape's running warm interval into the spend FIRST and return the session total as it stood —
    the four inline copies of this block disagreed, and the connect re-bind's copy silently dropped a
    warm block's interval (found in review). Callers hold app.lock."""
    app.tasks.clear()
    for rt in app.shapes.values():
        if bank:
            _bank_warm_interval(rt, app)
        if rt.runner is not None:
            rt.runner.close()
    spent = _total_session_spend(app) if bank else 0.0
    app.shapes.clear()
    app.state = EndpointState()
    return spent


def _bank_warm_interval(rt: ShapeRuntime, app: AppCtx) -> None:
    """Fold the elapsed warm interval into accrued spend and stop the clock."""
    if rt.warm_since is not None:
        rt.spend_accrued += estimate_spend(
            time.monotonic() - rt.warm_since, app.profile.nodes_per_block, app.charge_factor
        )
        rt.warm_since = None


def _billable(rt: ShapeRuntime) -> bool:
    """LocalProvider (login-node) shapes consume no allocation, so they don't bill."""
    return rt.user_endpoint_config.get("provider_type") != "LocalProvider"


def _settle_billing(rt: ShapeRuntime, app: AppCtx, block: str) -> None:
    """Drive the session-spend clock from TRUE worker presence (the canary), not manager
    liveness. Banking on warm->not-warm makes spend survive an idle block release without
    over-counting the idle gap (the clock stays stopped while cold) — closes the over-report
    without the symmetric under-report of simply resetting. Login (LocalProvider) shapes are
    not billable, so their clock never starts and nothing accrues."""
    if block == "warm" and _billable(rt):
        if rt.warm_since is None:
            rt.warm_since = time.monotonic()
    else:
        _bank_warm_interval(rt, app)


def _session_spend(rt: ShapeRuntime, app: AppCtx) -> float:
    spent = rt.spend_accrued
    if rt.warm_since is not None:
        spent += estimate_spend(
            time.monotonic() - rt.warm_since, app.profile.nodes_per_block, app.charge_factor
        )
    return spent


def _total_session_spend(app: AppCtx) -> float:
    """Total spend across every shape — the cost the agent sees on outcomes/status."""
    return sum(_session_spend(rt, app) for rt in app.shapes.values())


def _local_dill() -> str | None:
    try:
        import dill  # type: ignore[import-untyped]

        return dill.__version__
    except Exception:  # noqa: BLE001 - dill absent locally just means we can't compare
        return None


def _worker_notice(canary: CanaryResult | None) -> str | None:
    """A short warm descriptor for the agent: where the worker landed, its Python/Dill, and a
    serialization-skew warning when worker Dill differs from ours (the real failure mode)."""
    if canary is None:
        return None
    head = f"worker live on {canary.worker_host}" if canary.worker_host else "worker live"
    vers = [v for v in (
        f"py{canary.worker_python}" if canary.worker_python else None,
        f"dill{canary.worker_dill}" if canary.worker_dill else None,
    ) if v]
    note = head + (f" ({', '.join(vers)})" if vers else "")
    local = _local_dill()
    if canary.worker_dill and local and canary.worker_dill != local:
        note += f"; ⚠ dill skew: worker {canary.worker_dill} vs local {local} (serialization may fail)"
    return note


def _idle_release_s(app: AppCtx) -> int:
    """The block's idle-release window: the facility's own (a MEP's template), else our profile's.
    One source — the warm-block bounds note and the MEP stop notice used to read different ones."""
    return int(getattr(app.facility, "max_idletime_s", None) or app.profile.max_idletime_s)


def _billed_bounds_note(app: AppCtx, rt: ShapeRuntime) -> str:
    """The bounds of a billed compute block ([#21]), surfaced so a caller runs long work AS A TASK
    rather than being surprised: a run_shell task runs up to the block walltime (then the worker kills
    it, exit 124) and, if it outlives the sync-wait, comes back as a poll handle (poll_task) — it is
    NOT cut at ~110s any more. The block idle-releases after `max_idletime` once nothing is running or
    queued, so keep long work in the FOREGROUND (a running task holds the block); a detached process
    is not a Compute task and would be idle-released out from under itself."""
    idle = _idle_release_s(app)
    ceiling = int(_task_ceiling_s(rt.user_endpoint_config))
    return (f"billed block bounds — a task runs up to ~{ceiling}s (the block walltime); one that "
            f"outlives the ~{int(SYNC_WAIT_S)}s sync-wait returns a poll handle (poll_task), it is NOT "
            f"cut. The block idle-releases after ~{idle}s once nothing runs or is queued, so run long "
            "work as a foreground task — don't detach it (a detached process isn't a Compute task).")


def _note_dispatch(rt: ShapeRuntime, out: ShellOutcome) -> None:
    """A real result — or a task still running — is the strongest liveness proof, so refresh the canary
    TTL. A dispatch FAILURE (transport timeout/error) means the worker may be gone, so void the
    confirmation to force a re-canary. A completed exit-124 is the worker ENFORCING the task ceiling
    (it answered — it's alive), so it no longer voids (the old timeout==124 heuristic is obsolete now
    that a slow task returns a poll handle, not a 124 failure)."""
    if out.phase in ("complete", "running"):
        rt.warm_confirmed_at = time.monotonic()
    elif out.phase == "failed":
        rt.warm_confirmed_at = None


async def _provision(
    app: AppCtx, shape: str, *, force_canary: bool = False, confirm_spend: bool = False
) -> str:
    """Provision/probe under the session profile and update the spend clock. Returns the
    block state. 'warm' means a WORKER answered a canary — not merely that the manager is
    online; that distinction is the cold-start gap this closes.

    Deterministic spend floor: a scheduler compute shape returns 'needs_confirmation' and starts
    NOTHING until spend is acknowledged (confirm_spend=True, or already confirmed this session).
    The carve-out only applies to billable shapes — a login (LocalProvider) shape is free and
    provisions straight through."""
    rt = _shape_runtime(app, shape)
    if _billable(rt) and not rt.spend_confirmed:
        if not confirm_spend:
            return "needs_confirmation"  # gate BEFORE bootstrap/probe/canary — no block, no charge
        rt.spend_confirmed = True  # ack persists for the session
    if app.state.endpoint_id is None:
        bootstrap = getattr(app.facility, "bootstrap", None)
        if bootstrap is not None:
            handle = await bootstrap(app.profile)
            app.state = EndpointState(endpoint_id=handle.endpoint_id, reused=handle.reused)
    block, app.state = await ensure_warm(app.facility, app.profile, app.state)
    if block == "warm":  # manager online -> confirm a worker is actually live
        block = await _confirm_worker(app, shape, force=force_canary)
    _settle_billing(rt, app, block)
    return block


# Partition names come from the discovery gate (agent/user-supplied), then flow into a Jinja
# template rendered on the login node — so validate the token at the boundary (no shell/YAML
# metacharacters). Scheduler partition/queue names are short identifiers; this allowlist covers
# real ones (letters, digits, '_', '-', '.', ':') without admitting an injection vector.
_VALID_PARTITION = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_VALID_ACCOUNT = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _apply_partition(app: AppCtx, shape: str, rt: ShapeRuntime, partition: str | None) -> str | None:
    """Point this shape's next provision at `partition`, invalidating a stale runner. Returns a
    rejection notice (and applies nothing) when a task is still running on the shape: the change would
    mark the runner stale, and the next _runner_for would close its Executor and cancel that task — so
    make the caller poll_task/stop_endpoint first. Otherwise returns None.

    No-op when `partition` is None (keep the facility/profile default) or unchanged, or for the
    login shape, which has no partition. A real change means a different scheduler block,
    so we mark the cached runner stale (its Executor captured the old partition at build time —
    _runner_for rebuilds it and banks the prior warm interval) and drop the warm confirmation;
    the old block idle-releases on its own (min_blocks=0). The selection persists in
    user_endpoint_config for the rest of the session."""
    if partition is None or not rt.user_endpoint_config.get("compute"):
        return None
    if rt.user_endpoint_config.get("partition") == partition:
        return None
    live = _live_task_handles(app, shape)
    if live:
        return (f"can't change partition to {partition!r}: a task is still running "
                f"(task_id={live[0][0]!r}) on shape {shape!r}. poll_task it or stop_endpoint first.")
    rt.user_endpoint_config["partition"] = partition
    rt.runner_stale = True
    rt.warm_confirmed_at = None
    return None


def _apply_account(app: AppCtx, shape: str, rt: ShapeRuntime, account: str | None) -> str | None:
    """Point this shape's next provision at `account` (the chosen allocation) — the account
    analogue of _apply_partition. Returns a rejection notice (and applies nothing) when a task is still
    running on the shape (the runner swap would cancel it). compute-shape only; the config_template
    renders `account` from user_endpoint_config with the profile default, so a selection here overrides
    it. A change invalidates the cached runner (banking the prior warm interval) and drops the warm
    confirmation; the selection persists for the session."""
    if account is None or not rt.user_endpoint_config.get("compute"):
        return None
    if rt.user_endpoint_config.get("account") == account:
        return None
    live = _live_task_handles(app, shape)
    if live:
        return (f"can't change account to {account!r}: a task is still running "
                f"(task_id={live[0][0]!r}) on shape {shape!r}. poll_task it or stop_endpoint first.")
    rt.user_endpoint_config["account"] = account
    rt.runner_stale = True
    rt.warm_confirmed_at = None
    return None


def _needs_confirmation_notice(app: AppCtx, where: str) -> str:
    """The spend-floor notice. Names the free login shape as the alternative ONLY where one exists —
    on a compute-only facility every shape is billed, so pointing at shape='login' is a dead-end."""
    head = (f"scheduler compute block{where} ({app.profile.nodes_per_block} node(s)): spend "
            "not yet confirmed. ")
    return head + _spend_floor_guidance(app)


def _spend_floor_guidance(app: AppCtx | None) -> str:
    """What to do about an unconfirmed spend — ONE text for ensure_endpoint_up and run_shell/reset
    (they used to drift). Names the free login shape only where one exists: on a compute-only
    facility every shape is billed, so pointing at shape='login' is a dead-end."""
    if app is not None and not _has_login_shape(app):
        return ("This facility is compute-only (no free login shape — every command bills a block, which "
                "then stays warm between calls). Confirm with the user, then call "
                "ensure_endpoint_up(confirm_spend=True) before running work.")
    return ("Surface the allocation balance (e.g. run_shell('mybalance', shape='login')) and call "
            "ensure_endpoint_up(confirm_spend=True) to proceed — or use shape='login' for free "
            "login-node work.")


async def _ensure_endpoint_up(
    app: AppCtx,
    shape: str = DEFAULT_SHAPE,
    partition: str | None = None,
    confirm_spend: bool = False,
    account: str | None = None,
) -> EndpointStatus:
    if reject := _shape_reject(app, shape):  # compute-only facility: never build a login runtime
        return EndpointStatus(
            status="down", block_state="cold", endpoint_id=app.state.endpoint_id, notice=reject,
        )
    if partition is not None and not _VALID_PARTITION.match(partition):
        return EndpointStatus(
            status="down",
            block_state="cold",
            endpoint_id=app.state.endpoint_id,
            notice=f"invalid partition {partition!r}: must match [A-Za-z0-9_.:-]{{1,64}}",
        )
    if account is not None and not _VALID_ACCOUNT.match(account):
        return EndpointStatus(
            status="down",
            block_state="cold",
            endpoint_id=app.state.endpoint_id,
            notice=f"invalid account {account!r}: must match [A-Za-z0-9_.:-]{{1,64}}",
        )
    async with app.lock:  # serialize provisioning/state mutation across concurrent tool calls
        rt = _shape_runtime(app, shape)
        # A login shape has no partition; surface that we ignored a supplied one rather than
        # silently dropping the user's selection.
        ignored = partition is not None and not rt.user_endpoint_config.get("compute")
        reject = _apply_partition(app, shape, rt, partition) or _apply_account(app, shape, rt, account)
        if reject:  # a live task blocks repointing the block (the swap would cancel it) — change nothing
            return EndpointStatus(
                status="up",
                block_state="warm",
                endpoint_id=app.state.endpoint_id,
                session_spend=_total_session_spend(app),
                partition=rt.user_endpoint_config.get("partition"),
                account=rt.user_endpoint_config.get("account"),
                notice=reject,
            )
        active_partition = rt.user_endpoint_config.get("partition")
        active_account = rt.user_endpoint_config.get("account")
        try:
            # force_canary: a status probe must re-verify the worker (and kick a cold block),
            # never trust the TTL — that's exactly the cold-start gap callers are asking about.
            block = await _provision(app, shape, force_canary=True, confirm_spend=confirm_spend)
        except Exception as exc:  # noqa: BLE001 - provisioning unavailable (e.g. non-Linux host)
            return EndpointStatus(
                status="down",
                block_state="cold",
                endpoint_id=app.state.endpoint_id,
                partition=active_partition,
                account=active_account,
                notice=f"hpc-bridge error: {type(exc).__name__}: {exc}"[:500],
            )
        if block == "needs_confirmation":  # the deterministic spend floor — nothing was started
            where = f" on {active_partition!r}" if active_partition else ""
            return EndpointStatus(
                status="needs_confirmation",
                block_state="cold",
                endpoint_id=app.state.endpoint_id,
                partition=active_partition,
                account=active_account,
                notice=_needs_confirmation_notice(app, where),
            )
        billable = _billable(rt)
        eid = app.state.endpoint_id
        spend = _total_session_spend(app)
        provisioning_elapsed = 0.0
        if block == "warm":
            status, notice = "up", _worker_notice(rt.last_canary) or "worker live"
            rt.provisioning_since = None  # warm -> the cold-start grace clock resets (#32)
            if billable:  # #21: name the block's bounds so a caller runs long work as a task
                bounds = _billed_bounds_note(app, rt)
                notice = f"{notice}. {bounds}" if notice else bounds
                if not app.charge_factor:  # walk finding: "session_spend: 0" on a billed block misleads
                    notice += (" (session_spend stays 0 here because no charge factor is configured for this "
                               "facility — the block is still a billed allocation, not a free tier)")
        else:
            status = "provisioning"
            if rt.provisioning_since is None:  # start the grace clock on the first cold poll
                rt.provisioning_since = time.monotonic()
            provisioning_elapsed = time.monotonic() - rt.provisioning_since
            notice = f"allocating nodes on {active_partition!r}…" if active_partition else "allocating nodes…"
            if rt.transient_conflicts >= TRANSIENT_CONFLICT_LIMIT:
                rt.provisioning_since = None
                return EndpointStatus(
                    status="down", block_state="cold", endpoint_id=eid, session_spend=spend,
                    partition=active_partition, account=active_account,
                    notice=(f"the endpoint refused to start for this identity {rt.transient_conflicts} times in a row "
                            f"(RESOURCE_CONFLICT: 'already in use … concurrent requests'). This is NO LONGER transient: "
                            "another session with the SAME Globus identity is starting or holding a user endpoint here "
                            "(a concurrent hpc-bridge run?), or the facility's manager is wedged. Stop retrying: end the "
                            "other session or wait a few minutes, then call ensure_endpoint_up again. Nothing was started."),
                )
            if rt.last_canary is not None and _no_account_failure(rt.last_canary.error):
                # The manager refused to start a user endpoint for THIS identity: no local account. Not
                # 'allocating nodes' — a terminal `down`, so the agent stops polling and tells the user.
                from .login import globus_identity_label

                identity = await asyncio.to_thread(globus_identity_label)
                rt.provisioning_since = None
                return EndpointStatus(
                    status="down", block_state="cold", endpoint_id=eid, session_spend=spend,
                    partition=active_partition, account=active_account,
                    notice=_no_account_notice(app, rt.last_canary.error, identity),
                )
            if not _has_login_shape(app) and rt.last_canary is None:
                # On a MEP a canary runs on EVERY poll whose manager gate passes (and is recorded even
                # when it fails), so "provisioning with no canary ever recorded" means the manager
                # itself reported OFFLINE — a facility outage, not a queue wait. "allocating nodes…"
                # would have the agent wait on a queue that doesn't exist (the #32 pilot query that
                # normally disambiguates rides the login shape, which a MEP hasn't got).
                notice = (
                    f"the facility's multi-user endpoint {eid} reports OFFLINE — not a queue wait. It is run "
                    "by the facility (not hpc-bridge), so nothing here restarts it: contact the facility / "
                    "check its status page, then try again."
                )
            notice += _dispatch_error_suffix(rt.last_canary)
        if ignored:
            notice = f"{notice or ''} (login shape has no partition; ignored {partition!r})".strip()
    # OUTSIDE the lock (dispatch takes it): for a still-cold BILLED block, ask the scheduler whether
    # the pilot actually submitted. A rejected/held qsub is otherwise indistinguishable from a normal
    # queue wait, leaving the caller stuck on "allocating nodes…" forever ([#32]). The grace clock
    # keeps a not-yet-visible pilot during normal cold-start from being cried as rejected.
    # (The query rides the free login shape — a compute-only facility has none, so skip it there; the
    # #37 failure-signal path for a MEP is the dispatch-error suffix already on the notice.)
    if status == "provisioning" and billable and eid and _has_login_shape(app):
        notice = await _augment_provisioning_notice(app, eid, notice, provisioning_elapsed)
    return EndpointStatus(
        status=status,
        block_state=block,
        endpoint_id=eid,
        session_spend=spend,
        partition=active_partition,
        account=active_account,
        notice=notice,
    )


@mcp.tool()
async def ensure_endpoint_up(
    ctx: Context,
    shape: str = DEFAULT_SHAPE,
    partition: str | None = None,
    confirm_spend: bool = False,
    account: str | None = None,
) -> EndpointStatus:
    """Ensure the personal HPC endpoint is up; report whether its pilot block is warm.

    Pass `partition` (from the discovery selection gate) to provision the scheduler block onto that
    partition; the choice persists for the session until changed. Omit it to keep the facility
    default. Ignored for shape="login" (a login-node LocalProvider has no partition).

    Pass `account` (the allocation chosen from connect_facility's options) to charge the scheduler
    block to it; like `partition`, it persists for the session and is ignored for shape="login".

    `confirm_spend` is the deterministic budget floor: a scheduler compute block will not start until
    you pass confirm_spend=True (after surfacing the allocation balance to the user — see the
    driving-hpc skill). Without it the call returns status="needs_confirmation" and provisions
    nothing. The acknowledgement persists for the session. Not needed for shape="login" (free)."""
    return await _ensure_endpoint_up(
        ctx.request_context.lifespan_context, shape, partition, confirm_spend, account
    )


def _registry_transport_error(exc: BaseException) -> bool:
    """Network / Globus API / OS failures reaching the registry — the cases an empty list may stand for."""
    if isinstance(exc, (OSError, TimeoutError)) or hasattr(exc, "http_status"):
        return True
    return type(exc).__name__ in ("GlobusAPIError", "GlobusConnectionError", "GlobusTimeoutError",
                                  "NetworkError", "SearchAPIError", "GlobusConnectionTimeoutError")


async def _list_facilities(query: str = "") -> list[CatalogSummary]:
    try:
        return await make_catalog().discover(query)
    except Exception as exc:  # noqa: BLE001 - classified below: transport -> [], anything else re-raised
        # The registry id is built in, so what lands here is either the network (an empty list is
        # honest: the agent can still BYO) or a BUG — which must not hide behind "no facilities"
        # (found in review: the old blanket net reported an AttributeError as an empty registry).
        print(f"hpc-bridge: list_facilities failed ({type(exc).__name__}: {exc})", file=sys.stderr)
        if _registry_transport_error(exc):
            return []
        raise


async def _authenticate(app: AppCtx, force: bool = False, mode: str | None = None) -> LoginStatus:
    flow = app.login_flow
    if flow is None:
        flow = app.login_flow = LoginFlow()
    if not force and not await asyncio.to_thread(flow.login_required):
        return LoginStatus(phase="logged_in", notice="Globus login present with every scope hpc-bridge needs.")
    start, status = await _start_login_and_wait(flow, mode)
    if status == "done":
        _forget_identity_verdicts(app)
        return LoginStatus(phase="logged_in", notice="Globus login completed in the browser; carry on.")
    return LoginStatus(phase="needs_login", login_url=start.login_url, login_mode=start.mode,
                       notice=_login_notice(start, flow.error,
                                            waited_s=_login_wait_s() if status == "waiting" else None))


async def _complete_login(app: AppCtx, code: str) -> LoginStatus:
    flow = app.login_flow
    if flow is None:
        return LoginStatus(phase="failed", notice="no login is waiting — call authenticate() first")
    try:
        await asyncio.to_thread(flow.complete_with_code, code)
    except Exception as exc:  # noqa: BLE001 - a bad/expired code is a structured outcome, not a crash
        return LoginStatus(phase="failed", notice=f"login code not accepted: {type(exc).__name__}: {exc}"[:300]
                           + " — call authenticate() for a fresh link.")
    _forget_identity_verdicts(app)
    return LoginStatus(phase="logged_in", notice="Globus login complete. Continue: connect_facility again.")


@mcp.tool()
async def authenticate(ctx: Context, force: bool = False, mode: str | None = None) -> LoginStatus:
    """Log in to Globus from the terminal — the ONE credential hpc-bridge needs (it covers computing,
    starting an endpoint, and reading the facility registry). Normally you don't call this: a
    connect_facility that needs it returns phase="needs_login" with the same link. Call it to log in
    proactively, to get a FRESH link after one expired (~10 min), or with force=True to re-login.

    Returns `login_url` for the USER to open. `login_mode="browser"`: their browser completes it and
    this process receives the result — nothing to paste; just call connect_facility again afterwards.
    `login_mode="paste"` (remote/headless sessions): Globus shows a one-time code — ask the user to
    paste it and call complete_login(code). `mode="paste"` forces paste mode (e.g. no browser on this
    machine). Never ask for a Globus password."""
    return await _authenticate(ctx.request_context.lifespan_context, force=force, mode=mode)


@mcp.tool()
async def complete_login(code: str, ctx: Context) -> LoginStatus:
    """Finish a paste-mode Globus login with the one-time authorization code the user pasted (from
    the page Globus showed after they approved). Single-use and short-lived — not a password, not a
    token. Only needed when authenticate()/connect_facility reported login_mode="paste"."""
    return await _complete_login(ctx.request_context.lifespan_context, code)


@mcp.tool()
async def list_facilities(query: str = "") -> list[CatalogSummary]:
    """List the HPC machines hpc-bridge can stand up, from the public facility registry (a Globus
    Search index, read anonymously — works with no login). Empty query lists all; a query filters by
    name/description.

    Returns agent-safe summaries (no executable config or raw UUIDs). Pick one and call
    connect_facility(facility=…) to bring up its login node and see your allocations. No SSH, no
    provisioning, no spend."""
    return await _list_facilities(query)


def _session_endpoint_name(ssh_host: str) -> str:
    """A stable endpoint name for a session (BYO) facility, keyed on the **SSH host** — the canonical
    per-cluster identity — so it never SHARES a registration with another facility AND doesn't sprawl
    when the agent picks different facility ids for the same host (`midway` vs `midway3` both →
    `hpc-bridge-midway3`). Endpoints are keyed by (identity, name); a bare 'hpc-bridge' would collide
    with the curated Anvil endpoint and any stale 'online' registration, which find_online_endpoint
    would then wrongly reuse — leaving a canary that can never warm."""
    slug = re.sub(r"[^a-z0-9]+", "-", (ssh_host or "session").lower()).strip("-") or "session"
    return f"hpc-bridge-{slug}"


def _commit_proven_facility(app: AppCtx, facility: str) -> None:
    """PROVEN: the login shape's canary answered — the only step that exercises the network interface
    the probe flags as its riskiest guess. Only now does a BYO config earn a zero-probe reconnect
    (decision 2026-09-03; caching on acceptance remembered a wrong interface every session)."""
    pending = app.pending_facility_cache.pop(facility, None)
    if pending is not None:
        _facility_store().put(*pending)


def _drop_dead_pin(fac) -> bool:
    """A login-node PIN (endpoints.json) that no longer answers is dropped, so the next connect goes
    back to the facility's canonical host — pins used to be permanent (vault audit 2026-09-03). Only
    an UNREACHABLE host qualifies: a refused login keeps the pin (the host is fine; the access isn't).
    True when a pin was in use and got dropped."""
    store, alias = getattr(fac, "store", None), getattr(fac, "alias", None)
    target = getattr(getattr(fac, "cli", None), "target", None)
    name = getattr(getattr(fac, "profile", None), "endpoint_name", None)
    if store is None or not alias or target is None or not name or getattr(target, "host", alias) == alias:
        return False
    try:
        store.remove(alias=alias, name=name)
    except Exception:  # noqa: BLE001 - best-effort hygiene; the structured failure is what matters
        return False
    return True


def _facility_store():
    """The persistent local-discovery cache of confirmed BYO facility configs (keyed by ssh_host).
    A thin indirection so tests can point it at a tmp path."""
    from .state import FacilityStore

    return FacilityStore()


def _entry_from_details(facility: str, details: FacilityDetails) -> CatalogEntry:
    """Build a SESSION-LOCAL CatalogEntry from user-supplied details — the Socratic fallback for a
    machine not in the catalog. provenance="session"; never written to the shared index. Identity is
    defaulted from the id; the transfer endpoint is omitted (compute-only); the allocation block is
    set only when a listing command + a parser were given (else the human supplies the account)."""
    alloc = None
    if details.allocation_command and details.allocation_parser:
        alloc = Allocation(command=details.allocation_command, parser=details.allocation_parser)
    # HPC_BRIDGE_ENDPOINT_NAME: opt-in override giving each agentic-harness RUN a DISTINCT endpoint
    # name — the shared ssh-host name + a shared test identity would otherwise collide one registration
    # across concurrent runs. Wins over an agent-supplied name too, so a flailing agent can't defeat run
    # isolation. Real users leave it unset and get the ssh-host key (_session_endpoint_name).
    ep_name = ((config.endpoint_name() or "")
               or details.endpoint_name or _session_endpoint_name(details.ssh_host or facility))
    return CatalogEntry(
        id=facility,
        facility_key="session",
        facility=details.display_name or facility,
        description="session-local facility (user-supplied, not catalogued)",
        # The endpoint's UI title (manager config display_name) follows the same convention as its
        # registration name — `hpc-bridge-<ssh_host>` (ssh-host-keyed) — so the two never diverge.
        display_name=details.display_name or ep_name,
        transfer_endpoint_uuid=None,
        ssh_host=details.ssh_host,
        allocation=alloc,
        compute=Compute(
            scheduler=details.scheduler,
            interface=details.interface,
            env_setup=details.env_setup,
            scratch_root=details.scratch_root,
            endpoint_name=ep_name,
            amqp_port=details.amqp_port,
            scheduler_options=details.scheduler_options,
        ),
        defaults=Defaults(
            partition=details.partition,
            walltime=details.walltime,
            cpus_per_node=details.cpus_per_node,
        ),
        provenance="session",
        last_validated=datetime.date.today(),
    )


async def _connect_facility(
    app: AppCtx, facility: str, ssh_host: str | None = None, details: FacilityDetails | None = None
) -> ConnectFacilityResult:
    # Globus login gate — FIRST, before the catalog read and before any SSH. Every non-`unsupported`
    # outcome needs Globus (the SSH path seeds the endpoint's credential from our token storage; the
    # MEP path dispatches with it) — and, found in review, constructing the Compute SDK Client for
    # the catalog on a fresh install would run the SDK's OWN command-line login: a URL on stdout and
    # input() on stdin — i.e. the MCP transport. A phase, not a prompt: the agent shows the link, the
    # user's browser completes it, the next call proceeds. (login_required() is a local SQLite read.)
    if app.login_flow is not None and await asyncio.to_thread(app.login_flow.login_required):
        start, status = await _start_login_and_wait(app.login_flow)
        if status != "done":
            return _needs_login_result(facility, start, app.login_flow.error,
                                       waited_s=_login_wait_s() if status == "waiting" else None)
        # the browser flow completed while we waited — carry straight on with the connection
    # Resolve the entry: a session-local one the agent already supplied wins; else the catalog. An
    # index error is treated as "unresolved" (the agent can still supply details), not a hard fail.
    if details is not None:
        # An explicit details= is a (re)definition — it OVERRIDES any cached session entry or catalog
        # match, so a correction after discovery actually takes effect. Previously the cached entry
        # (frozen on the FIRST call — even one that later failed) silently won, so a wrong field could
        # never be fixed and stranded the whole session (seen live on Midway).
        try:
            entry = _entry_from_details(facility, details)
        except Exception as exc:  # noqa: BLE001 - bad details -> structured failure, not a crash
            return ConnectFacilityResult(
                phase="failed",
                facility=facility,
                notice=f"invalid facility details: {type(exc).__name__}: {exc}"[:300],
            )
        app.session_facilities[facility] = entry  # this session's config; on disk only once PROVEN (below)
        if details.ssh_host:  # persist for LOCAL DISCOVERY — but only once the login shape is PROVEN warm
            app.pending_facility_cache[facility] = (details.ssh_host, details.model_dump(mode="json"))
    else:
        entry = app.session_facilities.get(facility)
        registry_error: Exception | None = None
        if entry is None:
            # THE REGISTRY WINS for any catalogued id (decision 2026-09-03: curated entries are the stable
            # ones). Found live: the maintainer's local cache held an SSH-era `globus1` config that would
            # have shadowed the registry's MEP entry and silently taken the SSH path.
            try:
                entry = await make_catalog().get(facility)
            except Exception as exc:  # noqa: BLE001 - registry unreachable -> the cache may still serve
                registry_error = exc
        if entry is None:
            # LOCAL DISCOVERY: a previously-confirmed BYO config for this host, cached to disk (keyed on
            # ssh_host, canonical; facility id as fallback) — only for facilities the registry does NOT
            # know (or when it is unreachable). Used with NO SSH probe; bootstrap then reuses the online
            # endpoint over the web. A stale/invalid cache falls through to the probe.
            cached = _facility_store().get(ssh_host or facility)
            if cached is not None:
                try:
                    entry = _entry_from_details(facility, FacilityDetails(**cached))
                    app.session_facilities[facility] = entry
                except Exception:  # noqa: BLE001 - stale/invalid cached config
                    entry = None
        if entry is None and registry_error is not None:
            return await _propose_or_ask(
                facility, ssh_host,
                f"registry unavailable ({type(registry_error).__name__}); give me this facility's SSH "
                "host (ssh_host=… or HPC_BRIDGE_SSH_HOST) to probe it, or supply details= directly.",
            )
        if entry is None:
            return await _propose_or_ask(
                facility, ssh_host,
                f"{facility!r} isn't in the catalog. Give me its SSH host (ssh_host=… or "
                "HPC_BRIDGE_SSH_HOST) and I'll probe the login node to propose a config, or supply "
                "details= directly (or list_facilities() if you meant a catalogued one).",
            )
    reason = _unsupported_entry_reason(entry)
    if reason is None and entry.allocation is not None and entry.allocation.parser not in PARSERS:
        reason = (
            f"allocation parser {entry.allocation.parser!r} not implemented yet "
            f"(have: {sorted(PARSERS)})"
        )
    if reason:
        return ConnectFacilityResult(phase="unsupported", facility=facility, notice=reason)
    try:
        # off the loop: it may run `ssh -G` (a subprocess with a 10 s timeout) to read ~/.ssh/config
        fac = await asyncio.to_thread(_facility_from_entry, entry, account=(config.account() or ""))
    except Exception as exc:  # noqa: BLE001 - surface a missing SSH_USER/KEY as a structured result
        return ConnectFacilityResult(
            phase="failed",
            facility=facility,
            notice=f"hpc-bridge error: {type(exc).__name__}: {exc}"[:500],
        )
    async with app.lock:  # switch facilities: drop the old shapes/endpoint, bind the new one
        prior_spend = _drop_all_shapes(app, bank=True)  # the old endpoint's blocks/handles are gone
        app.facility = fac
        app.machine = facility
        # The session-shell root follows the bound facility — else run_shell would use the local
        # ~/.hpc-bridge path on the remote node (same resolution as lifespan's).
        app.scratch_root = _resolve_scratch_root(fac)
        if not _has_login_shape(app):  # a facility-run multi-user endpoint: attach, don't provision
            return await _connect_mep(app, facility, fac)
        try:
            block = await _provision(app, "login", force_canary=True)
        except Exception as exc:  # noqa: BLE001 - provisioning unavailable (e.g. non-Linux host)
            notice = _explain_provision_error(exc, fac)
            if notice.startswith("CANNOT REACH") and _drop_dead_pin(fac):
                notice += " (The remembered login-node pin was dropped: the next connect resolves the facility's host afresh.)"
            return ConnectFacilityResult(phase="failed", facility=facility, notice=notice)
        if block == "warm":
            _commit_proven_facility(app, facility)
    reused = app.state.reused  # reattached to an already-online endpoint (zero SSH), not a fresh bootstrap
    reuse_note = "reused the already-online endpoint (zero-SSH reconnect). " if reused else ""
    if prior_spend > 0:  # a re-bind released a warm block: say what it cost rather than lose the number
        reuse_note = f"the previous facility's shapes were released (session spend so far ≈ {prior_spend:.2f}). " + reuse_note
    if block != "warm":  # login node still coming up — nothing to read yet
        return ConnectFacilityResult(
            phase="provisioning",
            facility=facility,
            reused=reused,
            notice=reuse_note + "bringing up the login node; call connect_facility again shortly to read your allocations",
        )
    if entry.allocation is None:  # no auto-listable allocations -> the human supplies the account
        return ConnectFacilityResult(
            phase="needs_account",
            facility=facility,
            reused=reused,
            allocations=[],
            notice=reuse_note + "login node is up; this facility has no allocation listing — charge a block by "
            "passing the account directly: ensure_endpoint_up(account=…, partition=…, confirm_spend=True).",
        )
    out = await _run_shell(app, entry.allocation.command, shape="login")
    if out.phase != "complete" or out.exit_code != 0:
        return ConnectFacilityResult(
            phase="failed",
            facility=facility,
            notice=f"allocation discovery ({entry.allocation.command!r}) failed: "
            f"{out.notice or out.stderr_snippet or out.phase}",
        )
    allocations = PARSERS[entry.allocation.parser](out.stdout)
    return ConnectFacilityResult(
        phase="needs_account",
        facility=facility,
        reused=reused,
        allocations=allocations,
        notice=reuse_note + "pick an allocation, then ensure_endpoint_up(account=…, partition=…, confirm_spend=True)",
    )


async def _connect_mep(app: AppCtx, facility: str, fac) -> ConnectFacilityResult:
    """Bind a facility-run multi-user endpoint (MEP). Called under app.lock, after the bind.

    There is nothing to stand up: the facility runs the manager and its identity mapping makes our
    Globus identity a local account — zero SSH. So we only ATTACH the catalogued UUID (free) and
    read the manager's status. We deliberately do NOT warm a block here: on a MEP every shape is a
    billed scheduler block, so warming belongs behind the spend gate (ensure_endpoint_up
    confirm_spend=True), not inside connect. There is no login node, hence no allocation listing —
    the account (if the facility needs one) is passed directly."""
    try:
        block, app.state = await ensure_warm(app.facility, app.profile, app.state)
    except Exception as exc:  # noqa: BLE001 - e.g. the SDK can't reach the status API
        return ConnectFacilityResult(
            phase="failed", facility=facility,
            notice=f"hpc-bridge error: {type(exc).__name__}: {exc}"[:500],
        )
    if block != "warm":
        # The manager reported OFFLINE. It isn't ours: no retry on our side brings it up, so don't
        # say "call again shortly" — name the facility as the owner.
        return ConnectFacilityResult(
            phase="failed", facility=facility, reused=True,
            notice=(
                f"the facility's multi-user endpoint {app.state.endpoint_id} reports offline. It is "
                "run by the facility (not by hpc-bridge), so there is nothing to restart here — "
                "contact the facility / check its status page, then connect_facility again."
            ),
        )
    if getattr(fac, "account_required", True):
        how = ("pass the account directly: ensure_endpoint_up(account=…, partition=…, "
               "confirm_spend=True) — no allocation listing exists on a multi-user endpoint.")
    else:
        how = ("NO allocation account is needed at this facility — do not look for one. Confirm spend and go: "
               "ensure_endpoint_up(partition=…, confirm_spend=True).")
    return ConnectFacilityResult(
        phase="needs_account",
        facility=facility,
        reused=True,  # attached to the facility's always-on endpoint: zero SSH, nothing bootstrapped
        allocations=[],
        notice=(
            "attached to the facility's multi-user endpoint (zero SSH, nothing to bootstrap). Attaching "
            "does NOT test your identity mapping — the first block start does (no account there ⇒ a "
            "terminal NO ACCOUNT then, nothing billed). This "
            "facility is COMPUTE-ONLY: there is no free login shape — every command runs on a "
            "billed scheduler block that stays warm between calls. " + how
        ),
    )


def _login_wait_s() -> float:
    """How long a tool call waits for a browser login to land before returning needs_login. Long enough
    for a real IdP round-trip (password + Duo), short enough to stay well under the flow's TTL and any
    MCP tool timeout (run_shell already blocks far longer)."""
    return config.login_wait_s()


async def _start_login_and_wait(flow: LoginFlow, mode: str | None = None) -> tuple[LoginStart, str]:
    """Arm a login and, in browser mode, wait for it. Returns (start, status). A browser attempt that
    FAILS during the wait (no browser after all, Globus rejected the redirect) is re-armed at once in
    paste mode — the failure is remembered by the flow — so the caller shows a usable link, not an error."""
    start = await asyncio.to_thread(flow.start, mode)
    if start.mode != "browser":
        return start, "waiting"  # paste mode: nothing to wait for — the user must hand us a code
    status = await asyncio.to_thread(flow.wait, _login_wait_s())
    if status == "failed":
        start = await asyncio.to_thread(flow.start)  # goes to paste (browser failure remembered)
        status = "waiting"
    return start, status


def _login_notice(start: LoginStart, flow_error: str | None, *, facility: str | None = None,
                  waited_s: float | None = None) -> str:
    """The agent-facing instructions for a login, by mode. Same discipline as needs_preauth: relay
    the link, never ask for a password, never handle a token; in paste mode only a one-time code
    (not a token) passes through the chat."""
    for_what = f" before {facility!r} can be reached" if facility else ""
    head = (f"A Globus login is needed{for_what} (first use, or a stored credential missing a scope an "
            "endpoint needs). ")
    tail = (" The login happens ONLY in the browser: never ask the user for a Globus password, and "
            "never paste the link into a shell.")
    prefix = f"({flow_error}) " if flow_error else ""
    if start.mode == "browser":
        waited = (f"A browser window opened to Globus and I waited {waited_s:.0f}s for the login to land; it "
                  "hasn't yet. ") if waited_s else "A browser window should have opened to Globus. "
        return prefix + head + waited + (
            "If the browser already shows the login finished ('you may close this window'), just call "
            "connect_facility again — nothing to paste. If no browser opened, give the USER this link to "
            f"open: {start.login_url}\nThe link is SINGLE-USE: once the page says they can return, do not "
            "reopen it (it will fail — that is not an error). It is valid for ~10 minutes; a new "
            "connect_facility issues a fresh one and waits again."
        ) + tail
    return prefix + head + (
        f"Give the USER this link to open: {start.login_url}\nAfter they log in and approve, Globus shows "
        "a one-time authorization CODE. Ask them to paste that code here and call complete_login(code). "
        "It is single-use and expires in minutes."
    ) + tail


def _needs_login_result(facility: str, start: LoginStart, flow_error: str | None,
                        waited_s: float | None = None) -> ConnectFacilityResult:
    return ConnectFacilityResult(
        phase="needs_login",
        facility=facility,
        login_url=start.login_url,
        login_mode=start.mode,
        notice=_login_notice(start, flow_error, facility=facility, waited_s=waited_s),
    )


def _needs_preauth_result(facility: str, target) -> ConnectFacilityResult:
    """Surface a one-time interactive-auth handoff (password / MFA / Duo). The user opens a
    ControlMaster in THEIR OWN terminal (entering the secret there); hpc-bridge then multiplexes
    over it. The agent relays the command and NEVER handles the secret — see the credential-handling
    policy in the vault (`Planned/MFA and interactive SSH auth`)."""
    if not getattr(target, "control_dir", None):  # multiplexing off -> a pre-opened master can't be shared
        return ConnectFacilityResult(
            phase="needs_preauth",
            facility=facility,
            notice=f"{target.host} needs an interactive login (password/MFA), but SSH multiplexing is "
            "off. Set HPC_BRIDGE_SSH_CONTROL_PERSIST (e.g. 3600) so a pre-opened master is reusable, "
            "then call connect_facility again.",
        )
    cmd = target.preauth_command()
    return ConnectFacilityResult(
        phase="needs_preauth",
        facility=facility,
        preauth_command=cmd,
        notice=(
            f"{target.host} needs a one-time interactive login (a password and/or MFA/Duo). Ask the "
            "USER to run this in THEIR OWN terminal — they enter the secret directly; never ask for, "
            f"type, or run it with their password yourself:\n    {cmd}\n"
            "It authenticates once and opens a reusable connection. When they confirm it's connected, "
            "call connect_facility again — the session then rides that connection with no further auth."
        ),
    )


async def _propose_or_ask(
    facility: str, ssh_host: str | None, ask_notice: str
) -> ConnectFacilityResult:
    """Index miss + no details: if we have an SSH host, probe the login node and PROPOSE a draft
    config; otherwise ask the agent for the host (SSH access is the one irreducible input). The
    discovery target carries the same ControlMaster socket as the later bootstrap, so probing warms
    the master the bootstrap then rides — no extra authentication."""
    host = (ssh_host or config.ssh_host() or "").strip()
    if not host:
        return ConnectFacilityResult(
            phase="needs_facility_details", facility=facility, notice=ask_notice
        )
    from .facility.remote import NeedsPreauth, SshTarget

    try:
        control_dir, persist = _control_settings()
        key = (config.ssh_key() or "")
        target = SshTarget(
            host=host,
            user=config.ssh_user(),  # else ~/.ssh/config User
            key_path=os.path.expanduser(key) if key else None,  # else config's IdentityFile
            control_dir=control_dir,
            control_persist=persist,
        )
        draft, notes = await discover_facility_details(target)
    except NeedsPreauth as pre:  # host wants an interactive login (password/MFA) — hand off to the user
        return _needs_preauth_result(facility, pre.target)
    except Exception as exc:  # noqa: BLE001 - probe/connect/creds failure -> structured result
        # The same first-contact explanation the bootstrap gives (stranger's walk): a refused SSH is
        # "NO SSH ACCESS to <host> as <user>" with the remedies, not a raw rc=255 dump.
        return ConnectFacilityResult(
            phase="failed",
            facility=facility,
            notice=_explain_provision_error(
                exc, host=host, user=config.ssh_user(),
                fallback=f"discovery over SSH to {host!r} failed: {type(exc).__name__}: {exc}"[:400],
            ),
        )
    notice = (
        "probed the login node and proposed this config — review/correct it WITH THE USER "
        "(confirm the flagged fields, above all `interface`), then call connect_facility(details=…). "
        "Notes: " + " | ".join(notes)
    )
    return ConnectFacilityResult(
        phase="proposed_facility_details",
        facility=facility,
        proposed_details=draft,
        notice=notice[:1800],
    )


@mcp.tool()
async def connect_facility(
    facility: str, ctx: Context, ssh_host: str | None = None, details: FacilityDetails | None = None
) -> ConnectFacilityResult:
    """Select an HPC facility and bring up its (free) login node, then list the allocations a scheduler
    block can be charged to.

    **This is the ENTRY POINT for reaching any facility — ALWAYS call it first** (before login_shell,
    and before reasoning about SSH/Duo yourself): it decides whether SSH is even needed. Don't
    pre-check for an SSH master or assume a password/Duo is required — call this and let it tell you.

    **Reconnecting to a facility you've used before? Pass its `ssh_host`.** connect_facility resolves
    the config from the LOCAL cache (a previously-confirmed BYO facility) with **no SSH probe**, then
    reuses the still-online endpoint over the web (`reused: true`) — a **fully zero-SSH reconnect, no
    re-auth**. So a known MFA facility reconnects with NO Duo prompt while its endpoint is up.

    `facility` is an id/subject/alias from list_facilities() (e.g. "anvil"). This binds the facility,
    stands up the login shape (SSH cold-bootstrap once, or reuse an online endpoint — no scheduler
    account needed), runs the allocation command over Compute, and returns phase="needs_account".
    Pick one, then ensure_endpoint_up(account=…, partition=…, confirm_spend=True). phase=
    "provisioning" ⇒ login node still warming — call again shortly.

    NOT in the catalog and not cached → discover, don't interrogate. Pass `ssh_host` (login
    host/alias; SSH user+key come from the environment) and the tool PROBES the login node →
    phase="proposed_facility_details" with a draft — review/correct it with the user (above all
    `interface`), then call again with details=… to register the session facility (then CACHED for
    zero-SSH reconnects; the canary validates). phase="needs_preauth" ⇒ the host needs a one-time
    interactive login (password/MFA) — relay its `preauth_command` for the user to run in THEIR OWN
    terminal; never handle the secret. neither ssh_host nor details ⇒ needs_facility_details."""
    app = ctx.request_context.lifespan_context
    return await _connect_facility(app, facility, ssh_host=ssh_host, details=details)


def _release_cmd(scheduler: str, eid: str) -> str:
    """Login-shape shell one-liner that cancels THIS endpoint's scheduler block(s), matched
    precisely by the `uep.<eid>` StdOut marker Parsl writes under the UEP dir. Scheduler-specific:
    Slurm reads squeue/scancel; PBS reads qstat -f (unwrapping its 80-col line continuations so a
    wrapped Output_Path can't split the marker) and qdel."""
    marker = f"uep.{eid}"
    if scheduler == "pbs":
        # NB: `qstat -f -u $USER` yields NOTHING on PBS Pro — the -u filter suppresses full-format
        # output entirely (unlike Slurm's `squeue -u`), which silently no-ops the cancel and lets the
        # block burn to walltime (caught in live Polaris validation). Use bare `qstat -f` (all jobs)
        # and let the endpoint-unique `uep.<eid>` marker scope the match to only our jobs.
        return (
            'ids=$(qstat -f 2>/dev/null '
            "| sed ':a;N;$!ba;s/\\n\\t//g' "
            f"| awk -v m={shlex.quote(marker)} 'BEGIN{{RS=\"Job Id: \"}} index($0,m){{print $1}}'); "
            '[ -n "$ids" ] && qdel $ids; echo "released ${ids:-none}"'
        )
    return (
        'ids=$(squeue -u "$USER" -h -O "JobID:30,StdOut:1024" 2>/dev/null '
        f"| grep -F {shlex.quote(marker)} | awk '{{print $1}}'); "
        '[ -n "$ids" ] && scancel $ids; echo "released ${ids:-none}"'
    )


async def _release_blocks_over_login(app: AppCtx, eid: str) -> tuple[bool, str]:
    """Cancel this endpoint's scheduler block(s) by running the scheduler's cancel (scancel/qdel)
    on the **login shape (AMQP)** — never SSH. That's the whole point of the login-node endpoint:
    talk to the cluster over Compute, not a fresh SSH. Matches blocks precisely by the UEP StdOut
    marker (`uep.<eid>`) so it never touches another endpoint's jobs.

    A cold login worker can't dispatch on the first try — it returns cold_start ("allocating
    nodes…"), not `complete`. But that first hit WAKES the worker, so we retry a bounded few times
    to *confirm* the cancel instead of walking away while the block keeps burning. Returns
    `(confirmed, detail)`: `confirmed=False` means the channel stayed cold across the retries and the
    cancel was NOT verified — the caller must report that honestly (never "down"; see #24). An
    unconfirmed cancel is still backstopped by idle-release (`min_blocks=0` + `max_idletime`), and
    re-calling stop (channel now warming) confirms it. Retry budget: HPC_BRIDGE_RELEASE_ATTEMPTS
    (default 3) × HPC_BRIDGE_RELEASE_BACKOFF_S (default 6s)."""
    # The scheduler lives on the facility's MachineProfile (SlurmFacility.profile.scheduler); a
    # facility without one (LocalFacility/dev, or test doubles) has never spoken anything but
    # Slurm's squeue/scancel, so default there instead of assuming an attribute that isn't part
    # of the Facility protocol.
    scheduler = getattr(getattr(app.facility, "profile", None), "scheduler", "slurm")
    cmd = _release_cmd(scheduler, eid)
    attempts = config.release_attempts()
    backoff = config.release_backoff_s()
    detail = "unconfirmed"
    for i in range(attempts):
        out = await _run_shell(app, cmd, shape="login")
        if out.phase == "complete" and out.exit_code == 0:
            line = (out.stdout or "").strip().splitlines()
            return True, (line[-1] if line else "released none")
        detail = out.notice or out.phase or "unconfirmed"
        if i + 1 < attempts and backoff > 0:
            await asyncio.sleep(backoff)  # let the woken login worker register, then re-confirm
    return False, f"cancel not confirmed ({detail}); idle-release will reclaim it"


def _pilot_status_cmd(scheduler: str, eid: str) -> str:
    """Login-shape one-liner that prints THIS endpoint's pilot block(s) as `STATE JOBID` lines,
    matched by the same `uep.<eid>` StdOut marker `_release_cmd` uses (so it never reads another
    endpoint's jobs). Read-only — the diagnostic twin of `_release_cmd`. Empty output ⇒ no pilot is
    in the scheduler (submission rejected, or not yet registered)."""
    marker = f"uep.{eid}"
    if scheduler == "pbs":
        # Bare `qstat -f` (the -u filter suppresses full-format output on PBS Pro); unwrap the 80-col
        # line continuations, split on records, and for records carrying the marker print the
        # job_state letter (R/Q/H) + the job id.
        return (
            "qstat -f 2>/dev/null | sed ':a;N;$!ba;s/\\n\\t//g' "
            f"| awk -v m={shlex.quote(marker)} 'BEGIN{{RS=\"Job Id: \"}} index($0,m){{"
            's="?"; if (match($0,/job_state = [A-Za-z]/)) s=substr($0,RSTART+12,1); '
            "print s\" \"$1}'"
        )
    return (
        # Filter by the marker INSIDE awk (not `grep -F | awk`): grep exits non-zero on no-match,
        # which under a `set -o pipefail` shell would mask an empty result as an error and swallow the
        # "no pilot -> rejected" signal this exists to surface. awk matches AND exits 0 either way.
        'squeue -u "$USER" -h -O "State:20,JobID:24,StdOut:1024" 2>/dev/null '
        f"| awk -v m={shlex.quote(marker)} 'index($0,m){{print $1\" \"$2}}'"
    )


def _summarize_pilot(stdout: str, provisioning_elapsed_s: float) -> tuple[str, str]:
    """(category, notice-suffix) from `_pilot_status_cmd` output. category ∈ {starting, queued, held,
    rejected}. A visible pilot (Q/R/H) is reported at once; a MISSING pilot is only called
    `rejected` once the block has been cold past `PROVISION_GRACE_S` — before that it's a normal
    cold-start gap (empty suffix ⇒ the caller leaves 'allocating nodes…' unchanged)."""
    rows = [ln.split() for ln in stdout.splitlines() if ln.strip()]
    if not rows:
        if provisioning_elapsed_s < PROVISION_GRACE_S:
            return "starting", ""  # normal cold-start window — pilot not visible yet, don't cry wolf
        return "rejected", (
            f"— but NO pilot job is in the scheduler after ~{int(provisioning_elapsed_s)}s. The block "
            "submission was likely REJECTED (e.g. inactive allocation, wrong account, or bad queue) "
            "rather than queued. Check run_shell('qstat -u $USER', shape='login') (squeue on Slurm) "
            "and the endpoint log."
        )
    states = {r[0][:1].upper() for r in rows if r}
    jid = rows[0][1] if len(rows[0]) > 1 else "?"
    if "H" in states:
        return "held", (
            f"— pilot {jid} is HELD; a held job usually means a bad scheduler directive "
            "(e.g. filesystems/account) — inspect qstat -f / the #PBS|#SBATCH directives."
        )
    if "R" in states:
        return "starting", f"— pilot {jid} is RUNNING; the worker is starting, retry shortly."
    return "queued", f"— pilot {jid} is queued (PENDING); waiting on the scheduler."


async def _pilot_status_over_login(app: AppCtx, eid: str, elapsed_s: float) -> tuple[str, str] | None:
    """Ask the scheduler (over the login shape — AMQP, no SSH) what state THIS endpoint's pilot is in.
    Best-effort: returns None when it can't tell (login worker cold, scheduler unreachable) so the
    caller leaves its notice unchanged. `elapsed_s` is how long the block has been provisioning — it
    gates the rejection hint past the cold-start grace."""
    scheduler = getattr(getattr(app.facility, "profile", None), "scheduler", "slurm")
    out = await _run_shell(app, _pilot_status_cmd(scheduler, eid), shape="login")
    if out.phase != "complete" or out.exit_code != 0:
        return None
    return _summarize_pilot(out.stdout or "", elapsed_s)


async def _augment_provisioning_notice(app: AppCtx, eid: str, notice: str, elapsed_s: float) -> str:
    """Enrich a still-cold BILLED block's 'allocating nodes…' with the pilot's ACTUAL scheduler state,
    so a rejected/held submission isn't silently indistinguishable from a queue wait ([#32]). A
    diagnostic must never break the result it annotates, so any failure — or an empty suffix (the
    normal cold-start window) — leaves the notice as-is."""
    try:
        status = await _pilot_status_over_login(app, eid, elapsed_s)
    except Exception:  # noqa: BLE001 - the pilot probe is advisory; never fail provisioning on it
        return notice
    suffix = status[1] if status else ""
    return f"{notice} {suffix}" if suffix else notice


async def _drop_compute_shape(app: AppCtx) -> float:
    """Drop the billed (compute) shape so a later run re-provisions a FRESH block (its runner now
    points at the released block) and stop its spend clock. Keep the login shape (if any), the
    manager, the endpoint_id, and the login-node pin — the endpoint stays online and reusable. Done
    regardless of cancel confirmation: the runner is dead either way, and banking must stop now.
    Returns the spend the dropped shape had accrued, so the caller can still report it — the shape
    is gone from app.shapes, so _total_session_spend() no longer sees it (the dropped block's spend
    must not vanish from the stop report)."""
    async with app.lock:
        _drain_shape_tasks(app, DEFAULT_SHAPE)  # the released block's poll handles are now dead
        compute = app.shapes.pop(DEFAULT_SHAPE, None)
        if compute is None:
            return 0.0
        _bank_warm_interval(compute, app)  # stop the spend clock for the released block
        if compute.runner is not None:
            compute.runner.close()
        return compute.spend_accrued


async def _stop_mep(app: AppCtx, eid: str) -> EndpointStatus:
    """Stop on a facility-run multi-user endpoint: **draining-only, never 'down'**.

    We own neither the manager nor a login channel, and the Globus SDK offers no foreign-endpoint
    cancel (ComputeFuture.cancel is pre-run only; stop/delete_endpoint act on OUR registration). So
    the honest thing (#24) is: stop submitting, drop the shape so no further work lands on the
    block, and rely on the facility template's idle-release (max_idletime) to reclaim it. The block
    keeps burning for up to that idle window after our last task — we report that tail rather than
    pretend it's gone. `draining` here is TERMINAL: re-polling stop will never yield `down`."""
    live = _live_task_handles(app, DEFAULT_SHAPE)
    if live:
        # A running task is exactly what "stop" cannot touch here: there is no cancel channel, so
        # the block stays BUSY (billing) until the task ends — not idle-releasing in ~600s. Refuse,
        # like _apply_partition does for a live task, rather than drain the handles (which would
        # make the result unretrievable) while claiming the block is idle.
        rt = app.shapes[DEFAULT_SHAPE]
        ceiling = int(_task_ceiling_s(rt.user_endpoint_config))
        ids = ", ".join(tid for tid, _ in live)
        return EndpointStatus(
            status="up",
            block_state="warm",
            endpoint_id=eid,
            session_spend=_total_session_spend(app),
            notice=(
                f"can't stop yet: task(s) {ids} are still running on the block, and on a facility "
                "multi-user endpoint hpc-bridge has NO cancel channel — nothing here can end them. The "
                f"block stays busy (billing) until they finish, at most ~{ceiling}s more. poll_task them "
                "to completion (their results stay retrievable), then call stop_endpoint."
            ),
        )
    dropped = await _drop_compute_shape(app)
    idle = _idle_release_s(app)
    return EndpointStatus(
        status="draining",
        block_state="cold",
        endpoint_id=eid,
        session_spend=_total_session_spend(app) + dropped,
        notice=(
            "stopped submitting; the block is DRAINING. On a facility multi-user endpoint hpc-bridge "
            "has no cancel channel, so the block cannot be released or confirmed from here — the "
            f"facility's idle-release reclaims it after ~{int(idle)}s of no tasks (or at walltime). "
            f"Spend may accrue for up to that tail. 'draining' is FINAL on this facility: do NOT "
            "re-poll stop_endpoint waiting for 'down'. The endpoint stays available (it's the facility's)."
        ),
    )


async def _stop_endpoint(app: AppCtx) -> EndpointStatus:
    """Release the compute block over the **login endpoint (AMQP)** and LEAVE the manager online for
    reuse. "Stop" means *stop spending*, not destroy the endpoint: the login-node manager is the
    whole point — it persists so the next session reuses it with **zero SSH** ([[Standing up the
    endpoint|SSH-once]], #12). Fully pulling the endpoint down (`gce stop`, the facility's
    `teardown()`) is a separate, rarer operation, not done here."""
    eid = app.state.endpoint_id
    if eid is None:
        return EndpointStatus(status="down", block_state="cold", notice="no endpoint was up")
    if not _has_login_shape(app):  # a facility MEP: no release channel exists — drain honestly
        return await _stop_mep(app, eid)
    # Cancel the scheduler block over the login shape (AMQP) — no SSH.
    confirmed, detail = await _release_blocks_over_login(app, eid)
    dropped = await _drop_compute_shape(app)
    if confirmed:
        return EndpointStatus(
            status="down",  # cancel CONFIRMED: no billed block running (manager stays online for reuse)
            block_state="cold",
            endpoint_id=eid,
            session_spend=_total_session_spend(app) + dropped,
            notice=f"compute block released over AMQP ({detail}); the login endpoint stays online for "
            "reuse (reconnecting is zero-SSH).",
        )
    if await _endpoint_gone(app):
        # The login endpoint is OFFLINE/gone (the #44 liveness check poll_task got, applied here — found
        # in review): "call again in a few seconds" would loop forever. A block whose manager is gone
        # exits on its own (workers lose their manager), so nothing spends through hpc-bridge.
        return EndpointStatus(
            status="down", block_state="cold", endpoint_id=eid,
            session_spend=_total_session_spend(app) + dropped,
            notice=("the login endpoint is OFFLINE — the cancel cannot be dispatched through it (ORPHANED). "
                    "A block without its manager exits on its own; nothing is spending through hpc-bridge. "
                    "Do not call stop_endpoint again; connect_facility stands the endpoint up afresh."),
        )
    return EndpointStatus(
        # HONEST unconfirmed release (#24): the cancel dispatched but the cold login channel couldn't
        # confirm it, so spend may still be running. NEVER "down" here — the agent must know.
        status="draining",
        block_state="cold",
        endpoint_id=eid,
        session_spend=_total_session_spend(app) + dropped,
        notice=f"{detail}. Spend is NOT confirmed stopped — the login release channel was cold. "
        "idle-release (~10 min, min_blocks=0) is the backstop; call stop_endpoint again in a few "
        "seconds (the channel is warming) to confirm the cancel. The login endpoint stays online for reuse.",
    )


@mcp.tool()
async def stop_endpoint(ctx: Context) -> EndpointStatus:
    """Release the HPC compute block so the allocation stops being charged. Cancels the billed
    scheduler block over the login endpoint (no SSH) and **leaves the login-node endpoint online** so a
    later reconnect reuses it with zero SSH — "stop" means stop spending, not tear the endpoint
    down. Call when you're done with a compute block."""
    return await _stop_endpoint(ctx.request_context.lifespan_context)


async def _teardown_endpoint(app: AppCtx) -> EndpointStatus:
    """FULLY tear the endpoint down: release the billed block, then `gce stop` + delete the login
    manager over SSH (the facility's `teardown()`), and clear ALL shape/state so nothing lingers.
    The rare, explicit 'destroy it' op — normally the login endpoint STAYS ONLINE for zero-SSH reuse
    and costs nothing; a later run_shell would re-bootstrap a fresh endpoint from scratch."""
    eid = app.state.endpoint_id
    if eid is None:
        return EndpointStatus(status="down", block_state="cold", notice="no endpoint was up")
    if not _has_login_shape(app):
        # A facility MEP is NOT ours to destroy (and there's no release channel): detach — drop our
        # shapes/state so nothing of ours lingers — and say exactly that. The facility's endpoint
        # stays online; a block we left is reclaimed by its idle-release (see _stop_mep).
        dropped = await _drop_compute_shape(app)
        async with app.lock:
            spent = _drop_all_shapes(app, bank=True)
        return EndpointStatus(
            status="down",  # OUR state is fully cleared (nothing of ours remains); the facility's endpoint is untouched
            block_state="cold",
            endpoint_id=eid,
            session_spend=spent + dropped,
            notice=(
                "detached from the facility's multi-user endpoint (nothing of ours to tear down — the "
                "facility runs it). Any block still draining is reclaimed by the facility's idle-release; "
                "it cannot be cancelled from here. Do NOT call run_shell now (it would re-attach); "
                "connect_facility re-attaches with zero SSH."
            ),
        )
    await _release_blocks_over_login(app, eid)  # halt spend first (a confirmed stop is stop_endpoint's job)
    notice = "endpoint fully torn down (block released; manager gce-stopped + deleted)"
    teardown = getattr(app.facility, "teardown", None)
    if teardown is not None:
        try:
            await teardown(eid)
        except Exception as exc:  # noqa: BLE001 - report, don't crash the tool
            notice = f"block released; manager teardown reported {type(exc).__name__}: {exc}"[:280]
    async with app.lock:  # clear everything so a stray run_shell can't silently revive a stale endpoint
        spent = _drop_all_shapes(app, bank=True)
    return EndpointStatus(
        status="down",
        block_state="cold",
        endpoint_id=eid,
        session_spend=spent,
        notice=notice + ". It will NOT be reused — a fresh connect_facility re-bootstraps over SSH. "
        "Do NOT call run_shell now (it would provision a new endpoint).",
    )


@mcp.tool()
async def teardown_endpoint(ctx: Context) -> EndpointStatus:
    """FULLY tear down the login-node endpoint (gce stop + delete over SSH) — the rare 'destroy it'
    operation. **Normally do NOT call this.** The login endpoint is DESIGNED to stay online for
    zero-SSH reuse and costs nothing (a free login-node process, no allocation); `stop_endpoint`
    already halts ALL spend by releasing the billed block. Only call this when the user EXPLICITLY
    insists on removing the endpoint entirely. Afterwards, do not call run_shell (it re-provisions)."""
    return await _teardown_endpoint(ctx.request_context.lifespan_context)


async def _login_shell(app: AppCtx, command: str) -> LoginShellResult:
    # No lock: read-only login-node command, independent of the provision/runner state machine.
    login_exec = getattr(app.facility, "login_exec", None)
    if login_exec is None and not _has_login_shape(app):
        return LoginShellResult(
            exit_code=1,
            notice="This facility is a compute-only multi-user endpoint: there is no SSH and no login "
            "node to shell into (the facility maps your Globus identity to a local account over "
            "AMQP). Use run_shell(shape='compute') — the block stays warm between calls.",
        )
    if login_exec is None:
        return LoginShellResult(
            exit_code=1,
            notice="No facility connected. Call connect_facility(facility, ssh_host=…) FIRST — it's "
            "the entry point: for a facility you've used before it reuses the endpoint over the web "
            "with ZERO SSH (no re-auth), and it decides whether SSH is even needed. Don't reach for "
            "login_shell or a manual SSH before that. (Or pin one via HPC_BRIDGE_MACHINE=<id>.)",
        )
    try:
        rc, out, err = await login_exec(command)
    except Exception as exc:  # noqa: BLE001 - never crash the tool; report structurally
        return LoginShellResult(exit_code=1, notice=f"login_shell error: {type(exc).__name__}: {exc}"[:300])
    return LoginShellResult(
        exit_code=rc,
        stdout=cap_output(out, app.max_output_chars),
        stderr_snippet=cap_output(err, app.max_output_chars),
    )


@mcp.tool()
async def login_shell(command: str, ctx: Context) -> LoginShellResult:
    """Run a READ-ONLY command on the HPC login node over a FRESH SSH connection — the
    cold-start discovery escape hatch (`sinfo`, `sacctmgr`, `echo $SCRATCH`) for when no
    endpoint exists yet. It provisions nothing, starts no scheduler job, costs no allocation.

    Prefer `run_shell(command, shape="login")` once an endpoint is up: that runs the same
    login-node command THROUGH the endpoint (over the network), avoiding a fresh SSH — which
    on an MFA facility can force a re-auth. SSH is meant to be a one-time bootstrap, not a
    channel. Only available for an SSH facility (a catalog machine via HPC_BRIDGE_MACHINE or
    connect_facility), not local dev."""
    return await _login_shell(ctx.request_context.lifespan_context, command)


def _dispatch_error_suffix(canary: CanaryResult | None) -> str:
    """A suffix naming a NON-timeout canary failure, else ''. A timeout is the normal cold-start wait
    and stays silent ('allocating nodes…'); anything else means a submit was refused or the dispatch
    path broke — the caller must see WHY rather than keep waiting on a block that will never come."""
    if canary is None or canary.ok or not canary.error or canary.error == "timeout":
        return ""
    if _transient_dispatch_failure(canary.error):
        return (f" — last dispatch was refused as TRANSIENT: {canary.error}. The endpoint is still processing "
                "a previous start request — wait ~10 s and call again (not a config problem)")
    return f" — last dispatch failed: {canary.error}. Not a queue wait: fix the config/partition and retry"


def _transient_dispatch_failure(error: str | None) -> bool:
    """The web service's 409 RESOURCE_CONFLICT ('Endpoint … is already in use: possibly due to concurrent
    requests -- please try again'): seen live when a submit followed another within ~2 s."""
    e = (error or "").lower()
    return "resource_conflict" in e or ("already in use" in e and ("endpoint" in e or "concurrent requests" in e))


def _forget_identity_verdicts(app: AppCtx) -> None:
    """A new Globus login may be a different identity: drop every sticky no-account verdict and make the
    runners rebuild (their Executors were built on the old credential)."""
    for rt in app.shapes.values():
        if rt.no_account:
            rt.no_account = None
            rt.last_canary = None
        rt.runner_stale = True


# The MEP manager's failure notices when it cannot start a user endpoint for the caller's identity
# (globus_compute_endpoint/endpoint/endpoint_manager.py; delivered by the web service as the task's
# failure reason, so our canary future raises with this text). None of them is a queue wait, and
# nothing on our side changes them: the facility must grant an account / add the identity mapping.
_NO_ACCOUNT_MARKERS = (
    "failed to map to a local user",          # identity mapping found no local user
    "local user does not exist",              # mapped, but the account isn't on the machine
    "untrusted identity",                     # single-user endpoint: not the owner's identity
)


def _no_account_failure(error: str | None) -> bool:
    e = (error or "").lower()
    return any(m in e for m in _NO_ACCOUNT_MARKERS)


_GLOBUS_USERNAME_RE = re.compile(r"Globus username:\s*([^\s'\"),]+)")


def _identity_from_error(error: str | None) -> str | None:
    """The web service echoes the submitter in the 422 ('Globus username: alice@example.edu')."""
    m = _GLOBUS_USERNAME_RE.search(error or "")
    return m.group(1) if m else None


def _no_account_notice(app: AppCtx | None, error: str | None, identity: str | None) -> str:
    identity = _identity_from_error(error) or identity
    who = f" ({identity})" if identity else ""
    eid = app.state.endpoint_id if app is not None else None
    where = f" {eid}" if eid else ""
    return (
        f"NO ACCOUNT at this facility: its multi-user endpoint{where} could not map the user's Globus "
        f"identity{who} to a local user — the facility's manager said: {(error or '').strip()[:320]}. "
        "This is TERMINAL, not a queue wait: no retry or poll from here changes it. The user needs an account "
        "on this machine with their Globus identity added to the endpoint's identity mapping — tell them to "
        "ask the facility's support, quoting that identity. Do not call ensure_endpoint_up again until they have."
    )


def _cold_outcome(block: str, canary: CanaryResult | None = None) -> ShellOutcome:
    if canary is not None and _no_account_failure(canary.error):
        from .login import globus_identity_label

        return ShellOutcome(phase="failed", block_state=block,
                            notice=_no_account_notice(None, canary.error, globus_identity_label(fetch=False)))
    return ShellOutcome(
        phase="cold_start",
        block_state=block,
        est_wait_s=60,
        notice="allocating nodes…" + _dispatch_error_suffix(canary),
    )


def _needs_confirmation_outcome(app: AppCtx | None = None) -> ShellOutcome:
    """A billed shape whose spend wasn't acknowledged: the command is NOT dispatched and no
    block is started. The agent must run the budget gate and confirm via ensure_endpoint_up."""
    return ShellOutcome(
        phase="needs_confirmation",
        block_state="cold",
        notice="scheduler compute shape: spend not confirmed, so nothing ran. " + _spend_floor_guidance(app),
    )


async def _ensure_warm_runner(app: AppCtx, shape: str) -> str | None:
    """Ensure a worker is live and the shape's runner is bound to it; returns the block state
    if NOT warm (caller returns a cold_start), else None. _provision -> _confirm_worker
    (re)creates the runner and proves a worker answered, so on 'warm' the runner is ready."""
    block = await _provision(app, shape, force_canary=False)
    return None if block == "warm" else block


def _with_spend(app: AppCtx, out: ShellOutcome) -> ShellOutcome:
    out.session_spend = _total_session_spend(app)
    return out


def _busy_session(app: AppCtx, shape: str, session_id: str) -> str | None:
    """task_id of a task still running on this (shape, session_id), else None. A busy session can't
    take a second command: the two would concurrently mutate the same on-disk cwd/env on the worker.
    (Covers the sequential case — a prior command that became a poll handle; two *simultaneously*
    submitted commands on one session is a pre-existing race, unchanged here.)"""
    for tid, h in _live_task_handles(app, shape):
        if h.session_id == session_id:
            return tid
    return None


def _busy_session_outcome(task_id: str, shape: str, session_id: str) -> ShellOutcome:
    return ShellOutcome(
        phase="failed",
        block_state="warm",
        exit_code=None,
        notice=(f"session {session_id!r} on shape {shape!r} still has a task running "
                f"(task_id={task_id!r}); poll_task it, or run in a different session_id. Two commands "
                "can't share one session's cwd/env at once."),
    )


def _register_task(app: AppCtx, shape: str, session_id: str, command: str, fut, ceiling_s: float) -> str:
    """Register a still-running task as a poll handle and return its id. Caller holds app.lock."""
    app.task_seq += 1
    task_id = f"{shape}-{app.task_seq}"
    app.tasks[task_id] = TaskHandle(
        future=fut,
        shape=shape,
        session_id=session_id,
        command=command,
        submitted_at=time.monotonic(),
        ceiling_s=ceiling_s,
    )
    return task_id


def _running_outcome(app: AppCtx, task_id: str, ceiling_s: float) -> ShellOutcome:
    out = ShellOutcome(
        phase="running",
        block_state="warm",
        task_id=task_id,
        notice=(f"still running past the ~{int(SYNC_WAIT_S)}s sync-wait — it was NOT cut. Poll for its "
                f"result with poll_task({task_id!r}). It runs up to ~{int(ceiling_s)}s (the block "
                "walltime) then is killed (exit 124); submit a batch job for anything longer. The "
                "block stays warm while it runs."),
    )
    return _with_spend(app, out)


def _resolve_task(app: AppCtx, task_id: str) -> ShellOutcome | None:
    """Under the caller's app.lock: shape a terminal outcome if the task is gone/cancelled/finished
    (popping it — the atomic claim, so a concurrent poll gets a benign miss), else None if it's still
    running. Refreshes worker liveness / spend on a finished task."""
    handle = app.tasks.get(task_id)
    if handle is None:
        return ShellOutcome(
            phase="failed", block_state="warm", exit_code=None,
            notice=f"no task {task_id!r} — already retrieved, or its block ended (stop / partition / switch).",
        )
    fut = handle.future
    if fut.cancelled():
        app.tasks.pop(task_id, None)
        return _with_spend(app, ShellOutcome(
            phase="failed", block_state="warm", exit_code=None,
            notice=f"task {task_id!r} was cancelled when its block was torn down.",
        ))
    if not fut.done():
        return None  # still running
    app.tasks.pop(task_id, None)  # atomic claim under the lock
    try:
        res = fut.result()  # done -> returns at once (or raises the task's own exception)
    except Exception as exc:  # noqa: BLE001 - shape a failed task exactly as execute() would
        out = dispatch.failure_outcome(exc, "warm", app.max_output_chars)
    else:
        out = dispatch.complete_outcome(res, "warm", app.max_output_chars)
    _note_dispatch(_shape_runtime(app, handle.shape), out)
    return _with_spend(app, out)


def _shape_reject_outcome(notice: str) -> ShellOutcome:
    return ShellOutcome(phase="failed", block_state="cold", exit_code=None, notice=notice)


async def _ready_session(app: AppCtx, shape: str, session_id: str) -> tuple[GlobusRunner, Session] | ShellOutcome:
    """The shared preamble of run_shell and reset_session: reject an unsupported shape, validate the
    session id, provision + bind the runner atomically under app.lock, and refuse to dispatch when
    the block is cold, the spend is unconfirmed, or a live task owns the session. Returns the runner
    and session, or the outcome to hand back."""
    if reject := _shape_reject(app, shape):
        return _shape_reject_outcome(reject)
    session = Session(session_id, app.scratch_root)  # validates session_id before provisioning
    busy = None
    async with app.lock:  # provision + bind the runner atomically (no race with a concurrent stop)
        not_warm = await _ensure_warm_runner(app, shape)
        runner = _shape_runtime(app, shape).runner
        if not_warm is None:
            busy = _busy_session(app, shape, session_id)
    if not_warm == "needs_confirmation":  # billed shape, spend not acknowledged -> don't dispatch
        return _needs_confirmation_outcome(app)
    if not_warm is not None:
        return _cold_outcome(not_warm, _shape_runtime(app, shape).last_canary)
    if busy is not None:  # a live task owns this session's cwd/env -> don't dispatch a second command
        return _busy_session_outcome(busy, shape, session_id)
    return runner, session


async def _run_shell(
    app: AppCtx, command: str, session_id: str = "default", shape: str = DEFAULT_SHAPE
) -> ShellOutcome:
    ready = await _ready_session(app, shape, session_id)
    if isinstance(ready, ShellOutcome):
        return ready
    runner, session = ready
    wrapped = session_shell.wrap(command, session)
    fut = runner.submit(wrapped)  # submit; wait a bounded time OFF the lock, else hand back a handle
    try:
        res = await asyncio.to_thread(fut.result, runner.timeout)
    except TimeoutError:  # still running past the sync-wait -> a poll handle, NOT a kill
        async with app.lock:
            task_id = _register_task(app, shape, session_id, command, fut, runner.walltime)
            out = _running_outcome(app, task_id, runner.walltime)
            _note_dispatch(_shape_runtime(app, shape), out)  # the worker took our task -> it's alive
            return out
    except Exception as exc:  # noqa: BLE001 - translate ALL dispatch failures to a structured outcome
        out = dispatch.failure_outcome(exc, "warm", app.max_output_chars)
    else:
        out = dispatch.complete_outcome(res, "warm", app.max_output_chars)
    async with app.lock:
        _note_dispatch(_shape_runtime(app, shape), out)
        return _with_spend(app, out)


async def _reset_session(
    app: AppCtx, session_id: str = "default", shape: str = DEFAULT_SHAPE
) -> ShellOutcome:
    ready = await _ready_session(app, shape, session_id)
    if isinstance(ready, ShellOutcome):
        return ready
    runner, session = ready
    cmd = session_shell.reset_command(session)
    out = await dispatch.execute(
        cmd, runner, block_state="warm", max_output_chars=app.max_output_chars
    )
    async with app.lock:
        _note_dispatch(_shape_runtime(app, shape), out)
    return out


async def _endpoint_gone(app: AppCtx) -> bool:
    """True when nothing can resolve a pending task any more: the endpoint the task was dispatched
    to is unbound, or its manager reports offline/deleted. A KILLED BLOCK under a LIVE endpoint is
    not this — Parsl relaunches the block and the task eventually runs, so polling stays correct."""
    eid = app.state.endpoint_id
    if eid is None:
        return True
    try:
        return not await app.facility.manager_online(eid)
    except Exception:  # noqa: BLE001 - a status hiccup must not condemn a live task; keep polling
        return False


def _orphaned_outcome(app: AppCtx, task_id: str) -> ShellOutcome:
    return _with_spend(app, ShellOutcome(
        phase="failed", block_state="cold", exit_code=None,
        notice=(
            f"task {task_id!r} is ORPHANED: the endpoint it was dispatched to is offline or gone, so its "
            "result can never arrive — stop polling. Its block state is unknown (a stopped/deleted "
            "endpoint takes its blocks with it; a facility outage may leave one billing). If you "
            "stopped or tore down the endpoint, this is expected; otherwise connect_facility again "
            "and re-run the command."
        ),
    ))


async def _poll_task(app: AppCtx, task_id: str, wait: float = 0.0) -> ShellOutcome:
    """Retrieve a running task's result (or report it still running). Optionally block up to `wait`
    seconds for it OFF the lock, then re-check under the lock.

    A pending future is only 'running' if something can still resolve it. If the endpoint behind the
    task is offline/gone (torn down by us or by someone else, a facility outage), the future never
    resolves and an agent would poll forever — seen live 2026-08-19: 25 polls over 20 minutes after
    another process deleted the endpoint. So a pending task on a dead endpoint is reported as a
    terminal `failed` (orphaned) and its handle dropped."""
    wait = max(0.0, min(wait, 600.0))  # a bounded courtesy wait; never an unbounded tool hang
    async with app.lock:
        resolved = _resolve_task(app, task_id)
        if resolved is not None:
            return resolved
        handle = app.tasks[task_id]  # resolved is None => the still-running handle is present
        fut, ceiling_s = handle.future, handle.ceiling_s
    if wait > 0:
        try:
            await asyncio.to_thread(fut.result, wait)
        except Exception:  # noqa: BLE001 - the re-resolve reads the true state (done / failed / timeout)
            pass
        async with app.lock:
            resolved = _resolve_task(app, task_id)
            if resolved is not None:
                return resolved
            handle = app.tasks.get(task_id)
            if handle is not None:
                ceiling_s = handle.ceiling_s
    # Still pending: can anything resolve it? (web call — off the lock; then claim under the lock)
    if await _endpoint_gone(app):
        async with app.lock:
            resolved = _resolve_task(app, task_id)  # it may have raced to done in the meantime
            if resolved is not None:
                return resolved
            if app.tasks.pop(task_id, None) is not None:
                return _orphaned_outcome(app, task_id)
    return _running_outcome(app, task_id, ceiling_s)


def _error_outcome(exc: Exception) -> ShellOutcome:
    return ShellOutcome(
        phase="failed",
        block_state="cold",
        exit_code=1,
        notice=f"hpc-bridge error: {type(exc).__name__}: {exc}"[:500],
    )


@mcp.tool()
async def run_shell(
    command: str, ctx: Context, session_id: str = "default", shape: str = DEFAULT_SHAPE
) -> ShellOutcome:
    """Run a shell command on the warm HPC compute block.

    `shape` picks the execution target on the same endpoint: "compute" runs on a
    scheduler block (heavy compute, billed, idle-released); "login" runs on the login
    node via a LocalProvider (lightweight, no allocation). Sessions (cwd/env) persist
    per session_id within a shape.

    LONG WORK: run it as a normal (foreground) command — do NOT background/detach it. A command
    still running past the sync-wait comes back phase="running" with a task_id; poll it with
    poll_task(task_id) until phase="complete". The task runs up to the block walltime and keeps the
    block warm while it runs, so it won't be cut or idle-released — but a *detached* process is not a
    task, so the block would idle-release out from under it (issue #21)."""
    try:
        return await _run_shell(
            ctx.request_context.lifespan_context, command, session_id, shape
        )
    except Exception as exc:  # noqa: BLE001
        return _error_outcome(exc)


@mcp.tool()
async def poll_task(task_id: str, ctx: Context, wait: float = 0.0) -> ShellOutcome:
    """Retrieve the result of a long task that run_shell returned as phase="running" (with a task_id).

    Returns phase="complete" (exit_code, stdout, stderr) once the task finishes, or phase="running"
    if it's still going — poll again. `wait` optionally blocks up to that many seconds for the result
    before returning (default 0 = check once and return now). The task runs up to the block walltime
    and the block stays warm while it runs, so a long job never needs detaching. An unknown or ended
    task_id returns a failed outcome explaining why (already retrieved, or the block was
    stopped/repointed)."""
    try:
        return await _poll_task(ctx.request_context.lifespan_context, task_id, wait)
    except Exception as exc:  # noqa: BLE001 - never crash the tool; return a structured failure
        return _error_outcome(exc)


@mcp.tool()
async def reset_session(
    ctx: Context, session_id: str = "default", shape: str = DEFAULT_SHAPE
) -> ShellOutcome:
    """Clear a session's persisted working directory and environment (fresh slate)."""
    try:
        return await _reset_session(
            ctx.request_context.lifespan_context, session_id, shape
        )
    except Exception as exc:  # noqa: BLE001 - never crash the tool; return a structured failure
        return _error_outcome(exc)


def main() -> None:
    mcp.run()
