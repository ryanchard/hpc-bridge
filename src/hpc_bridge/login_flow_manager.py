"""The loopback (browser) and paste-back pieces of the in-terminal login — thin wrappers over the
Globus SDK's own flow managers, kept separate from `login.py` so that module stays SDK-import-free
for hermetic tests."""
from __future__ import annotations

import platform
import threading
from contextlib import contextmanager
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

            @contextmanager
            def background_local_server(self):  # type: ignore[override]
                """The SDK's server, but with a QUIET handler: BaseHTTPRequestHandler's default
                log_message() writes the request line — `GET /?code=<one-time auth code>&state=…` — to
                stderr, which in the MCP server flows into logs/transcripts (seen in the L4 live check).
                Nothing about a login may be logged. We also keep the server handle so abort() can
                unblock the SDK's wait at our TTL."""
                from globus_sdk.login_flows.local_server_login_flow_manager.local_server import (
                    RedirectHandler, RedirectHTTPServer,
                )

                class _QuietHandler(RedirectHandler):
                    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler's signature
                        return None

                server = RedirectHTTPServer(
                    server_address=self.server_address,
                    handler_class=_QuietHandler,
                    html_template=self.html_template,
                )
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self._server = server
                try:
                    yield server
                finally:
                    self._server = None
                    server.shutdown()

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


def store_paste_tokens(client, code: str) -> None:
    """Exchange a pasted one-time code and store the tokens where the Compute SDK (and the seeding)
    read them. Prefer the app's VALIDATING storage (it enforces the SDK's unchanging-identity check,
    so a paste login as a different Globus identity is refused like a browser login would be);
    fall back to the raw SQLite storage only if the SDK's private attribute isn't there."""
    from globus_compute_sdk.sdk.auth.token_storage import get_token_storage

    from .login import _default_app_factory

    token_response = client.oauth2_exchange_code_for_tokens(code)
    storage = None
    try:
        storage = getattr(_default_app_factory(None), "_token_storage", None)
    except Exception:  # noqa: BLE001
        storage = None
    if storage is None or not hasattr(storage, "store_token_response"):
        storage = get_token_storage()
    storage.store_token_response(token_response)
