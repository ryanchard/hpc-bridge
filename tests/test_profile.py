import pytest

from hpc_bridge.profile import Profile


def test_profile_defaults_to_batch():
    assert Profile().mode == "batch"


def test_profile_rejects_invalid_mode():
    with pytest.raises(ValueError):
        Profile(mode="garbage")  # type: ignore[arg-type]


def test_profile_rejects_nonpositive_idletime():
    with pytest.raises(ValueError):
        Profile(max_idletime_s=0)
