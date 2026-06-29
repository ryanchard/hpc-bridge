# src/hpc_bridge/catalog/entry.py
from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, field_validator


class Allocation(BaseModel):
    """How to LIST a user's allocations on this machine — not the allocations themselves.

    `parser` names a deterministic, plugin-side parser (Plan 2). The command's stdout is
    parsed in code, never handed to the model — inference is exactly what the catalog removes.
    """

    command: str
    parser: Literal["sbank", "iris", "mybalance"]


class Compute(BaseModel):
    """Machine-invariant facts the plugin PINS — the user/agent cannot override these.

    Getting one wrong breaks the endpoint silently (e.g. the wrong `interface` means workers
    never phone home), so this is the same "look up, never infer" category as the UUIDs.
    """

    scheduler: Literal["slurm", "pbs", "lsf"]
    interface: str  # address_by_interface ifname (e.g. ib0)
    env_setup: str  # bash that puts globus-compute-endpoint on PATH (module + venv)
    scratch_root: str  # session-shell root on the shared filesystem; {user} templated
    endpoint_name: str | None = None  # None ⇒ derive hpc-bridge-<id> (never the bare collision name)
    amqp_port: int = 443  # facilities firewall AMQPS 5671; 443 is the near-universal allowed port
    scheduler_options: str | None = None  # raw scheduler directives, verbatim (e.g. #SBATCH for Slurm, #PBS for PBS)


class Defaults(BaseModel):
    """Per-run tunables the agent/user MAY override at submit time via user_endpoint_config."""

    partition: str
    walltime: str = "00:30:00"
    max_workers_per_node: int = 2
    nodes_per_block: int = 1
    max_blocks: int = 1
    available_accelerators: int | list[str] | None = None


class CatalogSummary(BaseModel):
    """The agent-safe view of an entry — identity only, no executable config or raw UUIDs."""

    subject: str
    id: str
    facility: str
    description: str
    display_name: str
    provenance: Literal["curated", "community", "scraped", "plugin-validated", "session"]
    last_validated: datetime.date


class CatalogEntry(BaseModel):
    """One machine. A superset of MachineProfile; `profile_kwargs()` is the binding seam."""

    # identity
    id: str
    facility_key: str  # short slug for the subject, e.g. "purdue" (distinct from display `facility`)
    facility: str  # display, e.g. "Purdue / ACCESS"
    description: str
    display_name: str

    # identifiers (look up, never infer)
    compute_mep_uuid: str | None = None
    transfer_endpoint_uuid: str | None = None  # Globus Transfer (not wired yet) — compute-only entries omit it

    # access
    ssh_host: str
    auth_method: Literal["ssh-key", "mfa-otp", "sfapi"] = "ssh-key"  # only ssh-key wired in v1

    allocation: Allocation | None = None  # a facility may have no auto-listable allocation tool
    compute: Compute
    defaults: Defaults

    # trust / provenance
    provenance: Literal["curated", "community", "scraped", "plugin-validated", "session"] = "curated"
    last_validated: datetime.date

    @field_validator("compute_mep_uuid", "transfer_endpoint_uuid")
    @classmethod
    def _valid_uuid(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return str(uuid.UUID(v))  # normalize to canonical lowercase hyphenated form

    @property
    def subject(self) -> str:
        return f"{self.facility_key}:{self.id}"

    def summary(self) -> CatalogSummary:
        return CatalogSummary(
            subject=self.subject,
            id=self.id,
            facility=self.facility,
            description=self.description,
            display_name=self.display_name,
            provenance=self.provenance,
            last_validated=self.last_validated,
        )

    def profile_kwargs(self) -> dict[str, Any]:
        """Constructor kwargs for MachineProfile (Plan 2 builds the profile from these).

        `account` is intentionally absent — it is per-user, from allocation selection.
        `worker_init` is absent — in the code it is derived as `= env_setup`.
        `ssh_host` and `compute.scheduler` are also absent — consumed by the transport layer
        (SshTarget) / facility selection, not MachineProfile. `auth_method` is reserved (only
        ssh-key is wired; nothing reads it yet).
        """
        return {
            "name": self.id,
            "endpoint_name": self.compute.endpoint_name,
            "display_name": self.display_name,
            "env_setup": self.compute.env_setup,
            "interface": self.compute.interface,
            "partition": self.defaults.partition,
            "walltime": self.defaults.walltime,
            "max_workers_per_node": self.defaults.max_workers_per_node,
            "nodes_per_block": self.defaults.nodes_per_block,
            "max_blocks": self.defaults.max_blocks,
            "available_accelerators": self.defaults.available_accelerators,
            "amqp_port": self.compute.amqp_port,
            "scheduler_options": self.compute.scheduler_options,
            "scratch_root": self.compute.scratch_root,
        }
