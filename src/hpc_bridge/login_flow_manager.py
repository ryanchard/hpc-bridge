"""The loopback (browser) and paste-back pieces of the in-terminal login — thin wrappers over the
Globus SDK's own flow managers, kept separate from `login.py` so that module stays SDK-import-free
for hermetic tests."""
from __future__ import annotations

import platform
from typing import Callable


class CapturingLocalServerManager:
    """Build the SDK's LocalServerLoginFlowManager subclassed to CAPTURE the authorize URL (the SDK
    only opens the browser with it; the agent must also be able to show it) and to be abortable
    (our 10-minute lifetime; the SDK would wait up to an hour)."""

    @staticmethod
    def build(*, on_url: Callable[[str], None]):
        import globus_sdk
        from globus_sdk.login_flows import LocalServerLoginFlowManager
        from globus_compute_sdk.sdk.auth.globus_app import DEFAULT_CLIENT_ID

        class _Manager(LocalServerLoginFlowManager):
            _server = None

            def _get_authorize_url(self, auth_parameters, redirect_uri):  # type: ignore[override]
                url = super()._get_authorize_url(auth_parameters, redirect_uri)
                on_url(url)
                return url

            def background_local_server(self):  # type: ignore[override]
                ctx = super().background_local_server()
                # keep a handle so abort() can unblock wait_for_code()
                outer = self

                class _Ctx:
                    def __enter__(self_inner):
                        outer._server = ctx.__enter__()
                        return outer._server

                    def __exit__(self_inner, *a):
                        outer._server = None
                        return ctx.__exit__(*a)

                return _Ctx()

            def abort(self, why: str) -> None:
                srv = self._server
                if srv is not None:
                    try:  # the SDK's wait_for_code() returns a BaseException from its queue -> flow fails fast
                        srv._auth_code_queue.put(TimeoutError(why))
                    except Exception:  # noqa: BLE001 - private attr moved: the thread just waits out the SDK's own timeout
                        pass

        login_client = globus_sdk.NativeAppAuthClient(DEFAULT_CLIENT_ID)
        return _Manager(
            login_client,
            request_refresh_tokens=True,
            native_prefill_named_grant=f"hpc-bridge on {platform.node()}",
        )


def paste_flow_url() -> tuple[str, object]:
    """Start a native-app auth-code flow whose redirect is Globus's own 'copy this code' page. Returns
    (authorize_url, client) — the client holds the PKCE verifier needed to exchange the code."""
    import globus_sdk
    from globus_compute_sdk.sdk.auth.globus_app import DEFAULT_CLIENT_ID

    from .login import required_scopes

    scopes = [s for rs in required_scopes().values() for s in rs]
    client = globus_sdk.NativeAppAuthClient(DEFAULT_CLIENT_ID)
    client.oauth2_start_flow(requested_scopes=scopes, refresh_tokens=True)
    return client.oauth2_get_authorize_url(), client


def store_paste_tokens(client, code: str, app_factory=None) -> None:
    """Exchange a pasted one-time code and store the tokens in the Compute SDK's token storage
    (same file, same namespace the SDK and the seeding read)."""
    from globus_compute_sdk.sdk.auth.token_storage import get_token_storage

    token_response = client.oauth2_exchange_code_for_tokens(code)
    storage = get_token_storage()
    storage.store_token_response(token_response)
