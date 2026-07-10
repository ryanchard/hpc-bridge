# src/hpc_bridge/shapes.py
"""Resource shapes -> user_endpoint_config payloads.

One templatable endpoint serves many resource shapes: the manager renders the UEP
config template per task from the dict we pass as user_endpoint_config. A "shape" is
just a named base of template variables. ``login`` runs on the login node (LocalProvider);
``compute`` requests a scheduler block.
"""
from __future__ import annotations

from typing import Any

SHAPES = ("login", "compute")


def shape_config(shape: str, **overrides: Any) -> dict[str, Any]:
    """Return the user_endpoint_config dict for `shape`, merging caller overrides.

    The keys here MUST match the Jinja variables in the UEP template (see
    SlurmFacility.config_template). Raises ValueError for an unknown shape.

    `compute` is a BOOLEAN discriminator the template branches on (scheduler block vs
    login node). It must be a bool, not a string compared in-template: the endpoint
    manager runs user_opts through `_sanitize_user_json`, which json.dumps's every
    string (so "PBSProProvider" becomes '"PBSProProvider"') — a template
    `{% if provider_type == 'PBSProProvider' %}` then silently drops the provider block.
    Bools pass through the sanitizer unchanged. The `compute` shape does NOT pin a
    provider_type: the per-scheduler template supplies it (SlurmProvider / PBSProProvider)."""
    if shape == "login":
        base: dict[str, Any] = {
            "provider_type": "LocalProvider",
            "max_workers_per_node": 1,
            "compute": False,
        }
    elif shape == "compute":
        base = {"compute": True}
    else:
        raise ValueError(f"unknown shape {shape!r}: expected one of {SHAPES}")
    base.update({k: v for k, v in overrides.items() if v is not None})
    return base
