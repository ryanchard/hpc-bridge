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


def _required_scopes() -> dict[str, list[str]]:
    """resource_server -> scope strings the endpoint must hold to START.

    The endpoint registers these as hard requirements (compute_endpoint/auth.py
    get_globus_app_with_scopes), so a token missing any of them makes the manager's
    login_required() True — it then tries an interactive login in a detached daemon and
    dies. A plain SDK `Client` login only gets `openid` on auth.globus.org, NOT
    `manage_projects`, so we must verify before shipping (learned live on Anvil)."""
    from globus_compute_sdk.sdk.auth.auth_client import ComputeAuthClient

    out = {
        ComputeAuthClient.scopes.resource_server: [
            str(s) for s in ComputeAuthClient.default_scope_requirements
        ]
    }
    try:
        from globus_sdk import ComputeClientV3

        out[ComputeClientV3.scopes.resource_server] = [
            str(s) for s in ComputeClientV3.default_scope_requirements
        ]
    except Exception:  # noqa: BLE001 - compute scope check is best-effort
        pass
    return out


def _missing_scopes(token_scope: str | None, required: list[str]) -> list[str]:
    have = token_scope or ""
    return [s for s in required if s.split("[", 1)[0].strip() not in have]


class MissingCredentials(RuntimeError):
    """The source storage.db lacks a usable, adequately-scoped token for a required RS."""


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

    # Scope adequacy: a token present but under-scoped (e.g. auth.globus.org with
    # `openid` but no `manage_projects`, as a plain SDK login produces) silently kills
    # the remote manager. Fail here, locally, with a clear remediation instead.
    for rs, required in _required_scopes().items():
        td = kept.get(rs)
        if td is None:
            continue
        missing = _missing_scopes(td.scope, required)
        if missing:
            raise MissingCredentials(
                f"token for {rs!r} is missing required scope(s) {missing} "
                f"(have: {td.scope!r}). Run an endpoint-scoped login "
                f"(`globus-compute-endpoint login`) so it carries openid + "
                f"manage_projects, then retry."
            )

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst = _open(dst_path, namespace)
    try:
        dst.store_token_data_by_resource_server(kept)
    finally:
        dst.close()
    return dst_path
