from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

NodeHours = float


class ShellOutcome(BaseModel):
    phase: Literal["complete", "cold_start", "failed"]
    exit_code: int | None = None
    stdout: str = ""
    stderr_snippet: str = ""
    cwd: str | None = None
    block_state: Literal["warm", "cold", "provisioning"]
    session_spend: NodeHours = 0.0
    task_handle: str | None = None
    est_wait_s: int | None = None
    notice: str | None = None


class EndpointStatus(BaseModel):
    status: Literal["up", "provisioning", "down"]
    block_state: Literal["warm", "cold", "provisioning"]
    endpoint_id: str | None = None
    session_spend: NodeHours = 0.0
    cert_expires_in: str | None = None
    notice: str | None = None


class LoginShellResult(BaseModel):
    """Result of a read-only login-node command (facility discovery) — a separate channel
    from the compute-block `ShellOutcome`: no block, no allocation, no session spend."""

    exit_code: int
    stdout: str = ""
    stderr_snippet: str = ""
    notice: str | None = None
