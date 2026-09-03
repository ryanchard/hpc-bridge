# login_flow_manager.py

> [!abstract] Role
> The SDK-touching half of the in-terminal login: the **loopback (browser) flow manager** and the **paste-back** helpers — thin wrappers over the Globus SDK's own flow managers, kept out of [[login]] so that module stays SDK-import-free for hermetic tests.

## What it does

- **`CapturingLocalServerManager.build(on_url)`** (`login_flow_manager.py:18`) — the SDK's `LocalServerLoginFlowManager`, subclassed to (1) **capture the authorize URL** (`_get_authorize_url` calls `on_url` — the SDK only opens the browser with it, but the agent must be able to show it), (2) run a **quiet** redirect server (`background_local_server` builds the SDK's `RedirectHTTPServer` with a `_QuietHandler` whose `log_message` is a no-op), and (3) be **abortable** (`abort(why)` pushes a `TimeoutError` into the SDK's auth-code queue so *our* 10-minute TTL — not the SDK's hour — ends the wait). Built on a `NativeAppAuthClient(DEFAULT_CLIENT_ID)` with refresh tokens and a `hpc-bridge on <host>` named grant.
- **`paste_flow_url()`** (`:76`) — start a native-app auth-code flow (PKCE, refresh tokens, `required_scopes()`) whose redirect is Globus's own "copy this code" page; returns `(authorize_url, client)` — the client holds the PKCE verifier the exchange needs.
- **`store_paste_tokens(client, code)`** (`:90`) — exchange the pasted one-time code and store the tokens where the Compute SDK (and [[Credential seeding]]) read them — preferring the app's **validating** storage (the SDK's unchanging-identity check, so a paste login as a *different* Globus identity is refused exactly as a browser login would be), falling back to the raw SQLite storage only if the SDK's private attribute isn't there.

> [!warning] Nothing about a login may be logged
> `BaseHTTPRequestHandler`'s default `log_message` writes the request line — `GET /?code=<one-time auth code>&state=…` — to stderr, which in the MCP server flows into logs and transcripts (seen in the L4 live check, [[In-terminal Globus login]]). `_QuietHandler` silences it.

> [!note] Localhost redirects need no registration
> Globus Auth allows native clients an implicit redirect to `localhost:<any port>`; the SDK's own manager relies on it, and the L4 check confirmed Globus honours it for the Compute client id.

## See also
[[login]] · [[server]] · [[credentials]] · [[In-terminal Globus login]]
