# tests/test_shapes.py
import pytest

from hpc_bridge.shapes import SHAPES, shape_config


def test_known_shapes():
    assert set(SHAPES) == {"login", "slurm"}


def test_login_shape_selects_localprovider():
    cfg = shape_config("login")
    assert cfg["provider_type"] == "LocalProvider"
    assert cfg["is_slurm"] is False  # bool discriminator survives the manager's json sanitizer


def test_slurm_shape_selects_slurmprovider_with_overrides():
    cfg = shape_config("slurm", partition="debug", account="ACC", walltime="00:30:00")
    assert cfg["provider_type"] == "SlurmProvider"
    assert cfg["is_slurm"] is True  # gates the slurm provider block (not a string compare)
    assert cfg["partition"] == "debug" and cfg["account"] == "ACC"


def test_unknown_shape_raises():
    with pytest.raises(ValueError, match="unknown shape"):
        shape_config("gpu-quantum")
