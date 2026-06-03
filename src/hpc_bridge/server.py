from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

from . import dispatch, session_shell
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


def make_facility() -> Facility:
    user_dir = Path(os.environ.get("HPC_BRIDGE_USER_DIR", str(Path.home() / ".globus_compute")))
    return LocalFacility(EndpointCLI(user_dir=user_dir))


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppCtx]:
    mode = os.environ.get("HPC_BRIDGE_PROFILE", "batch")
    scratch = os.path.expanduser(os.environ.get("HPC_BRIDGE_SCRATCH", "~/.hpc-bridge"))
    app = AppCtx(
        facility=make_facility(),
        profile=Profile(mode=mode),  # type: ignore[arg-type]
        scratch_root=scratch,
    )
    try:
        yield app
    finally:
        if app.runner is not None:
            app.runner.close()


mcp = FastMCP("hpc-bridge", lifespan=lifespan)


async def _ensure_endpoint_up(app: AppCtx) -> EndpointStatus:
    block, app.state = await ensure_warm(app.facility, app.profile, app.state)
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
    block, app.state = await ensure_warm(app.facility, app.profile, app.state)
    if block != "warm":
        return block
    if app.runner is None or app.runner.endpoint_id != app.state.endpoint_id:
        if app.runner is not None:
            app.runner.close()
        app.runner = GlobusRunner(app.state.endpoint_id)  # type: ignore[arg-type]
    return None


async def _run_shell(app: AppCtx, command: str, session_id: str = "default") -> ShellOutcome:
    not_warm = await _ensure_warm_runner(app)
    if not_warm is not None:
        return _cold_outcome(not_warm)
    wrapped = session_shell.wrap(command, Session(session_id, app.scratch_root))
    return await dispatch.execute(wrapped, app.runner, block_state="warm")


async def _reset_session(app: AppCtx, session_id: str = "default") -> ShellOutcome:
    not_warm = await _ensure_warm_runner(app)
    if not_warm is not None:
        return _cold_outcome(not_warm)
    cmd = session_shell.reset_command(Session(session_id, app.scratch_root))
    return await dispatch.execute(cmd, app.runner, block_state="warm")


@mcp.tool()
async def run_shell(command: str, ctx: Context, session_id: str = "default") -> ShellOutcome:
    """Run a shell command on the warm HPC compute block.

    The session keeps a persistent working directory and environment, so `cd` and
    relative paths carry across calls within the same session_id.
    """
    return await _run_shell(ctx.request_context.lifespan_context, command, session_id)


@mcp.tool()
async def reset_session(ctx: Context, session_id: str = "default") -> ShellOutcome:
    """Clear a session's persisted working directory and environment (fresh slate)."""
    return await _reset_session(ctx.request_context.lifespan_context, session_id)


def main() -> None:
    mcp.run()
