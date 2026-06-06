# tests/test_credentials.py
from pathlib import Path

import pytest
from globus_sdk.token_storage import SQLiteTokenStorage, TokenStorageData

from hpc_bridge.credentials import (
    REQUIRED_RESOURCE_SERVERS,
    MissingCredentials,
    build_minimal_storage_db,
)

NS = "user/production"


# A scope string that satisfies the endpoint's requirements for BOTH resource servers
# (openid + manage_projects for auth.globus.org, the compute /all scope for funcx).
_OK_SCOPE = (
    "openid urn:globus:auth:scope:auth.globus.org:manage_projects "
    "https://auth.globus.org/scopes/facd7ccc-c5f4-42aa-916b-a0e270e2c2a9/all"
)


def _td(
    rs: str, *, refresh: str | None = "r-tok", scope: str = _OK_SCOPE
) -> TokenStorageData:
    return TokenStorageData(
        resource_server=rs,
        identity_id="id-1",
        scope=scope,
        access_token="a-tok",
        refresh_token=refresh,
        expires_at_seconds=9999999999,
        token_type="Bearer",
    )


def _seed_source(
    path: Path, records: dict[str, TokenStorageData], namespace=NS
) -> None:
    src = SQLiteTokenStorage(
        filepath=path,
        namespace=namespace,
        connect_params={"check_same_thread": False},
    )
    src.store_token_data_by_resource_server(records)
    src.close()


def _read(path: Path, namespace=NS) -> dict[str, TokenStorageData]:
    st = SQLiteTokenStorage(
        filepath=path,
        namespace=namespace,
        connect_params={"check_same_thread": False},
    )
    out = st.get_token_data_by_resource_server()
    st.close()
    return out


def test_required_resource_servers_are_compute_and_auth():
    assert REQUIRED_RESOURCE_SERVERS == frozenset({"funcx_service", "auth.globus.org"})


def test_build_keeps_only_required_resource_servers(tmp_path):
    src = tmp_path / "src.db"
    _seed_source(src, {
        "funcx_service": _td("funcx_service"),
        "auth.globus.org": _td("auth.globus.org"),
        "transfer.api.globus.org": _td("transfer.api.globus.org"),  # must be dropped
    })
    dst = tmp_path / "out.db"
    build_minimal_storage_db(src_path=src, dst_path=dst, namespace=NS)
    kept = _read(dst)
    assert set(kept) == {"funcx_service", "auth.globus.org"}
    assert kept["funcx_service"].access_token == "a-tok"
    assert kept["funcx_service"].refresh_token == "r-tok"


def test_build_raises_when_required_token_missing(tmp_path):
    src = tmp_path / "src.db"
    _seed_source(src, {"funcx_service": _td("funcx_service")})  # auth.globus.org absent
    with pytest.raises(MissingCredentials, match="auth.globus.org"):
        build_minimal_storage_db(
            src_path=src, dst_path=tmp_path / "out.db", namespace=NS
        )


def test_build_raises_when_refresh_token_absent(tmp_path):
    src = tmp_path / "src.db"
    _seed_source(
        src,
        {
            "funcx_service": _td("funcx_service", refresh=None),  # no refresh -> dies
            "auth.globus.org": _td("auth.globus.org"),
        },
    )
    with pytest.raises(MissingCredentials, match="refresh"):
        build_minimal_storage_db(
            src_path=src, dst_path=tmp_path / "out.db", namespace=NS
        )


def test_build_raises_when_auth_token_lacks_manage_projects(tmp_path):
    # Regression (caught live on Anvil): a plain SDK login yields auth.globus.org with
    # only `openid`. The endpoint needs `manage_projects` too, else the remote manager
    # dies on a headless interactive-login attempt. Fail loudly here instead.
    src = tmp_path / "src.db"
    _seed_source(
        src,
        {
            "funcx_service": _td("funcx_service"),
            "auth.globus.org": _td("auth.globus.org", scope="openid"),  # no mgmt scope
        },
    )
    with pytest.raises(MissingCredentials, match="manage_projects"):
        build_minimal_storage_db(
            src_path=src, dst_path=tmp_path / "out.db", namespace=NS
        )


def test_build_creates_dst_parent_dir(tmp_path):
    src = tmp_path / "src.db"
    _seed_source(
        src,
        {
            "funcx_service": _td("funcx_service"),
            "auth.globus.org": _td("auth.globus.org"),
        },
    )
    dst_path = tmp_path / "nested" / "out.db"
    assert not dst_path.parent.exists()
    build_minimal_storage_db(src_path=src, dst_path=dst_path, namespace=NS)
    assert dst_path.exists()
    kept = _read(dst_path)
    assert set(kept) == {"funcx_service", "auth.globus.org"}
