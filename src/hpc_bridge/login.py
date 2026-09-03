"""In-terminal Globus login — the Cloudflare-shaped OAuth flow, surfaced as a *phase*, never a prompt.

The MCP server runs over stdio and can never prompt. So when Globus credentials are missing or
under-scoped, `connect_facility` returns `phase="needs_login"` carrying an authorize URL: the agent
shows it, the user's browser completes the login, Globus redirects to a **loopback listener inside
this process**, tokens are stored, and the next `connect_facility` proceeds. Paste-back (a one-time
auth CODE, not a token, handed to `complete_login`) is the fallback for remote/headless sessions.
Design: docs/hpc-bridge-vault/Planned/In-terminal Globus login.md.

Two constraints are load-bearing:
- **Ride the Compute SDK's own client id and token storage.** The remote `globus-compute-endpoint`
  refreshes tokens with the Compute SDK's native client id; a token issued to any other client can't
  be refreshed there, and credential seeding (`credentials.build_minimal_storage_db`) would ship a
  dead credential. So this module builds a `UserApp` with the SAME client id + the SAME
  `storage.db`/namespace the SDK uses — it differs only in the login-flow manager. (Registering a
  separate OAuth client, the literal Cloudflare approach, is exactly what NOT to do here.)
- **One consent covers everything:** the Compute scope, Auth `openid` + `manage_projects` (a started
  manager requires it — the silent-death case in the seeding note), and Search read — with refresh
  tokens, so it is one browser round-trip for the lifetime of the install.

Globus Auth allows native clients an implicit redirect to `localhost:<any-port>` (the SDK's own
LocalServerLoginFlowManager relies on it), so nothing needs registering.
"""
from __future__ import annotations

import os
import platform
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

LoginMode = Literal["browser", "paste"]
FLOW_TTL_S = 600.0  # a login URL / listener lives this long, then a fresh needs_login re-arms one

SEARCH_SCOPE = "urn:globus:auth:scope:search.api.globus.org:search"


def required_scopes() -> dict[str, list[str]]:
    """resource_server -> scopes the whole product needs, in ONE consent: what a started endpoint
    requires (Compute + Auth openid/manage_projects — from `credentials`, resolved from the SDK so it
    tracks upstream) plus Search read (registry entries that are not public)."""
    from .credentials import _required_scopes

    out = {rs: list(scopes) for rs, scopes in _required_scopes().items()}
    out.setdefault("search.api.globus.org", [])
    if SEARCH_SCOPE not in out["search.api.globus.org"]:
        out["search.api.globus.org"].append(SEARCH_SCOPE)
    return out


def _remote_session() -> bool:
    """A browser on THIS machine can't reach a loopback listener in a remote/headless session."""
    return bool(os.environ.get("SSH_TTY") or os.environ.get("SSH_CONNECTION"))


@dataclass
class LoginStart:
    """What the agent gets: a URL to show the user, and how the login will complete."""

    login_url: str
    mode: LoginMode
    expires_at: float  # time.monotonic()


@dataclass
class LoginFlow:
    """The one in-flight login (per server process). `start()` is idempotent while a flow is live.

    `app_factory(manager)` builds the Globus app to log in with — the real one rides the Compute SDK
    (see `_default_app_factory`); tests inject a fake. `mode_override` forces browser/paste."""

    app_factory: Callable | None = None
    mode_override: LoginMode | None = None
    ttl_s: float = FLOW_TTL_S
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _start: LoginStart | None = None
    _thread: threading.Thread | None = None
    _manager: object | None = None
    _state: Literal["idle", "waiting", "done", "failed", "expired"] = "idle"
    error: str | None = None

    # ---- state ----------------------------------------------------------------------------------

    def login_required(self) -> bool:
        """Non-prompting: does the stored credential satisfy every required scope? Errors (no
        storage.db, unreadable db) count as 'required' — the safe default for a first run."""
        try:
            app = (self.app_factory or _default_app_factory)(None)
            return bool(app.login_required())
        except Exception:  # noqa: BLE001 - treat an unreadable credential as absent
            return True

    def status(self) -> str:
        with self._lock:
            self._expire_locked()
            return self._state

    def _expire_locked(self) -> None:
        if self._state == "waiting" and self._start and time.monotonic() > self._start.expires_at:
            self._state = "expired"
            self._stop_listener_locked("login URL expired")

    # ---- the flow -------------------------------------------------------------------------------

    def start(self, mode: LoginMode | None = None) -> LoginStart:
        """(Re)arm a login. Idempotent while one is waiting: the same URL comes back. Browser mode
        runs the SDK's loopback flow in a background thread (it opens the browser itself; the URL
        is ALSO returned so the agent can show it if that failed). Paste mode builds the auth-code
        URL for `complete_login`."""
        with self._lock:
            self._expire_locked()
            if self._state == "waiting" and self._start is not None:
                return self._start
            mode = mode or self.mode_override or ("paste" if _remote_session() else "browser")
            self.error = None
            if mode == "browser":
                return self._start_browser_locked()
            return self._start_paste_locked()

    def _start_browser_locked(self) -> LoginStart:
        from .login_flow_manager import CapturingLocalServerManager

        url_ready = threading.Event()
        captured: dict[str, str] = {}

        def on_url(url: str) -> None:
            captured["url"] = url
            url_ready.set()

        manager = CapturingLocalServerManager.build(on_url=on_url)
        app = (self.app_factory or _default_app_factory)(manager)

        def run() -> None:
            # NB: plain attribute writes only — start() holds self._lock while it waits for the URL,
            # so taking the lock here would deadlock the fallback path (found by the unit test).
            try:
                app.login(force=True)  # blocks until the loopback receives the code (or fails)
                if self._state == "waiting":
                    self._state = "done"
            except Exception as exc:  # noqa: BLE001 - browser missing / remote / Globus error
                if self._state == "waiting":
                    self._state = "failed"
                    self.error = f"{type(exc).__name__}: {exc}"[:300]
            finally:
                url_ready.set()  # never leave start() hanging

        self._manager = manager
        self._state = "waiting"
        self._thread = threading.Thread(target=run, name="hpc-bridge-login", daemon=True)
        self._thread.start()
        url_ready.wait(timeout=15)
        url = captured.get("url")
        if not url:  # the flow died before producing a URL (e.g. no browser): fall back to paste
            self._state = "idle"
            return self._start_paste_locked(reason=self.error)
        self._start = LoginStart(login_url=url, mode="browser", expires_at=time.monotonic() + self.ttl_s)
        return self._start

    def _start_paste_locked(self, reason: str | None = None) -> LoginStart:
        from .login_flow_manager import paste_flow_url

        url, self._paste_client = paste_flow_url()
        self._state = "waiting"
        self._start = LoginStart(login_url=url, mode="paste", expires_at=time.monotonic() + self.ttl_s)
        if reason:
            self.error = f"browser flow unavailable ({reason}); using paste-back"
        return self._start

    def complete_with_code(self, code: str) -> None:
        """Paste-back: exchange the one-time auth code and store the tokens where the Compute SDK
        (and the seeding) read them. Raises on a bad/expired code."""
        from .login_flow_manager import store_paste_tokens

        with self._lock:
            client = getattr(self, "_paste_client", None)
            if client is None or self._state != "waiting" or (self._start and self._start.mode != "paste"):
                raise RuntimeError("no paste-back login is waiting — call authenticate() first")
        store_paste_tokens(client, code.strip(), self.app_factory)
        with self._lock:
            self._state = "done"

    def _stop_listener_locked(self, why: str) -> None:
        m = self._manager
        if m is not None and hasattr(m, "abort"):
            try:
                m.abort(why)
            except Exception:  # noqa: BLE001 - best-effort
                pass


def _default_app_factory(manager):
    """The REAL app: the Compute SDK's client id + token storage (so the endpoint can refresh what
    we obtain), our scope set, and — when given — our capturing loopback manager. With
    manager=None it is a non-prompting instance for `login_required()` only."""
    from globus_sdk import GlobusAppConfig, UserApp
    from globus_compute_sdk.sdk.auth.globus_app import DEFAULT_CLIENT_ID
    from globus_compute_sdk.sdk.auth.token_storage import get_token_storage

    config = GlobusAppConfig(
        token_storage=get_token_storage(),
        request_refresh_tokens=True,
        **({"login_flow_manager": manager} if manager is not None else {}),
    )
    app = UserApp(app_name=f"hpc-bridge on {platform.node()}", client_id=DEFAULT_CLIENT_ID, config=config)
    app.add_scope_requirements(required_scopes())
    return app
