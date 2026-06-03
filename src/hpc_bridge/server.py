from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

from .endpoint import EndpointCLI
from .facility.base import Facility
from .facility.local import LocalFacility
from .lifecycle import EndpointState, ensure_warm
from .models import EndpointStatus
from .profile import Profile


@dataclass
class AppCtx:
    facility: Facility
    profile: Profile
    state: EndpointState = field(default_factory=EndpointState)


def make_facility() -> Facility:
    user_dir = Path(os.environ.get("HPC_BRIDGE_USER_DIR", str(Path.home() / ".globus_compute")))
    return LocalFacility(EndpointCLI(user_dir=user_dir))


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppCtx]:
    mode = os.environ.get("HPC_BRIDGE_PROFILE", "batch")
    yield AppCtx(facility=make_facility(), profile=Profile(mode=mode))  # type: ignore[arg-type]


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


def main() -> None:
    mcp.run()
