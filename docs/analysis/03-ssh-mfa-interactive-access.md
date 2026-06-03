# Interactive SSH & MFA Access Design

> **Scope.** This is the deep design doc for the single hardest dependency in the hpc-bridge plugin: the SSH/MFA bootstrap. It answers the question the project owner asked directly — *can Claude drive the MFA challenge interactively, and how, while keeping the OTP/password/cert out of the model channel?* — and then maps the answer onto the real authentication mechanisms at NERSC, ALCF, OLCF, and TACC.
>
> **Mandate.** Steelman + de-risk. We assume the bootstrapper *should* exist and try hard to make it work. But every facility claim is grounded in the research brief, and where a load-bearing vision claim was marked **CONTESTED** or **REFUTED** by verification, we say so rather than paper over it. The two claims that govern this document are:
> - **C3 (non-interactive SSH under facility MFA)** — verdict **REFUTED** as a universal claim; *holds only at NERSC*.
> - **C9 (credential isolation via separate process + scrubbing hooks)** — verdict **REFUTED** as "effectively closed"; the acquisition path is sound, the closure framing is not.
>
> Read this document as: *the mechanism is genuinely clean and shippable at one facility; everywhere else the honest answer is "human-in-the-loop MFA per (re)auth event," and no amount of engineering removes that.*

---

## 1. The question, answered directly

**Can Claude drive the MFA challenge interactively, without the secret entering the model context?** **Yes.** As of Claude Code v2.1.76 (March 2026) there is a clean mechanism that did not exist when `vision.md` was written: **MCP URL-mode elicitation**. The MCP server pauses inside a tool call, emits an `elicitation/create` with `mode:"url"`, and Claude Code opens a localhost page *served by the MCP server itself*. The user types password+OTP into that page; the secret travels MCP-server-process → out-of-band, never through the LLM context, the MCP client, or the transcript. The MCP spec guarantees for url-mode that "credentials never pass through the LLM context, MCP client, or any intermediate MCP servers." [verified against MCP spec rev 2025-11-25 and the Claude Code v2.1.76 changelog; the spec in fact *requires* url-mode (and *forbids* form-mode) for passwords/secrets — see §9. One mechanism nuance: url-mode hands the *client* a URL to open and the server hosts the page (its own HTTPS/loopback endpoint), rather than the client rendering a form.]

The division of labor that makes this safe:

| Claude *may* drive | Claude *must never* drive |
|---|---|
| Decide **when** auth is needed (`{status:"auth_required"}`) | Receive, request, or pass-through the OTP/password/cert |
| **Narrate** it to the user ("your HPC endpoint needs MFA to restart") | Hold a `submit_mfa(token=…)`-shaped tool that puts the secret in tool-call args |
| **Wait** on `{status:"up"}` | Read any credential file/socket via `run_shell` |

The hard part is **not** the Claude Code surface. The hard part is **per-facility MFA economics**: at NERSC one MFA event mints a 24-hour certificate and the agent then "just works"; at ALCF/OLCF/TACC every login needs a *fresh single-use* OTP, so the interactive auth event recurs on **every** (re)auth and the "self-healing reconcile loop" degrades to "wait for a human and their token."

---

## 2. What "keeping the secret out of the model channel" actually requires

The vision's stated boundary (`vision.md` line 71) is *"credentials never reach the LLM because the MCP server is a separate process and the model only sees `{status:up}`."* **Verification verdict on this (C9): REFUTED as written.** Process separation is necessary but is the floor, not the boundary. It closes exactly one channel (the abstract `ensure_endpoint_up` return value) and is silent on the channels that actually leak:

1. **`run_shell` is the firehose.** The MCP server's headline feature executes arbitrary shell *as the user*, on a host where the user's own credentials live on disk: `~/.ssh/nersc(-cert.pub)` (sshproxy cert), `~/.globus_compute/storage.db` (cached Globus OAuth **refresh** tokens), `$SSH_AUTH_SOCK`, ControlMaster sockets, `GLOBUS_COMPUTE_CLIENT_ID/SECRET`. An injected agent never needs the MCP process's in-memory secret — it issues `run_shell("base64 -w0 ~/.ssh/nersc")` and the cert returns base64-encoded through the *legitimate* tool-result channel, with no key-shaped string for a scrubber to catch (empirical: ~73.5% of credential leaks flow via stdout fed straight into context).
2. **Pattern scrubbing is provably evadable.** "Reject credential-looking strings" is the exact class of output filter the literature shows is readily bypassed under encoding/splitting/URL-routing. It is **defense-in-depth, not the boundary** — and naming it as the boundary manufactures false confidence.
3. **The ControlMaster socket is itself a bearer credential.** `ssh -S <ctl-socket> login.facility <cmd>` rides an already-authenticated channel with **zero credential and zero MFA**. The "authenticate once per session" property the vision sells as UX is, cryptographically, the creation of a reusable MFA-exempt capability sitting in the filesystem for the `ControlPersist` window.

So the correct framing is **not** "credentials never *reach* the LLM" but **"credentials are unreachable by the agent's execution context, by construction."** Concretely the design must:

- Make the **hot path credential-free**: the interactive loop (`run_shell`/`read_file`/`write_file`) rides Globus Compute over a **Globus Auth bearer token scoped to task-submit on one endpoint UUID** — *no SSH material on this path at all*. SSH is touched **only** by the bootstrap/repair path.
- Acquire the SSH/MFA secret in a **broker** the agent's shell cannot address (separate process; ideally separate UID — at NERSC a collaboration account is a real broker home; at ALCF/OLCF **no documented service-account home exists** [low-confidence — absence of public docs, not confirmed absence]).
- Build tool results from an **allow-list of typed fields**, never raw child stdout/stderr or exception tracebacks.
- Set `ForwardAgent=no`, agent-forwarding off (CVE-2023-38408), `IdentitiesOnly=yes`, sockets `0600` in a `0700` dir, ssh verbosity off in production.

**Irreducible residual (cannot be engineered away):** the agent legitimately runs shell as you, on a host where *your own* at-rest credentials are readable by *your* UID. The broker protects the MFA *entry* channel; it cannot stop an injected agent from `cat`-ing the user's own cert/tokens and exfiltrating them (e.g. via Globus Transfer — unencrypted by default, facility-unmonitored). This is the same exposure as handing an injectable agent your shell, and it must be stated plainly to facility security.

---

## 3. The five approaches

Each approach is rated **how-it-works / credential isolation / failure modes / UX**. Lettering matches the SSH design track.

### A. URL-mode MCP elicitation — *recommended primary OTP-collection channel*

- **How it works.** On `ensure_endpoint_up`, the broker detects it must (re)auth, stands up a single-use loopback listener (`127.0.0.1:random`, nonce-in-path, ~120s TTL, one-POST-then-dead), and returns `elicitation/create mode:"url"` with a facility-labeled message. The user opens the URL and types password+OTP into the MCP server's *own* page. The broker consumes it out-of-band (drives `sshproxy`/`ssh`), zeroizes it, sends `notifications/elicitation/complete`, and the tool returns `{status:"up"}`.
- **Isolation.** Strongest available. Per spec, "data other than the URL is not exposed to the client"; the secret lives only in broker memory and is zeroized after use. It is transport for the secret, not a facility feature — so it works *anywhere* an OTP must be typed.
- **Failure modes.** Requires Claude Code ≥ 2.1.76 with the `elicitation.url` capability [low-confidence on exact version]. Headless/no-browser sessions can't open the page → fall back to C1 (SSH_ASKPASS). Loopback page is a local-only attack surface — bind `127.0.0.1`, single-use nonce, hard TTL, reject after one POST.
- **UX.** "I need to bring up your endpoint — approve the auth prompt." One click, type password+OTP once into a clearly facility-labeled local page. At NERSC recurs ≤ once/24h; at ALCF/OLCF/TACC recurs on **every** (re)auth event.

### B. ssh-agent + short-lived certificate (NERSC `sshproxy`) — *the session-reuse engine (NERSC only)*

- **How it works.** After the OTP is collected (via A or C), the broker drives `sshproxy` under its own PTY, answering the single `Password+OTP:` prompt, and obtains `~/.ssh/nersc` + `nersc-cert.pub` (default **24h**, extensible to weeks/months on request with workflow justification). `sshproxy -a` loads it into a **broker-private** ssh-agent (never the user's forwarded agent). All later SSH reuses the cert with zero further auth until expiry.
- **Isolation.** Excellent for the *durable* secret: after one MFA event there is no password/OTP in play — only a short-lived cert on disk. But the cert is now **bearer material at a known path** that any `run_shell` can read (§2.1). Isolation from the *LLM channel* improves; exposure to the *agent's own shell* does not.
- **Failure modes.** `sshproxy` has no documented stdin/pipe mode → broker must drive its PTY and match the literal `Password+OTP:` prompt (brittle to any NERSC prompt change; fail-safe and re-prompt rather than guess). Cert expiry mid-session → next call triggers a fresh elicitation. Extended-lifetime certs require a NERSC request with undocumented SLA.
- **UX.** Best-in-class: **one OTP per 24h** (or per week/month with an extended cert), then silent. This is the flagship experience to lead GTM with. **Facility scope: NERSC/Perlmutter only — `sshproxy` is NERSC-specific.**

### C. MCP-owned PTY + masked prompt — *viable fallback when no browser*

- **How it works.** The broker spawns `ssh`/`sshproxy` under a PTY it owns. **C1:** `SSH_ASKPASS` + `SSH_ASKPASS_REQUIRE=force` point ssh at a broker-controlled helper that blocks until the broker hands it the user's secret — *value never modeled*. **C2:** form-mode elicitation with a password field, intercepted by an `ElicitationResult` hook that redacts before logging.
- **Isolation.** **Weaker, must be hardened.** The MCP spec explicitly says servers **MUST NOT** use form-mode for passwords/tokens (form-mode data *is* exposed to the client). Prefer C1. If C2 is unavoidable, the `ElicitationResult` hook replacing the value with `[REDACTED]` is a *mitigation, not a guarantee* — if Claude Code persists the transcript before the hook runs, a single-use OTP can momentarily appear in logs (acceptable only because it is already burned by the time it could be read).
- **Failure modes.** `SSH_ASKPASS` is itself a leak *amplifier* if misused: helper argv/env/stdout/prompt-string are all surfaces; a secret in an env var (`SSH_PASS=…`) is readable via `/proc/<pid>/environ` by a co-located `run_shell` task; `SSH_ASKPASS_REQUIRE`/`DISPLAY` misconfig can fall back to the controlling TTY (the Claude Code PTY) and surface the prompt in the transcript. Needs recent OpenSSH.
- **UX.** Masked input field in Claude Code, type OTP, submit. Works in any terminal including headless. Same per-facility recurrence as A.

### D. Once-per-session ControlMaster/ControlPersist — *connection-reuse layer*

- **How it works.** The broker opens one master connection (authenticated via A/C with the facility OTP), `ControlMaster=auto`, long `ControlPersist` (8–12h), in a **broker-private** ssh config (not `~/.ssh/config`). Every later tool call multiplexes with no re-auth. At NERSC it layers on top of `sshproxy` (cert handles auth, master handles connection reuse, avoiding a `sshproxy` re-run per call). At OTP-per-login facilities, the master is what makes a single OTP cover a whole working window.
- **Isolation.** Good and orthogonal to the secret — most calls trigger no auth at all. **But the socket is ambient authority**: anything that can address the socket path inherits an authenticated SSH channel with *no credential and no MFA* (§2.3). Mitigate with `0700` dir, `0600` socket, `ForwardAgent=no`, path **not inheritable** into any `run_shell` task environment. This is a configuration-discipline guarantee, not a structural one.
- **Failure modes.** Socket loss (`ControlPersist` lapse, login-node reboot, network blip) → next call re-auths → fresh OTP at OTP-per-login facilities. **Explicitly banned at OLCF** (see §4). On a shared login node the socket is a local attack surface.
- **UX.** Excellent where allowed: one auth event covers hours; the agent appears to "just work." Re-prompt only when the persist window lapses.

### E. OAuth2 / Globus Auth out-of-band — *the MFA-free escape hatch where it exists*

- **How it works.** Two distinct uses. **E1 (Globus Compute client auth — the hot path, not SSH):** registering/invoking the endpoint uses Globus Auth. Globus has **no RFC-8628 device flow**; the pattern is authorization-code with `--no-local-server` (manual code copy-paste), surfaced via url-mode elicitation or Claude Code's built-in `/mcp` OAuth. For headless restarts, `GLOBUS_COMPUTE_CLIENT_ID/SECRET` in the broker. **E2 (NERSC Superfacility API "Red" client):** OAuth2 client-credentials, **no runtime MFA**, ~10-min tokens, IP-whitelisted, can submit Slurm jobs *and run arbitrary login-node commands* — i.e. it can **replace the SSH bootstrap entirely at NERSC**.
- **Isolation.** Cleanest of all where available. E2 needs no OTP: `client_id/secret` live in the broker/keychain, tokens minted server-side, model sees only `{status:up}`.
- **Failure modes.** E2 must be **pre-provisioned** (security review; 2–48 day client expiry; IP whitelist must include the broker's egress IP — a chicken-and-egg if you wanted the broker *on* a Perlmutter login node). E1 confidential-app registration is a one-time setup; Globus access tokens expire (~48h) so the endpoint needs a **refresh token in `storage.db`** or client credentials to self-renew. **Note the CISO objection:** that auto-renewing refresh token on a shared login-node filesystem is a standing, MFA-free, facility-invisible command channel — see §7.
- **UX.** NERSC + SFAPI Red client: **potentially zero interactive MFA** after one-time setup — the strongest UX of any facility. TACC has an analogous **Tapis** REST API; ALCF/OLCF have **no documented equivalent**.

---

## 4. Per-facility applicability matrix

This is the load-bearing table. Every cell is grounded in the research brief; facilities are ordered most-tractable to least. **C3 verdict (non-interactive SSH under MFA): REFUTED as universal — works cleanly only at NERSC.**

| Property | **NERSC** (Perlmutter) | **ALCF** (Polaris/Aurora) | **OLCF** (Frontier) | **TACC** (Frontera/Stampede3) |
|---|---|---|---|---|
| SSH MFA factor | Password+OTP → cert | CRYPTOCard / MobilePASS+ OTP, **single-use per login** | RSA SecurID **hardware fob**, single-use; reuse **locks account** | TOTP (Google Auth/Duo/1Password) or SMS, single-use per login |
| **Cert reuse (sshproxy-style)** | **Yes — 24h, extendable** | **No** (no cert CA documented) | **No** (no cert path) | **No** (no sshproxy equivalent) |
| **ControlMaster (D)** | Allowed (not prohibited) | **Undocumented** (untested/unblessed) | **Explicitly BANNED system-wide** | Undocumented |
| **MFA-free API path (E2)** | **Yes — SFAPI Red client** (OAuth2 client-creds, arbitrary login-node cmd) | **None documented** | **None documented** (myOLCF→OIDC is web-portal only; SSH stays RSA) | **Tapis** REST API (analogous) |
| Bastion / extra hops | — | **Bastion hop** (`bastion.alcf.anl.gov`) complicates ControlMaster | — | — |
| Special key constraint | — | — | — | **Do NOT run `ssh-keygen` on login nodes** (breaks batch SSH infra) |
| **First-auth UX** | One OTP → 24h silent | One OTP **per login** | One **fob code per login** | One OTP **per login** |
| **Reconcile / self-heal** | **Autonomous** within cert window (or zero-MFA via SFAPI) | **Human required** every restart | **Human required** every restart; no multiplexing | **Human required** every restart |
| Recommended primary path | **E2 (SFAPI Red) → else B+D** | A/C per (re)auth; **long walltimes to avoid restarts** | A/C per (re)auth; **keep coordinator alive (tmux/looped job), avoid restarts** | A/C per (re)auth; **Tapis where possible** |
| Net verdict | **Clean once-per-day (or zero-MFA)** | Bounded interactive session; **no autonomous self-heal** | **Hardest**; architecturally hostile to reuse | Medium; **plus AUP hostility to AI tools on login nodes** |

**Reading the matrix:**

- **NERSC is the only facility where the vision's "cattle not pets" self-healing reconcile loop is actually true.** Either the 24h `sshproxy` cert (autonomous restart within the window) or, better, the **SFAPI Red client** (no runtime MFA at all) makes the bootstrapper genuinely non-interactive. The brief calls NERSC "the most tractable target" and "the clearest path to the 'credentials never reach the LLM' architecture."
- **ALCF/OLCF/TACC all require a fresh single-use OTP on every login.** There is *no* cert reuse and *no* SFAPI equivalent. The agent **cannot self-heal a crashed endpoint without a human producing a token.** The honest design there is human-in-the-loop MFA on each repair, surfaced as an honest "restarting your endpoint, approve MFA" message — a **UX cost, not a security hole**, and one that must **not** be "optimized away" by caching reusable credential material.
- **OLCF is the hardest and is architecturally hostile.** ControlMaster (approach D) is *banned system-wide* — "SSH multiplexing is disabled on all of the OLCF's user-facing systems; users will receive an error if they attempt to reuse an SSH control path." Combined with single-use RSA fobs, every restart is a full human OTP event. The only mitigations are **very long Slurm walltimes** (to avoid restarts) and **keeping the coordinator alive in place** (tmux / OLCF looped-chained-job) rather than re-SSHing — neither removes the per-login OTP for the *initial* SSH.
- **TACC adds policy hostility orthogonal to auth.** "Running AI tools on the login nodes may result in account suspension" and the `ssh-keygen` prohibition both compound the per-session-TOTP problem. Tapis is the cleaner automated path where it covers the needed actions.

---

## 5. The ONE recommended end-to-end flow

A single design that degrades gracefully per facility. The model only ever sees `ensure_endpoint_up` / `run_shell` / `read_file` / `write_file` and structured results like `{status:"up", cert_expires_in:"23h"}` — no secret-shaped value is ever a tool argument or result.

```
                         ┌─────────────────────── Claude Code (laptop) ───────────────────────┐
                         │  model calls ensure_endpoint_up / run_shell / read_file / write_file │
                         │  model sees only {status, cert_expires_in, exit_code, named fields}  │
                         └───────────────┬──────────────────────────────────────┬──────────────┘
                                         │ (1) tool call                        │ (5) hot path: Globus Auth
                                         ▼                                       ▼ bearer token (task-submit
                                ┌──────────────────┐                     scope, one endpoint UUID) —
                                │   MCP server     │                     NO SSH MATERIAL on this path
                                │ (auth state mgr) │
                                └───────┬──────────┘
                          live channel? │  no
                          (cert valid?  ▼
                           socket live? ┌──────────────────────────────────────────────────────┐
                           SFAPI token?)│ (2) interactive auth event = URL-mode elicitation (A)  │
                              yes│       │     → user types password+OTP into broker's loopback   │
                       proceed   │       │       page (127.0.0.1, nonce, 120s TTL, one-POST)       │
                       silently  │       │     fallback: PTY + SSH_ASKPASS (C1); last resort C2    │
                                 │       └───────────────────────┬──────────────────────────────┘
                                 │                               │ (3) secret → BROKER (separate
                                 │                               ▼      process/UID), never the model
                                 │                       ┌───────────────┐
                                 │                       │ Credential    │  NERSC: drive sshproxy → 24h cert
                                 │                       │ BROKER        │         → sshproxy -a → private agent
                                 │                       │ (own agent,   │         → one ControlMaster (8–12h)
                                 │                       │  own 0700     │  others: feed single-use OTP into ssh
                                 │                       │  ctl dir)     │          PTY to open master (OLCF: 1
                                 │                       └───────┬───────┘          connection, NO multiplexing)
                                 │                               │ (4) zeroize secret; SSH to login node
                                 ▼                               ▼      (ForwardAgent=no, agent-fwd off)
                          run_shell rides Globus Compute  start/repair endpoint (gce start / reconcile)
```

**Step-by-step:**

1. **Tool boundary unchanged** from `vision.md`. Structured results only.
2. **Auth state machine inside the broker (not the model).** On each tool call: do I have a live authenticated channel? = (NERSC: valid `sshproxy` cert AND/OR live ControlMaster socket) OR (other: live ControlMaster within `ControlPersist`) OR (NERSC: valid SFAPI client-creds token). If yes → proceed silently, no prompt. If no → trigger interactive auth (step 3).
3. **Interactive auth event = URL-mode elicitation (A)**, fallback PTY+`SSH_ASKPASS` (C1), last resort form-mode + redaction hook (C2).
4. **Broker consumes the secret out-of-band**, then zeroizes. NERSC → `sshproxy` 24h cert + one ControlMaster master. Others → single-use OTP into the ssh PTY to open the master (OLCF: single connection only). Harden: `SSH_ASKPASS_REQUIRE=force`, `ForwardAgent=no`, agent-forwarding off, sockets `0600` in `0700` dir, broker-private ssh config.
5. **Hot path is credential-free.** Every `run_shell`/`read_file`/`write_file` rides Globus Compute over a narrowly-scoped Globus Auth bearer token — *no SSH secret on this path*. `{status:up}` is genuinely empty of secrets rather than scrubbed-clean.
6. **Re-auth mid-session.** When the cert expires / `ControlPersist` lapses / SFAPI token can't refresh / the reconcile loop needs a fresh login, the next tool call re-enters step 3. Single-use OTPs are **never cached**; the NERSC cert is the only reusable artifact and lives only in `~/.ssh`, never modeled.
7. **Defense in depth (explicitly non-load-bearing).** `PreToolUse` hook rejects credential-looking *inbound* strings; `ElicitationResult` hook redacts; tool outputs built from typed allow-lists (not raw stdout); append-only local audit log of each auth event and tool call (no secrets) for facility accountability.
8. **Facility-best-path override.** At NERSC, **prefer the SFAPI "Red" client** for the endpoint start/restart path when the deployment satisfies the IP whitelist — this removes the OTP prompt entirely for bootstrap/reconcile and is the single biggest UX win. Keep `sshproxy`+ControlMaster as the no-SFAPI default.

**Why this is the recommended single flow:** it has exactly one OTP-collection channel (url-mode, with documented fallbacks), one auth state machine, and one credential-free hot path. It degrades by *changing only what the broker does with the secret* (cert vs. single-use master vs. SFAPI token), not by changing the model-facing contract. The model's experience is identical across all four facilities; only the *frequency* of the human MFA prompt differs.

---

## 6. Required change to `vision.md`

The vision's security section (line 71) names `SSH_ASKPASS` as the OTP channel and asserts non-interactive SSH "across" facilities. Two concrete edits:

1. **Name url-mode elicitation as the primary OTP-collection channel** (it post-dates the vision; `SSH_ASKPASS` becomes the no-browser fallback C1).
2. **Add the per-facility recurrence reality**: NERSC once/day (or zero via SFAPI); ALCF/OLCF/TACC **once per login**, so the reconcile loop is autonomous *only at NERSC*. Drop any framing that implies non-interactive SSH is uniform — verification marked that **REFUTED**.

---

## 7. Residual risks that cannot be engineered away

These survive *any* implementation of the above. They are the items to put in front of a facility CISO honestly.

1. **The agent runs as you, on a host holding your credentials (irreducible #1).** `run_shell` executes where `~/.ssh/*`, the `sshproxy` cert, `~/.globus_compute/storage.db` tokens, and Kerberos caches are readable by your UID. A prompt-injected agent can `cat` and exfiltrate them (Globus Transfer is a default-unencrypted, facility-unmonitored, tens-of-Gbps channel). The broker protects the MFA *entry* channel; it **cannot** protect at-rest credentials the agent is authorized to read. *This is the dominant residual* and is the same exposure as handing an injectable agent your shell.

2. **The Globus Auth refresh token is a standing, MFA-free, facility-invisible command channel (the CISO's hardest objection).** The hot path's auto-renewing refresh token sits in plaintext SQLite (`storage.db`) on a *shared* login-node filesystem. Unlike a stolen SSH key (which still hits the facility's MFA wall at NERSC/OLCF), a stolen refresh token grants execution **without** further MFA and auto-renews — theft is silent and persistent, and a co-tenant who can read `storage.db` inherits it. File permissions are the only barrier. The honest mitigation is to **prefer a facility-issued, IP-pinned, short-TTL, facility-revocable credential** (NERSC SFAPI Red client) over a user-held refresh token, and to impose a hard TTL on the warm subscription that forces periodic re-MFA — re-coupling the MFA event to command origination instead of severing it permanently.

3. **The one-time MFA entry moment is locally observable (irreducible #3).** At the instant the user types the OTP into the broker, anything co-resident under the same UID (keylogger, compromised terminal multiplexer, malicious pinentry) can capture it. Process separation cannot defeat same-UID local compromise. Mitigated by minimizing entry frequency (24h certs) and OS secure-input, not eliminable.

4. **ControlMaster/socket-as-ambient-authority (residual #4).** Wherever a live authenticated socket exists, it *is* a credential needing no further auth. Any misconfiguration making its path reachable from a `run_shell` task environment hands the agent an authenticated SSH channel with **zero credential material** — undetectable by any credential-string filter. Defended by `0700` paths + non-inheritance + short `ControlPersist`; this is config discipline, not a structural guarantee, and it varies with facility templates.

5. **Pattern-filter false confidence (residual #5).** The `PreToolUse` hook and output scrubber catch the easy plaintext case and thereby tempt operators to treat the boundary as solved. They are **non-load-bearing backstops**, readily bypassed under base64/hex/split/encoding. If any part of the design comes to depend on them, the boundary is effectively open.

6. **Transcript/log durability (residual #6).** Claude Code session JSON and debug logs are durable and re-readable by a later injected agent. One stray `ssh -v` line, exception traceback, or askpass prompt landing in those logs once defeats an otherwise-correct `{status:up}` design. Requires production-disabling of ssh/MCP debug verbosity and never surfacing child stderr — an operational discipline that can regress.

7. **Facility-specific collapse of the SSH-isolation story (residual #7).** At OLCF (ControlMaster banned, RSA per-login) and ALCF (per-login MobilePASS+), every endpoint repair forces a fresh human MFA. The credential boundary still *holds* (the hot path is Globus Auth, not SSH), but the "autonomous self-heal / cattle not pets" claim **breaks** — an agent cannot silently restart a crashed endpoint without a human producing a one-time token. Any "self-healing" that tried to automate this would have to cache reusable credential material, which is exactly the anti-pattern that re-opens every channel above. **The safe answer (human-in-the-loop MFA on repair) is the only safe answer at three of four facilities.**

8. **A compromised broker defeats every guarantee.** The broker is the single point that ever holds the plaintext OTP. A supply-chain compromise of the plugin defeats all isolation. Sign/pin the plugin; minimize the secret's in-memory lifetime (zeroize immediately after use).

---

## 8. Recommendation summary

- **Ship NERSC first.** It is the *only* facility where the bootstrapper is non-interactive and the reconcile loop is autonomous — via the 24h `sshproxy` cert, or (better) the **SFAPI Red client** which removes runtime MFA entirely. NERSC also gives the broker a real home (collaboration accounts) for an inter-UID credential boundary.
- **Make url-mode elicitation the primary OTP channel**, `SSH_ASKPASS` (C1) the no-browser fallback, form-mode (C2) a hardened last resort behind an `ElicitationResult` redactor.
- **Make the hot path credential-free by construction** (Globus Auth bearer token, narrowest scope) so `{status:up}` is genuinely empty of secrets — and **retire regex scrubbing and the inbound credential hook as the *primary* controls**; they are defense-in-depth only.
- **Mark ALCF/OLCF/TACC explicitly as "human-in-the-loop restart only, no autonomous reconcile."** Do not paper over them with the same "cattle not pets" claim the ControlMaster ban (OLCF) and missing cert/service-account paths make impossible. There, minimize re-auth events (long walltimes, keep the coordinator alive in place) rather than re-SSHing.
- **State residuals #1 and #2 plainly to facility security.** hpc-bridge does not leak the SSH password into the LLM — but it *does* give an injectable agent read access to the user's own credential files, and the refresh-token-on-shared-filesystem is a standing MFA-free channel. These are the conversations that determine facility acceptance, and they are honest selling points only if named.

---

## 9. Open questions / low-confidence facts to verify

Flagged so the project owner does not over-rely on them:

- **Claude Code version gate — VERIFIED.** MCP url-mode elicitation is real: the MCP spec (rev 2025-11-25) carries the quoted guarantee verbatim and explicitly *requires* url-mode — and *forbids* form-mode — for passwords/secrets; Claude Code v2.1.76 (March 2026) shipped elicitation with both form and url modes. One mechanism nuance to honor in implementation: url-mode hands the *client* a URL to open; the server hosts the page (its own HTTPS/loopback endpoint is fine), so this is an out-of-band server flow, not a client-rendered form. Still detect the `elicitation.url` capability at runtime and **refuse to collect a secret over any unsafe channel** if absent (older clients, headless sessions).
- **NERSC extended-cert lifetime & SFAPI Red review.** "Longer ones are possible" has **no documented upper bound or SLA**; the SFAPI Red client requires a security review of **undocumented latency**, and its IP whitelist creates a chicken-and-egg if the broker is meant to run *on* a Perlmutter login node. The zero-MFA NERSC path is **not self-serve on day one**.
- **ALCF undocumented exception path.** Whether ALCF has a service-account / IP-restricted-key / cert-CA path is an **open question** — public docs are silent, which is *not* the same as confirmed absence. Same for an inter-UID broker home at ALCF/OLCF.
- **Globus device flow.** Globus Auth has **no RFC-8628 device flow**; the Compute-client OAuth still needs a one-time browser auth-code copy-paste, and the endpoint needs a refresh token or client credentials to renew silently (~48h token expiry) or it demands human re-auth.
- **OLCF trajectory.** myOLCF is migrating to OIDC, but **SSH auth remains RSA SecurID**; whether OIDC tokens become exchangeable for SSH certs (which would change the automation story) is unannounced for SSH.
- **TACC AUP coverage.** Whether a Globus Compute coordinator + agent is covered by TACC's "AI tools on login nodes → suspension" clause is **unconfirmed** and should be settled with TACC staff before any deployment.

---

### Appendix: claim cross-reference

| Claim | Subject | Verdict (verification) | How this doc treats it |
|---|---|---|---|
| **C3** | Non-interactive SSH under facility MFA via cert/ControlMaster, *across* major facilities | **REFUTED** (universal); holds at NERSC only | §4 matrix scopes it to NERSC; ALCF/OLCF/TACC marked human-in-the-loop |
| **C9** | Credentials kept out of LLM via separate process + scrubbing hooks; risk "effectively closed" | **REFUTED** as "closed"; acquisition path sound | §2 reframes to "unreachable by construction"; scrubbing demoted to backstop; §7 residuals |
| **C2** | Endpoints are disposable cattle; reconcile loop SSH-restarts transparently | **REFUTED**; transparent only at NERSC in scale-to-zero | §4 + residual #7: autonomous self-heal only at NERSC |
| **C1** | Personal endpoint = identical attack surface to SSH, zero new review | **REFUTED** | §7 residuals #1, #2 name the net-new surfaces (standing refresh-token channel, agent-readable creds) |
