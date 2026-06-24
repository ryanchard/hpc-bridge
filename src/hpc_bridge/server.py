from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

from . import dispatch, session_shell
from .cost import cap_output, estimate_spend
from .endpoint import EndpointCLI
from .facility.base import Facility
from .facility.local import LocalFacility
from .lifecycle import EndpointState, ensure_warm
from .models import EndpointStatus, LoginShellResult, ShellOutcome
from .profile import Profile
from .runner import CanaryResult, GlobusRunner
from .session_shell import Session
from .shapes import SHAPES, shape_config

DEFAULT_SHAPE = "slurm"


@dataclass
class ShapeRuntime:
    """Warm/canary/spend state for ONE resource shape (its own Executor + AMQP sub)."""

    user_endpoint_config: dict
    runner: GlobusRunner | None = None
    warm_since: float | None = None
    warm_confirmed_at: float | None = None
    spend_accrued: float = 0.0
    last_canary: CanaryResult | None = None
    # Set when user_endpoint_config changed under a live runner (e.g. a new partition): the
    # cached Executor captured the old config at build time, so _runner_for must rebuild it.
    runner_stale: bool = False
    # Deterministic spend floor: a billed (Slurm) shape may not start a block until spend is
    # explicitly acknowledged via ensure_endpoint_up(confirm_spend=True). Persists for the
    # session once given (no re-nagging); cleared on stop/reset when the shape state is dropped.
    spend_confirmed: bool = False


@dataclass
class AppCtx:
    facility: Facility
    profile: Profile
    state: EndpointState = field(default_factory=EndpointState)
    scratch_root: str = "~/.hpc-bridge"
    charge_factor: float = 0.0
    max_output_chars: int = 1_000_000
    shapes: dict[str, ShapeRuntime] = field(default_factory=dict)
    runner_factory: Callable[..., GlobusRunner] = GlobusRunner
    # serializes provision / runner-swap / teardown so concurrent tool calls can't race AppCtx state
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"{name} is required for the selected HPC_BRIDGE_FACILITY")
    return val


def make_facility() -> Facility:
    """Select the facility: a remote Slurm cluster (HPC_BRIDGE_FACILITY) or local dev."""
    fac = os.environ.get("HPC_BRIDGE_FACILITY", "").strip().lower()
    if fac == "anvil":
        from .facility.remote import RemoteEndpointCLI, SlurmFacility, SshTarget, anvil_profile
        from .state import LoginNodeStore

        alias = os.environ.get("HPC_BRIDGE_SSH_HOST", "anvil.rcac.purdue.edu")
        target = SshTarget(
            host=alias,
            user=_require_env("HPC_BRIDGE_SSH_USER"),
            key_path=os.path.expanduser(_require_env("HPC_BRIDGE_SSH_KEY")),
        )
        profile = anvil_profile(
            account=_require_env("HPC_BRIDGE_ACCOUNT"),
            user=target.user,
            partition=os.environ.get("HPC_BRIDGE_PARTITION", "debug"),
        )
        cli = RemoteEndpointCLI(target, profile.env_setup)
        store = LoginNodeStore()
        rec = store.get(alias=alias, name=profile.endpoint_name)
        if rec is not None:  # reconnect direct-to-node instead of the round-robin alias
            # Dead-pin limitation: if the pinned node is down or the endpoint is gone, the
            # next SSH fails fast (BatchMode) and surfaces as a structured error; clearing
            # or reconciling a stale pin is deferred (delete ~/.hpc-bridge/endpoints.json
            # to reset).
            cli.rebind(rec.login_host)
        return SlurmFacility(profile, cli, store=store, alias=alias)
    user_dir = Path(os.environ.get("HPC_BRIDGE_USER_DIR", str(Path.home() / ".globus_compute")))
    return LocalFacility(EndpointCLI(user_dir=user_dir))


def _make_search_client():
    """Build a Globus SearchClient reusing the Compute SDK's identity.

    Spec §8 open item: confirm `search.api.globus.org` composes with the Compute SDK token
    cache and triggers NO second login. Isolated here so make_catalog() can fall back if it
    fails, and so tests can substitute it.
    """
    from globus_compute_sdk import Client
    from globus_sdk import SearchClient

    authorizer = Client().login_manager.get_authorizer("search.api.globus.org")
    return SearchClient(authorizer=authorizer)


def make_catalog():
    """Select the catalog provider from env, mirroring make_facility().

    HPC_BRIDGE_SEARCH_INDEX set -> SearchCatalog (Globus Search) with bundled+cache fallback.
    Otherwise, or if the Search client can't be built -> BundledCatalog (the packaged seed YAML).
    """
    from .catalog.bundled import BundledCatalog

    index = os.environ.get("HPC_BRIDGE_SEARCH_INDEX", "").strip()
    if not index:
        return BundledCatalog()

    from .catalog.search import SearchCatalog

    try:
        client = _make_search_client()
        cache_dir = (
            Path(os.environ.get("CLAUDE_PLUGIN_DATA", str(Path.home() / ".hpc-bridge")))
            / "catalog-cache"
        )
        return SearchCatalog(
            index_id=index, client=client, fallback=BundledCatalog(), cache_dir=cache_dir
        )
    except Exception as exc:  # noqa: BLE001 - never crash startup on a Search/auth/cache problem
        print(
            f"hpc-bridge: Globus Search unavailable ({type(exc).__name__}: {exc}); "
            "using bundled catalog",
            file=sys.stderr,
        )
        return BundledCatalog()


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"hpc-bridge: ignoring invalid {name}={raw!r}; using {default}", file=sys.stderr)
        return default


def _env_mode(default: str = "batch") -> str:
    mode = os.environ.get("HPC_BRIDGE_PROFILE", default)
    if mode not in ("interactive", "batch"):
        print(f"hpc-bridge: ignoring invalid HPC_BRIDGE_PROFILE={mode!r}; using {default}", file=sys.stderr)
        return default
    return mode


def _env_endpoint_id() -> str | None:
    """An existing endpoint UUID to dispatch to instead of provisioning a local one.

    Set HPC_BRIDGE_ENDPOINT_ID to skip local provisioning entirely. Required on
    macOS/Windows, where globus-compute-endpoint (the local endpoint daemon) cannot
    run — the SDK dispatch path still reaches a remote/Linux endpoint by UUID.
    """
    eid = os.environ.get("HPC_BRIDGE_ENDPOINT_ID", "").strip()
    return eid or None


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppCtx]:
    facility = make_facility()
    # Session-shell root: explicit env wins, else the facility's shared-FS scratch
    # (e.g. Anvil $SCRATCH), else a local default.
    scratch = os.path.expanduser(
        os.environ.get("HPC_BRIDGE_SCRATCH") or getattr(facility, "scratch_root", None) or "~/.hpc-bridge"
    )
    app = AppCtx(
        facility=facility,
        profile=Profile(mode=_env_mode()),  # type: ignore[arg-type]
        state=EndpointState(endpoint_id=_env_endpoint_id()),
        scratch_root=scratch,
        charge_factor=_env_float("HPC_BRIDGE_CHARGE_FACTOR", 0.0),
    )
    try:
        yield app
    finally:
        for rt in app.shapes.values():
            if rt.runner is not None:
                rt.runner.close()


mcp = FastMCP("hpc-bridge", lifespan=lifespan)


CANARY_TTL_S = 45.0  # trust a confirmed worker this long before re-canarying. Safe: an idle
# block needs >= max_idletime (default 600s) of SILENCE to release, so a worker seen <45s ago
# cannot have idle-released out from under us.
CANARY_TIMEOUT_S = 8.0  # a live worker answers in ~1-2s; a cold block blows past this -> not warm


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


def _runner_for(app: AppCtx, shape: str) -> GlobusRunner:
    """Reuse the shape's runner if it's bound to the current endpoint, else (re)create it. A
    new endpoint voids the prior worker confirmation and banks the old endpoint's spend."""
    rt = _shape_runtime(app, shape)
    eid = app.state.endpoint_id
    if rt.runner is None or rt.runner.endpoint_id != eid or rt.runner_stale:
        if rt.runner is not None:
            rt.runner.close()
            _bank_warm_interval(rt, app)
        rt.runner = app.runner_factory(eid, user_endpoint_config=rt.user_endpoint_config)
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
    runner = _runner_for(app, shape)
    now = time.monotonic()
    if not force and rt.warm_confirmed_at is not None and now - rt.warm_confirmed_at < CANARY_TTL_S:
        return "warm"
    result = await runner.canary(timeout=CANARY_TIMEOUT_S)
    if result.ok:
        rt.warm_confirmed_at = now
        rt.last_canary = result
        return "warm"
    rt.warm_confirmed_at = None
    return "provisioning"


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


def _note_dispatch(rt: ShapeRuntime, out: ShellOutcome) -> None:
    """A real result is the strongest liveness proof — refresh the canary TTL. A dispatch
    timeout means the worker may be gone, so void the confirmation to force a re-canary."""
    if out.phase == "complete":
        rt.warm_confirmed_at = time.monotonic()
    elif out.exit_code == 124:
        rt.warm_confirmed_at = None


async def _provision(
    app: AppCtx, shape: str, *, force_canary: bool = False, confirm_spend: bool = False
) -> str:
    """Provision/probe under the session profile and update the spend clock. Returns the
    block state. 'warm' means a WORKER answered a canary — not merely that the manager is
    online; that distinction is the cold-start gap this closes.

    Deterministic spend floor: a billed (Slurm) shape returns 'needs_confirmation' and starts
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
            app.state = EndpointState(endpoint_id=handle.endpoint_id)
    block, app.state = await ensure_warm(app.facility, app.profile, app.state)
    if block == "warm":  # manager online -> confirm a worker is actually live
        block = await _confirm_worker(app, shape, force=force_canary)
    _settle_billing(rt, app, block)
    return block


# Partition names come from the discovery gate (agent/user-supplied), then flow into a Jinja
# template rendered on the login node — so validate the token at the boundary (no shell/YAML
# metacharacters). Slurm partition names are short identifiers; this allowlist covers real ones
# (letters, digits, '_', '-', '.', ':') without admitting an injection vector.
_VALID_PARTITION = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def _apply_partition(rt: ShapeRuntime, partition: str | None) -> None:
    """Point this shape's next provision at `partition`, invalidating a stale runner.

    No-op when `partition` is None (keep the facility/profile default) or unchanged, or for a
    non-Slurm (login) shape, which has no partition. A real change means a different Slurm block,
    so we mark the cached runner stale (its Executor captured the old partition at build time —
    _runner_for rebuilds it and banks the prior warm interval) and drop the warm confirmation;
    the old block idle-releases on its own (min_blocks=0). The selection persists in
    user_endpoint_config for the rest of the session."""
    if partition is None or not rt.user_endpoint_config.get("is_slurm"):
        return
    if rt.user_endpoint_config.get("partition") == partition:
        return
    rt.user_endpoint_config["partition"] = partition
    rt.runner_stale = True
    rt.warm_confirmed_at = None


async def _ensure_endpoint_up(
    app: AppCtx,
    shape: str = DEFAULT_SHAPE,
    partition: str | None = None,
    confirm_spend: bool = False,
) -> EndpointStatus:
    if partition is not None and not _VALID_PARTITION.match(partition):
        return EndpointStatus(
            status="down",
            block_state="cold",
            endpoint_id=app.state.endpoint_id,
            notice=f"invalid partition {partition!r}: must match [A-Za-z0-9_.:-]{{1,64}}",
        )
    async with app.lock:  # serialize provisioning/state mutation across concurrent tool calls
        rt = _shape_runtime(app, shape)
        # A login shape has no partition; surface that we ignored a supplied one rather than
        # silently dropping the user's selection.
        ignored = partition is not None and not rt.user_endpoint_config.get("is_slurm")
        _apply_partition(rt, partition)
        active_partition = rt.user_endpoint_config.get("partition")
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
                notice=f"hpc-bridge error: {type(exc).__name__}: {exc}"[:500],
            )
        if block == "needs_confirmation":  # the deterministic spend floor — nothing was started
            where = f" on {active_partition!r}" if active_partition else ""
            return EndpointStatus(
                status="needs_confirmation",
                block_state="cold",
                endpoint_id=app.state.endpoint_id,
                partition=active_partition,
                notice=(
                    f"billed Slurm block{where} ({app.profile.nodes_per_block} node(s)): spend "
                    "not yet confirmed. Surface the allocation balance (e.g. login_shell('mybalance')) "
                    "and re-call ensure_endpoint_up(confirm_spend=True) to proceed — or use "
                    "shape='login' for free login-node work."
                ),
            )
        if block == "warm":
            status, notice = "up", _worker_notice(rt.last_canary)
        else:
            status = "provisioning"
            notice = f"allocating nodes on {active_partition!r}…" if active_partition else "allocating nodes…"
        if ignored:
            notice = f"{notice} (login shape has no partition; ignored {partition!r})"
        return EndpointStatus(
            status=status,
            block_state=block,
            endpoint_id=app.state.endpoint_id,
            session_spend=_total_session_spend(app),
            partition=active_partition,
            notice=notice,
        )


@mcp.tool()
async def ensure_endpoint_up(
    ctx: Context, shape: str = "slurm", partition: str | None = None, confirm_spend: bool = False
) -> EndpointStatus:
    """Ensure the personal HPC endpoint is up; report whether its pilot block is warm.

    Pass `partition` (from the discovery selection gate) to provision the Slurm block onto that
    partition; the choice persists for the session until changed. Omit it to keep the facility
    default. Ignored for shape="login" (a login-node LocalProvider has no partition).

    `confirm_spend` is the deterministic budget floor: a billed Slurm block will not start until
    you pass confirm_spend=True (after surfacing the allocation balance to the user — see the
    driving-hpc skill). Without it the call returns status="needs_confirmation" and provisions
    nothing. The acknowledgement persists for the session. Not needed for shape="login" (free)."""
    return await _ensure_endpoint_up(
        ctx.request_context.lifespan_context, shape, partition, confirm_spend
    )


async def _stop_endpoint(app: AppCtx) -> EndpointStatus:
    """Tear down the current endpoint, release its compute, and reset session state."""
    async with app.lock:  # exclude concurrent dispatch/provision while we tear down
        eid = app.state.endpoint_id
        teardown = getattr(app.facility, "teardown", None)
        torn_down = False
        if eid is None:
            notice = "no endpoint was up"
            torn_down = True  # nothing running -> the pin (if any) is safe to clear
        elif teardown is None:
            notice = "this facility has no teardown (local dev)"
        else:
            try:
                await teardown(eid)
                notice = "endpoint stopped; compute block released"
                torn_down = True
            except Exception as exc:  # noqa: BLE001 - report, never crash the tool
                notice = f"stop attempted; {type(exc).__name__}: {exc}"[:300]
        # Only drop the login-node pin if the daemon is actually gone. A failed teardown
        # may leave it running on the pinned node, so we keep the pin for reconnect rather
        # than orphan it (the exact bug pinning exists to prevent).
        store = getattr(app.facility, "store", None)
        alias = getattr(app.facility, "alias", None)
        name = getattr(getattr(app.facility, "profile", None), "endpoint_name", None)
        if torn_down and store is not None and alias is not None and name is not None:
            store.remove(alias=alias, name=name)
        for rt in app.shapes.values():
            if rt.runner is not None:
                rt.runner.close()
        app.shapes.clear()  # session ended -> spend/warm state starts fresh next time
        app.state = EndpointState()  # clear so the next ensure_endpoint_up re-provisions
        return EndpointStatus(status="down", block_state="cold", endpoint_id=eid, notice=notice)


@mcp.tool()
async def stop_endpoint(ctx: Context) -> EndpointStatus:
    """Stop the HPC endpoint and release its compute block(s). Call when finished with a
    session so the allocation stops being charged for the held block."""
    return await _stop_endpoint(ctx.request_context.lifespan_context)


async def _login_shell(app: AppCtx, command: str) -> LoginShellResult:
    # No lock: read-only login-node command, independent of the provision/runner state machine.
    login_exec = getattr(app.facility, "login_exec", None)
    if login_exec is None:
        return LoginShellResult(
            exit_code=1,
            notice="login_shell needs an SSH facility (set HPC_BRIDGE_FACILITY=anvil); "
            "the local dev facility has no login node.",
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
    endpoint exists yet. It provisions nothing, starts no Slurm job, costs no allocation.

    Prefer `run_shell(command, shape="login")` once an endpoint is up: that runs the same
    login-node command THROUGH the endpoint (over the network), avoiding a fresh SSH — which
    on an MFA facility can force a re-auth. SSH is meant to be a one-time bootstrap, not a
    channel. Only available for an SSH facility (HPC_BRIDGE_FACILITY=anvil), not local dev."""
    return await _login_shell(ctx.request_context.lifespan_context, command)


def _cold_outcome(block: str) -> ShellOutcome:
    return ShellOutcome(
        phase="cold_start",
        block_state=block,
        est_wait_s=60,
        notice="allocating nodes…",
    )


def _needs_confirmation_outcome() -> ShellOutcome:
    """A billed shape whose spend wasn't acknowledged: the command is NOT dispatched and no
    block is started. The agent must run the budget gate and confirm via ensure_endpoint_up."""
    return ShellOutcome(
        phase="needs_confirmation",
        block_state="cold",
        notice=(
            "billed Slurm shape: spend not confirmed, so nothing ran. Surface the allocation "
            "balance (login_shell('mybalance')) and call ensure_endpoint_up(confirm_spend=True) "
            "before running work — or use shape='login' for free login-node work."
        ),
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


async def _run_shell(
    app: AppCtx, command: str, session_id: str = "default", shape: str = DEFAULT_SHAPE
) -> ShellOutcome:
    session = Session(session_id, app.scratch_root)  # validates session_id before provisioning
    async with app.lock:  # provision + bind the runner atomically (no race with a concurrent stop)
        not_warm = await _ensure_warm_runner(app, shape)
        runner = _shape_runtime(app, shape).runner
    if not_warm == "needs_confirmation":  # billed shape, spend not acknowledged -> don't dispatch
        return _needs_confirmation_outcome()
    if not_warm is not None:
        return _cold_outcome(not_warm)
    wrapped = session_shell.wrap(command, session)
    out = await dispatch.execute(  # dispatch OUTSIDE the lock so long commands don't serialize
        wrapped, runner, block_state="warm", max_output_chars=app.max_output_chars
    )
    async with app.lock:
        _note_dispatch(_shape_runtime(app, shape), out)
        return _with_spend(app, out)


async def _reset_session(
    app: AppCtx, session_id: str = "default", shape: str = DEFAULT_SHAPE
) -> ShellOutcome:
    session = Session(session_id, app.scratch_root)  # validates session_id before provisioning
    async with app.lock:
        not_warm = await _ensure_warm_runner(app, shape)
        runner = _shape_runtime(app, shape).runner
    if not_warm == "needs_confirmation":  # billed shape, spend not acknowledged -> don't dispatch
        return _needs_confirmation_outcome()
    if not_warm is not None:
        return _cold_outcome(not_warm)
    cmd = session_shell.reset_command(session)
    out = await dispatch.execute(
        cmd, runner, block_state="warm", max_output_chars=app.max_output_chars
    )
    async with app.lock:
        _note_dispatch(_shape_runtime(app, shape), out)
    return out


def _error_outcome(exc: Exception) -> ShellOutcome:
    return ShellOutcome(
        phase="failed",
        block_state="cold",
        exit_code=1,
        notice=f"hpc-bridge error: {type(exc).__name__}: {exc}"[:500],
    )


@mcp.tool()
async def run_shell(
    command: str, ctx: Context, session_id: str = "default", shape: str = "slurm"
) -> ShellOutcome:
    """Run a shell command on the warm HPC compute block.

    `shape` picks the execution target on the same endpoint: "slurm" runs on a
    scheduler block (heavy compute, billed, idle-released); "login" runs on the login
    node via a LocalProvider (lightweight, no allocation). Sessions (cwd/env) persist
    per session_id within a shape."""
    try:
        return await _run_shell(
            ctx.request_context.lifespan_context, command, session_id, shape
        )
    except Exception as exc:  # noqa: BLE001
        return _error_outcome(exc)


@mcp.tool()
async def reset_session(
    ctx: Context, session_id: str = "default", shape: str = "slurm"
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
