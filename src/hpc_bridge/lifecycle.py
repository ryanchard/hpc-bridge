from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .facility.base import EndpointHandle, Facility
from .profile import Profile

BlockState = Literal["warm", "cold", "provisioning"]


@dataclass
class EndpointState:
    endpoint_id: str | None = None
    handle: EndpointHandle | None = None


async def probe(facility: Facility, state: EndpointState) -> BlockState:
    if state.endpoint_id is None:
        return "cold"
    # Correctness rule (spec §3): warm iff a worker is REGISTERED, not endpoint "Running".
    workers = await facility.worker_count(state.endpoint_id)
    return "warm" if workers >= 1 else "provisioning"


async def ensure_warm(
    facility: Facility, profile: Profile, state: EndpointState
) -> tuple[BlockState, EndpointState]:
    if state.endpoint_id is None:
        handle = await facility.provision(profile)
        state = EndpointState(endpoint_id=handle.endpoint_id, handle=handle)
    block = await probe(facility, state)
    return block, state
