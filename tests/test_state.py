# tests/test_state.py
from hpc_bridge.state import EndpointRecord, LoginNodeStore


def _rec(**kw):
    base = dict(
        endpoint_id="eid-1", login_host="login03.anvil.rcac.purdue.edu",
        alias="anvil.rcac.purdue.edu", user="x-u", key_path="~/.ssh/id",
        name="hpc-bridge", provisioned_at="2026-06-06T00:00:00Z",
    )
    base.update(kw)
    return EndpointRecord(**base)


def test_save_then_lookup_roundtrips(tmp_path):
    store = LoginNodeStore(tmp_path / "endpoints.json")
    store.put(_rec())
    got = store.get(alias="anvil.rcac.purdue.edu", name="hpc-bridge")
    assert got is not None and got.login_host == "login03.anvil.rcac.purdue.edu"


def test_get_returns_none_when_absent(tmp_path):
    store = LoginNodeStore(tmp_path / "endpoints.json")
    assert store.get(alias="nope", name="hpc-bridge") is None


def test_put_is_idempotent_on_alias_name_key(tmp_path):
    store = LoginNodeStore(tmp_path / "endpoints.json")
    store.put(_rec(login_host="login01.anvil.rcac.purdue.edu"))
    # same alias+name -> overwrite
    store.put(_rec(login_host="login05.anvil.rcac.purdue.edu"))
    assert len(store.all()) == 1
    got = store.get(alias="anvil.rcac.purdue.edu", name="hpc-bridge")
    assert got.login_host.startswith("login05")


def test_remove(tmp_path):
    store = LoginNodeStore(tmp_path / "endpoints.json")
    store.put(_rec())
    store.remove(alias="anvil.rcac.purdue.edu", name="hpc-bridge")
    assert store.get(alias="anvil.rcac.purdue.edu", name="hpc-bridge") is None


def test_file_is_user_only_readable(tmp_path):
    path = tmp_path / "endpoints.json"
    LoginNodeStore(path).put(_rec())
    assert (path.stat().st_mode & 0o077) == 0  # no group/other bits
