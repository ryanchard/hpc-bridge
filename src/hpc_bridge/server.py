from __future__ import annotations

import os
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

from . import dispatch, session_shell
from .cost import estimate_spend, gate_profile
from .endpoint import EndpointCLI
from .facility.base import Facility
from .facility.local import LocalFacility
from .lifecycle import EndpointState, ensure_warm
from .models import EndpointStatus, ShellOutcome
from .profile import Profile
from .runner import GlobusRunner
from .session_shell import Session


@dataclass
class AppCtx:
    facility: Facility
    profile: Profile
    state: EndpointState = field(default_factory=EndpointState)
    runner: GlobusRunner | None = None
    scratch_root: str = "~/.hpc-bridge"
    alloc_floor: float = 1000.0
    charge_factor: float = 0.0
    max_output_chars: int = 1_000_000
    warm_since: float | None = None


def make_facility() -> Facility:
    user_dir = Path(os.environ.get("HPC_BRIDGE_USER_DIR", str(Path.home() / ".globus_compute")))
    return LocalFacility(EndpointCLI(user_dir=user_dir))


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
    scratch = os.path.expanduser(os.environ.get("HPC_BRIDGE_SCRATCH", "~/.hpc-bridge"))
    app = AppCtx(
        facility=make_facility(),
        profile=Profile(mode=_env_mode()),  # type: ignore[arg-type]
        state=EndpointState(endpoint_id=_env_endpoint_id()),
        scratch_root=scratch,
        alloc_floor=_env_float("HPC_BRIDGE_ALLOC_FLOOR", 1000.0),
        charge_factor=_env_float("HPC_BRIDGE_CHARGE_FACTOR", 0.0),
    )
    try:
        yield app
    finally:
        if app.runner is not None:
            app.runner.close()


mcp = FastMCP("hpc-bridge", lifespan=lifespan)


async def _provision(app: AppCtx) -> str:
    """Provision/probe the endpoint under the cost-gated profile and manage the
    session-spend clock. Returns the block state ('warm'|'provisioning'|'cold')."""
    remaining = await app.facility.allocation_remaining()
    profile = gate_profile(app.profile, remaining, app.alloc_floor)
    block, app.state = await ensure_warm(app.facility, profile, app.state)
    if block == "warm":
        if app.warm_since is None:
            app.warm_since = time.monotonic()
    else:
        app.warm_since = None  # not warm -> stop the billing clock
    return block


async def _ensure_endpoint_up(app: AppCtx) -> EndpointStatus:
    try:
        block = await _provision(app)
    except Exception as exc:  # noqa: BLE001 - provisioning unavailable (e.g. non-Linux host)
        return EndpointStatus(
            status="down",
            block_state="cold",
            endpoint_id=app.state.endpoint_id,
            notice=f"hpc-bridge error: {type(exc).__name__}: {exc}"[:500],
        )
    status = "up" if block == "warm" else "provisioning"
    notice = None if block == "warm" else "allocating nodes…"
    return EndpointStatus(
        status=status,
        block_state=block,
        endpoint_id=app.state.endpoint_id,
        notice=notice,
    )


@mcp.tool()
async def ensure_endpoint_up(ctx: Context) -> EndpointStatus:
    """Ensure the personal HPC endpoint is up; report whether its pilot block is warm."""
    return await _ensure_endpoint_up(ctx.request_context.lifespan_context)


def _cold_outcome(block: str) -> ShellOutcome:
    return ShellOutcome(
        phase="cold_start",
        block_state=block,
        est_wait_s=60,
        notice="allocating nodes…",
    )


async def _ensure_warm_runner(app: AppCtx) -> str | None:
    """Ensure the endpoint is warm and a runner exists. Returns block state if NOT warm."""
    block = await _provision(app)
    if block != "warm":
        return block
    if app.runner is None or app.runner.endpoint_id != app.state.endpoint_id:
        if app.runner is not None:
            app.runner.close()
        app.warm_since = time.monotonic()  # fresh billing clock for the new endpoint
        app.runner = GlobusRunner(app.state.endpoint_id)  # type: ignore[arg-type]
    return None


def _with_spend(app: AppCtx, out: ShellOutcome) -> ShellOutcome:
    if app.warm_since is not None:
        out.session_spend = estimate_spend(
            time.monotonic() - app.warm_since, app.profile.nodes_per_block, app.charge_factor
        )
    return out


async def _run_shell(app: AppCtx, command: str, session_id: str = "default") -> ShellOutcome:
    session = Session(session_id, app.scratch_root)  # validates session_id before provisioning
    not_warm = await _ensure_warm_runner(app)
    if not_warm is not None:
        return _cold_outcome(not_warm)
    wrapped = session_shell.wrap(command, session)
    out = await dispatch.execute(
        wrapped, app.runner, block_state="warm", max_output_chars=app.max_output_chars
    )
    return _with_spend(app, out)


async def _reset_session(app: AppCtx, session_id: str = "default") -> ShellOutcome:
    session = Session(session_id, app.scratch_root)  # validates session_id before provisioning
    not_warm = await _ensure_warm_runner(app)
    if not_warm is not None:
        return _cold_outcome(not_warm)
    cmd = session_shell.reset_command(session)
    return await dispatch.execute(
        cmd, app.runner, block_state="warm", max_output_chars=app.max_output_chars
    )


def _error_outcome(exc: Exception) -> ShellOutcome:
    return ShellOutcome(
        phase="failed",
        block_state="cold",
        exit_code=1,
        notice=f"hpc-bridge error: {type(exc).__name__}: {exc}"[:500],
    )


@mcp.tool()
async def run_shell(command: str, ctx: Context, session_id: str = "default") -> ShellOutcome:
    """Run a shell command on the warm HPC compute block.

    The session keeps a persistent working directory and environment, so `cd` and
    relative paths carry across calls within the same session_id.
    """
    try:
        return await _run_shell(ctx.request_context.lifespan_context, command, session_id)
    except Exception as exc:  # noqa: BLE001 - never crash the tool; return a structured failure
        return _error_outcome(exc)


@mcp.tool()
async def reset_session(ctx: Context, session_id: str = "default") -> ShellOutcome:
    """Clear a session's persisted working directory and environment (fresh slate)."""
    try:
        return await _reset_session(ctx.request_context.lifespan_context, session_id)
    except Exception as exc:  # noqa: BLE001 - never crash the tool; return a structured failure
        return _error_outcome(exc)


def main() -> None:
    mcp.run()
