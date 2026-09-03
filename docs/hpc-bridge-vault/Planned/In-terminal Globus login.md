# In-terminal Globus login

> [!abstract] In one line
> Replace the manual prerequisite (`globus-compute-endpoint login` in a terminal, before first use) with a **Cloudflare-shaped OAuth login that the agent surfaces and the browser completes** — a `needs_login` phase carrying an authorize URL; the browser redirects back to a loopback listener inside the server and the session continues; paste-back as the fallback. One consent covers every scope hpc-bridge needs. It rides the **Compute SDK's own `UserApp`** (same client id, same `storage.db`) so endpoint credential seeding keeps working unchanged. Tier-2 item B of [[V1 release]]; the same mechanism later carries a MEP's consent (M2).

## Why

A stranger's first run today: hpc-bridge needs a Globus login carrying `openid` + `manage_projects` + refresh tokens ([[Credential seeding]]); a plain SDK login isn't enough; the MCP server **cannot prompt** (stdio), so `credentials.MissingCredentials` surfaces as a generic `failed` telling the user to go run a CLI command elsewhere and come back. That is the single worst moment of the new-user experience — and it's the exact problem the Cloudflare MCP solved with `authenticate` / `complete_authentication`.

## Verified facts this rests on (2026-09-03)

- **globus-sdk 4.8 ships the flow.** `GlobusAppConfig(login_flow_manager="local-server")` → `LocalServerLoginFlowManager`: opens the browser, runs a loopback HTTP server on an **ephemeral `localhost` port**, receives the auth code on redirect, exchanges it, returns tokens — the terminal continues. `"command-line"` is the paste-back variant (Globus shows a one-time code page). `_check_remote_session()` refuses the local-server flow in SSH/headless sessions — the natural switch to paste-back.
- **The client id is pinned — and must be.** The Compute SDK builds `UserApp(client_id=DEFAULT_CLIENT_ID …)` (`4cf29807-…`); an override requires id **and** secret (a *confidential* client — a different thing). The remote `globus-compute-endpoint` refreshes tokens with **that** client id, so any token the endpoint must refresh has to be issued to it. ⟹ hpc-bridge drives the **same** `UserApp` (same client, same `storage.db` at `GLOBUS_COMPUTE_USER_DIR`), only choosing the login-flow manager. **Registering our own OAuth client (the Cloudflare way) would break seeding — don't.**
- **Localhost redirect for that client:** an unauthenticated fetch of the authorize URL with `redirect_uri=http://localhost:8642/` rendered Globus's normal login page (HTTP 200), identical to the control with the client's known redirect — no up-front rejection. Suggestive, not conclusive (Globus may re-validate at consent). **The definitive check is one real login (~10 s) once built**; if it fails, the code falls back to paste-back automatically.
- **Anonymous registry reads** ([[Facility catalog]]): Search "requires authentication only for non-public entries" — so the *registry* does **not** need login. Login is needed only to **dispatch** (Compute) and to **start an endpoint** (`manage_projects`). Consequence: `list_facilities` never triggers login; `connect_facility` does, lazily, at first need.

## Design

**Phase, not prompt.** `connect_facility` checks, before any SSH, whether the Compute app is logged in with adequate scopes (`app.login_required()` against the required scope set, plus the seeding adequacy check `credentials` already does). If not, it returns **`phase="needs_login"`** with:
- `login_url` — the Globus authorize URL (PKCE, `refresh_tokens=True`, all scopes below);
- `login_mode` — `"browser"` (a loopback listener is waiting; completing the login in the browser finishes it) or `"paste"` (remote/headless: Globus will show a code; hand it to `complete_login`);
- a `notice` telling the agent exactly what to say: show the link, wait for the user, call `connect_facility` again. **The agent never sees or handles a token**; in browser mode it never sees the code either (browser → `localhost` → server).

**Tools.**
- `authenticate()` — explicit trigger: (re)starts the flow and returns the same `{login_url, login_mode}`; for "log me in first" or an expired URL.
- `complete_login(code)` — paste-back only: exchanges a one-time auth code (not a token) for tokens into the Compute `storage.db`. Mirrors Cloudflare's `complete_authentication`.
- `connect_facility` re-called after login proceeds as today (probe / bootstrap / seeding all unchanged).

**One consent, all scopes:** Compute (`…facd7ccc…/all`) · Auth `openid` + `manage_projects` (what a started manager requires — the silent-death case in [[Credential seeding]]) · Search `search` (registry reads if entries ever go `visible_to`-restricted). Requesting them together means **one browser round-trip for the lifetime of the install** (refresh tokens).

**The listener.** Bound to `127.0.0.1` only, ephemeral port, single-use `state`, PKCE, **10-minute lifetime** then it stops and the URL is dead (a fresh `needs_login` re-arms it — idempotent). Runs as a background thread in the server process; completing the login writes tokens via the SDK's own token storage. It exists only between `needs_login` and the next call.

**Fallbacks.** Remote/headless session (the SDK's `_check_remote_session`) → paste mode from the start. Globus rejects the localhost redirect at consent time → the browser shows Globus's error; the agent's next `connect_facility` re-arms in paste mode (we record the failure once). User never completes → the next connect simply returns `needs_login` again.

## What the agent is taught ([[driving-hpc skill|SKILL.md]])
On `needs_login`: present `login_url` as a link and say what will happen ("your browser will log you in to Globus; when it says you can return, tell me"); then call `connect_facility` again. In paste mode: ask the user to paste the code Globus shows and call `complete_login(code)`. **Never** ask for a Globus password; never paste a URL into a shell. Same discipline as `needs_preauth`.

## Not in scope / later
- M2: a facility MEP's consent-required 401 becomes a `needs_login` with the consent URL — same phase, same tools.
- Multiple Globus identities / choosing an identity: the Globus login page handles it.
- Logout / re-login (`force`): `authenticate(force=True)` is a natural extension; not V1.

## Milestones
| L | Deliverable |
|---|---|
| **L1** | login-state detection at connect → `needs_login` + `login_url` (browser mode via the loopback listener); `authenticate()` |
| **L2** | `complete_login(code)` + paste-mode detection/fallback |
| **L3** | SKILL.md + `commands/hpc-connect.md`; models; unit tests (fake flow manager: phases, listener lifecycle, scope set, mode switch); a `first_login` note in the harness (the browser step needs a human — hermetic + one live check) |
| **L4** | **Live check with the maintainer:** fresh `storage.db` → `connect_facility` → `needs_login` → browser → back → connect proceeds. This is the definitive localhost-redirect test. |

## See also
[[V1 release]] · [[Credential seeding]] · [[Facility catalog]] · [[Endpoint reuse and MEP integration]] (M2) · [[MFA and interactive SSH auth]] (the `needs_preauth` precedent) · [[credentials]]
