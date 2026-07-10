# tests/test_shapes.py
import pytest

from hpc_bridge.shapes import SHAPES, shape_config


def test_known_shapes():
    assert set(SHAPES) == {"login", "compute"}


def test_login_shape_selects_localprovider():
    cfg = shape_config("login")
    assert cfg["provider_type"] == "LocalProvider"
    assert cfg["compute"] is False  # bool discriminator survives the manager's json sanitizer


def test_compute_shape_sets_compute_flag_without_pinning_provider():
    cfg = shape_config("compute", partition="debug", account="ACC", walltime="00:30:00")
    assert cfg["compute"] is True  # gates the scheduler block (not a string compare)
    assert "provider_type" not in cfg  # provider comes from the per-scheduler template default
    assert cfg["partition"] == "debug" and cfg["account"] == "ACC"


def test_unknown_shape_raises():
    with pytest.raises(ValueError, match="unknown shape"):
        shape_config("gpu-quantum")
