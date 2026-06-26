from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

NodeHours = float


class ShellOutcome(BaseModel):
    # needs_confirmation: a billed (Slurm) shape whose spend hasn't been acknowledged — the
    # command was NOT dispatched and no block was started (run ensure_endpoint_up(confirm_spend=True)).
    phase: Literal["complete", "cold_start", "failed", "needs_confirmation"]
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
    # needs_confirmation: a billed (Slurm) block was requested without an explicit spend
    # acknowledgement — nothing was provisioned; re-call with confirm_spend=True to proceed.
    status: Literal["up", "provisioning", "down", "needs_confirmation"]
    block_state: Literal["warm", "cold", "provisioning"]
    endpoint_id: str | None = None
    session_spend: NodeHours = 0.0
    cert_expires_in: str | None = None
    # Slurm partition this shape will provision onto (from the discovery selection gate, or
    # the facility default). None for non-Slurm shapes (login/local).
    partition: str | None = None
    # Allocation/account this Slurm shape charges to (from connect_facility's selection, or the
    # facility default). None for non-Slurm shapes.
    account: str | None = None
    notice: str | None = None


class LoginShellResult(BaseModel):
    """Result of a read-only login-node command (facility discovery) — a separate channel
    from the compute-block `ShellOutcome`: no block, no allocation, no session spend."""

    exit_code: int
    stdout: str = ""
    stderr_snippet: str = ""
    notice: str | None = None


class AllocationOption(BaseModel):
    """One allocation the user could charge to — the agent-facing choice (from connect_facility)."""

    account: str  # the value that becomes SlurmProvider.account
    balance: float
    units: str = "SU"
    type: str | None = None  # e.g. CPU / GPU, when the facility distinguishes them


class FacilityDetails(BaseModel):
    """User-supplied config for a facility that isn't in the catalog — the elicitation template for
    `connect_facility`'s `needs_facility_details` phase. The field descriptions ARE the questions to
    ask the user; the agent fills this in from their answers. SSH login user + key come from the
    environment (HPC_BRIDGE_SSH_USER/KEY), not here — this is facility config, not credentials.
    Session-local: never written to the shared catalog."""

    ssh_host: str = Field(
        description="Login host to SSH to for the one-time bootstrap.",
        examples=["anvil.rcac.purdue.edu", "frontier.olcf.ornl.gov"],
    )
    interface: str = Field(
        description="High-speed network interface the compute workers bind to so they can phone "
        "home (address_by_interface). Wrong value ⇒ workers never register.",
        examples=["ib0", "hsn0"],
    )
    env_setup: str = Field(
        description="Bash that puts `globus-compute-endpoint` on PATH on the login node — usually a "
        "`module load …` and/or `source <venv>/bin/activate`. '{user}'/'{venv}' are templated.",
        examples=["module load anaconda && source {venv}/bin/activate"],
    )
    scratch_root: str = Field(
        description="A writable path on the shared filesystem for session state ('{user}' is "
        "templated to the SSH login name).",
        examples=["/anvil/scratch/{user}/.hpc-bridge", "/lustre/orion/scratch/{user}/.hpc-bridge"],
    )
    partition: str = Field(
        description="Default Slurm partition/queue for compute blocks.",
        examples=["shared", "batch", "debug"],
    )
    scheduler: Literal["slurm"] = Field(
        default="slurm", description="Scheduler. Only 'slurm' is supported in this version."
    )
    allocation_command: str | None = Field(
        default=None,
        description="Optional: a login-node command that lists the user's allocations/balances. "
        "Omit if the facility has none — you'll then pass account= directly.",
        examples=["mybalance", "xdusage -p <project>"],
    )
    allocation_parser: Literal["sbank", "iris", "mybalance"] | None = Field(
        default=None,
        description="Which built-in parser reads `allocation_command`'s output. Only 'mybalance' is "
        "implemented today; omit (with allocation_command) if unsure.",
    )
    walltime: str = Field(default="00:30:00", description="Default block walltime (HH:MM:SS).")
    amqp_port: int = Field(default=443, description="AMQPS port (443 is near-universally allowed).")
    endpoint_name: str = Field(default="hpc-bridge", description="On-disk/registration endpoint name.")
    display_name: str | None = Field(
        default=None, description="Optional human label for the facility (defaults to the id)."
    )


class ConnectFacilityResult(BaseModel):
    # needs_account: login node is up; `allocations` are the choices -> ensure_endpoint_up(account=…).
    # provisioning: login node still warming (call again). needs_facility_details: not in the catalog
    # -> supply `details` (ask the user). unsupported/failed: see notice.
    phase: Literal[
        "needs_account",
        "provisioning",
        "needs_facility_details",
        "unsupported",
        "failed",
    ]
    facility: str  # the id/subject that was connected (echoes connect_facility's arg)
    allocations: list[AllocationOption] = []
    notice: str | None = None
