# src/hpc_bridge/credentials.py
"""Build a minimally-scoped Globus Compute storage.db from a user's existing login.

A started endpoint needs tokens for exactly two resource servers: the Globus Compute
service (`funcx_service`) and Globus Auth (`auth.globus.org`, carrying openid +
manage_projects). We copy ONLY those records (with their refresh tokens) out of the
user's ~/.globus_compute/storage.db into a fresh db, so the credential shipped to a
remote login node is the least that lets `globus-compute-endpoint start` run
non-interactively. The resource-server names are resolved from the SDK rather than
hardcoded so they track upstream renames.
"""
from __future__ import annotations

from pathlib import Path

from globus_sdk.token_storage import SQLiteTokenStorage


def _required_resource_servers() -> frozenset[str]:
    from globus_compute_sdk.sdk.auth.auth_client import ComputeAuthClient

    compute_rs = "funcx_service"
    try:
        from globus_sdk import ComputeClientV3

        compute_rs = ComputeClientV3.scopes.resource_server
    except Exception:  # noqa: BLE001 - fall back to the stable literal
        pass
    return frozenset({compute_rs, ComputeAuthClient.scopes.resource_server})


REQUIRED_RESOURCE_SERVERS = _required_resource_servers()


class MissingCredentials(RuntimeError):
    """The source storage.db lacks a usable (refreshable) token for a required RS."""


def _open(path: Path, namespace: str) -> SQLiteTokenStorage:
    return SQLiteTokenStorage(
        filepath=str(path),
        namespace=namespace,
        connect_params={"check_same_thread": False},
    )


def build_minimal_storage_db(*, src_path: Path, dst_path: Path, namespace: str) -> Path:
    """Copy the required resource-server tokens from `src_path` into a fresh `dst_path`.

    Raises MissingCredentials if a required RS is absent or has no refresh token
    (without a refresh token the endpoint stops working when the access token expires).
    Returns dst_path.
    """
    src = _open(src_path, namespace)
    try:
        available = src.get_token_data_by_resource_server()
    finally:
        src.close()

    kept = {}
    for rs in REQUIRED_RESOURCE_SERVERS:
        td = available.get(rs)
        if td is None:
            raise MissingCredentials(
                f"no token for required resource server {rs!r} in {src_path} "
                f"(namespace {namespace!r}); run `globus-compute-endpoint login` first"
            )
        if not td.refresh_token:
            raise MissingCredentials(
                f"token for {rs!r} has no refresh token; re-login to obtain one"
            )
        kept[rs] = td

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst = _open(dst_path, namespace)
    try:
        dst.store_token_data_by_resource_server(kept)
    finally:
        dst.close()
    return dst_path
