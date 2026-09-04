# Security review 2026-09-04 — raw report B

> [!note] Verbatim report of the review agent (surface B); the consolidated record and decisions are in [[Security review 2026-09-04]]. Line numbers refer to the pre-fix code.

# hpc-bridge security review — Surface B: credentials, tokens, what leaves the machine / lands in logs

Repo: `/Users/gusellerm/Projects/hpc-bridge` @ `feat/mep-m1` (b311b90). globus-sdk 4.9.0, globus-compute-sdk 4.16.0 (the installed versions the code runs against).
Method: code-path tracing plus hermetic checks in `scratchpad/secB/` (`probe_loopback.py`; a scratch `GLOBUS_COMPUTE_USER_DIR`). Nothing touched Globus, a cluster, or `~/.globus_compute`.

## Findings table

| id | severity | verdict | where | claim |
|---|---|---|---|---|
| B-01 | low | CONFIRMED | `src/hpc_bridge/login_flow_manager.py:103` | Paste-mode login stores tokens through the **raw** `SQLiteTokenStorage` (`app._token_storage`), skipping the SDK's `UnchangingIdentityID`/`ScopeRequirements`/`HasRefreshTokens` validators the docs say it uses. |
| B-02 | medium | PLAUSIBLE | `login.py:37,150-219`; SDK `local_server.py:43-67` | Loopback login has no `state`/origin binding: anyone holding the authorize URL (tool result → transcript; browser argv) during the ≤10-min window can complete it as *their* identity into an empty token store; nothing tells the user which identity landed. |
| B-03 | medium | CONFIRMED | `server.py:595`, `facility/remote.py:824-835`, `docs/user/login.md`, `docs/user/install.md:66-71` | The refresh tokens seeded to each login node are never wiped by any tool path and never revoked (no logout); user docs say deleting the local store "logs you out entirely" and never mention the remote copy. |
| B-04 | low | CONFIRMED | `config.py:96-101,89-90` | The `/tmp/hpcb-cm-<uid>` control-dir squat guard uses `os.stat` (follows symlinks); a planted symlink passes and the victim's process `chmod 700`s the link target and puts its SSH master socket there. Narrow precondition. |
| B-05 | low | CONFIRMED | `login.py:198-205`, SDK `local_server.py:58-67` | Any unauthenticated local GET to the loopback (no `code`) fails the flow and sets sticky `_browser_failed` → the process is demoted to paste mode for its lifetime. Injected `error_description` text does **not** reach the agent (cleared on re-arm). |
| B-06 | medium | PLAUSIBLE | `connect.py:126`, `binding.py:109-120`, `remote.py:82,758-765` | A registry entry is trusted as the *destination of the bearer credential*: its `ssh_host` gets the user's SSH first contact (`accept-new`) and, when remote `whoami` fails (host-controlled), the trimmed refresh tokens; the registry path never shows or confirms the host. Threat 5. |
| B-07 | info | NOT AN ISSUE | `server.py:391-395`, `login.py:232-244` | The one-time auth code transits `complete_login(code)` → transcript. PKCE-bound to this process, single-use, minutes TTL; documented honestly. |
| B-08 | info | NOT AN ISSUE | `remote.py:102-113`, `notices.py:174-199` | `preauth_command` relayed to the agent carries host, user, key *path*, ControlPath — no secret. |
| B-09 | info | NOT AN ISSUE | SDK `local_server_login_flow_manager.py:99,169`; probe | Listener binds `127.0.0.1:<ephemeral>` only (refused on the LAN address). |
| B-10 | info | NOT AN ISSUE | `login_flow_manager.py:43-45`; `tests/test_login.py:281` | Request line with the auth code is no longer logged; no other stderr/notice path carries a token. |
| B-11 | info | NOT AN ISSUE (accepted) | `credentials.py:91-124`, `remote.py:759-765` | Trimmed db = 2 resource servers with refresh tokens; built in a 0700 temp dir as a 0600 file, shipped over SSH stdin (not argv), temp copy removed. |
| B-12 | info | NOT AN ISSUE | `remote.py:385-404` | Remote `storage.db` is written before its `chmod 600`, but inside a directory already `chmod 700` — the window is unreadable. Hardening only. |
| B-13 | info | NOT AN ISSUE | `state.py:56-59,103-106`, `config.py:89-90` | Local state files 0600 (pinned by test), control dir 0700, contents are config not secrets. |
| B-14 | info | NOT AN ISSUE | `.mcp.json`, `config.py`, SDK `client_login.py` | No secret is expected in env. Informational: inherited `GLOBUS_COMPUTE_CLIENT_ID/SECRET` would switch the token namespace and seed *that* identity's tokens. |
| B-15 | info | NOT AN ISSUE | `skills/driving-hpc/SKILL.md:39,41`, `commands/hpc-connect.md` | Skill forbids handling passwords/passcodes/Globus passwords and pasting links into shells; consistent with `notices.py`. |
| B-16 | info | NOT AN ISSUE | `session_shell.py:63-66`, `discovery.py:158` | Session `.env` persisted 0700/0600 on the shared FS; the `/tmp` scratch default needs `$HOME` unset remotely and is user-confirmed. |
| B-17 | info | informational | `login_flow_manager.py:73`, `login.py:308` | Workstation hostname is embedded in the authorize URL (`prefill_named_grant`) and the Globus consent name. |

Counts: critical 0 · high 0 · medium 3 (B-02, B-03, B-06) · low 3 (B-01, B-04, B-05) · informational 11.

---

## B-01 — Paste-mode tokens bypass the SDK's validating storage (CONFIRMED, low)

**Scenario.** `complete_login(code)` → `LoginFlow.complete_with_code` → `store_paste_tokens`. The function intends to store through the app's validating storage so that "a paste login as a different Globus identity is refused like a browser login would be" (docstring; vault `Modules/login_flow_manager.md:10`, `Planned/In-terminal Globus login.md:36`). It doesn't.

**Evidence.**
- `login_flow_manager.py:103`: `storage = getattr(_default_app_factory(None), "_token_storage", None)`; `:106` accepts it if it has `store_token_response`.
- In globus-sdk 4.9 `GlobusApp._token_storage` is the **inner raw** storage (`globus_app/app.py:83,114`); the validating wrapper is the public `token_storage` (`app.py:130`; `user_app.py:158` appends `UnchangingIdentityIDValidator`).
- Hermetic run (scratch `GLOBUS_COMPUTE_USER_DIR`): `type(app._token_storage)` = `SQLiteTokenStorage`, `hasattr(..., "store_token_response")` = True; `type(app.token_storage)` = `ValidatingTokenStorage` with validators `[HasRefreshTokensValidator, ScopeRequirementsValidator, UnchangingIdentityIDValidator]`. So the raw path is taken every time, not "only if the SDK's private attribute isn't there".
- Browser path, by contrast, stores via `RefreshTokenAuthorizerFactory.store_token_response_and_clear_cache` → the validating storage (`user_app.py:188`, `authorizer_factory.py:43-49`).

**Impact.** A paste login that lands as a different Globus identity (the user picks the wrong account on Globus's page; or, contrived, is talked into pasting a code someone else obtained from *this process's* paste URL) silently replaces the stored identity; scope/refresh-token adequacy isn't checked at store time either. Security exposure is bounded by PKCE (the code must match the verifier held in `self._paste_client`), so this is mostly a broken safeguard and a false doc claim rather than an exploit.

**Fix.** `storage = getattr(app, "token_storage", None)` (the public validating attribute); keep the raw fallback as written. Update the two vault sentences.

**Test.** Unit: a fake app exposing both `_token_storage` and `token_storage`; assert `store_token_response` is called on `token_storage`. Optional: real SDK, scratch store pre-seeded with identity A (`SQLiteTokenStorage.store_token_data_by_resource_server`), then `store_paste_tokens` with a fake client returning a token response for identity B → expect `IdentityMismatchError`.

---

## B-02 — Loopback login: no state/origin binding; the URL holder becomes the identity (PLAUSIBLE, medium)

**Scenario.** First run on a fresh install (empty `~/.globus_compute/storage.db`). `connect_facility` arms the browser flow: the SDK starts a loopback HTTP server, builds the authorize URL (PKCE `code_challenge`, `redirect_uri=http://127.0.0.1:<port>`, `state=_default`), opens the browser, and the tool returns `needs_login` with `login_url` if the user is slower than 90 s — leaving the listener armed for the rest of the 10-min TTL (`FLOW_TTL_S`). A co-local attacker who obtains that URL within the window opens it in *their* browser, logs in as *their* Globus identity; Globus redirects their browser to `127.0.0.1:<port>/?code=…` — the same host, so the victim's listener receives a code that is valid for the victim's PKCE verifier (the login was a continuation of the victim's own authorize request). The exchange succeeds, the attacker's tokens are stored, and the flow reports `done`.

**URL disclosure channels.** (threat 3) `ConnectFacilityResult.login_url` and the notice text (`notices.py:153-157`) → the plaintext transcript; (threat 1) on Linux the SDK's `_open_webbrowser` passes the URL as argv to `xdg-open`/the browser (`/proc/*/cmdline` is world-readable by default). On macOS `open` does not leak argv and home dirs are 750, so the practical exposure is Linux multi-user desktops.

**Evidence.**
- SDK `RedirectHandler.do_GET` (`local_server.py:43-67`) reads only `code`/`error*`; `state` is a constant (`native_app.py:107 state="_default"`) and never compared. Probe: `GET /?code=FIRST&state=bogus` accepted; first item wins; second GET ignored.
- The guard that stops this later is `UnchangingIdentityIDValidator.before_store` (`validators.py:133-156`): it returns early when `prior_identity_id is None` (`:148`) — i.e. it does **not** protect the first login, which is exactly the new-user path (and `authenticate(force=True)` after the store was deleted).
- Nothing reports the identity that landed: `_authenticate` returns "Globus login completed in the browser; carry on." (`login_gate.py:44`); `globus_identity_label()` exists (`login.py:264`) but is only used in the NO-ACCOUNT notice.

**Consequences if planted.** Every later call runs under the attacker's identity: `find_online_endpoint` lists endpoints the *attacker* owns (`remote.py:866`) and will reuse an attacker-registered endpoint named `hpc-bridge-<host>` — the victim's commands and their outputs then execute on the attacker's machine with zero SSH; on a real SSH bootstrap the attacker's tokens are seeded to the victim's login node (`remote.py:760-765`) and the personal endpoint started there is registered to — and dispatchable by — the attacker, i.e. command execution as the victim's HPC account. Preconditions (first login, co-local attacker, URL capture, winning the race against the user's own click) make this narrow; the blast radius is why it is rated medium rather than low. The paste-mode URL has the same property but requires the victim to paste a code the attacker obtained — contrived.

**Fix (minimal).** (1) After any login lands (`_authenticate`, the connect gate, `_complete_login`), resolve `globus_identity_label()` and put "logged in to Globus as `<username>`" in the `logged_in`/connect notice, so a foreign identity is visible immediately (the label is a courtesy call already written; costs one Auth API round-trip). (2) Add the caveat to `docs/user/login.md`: the link is not a secret but whoever completes it first becomes the logged-in identity — open it yourself. (3) Consider a shorter armed window once the 90-s wait has returned `needs_login` (e.g. 3 min) — the TTL is the attack window. A random `state` verified in `_QuietHandler` does not help against a URL holder (state is in the URL) but is cheap hygiene against blind injection and is part of the B-05 fix.

**Test.** Unit: `_authenticate`/`_complete_login` notices contain the identity label (monkeypatch `globus_identity_label`). Hermetic listener test already exists (`test_login.py:281`); extend it to assert a GET without our `state` is not accepted once B-05 is implemented.

---

## B-03 — Remote credential never wiped or revoked; docs misstate logout (CONFIRMED, medium)

**Scenario.** On the first SSH bootstrap the user's `funcx_service` + `auth.globus.org` records — access **and refresh** tokens, copied verbatim (`credentials.py:91-103,124`) — are written to `<login node>:~/.globus_compute/storage.db` (0600 in a 0700 dir; deliberate, vault `Concepts/Credential seeding.md`). That copy outlives every session and every tool.

**Evidence.**
- `RemoteEndpointCLI.wipe_storage_db` exists (`remote.py:464`) and `SlurmFacility.teardown(endpoint_id, *, wipe_credentials=False)` can call it (`:824-834`), but the only caller is `server._teardown_endpoint`: `await teardown(eid)` (`server.py:595`) — the flag is never passed. `grep wipe_credentials` hits only `remote.py` and `tests/test_remote_facility.py:943`. No MCP tool, CLI flag, or doc reaches it.
- No logout: no tool calls `GlobusApp.logout()` (SDK `app.py:389-417`, which revokes access and refresh tokens). Because the remote copy holds the *same* refresh-token strings, revocation is the only action that would invalidate it; deleting the local file does nothing to it.
- Docs: `docs/user/login.md` "Deleting the token store logs you out entirely."; `install.md:66-71` lists only the local store and says deleting it "logs you out of Globus"; `facilities.md:11` / `install.md` say the first connect "installs Globus Compute into your home directory there" — nowhere that a copy of the Globus credential is placed there and persists after `teardown_endpoint`. README's "The hot path carries a scoped Globus token, never SSH material" is accurate but silent on the at-rest copy. The vault (Credential seeding, Standing up the endpoint) states the design honestly; the user-facing pages do not.

**Impact.** A user who leaves a facility, suspects a compromised home directory/NFS, or simply "logs out" by deleting the local store still has live, refreshable Compute + Auth(`openid`,`manage_projects`) tokens on every login node they ever bootstrapped (Globus refresh tokens live until revoked or ~6 months idle). Root/admins on those nodes could read them — an accepted risk *if stated*.

**Fix.** (1) `teardown_endpoint(wipe_credentials: bool = True)` → pass it through to `facility.teardown`; (2) a `logout` tool (or `authenticate(logout=True)`) that runs `app.logout()` (revoke + remove local) and optionally `wipe_storage_db` on the bound facility; (3) doc lines in `login.md` ("Where the login lives") and `install.md`'s "Where things live": "…and, on each SSH facility you have bootstrapped, a copy of the same tokens at `~/.globus_compute/storage.db` (mode 600) so the endpoint can run unattended; `teardown_endpoint` removes it / to revoke everywhere use logout or remove hpc-bridge's consent at app.globus.org".

**Test.** `tests/test_server.py`: `_teardown_endpoint` calls `teardown(eid, wipe_credentials=True)` (FakeFacility records kwargs). Docs review.

---

## B-04 — `/tmp` control-dir fallback guard follows symlinks (CONFIRMED mechanism, low)

**Scenario.** When the ControlPath budget forces the fallback (`config.py:94-101`; reached only when both `HPC_BRIDGE_STATE_DIR/cm` and `~/.hpc-bridge/cm` exceed 59 bytes — i.e. a home path longer than 44 chars), the candidate `/tmp/hpcb-cm-<uid>` is accepted if it doesn't exist or `os.stat(cand).st_uid == getuid()`. Another local user (threat 1) pre-plants `/tmp/hpcb-cm-<uid>` as a **symlink** to any directory the victim owns (e.g. `~/public_html`). `os.stat` follows the link → owner is the victim → accepted. `_control_settings` then runs `os.makedirs(cd, mode=0o700, exist_ok=True)` (no-op, exists) and `os.chmod(cd, 0o700)` — which follows the link and chmods the victim's directory; ssh then creates the master socket inside it.

**Evidence.** Scratch demo (`secB/`): a symlink to a 0755 dir passed the `os.stat` uid check; `os.makedirs(..., exist_ok=True)` succeeded; `os.chmod(link, 0o700)` changed the *target* to 0700. Budget arithmetic printed: `_CONTROL_PATH_BUDGET=59`, `~/.hpc-bridge/cm` here is 31 → the fallback is unreachable on this machine; reachable for long home paths.

**Impact.** Bounded: OpenSSH creates the mux socket 0600 (umask 0177 around bind), so the master isn't usable by the attacker even in a 0755 dir; the effect is an unexpected `chmod 700` of a victim-chosen-by-attacker directory and a socket landing outside hpc-bridge's state. Low, but it is a symlink TOCTOU in exactly the `/tmp` fallback the review asked about.

**Fix.** Use `os.lstat` and reject `stat.S_ISLNK`; create with `os.mkdir(cand, 0o700)` and on `FileExistsError` re-check `lstat` (uid == ours, not a symlink, mode & 0o077 == 0) before use; prefer `$XDG_RUNTIME_DIR` (per-user 0700 tmpfs) ahead of `/tmp` when present. In `_control_settings`, skip the chmod when `lstat` shows a link.

**Test.** Unit: monkeypatch `Path.home()`/`HPC_BRIDGE_STATE_DIR` to long paths and the `/tmp` candidate to a tmp symlink (parametrise the candidate list) → assert it is skipped.

---

## B-05 — Any local GET kills the browser flow and demotes the process to paste mode (CONFIRMED, low)

**Scenario.** While a browser flow is armed (up to 10 min), a local process (threat 1) or a page that can navigate to `http://127.0.0.1:<port>/…` (threat 4; needs the port, findable via `lsof`/`ss` locally) issues `GET /?error_description=anything` or any GET without `code` (a port scanner, even a browser's own `/favicon.ico` if it races the redirect).

**Evidence.** Probe: the GET returned the SDK's "Login failed" page and queued `LocalServerLoginError('INJECTED TEXT FOR THE AGENT')`; `run_login_flow` raises on it (`local_server_login_flow_manager.py:179-181`); the `LoginFlow` worker then sets `_browser_failed = True`, `_state="failed"`, `error=<text>` (`login.py:198-205`). `_browser_failed` is never reset, so every later `start()` picks paste (`login.py:165-167`). Reproduced through the real state machine with a fake app: after the injected failure `status=failed`, and the re-arm returned `mode=paste`, `error=None`, `browser_failed=True`. Because `start()` sets `self.error = None` (`login.py:168`) before re-arming, the attacker's text never reaches `_login_notice` — the prompt-injection channel is closed by that reset, not by design; keep it.

**Impact.** Availability only: the user is pushed to paste mode for the life of the MCP process (and a legitimate transient failure does the same). No credential effect.

**Fix.** Subclass `_QuietHandler.do_GET`: honour a per-flow random `state` (pass it via `oauth2_start_flow(..., state=<token>)` in `_get_authorize_url`), respond 404 and *do not touch the queue* for any request whose `state` doesn't match; reset `_browser_failed` on `authenticate(force=True)`/after a TTL so one failure isn't permanent.

**Test.** Hermetic: GET without/with wrong `state` → `server._auth_code_queue.empty()`; GET with the right `state` and `code` → queued once.

---

## B-06 — Registry entry trusted as the credential's destination; no host binding on the SSH path (PLAUSIBLE, medium; threat 5)

**Scenario.** The public registry (Globus Search index `6ff95fb8-…`, curator-written via `hpc-bridge-catalog`) or a rogue `HPC_BRIDGE_SEARCH_INDEX` serves an entry for a known id (say `anvil`) whose `ssh_host` points at an attacker host. `connect_facility("anvil")` takes the registry over the local cache (`connect.py:121-126`), builds the SSH target from `entry.ssh_host` with the user's default identities (`binding.py:109-120`, no `-i` → all keys/agent), connects with `StrictHostKeyChecking=accept-new` (`remote.py:82`, TOFU), runs `globus-compute-endpoint whoami` — the attacker's host returns non-zero — and `bootstrap` ships the trimmed refresh tokens to it (`remote.py:758-765`). The connect result never named the host before that first contact; only the BYO path proposes-and-confirms.

Secondary: a poisoned `scratch_root` (world-writable path) makes every session source `<scratch_root>/sessions/<id>/.env` (`session_shell.py:82`) that a hostile login-node user (threat 2) can plant → code execution as the victim on the real facility; `env_setup` is already acknowledged as an injection vector (`docs/adding-a-facility.md:4-5`).

**Evidence.** As cited. `docs/adding-a-facility.md` states the injection risk and that entries arrive by review; `docs/user/facilities.md` tells the user what a facility needs from them but not that connecting to a registry facility trusts that entry with an SSH first contact and with their Globus credential. Registry write access is Globus-ACL-gated, so likelihood is low; the blast radius (token exfiltration + arbitrary remote host) is total, hence medium.

**Fix (minimal).** (1) On the registry path, before the first SSH to a host that has no pin in `endpoints.json` and no `known_hosts` entry, return a `confirm_host`-style notice naming `user@ssh_host` (or fold it into the existing `needs_account`/`provisioning` notice for the *next* call, as BYO's proposed-details step does). (2) Let entries carry SSH host-key fingerprints and pass `-o StrictHostKeyChecking=yes` with a generated `known_hosts` line; a poisoned entry still poisons, but DNS/MITM substitution is caught and the fingerprint becomes reviewable in the PR. (3) One sentence in `facilities.md`: connecting to a registry facility trusts its curated entry — the login host it names receives your SSH login and a copy of your Globus credential.

**Test.** Unit: first-contact registry entry → notice contains the host; entry with fingerprint → argv contains `StrictHostKeyChecking=yes` and the known_hosts path.

---

## B-07 … B-17 — not issues / informational (reasons)

- **B-07 auth code in transcript.** `paste_flow_url` keeps the PKCE verifier in the `NativeAppAuthClient` held by the flow (`login_flow_manager.py:86-88`, `login.py:225`); a transcript reader cannot exchange the code; single-use; Globus codes expire in minutes; `complete_with_code` refuses after the 10-min TTL (`login.py:240`). `docs/user/login.md` says exactly this. Hygiene: reject inputs that don't look like a Globus auth code so a mis-pasted *token* is not accepted into the transcript flow.
- **B-08 preauth command.** `preauth_command()` emits `ssh -fN -o ControlMaster=yes -o ControlPath=<0700 dir>/%C -o ControlPersist=1h [-i <path>] [user@]host`; pinned no-BatchMode by `test_remote_facility.py:475`. Key *path* only. The 1-h persisted master lives in a 0700 dir with a 0600 socket.
- **B-09 bind scope.** `RedirectHTTPServer` is an `HTTPServer` (AF_INET) bound to `("localhost", 0)`; probe: `getsockname()` = `127.0.0.1:<port>`, connection refused on the LAN address. `redirect_uri` is built from `getsockname()`. Vault statement holds; `test_login.py:294` pins the host loosely.
- **B-10 logging.** `_QuietHandler.log_message` is a no-op (`log_error` routes through it); `RedirectHTTPServer.handle_error` queues rather than prints; pinned by `test_loopback_handler_never_logs_the_request_line`. Other stderr prints (`login.py:122`, `remote.py:868`, `server.py:153,369`, `config.py:110,116`) carry exception text / env values. `MissingCredentials` messages include the scope string and local path only. `_error_outcome`/`_explain_provision_error` truncate exception text; no path formats a token. The MCP server does not enable DEBUG logging, so globus-sdk/urllib3 request logging is off.
- **B-11 trimmed db.** Only `funcx_service` + `auth.globus.org` records (pinned by `test_build_keeps_only_required_resource_servers`), refresh tokens required (design); built under `tempfile.TemporaryDirectory()` (mkdtemp 0700) with the SDK's `user_only_umask` → file 0600 (verified in scratch); removed with the directory after `seed_storage_db` returns; payload rides SSH **stdin** as base64 (`remote.py:383,394`), never argv, so not visible in `ps`. Not a secure erase — accepted.
- **B-12 remote write ordering.** `mkdir -p && chmod 700 dir` → `base64 -d > file` → `chmod 600 file` (order pinned by `test_seed_storage_db_streams_b64_and_locks_permissions`). The file's transient umask mode is inside a 0700 directory, hence unreadable by others (threat 2). Hardening: `umask 077; base64 -d > …` in the same round trip (also one SSH fewer).
- **B-13 local state.** `endpoints.json`/`facilities.json` created `O_CREAT|0o600` and re-chmod'ed (`state.py:56-59,103-106`; `test_state.py:47` pins no group/other bits). Contents: host, user, key *path*, env_setup, scratch — config, no secrets (as the docstring says). Control dir `makedirs(0o700)` + `chmod 0o700` (`config.py:89-90`). Catalog cache (`CLAUDE_PLUGIN_DATA/catalog-cache/*.json`, `search.py:31-37,76`) is public registry data in a user-owned directory; not attacker-writable under threat 1.
- **B-14 env passthrough.** `.mcp.json` adds only `HPC_BRIDGE_USER_DIR`; the stdio server inherits the launching shell's environment. `config.py` reads no secret. Informational: the Compute SDK reads `GLOBUS_COMPUTE_CLIENT_ID`/`GLOBUS_COMPUTE_CLIENT_SECRET` (`client_login.py`); if a user's shell exports a service identity, `_resolve_namespace()` becomes `clientprofile/<env>/<id>` (`token_storage.py:24-28`), `Client()` becomes a `ClientApp`, and `bootstrap` seeds that identity's tokens (`remote.py:763`) while `login.py` still logs the default client id into that namespace — a mixed state worth refusing at startup with a clear message. Also `HPC_BRIDGE_USER_DIR` does not move the SDK's token store (HANDOFF:144) — honest in the vault, not in user docs (minor).
- **B-15 skill guidance.** `SKILL.md:39` (never ask for a Globus password; never paste the link into a shell), `:41` (never ask for, type, or run a command containing a password or passcode; relay only the Duo push choice), `hpc-connect.md` defers to it; `docs/user/troubleshooting.md:73-76` tells users to refuse. Consistent with `notices.py:145-146,193-195`. No instruction contradicts the invariant.
- **B-16 session `.env`.** `umask 077` before `mkdir -p` (`session_shell.py:63-64`) → 0700 dirs, 0600 files; exported secrets persist only user-readable. `discovery.py:158`'s `/tmp` default requires `$HOME` to be empty on the login node and is surfaced as `proposed_facility_details` for confirmation.
- **B-17 hostname.** `platform.node()` is embedded in the authorize URL (`prefill_named_grant`) → transcript, and becomes the consent's display name at Globus. Harmless; mention if hostnames are considered sensitive.

## Accepted risks and whether the docs say so

| risk | accepted where | user docs honest? |
|---|---|---|
| Refresh tokens at rest on each login node (0600/0700), persisting after teardown | vault `Concepts/Credential seeding.md` | **No** — `login.md`/`install.md` describe only the local store and claim deleting it logs you out (B-03). |
| One-time auth code passes through the chat in paste mode | design | Yes (`login.md` "Paste mode"). |
| Login URL in the tool result / transcript; whoever completes it first is the identity (first login) | implicit | No caveat (B-02 fix 2). |
| SSH host keys accepted on first contact (`accept-new`) | `remote.py:82` | Not stated (B-06 fix 3). |
| Long-lived refresh tokens rather than access tokens (needed by the detached daemon) | vault | `login.md` "refreshed automatically" — adequate. |
| `~/.globus_compute/storage.db` shared with any other Globus Compute use on the machine | HANDOFF:144 | `install.md` "Globus Compute's standard token store" — adequate. |
| Tool results and notices are plaintext in the agent transcript | Claude Code property | `login.md` "What the agent never does" covers passwords; fine. |

## Surfaces examined with no issue

- Loopback bind address and family; redirect_uri construction; single-use (first code wins); TTL expiry and per-attempt generation guard; abort unblocks the SDK wait; listener dies with the process.
- `_QuietHandler` request-line silencing; `handle_error` queueing; no DEBUG logging of token exchanges.
- Paste-mode URL construction (PKCE, `access_type=offline`, minimum scope set — pinned by `test_paste_flow_url_carries_every_required_scope_and_pkce`, `test_required_scopes_are_the_endpoint_floor_and_nothing_more`).
- `login_required()` is non-prompting and fails closed; the gate runs before the catalog read and any SSH (pinned).
- `globus_identity_label` uses the existing authorizer only; failures are swallowed to `None`.
- Trimmed-db contents, temp-dir mode, SDK file mode (0600), base64-over-stdin transport, local cleanup; remote dir/file modes and their order.
- `SshTarget.argv`: `BatchMode=yes`, `IdentitiesOnly` only with an explicit key, ControlMaster options; `preauth_command` contents; `ssh -G` user lookup (local, no connect).
- `endpoints.json`, `facilities.json` modes and contents; ControlMaster dir mode; ownership squat check (except the symlink case, B-04); the registry cache location.
- `.mcp.json` / plugin manifests: no secrets, no `GLOBUS_*` expected; `config.py` accessor list vs `Reference/Configuration.md` (in step).
- `notices.py`: every notice/outcome builder formats hosts, users, key paths, exception text, scope names — never a token, code, or password; `_explain_provision_error` quotes ≤200 chars of ssh stderr.
- Session-shell wrapper file modes; `session_id` allowlist; `reset_command` scope.
- `SKILL.md`, `commands/hpc-connect.md`, `docs/user/troubleshooting.md` credential wording.
- `tests/test_login.py`, `tests/test_credentials.py`, `tests/test_remote_facility.py:825-943`, `tests/test_state.py:47`: what they pin is listed per finding above; **not pinned today:** the validating-storage path for paste logins (B-01), the identity of a landed login (B-02), teardown wiping the remote credential (B-03), symlink rejection in the control-dir fallback (B-04), listener ignoring un-stated GETs (B-05).

## Note on line numbers and a concurrent edit

All `file:line` references are against HEAD `b311b90` (the clean tree the review started from). During the review a concurrent editor modified `docs/user/*`, `README.md`, `.mcp.json` and `tests/test_plugin_packaging.py` in the working tree (not by this review, which wrote nothing in the repo). Re-checked against that working tree: `docs/user/login.md:46-47` still says "Deleting the token store logs you out entirely" and `install.md` (now `:92-98`) still says "deleting the token store logs you out of Globus"; the working-tree `install.md:73` adds that the consent "can be revoked at" app.globus.org — a step toward B-03's doc fix, but the remote copy on each login node is still unmentioned and no tool revokes or wipes it.
