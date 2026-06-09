# src/hpc_bridge/shapes.py
"""Resource shapes -> user_endpoint_config payloads.

One templatable endpoint serves many resource shapes: the manager renders the UEP
config template per task from the dict we pass as user_endpoint_config. A "shape" is
just a named base of template variables. `login` runs on the login node via Parsl's
LocalProvider (no scheduler, no allocation); `slurm` requests a scheduler block.
"""
from __future__ import annotations

from typing import Any

SHAPES = ("login", "slurm")


def shape_config(shape: str, **overrides: Any) -> dict[str, Any]:
    """Return the user_endpoint_config dict for `shape`, merging caller overrides.

    The keys here MUST match the Jinja variables in the UEP template (see
    SlurmFacility.config_template). Raises ValueError for an unknown shape."""
    # `is_slurm` is a BOOLEAN discriminator the template branches on. It must be a bool,
    # not a string compared in-template: the endpoint manager runs user_opts through
    # `_sanitize_user_json`, which json.dumps's every string (so "SlurmProvider" becomes
    # '"SlurmProvider"') — a template `{% if provider_type == 'SlurmProvider' %}` then
    # silently fails and drops the whole provider block. Bools pass through the sanitizer
    # unchanged, so the branch is reliable.
    if shape == "login":
        base: dict[str, Any] = {
            "provider_type": "LocalProvider",
            "max_workers_per_node": 1,
            "is_slurm": False,
        }
    elif shape == "slurm":
        base = {"provider_type": "SlurmProvider", "is_slurm": True}
    else:
        raise ValueError(f"unknown shape {shape!r}: expected one of {SHAPES}")
    base.update({k: v for k, v in overrides.items() if v is not None})
    return base
