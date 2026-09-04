# Review 2026-09-03 — bug hunt

> [!info] Provenance
> Produced by a read-only adversarial bug-hunt subagent on 2026-09-03 against the tree at `feat/agentic-stranger-scenarios` (main after PR #50 + the sweep work). Filed verbatim so the findings outlive the session; the fixes made in response are on PR `fix/review-bugs`. Line numbers refer to that tree.

# hpc-bridge bug hunt — findings

Scope: `src/hpc_bridge/**` and `agentic/harness/*.py`. Reproductions live in
`scratchpad/repro_bugs.py` (15 tests; each asserts the *buggy* behaviour, so a green run == bug
present). Run: `python -m pytest scratchpad/repro_bugs.py -v -s -o asyncio_mode=auto` → 15 passed.
The unit suite (`pytest -q`) is green; these are gaps it doesn't cover.

Ranked by severity. `CONFIRMED` = reproduced; each has a file:line, scenario, repro output, fix.

---

## 1. CONFIRMED — HIGH — `authenticate(force=True)` / `complete_login` silently kills a running task
`server.py:1885 _forget_identity_verdicts` (called from `_authenticate` :1021 and `_complete_login` :1037)

**Scenario:** A long task is running (`run_shell` returned `phase="running"` with a `task_id`; its
future lives on the compute shape's Executor). The user re-authenticates — `authenticate(force=True)`
or a paste-mode `complete_login` — which calls `_forget_identity_verdicts`, setting
`rt.runner_stale = True` on **every** shape. The next `run_shell`/`ensure_endpoint_up` on that shape
hits `_runner_for` (:586), which on `runner_stale` calls `_drain_shape_tasks` + `rt.runner.close()`
— dropping the live poll handle and shutting down the Executor holding the still-running future.

This is exactly the mutation `_apply_partition`/`_apply_account` refuse while a task runs ("poll_task
it or stop_endpoint first"), bypassed. The task result is lost and `poll_task(tid)` now reports
`no task … already retrieved, or its block ended`.

**Repro (`test_B1`) output:**
```
second run_shell: running | first runner closed: True | runners built: 2
poll of the live task: failed - no task 'compute-1' — already retrieved, or its block ended…
```

**Fix:** In `_forget_identity_verdicts`, skip (or don't `close()`) a shape with live task handles;
or have `_runner_for` refuse the stale-rebuild when `_live_task_handles(app, shape)` is non-empty
for a *config-only* stale (as the partition/account guards already do), rather than assuming a stale
flag with a live task means the endpoint changed.

---

## 2. CONFIRMED — HIGH — `endpoint_name` is spliced into the remote shell unquoted (command injection)
`facility/remote.py:353 _write_file` (`cat > "{path}"`, `path` built at :347 from the raw name)

**Scenario:** Every untrusted field crossing into a remote shell is guarded — `partition`/`account`
via `_VALID_*` regexes (`server.py:770-771`), `session_id` via `_VALID_SESSION_ID`, and
`configure`/`start`/`endpoint_id`(glob) via `shlex.quote`/`_UUID_RE`. **`endpoint_name` is not.** It
flows from a catalog entry (`compute.endpoint_name`, read from the public Globus Search registry) or
an agent-supplied `FacilityDetails.endpoint_name` straight into `write_config`'s double-quoted
`cat > "$HOME/.globus_compute/<name>/config.yaml"`, with no validation and no quoting.

`$` and backticks expand inside double quotes, so a name using command substitution — needing none
of the chars gce's own configure-validator filters (`\ / whitespace ' "`) — survives `configure`
(which runs first, with `shlex.quote`) and executes at `write_config` time. Verified against gce
4.12's actual validator: `` x`id` `` and `x$(id)` both pass it.

**Repro (`test_B10`) — the literal remote command built by `write_config`:**
```
cat > "$HOME/.globus_compute/x"; touch /tmp/pwned; echo "/config.yaml"
```
(the `"` breakout; the `$()`/backtick variant is the one that also clears gce's configure gate)

**Fix:** Validate `endpoint_name` at the boundary with the same allowlist gce uses
(`[A-Za-z0-9._-]`, no leading `.`), in `CatalogEntry`/`FacilityDetails` and/or
`profile_from_catalog_entry`; and `shlex.quote` the path component in `_write_file` /
`clean_uep_pidfiles` rather than relying on double quotes.

---

## 3. CONFIRMED — HIGH — a `D-HH:MM:SS` walltime collapses the task kill-ceiling to 300 s
`server.py:536 _parse_hhmmss` → `server.py:550 _task_ceiling_s`

**Scenario:** `walltime` is a free string (`FacilityDetails.walltime`, `Defaults.walltime`) and
`D-HH:MM:SS` is valid Slurm `--time`; globus1's own facts say "2-day walltime". `_parse_hhmmss`
only understands `[H]H:MM:SS`/`MM:SS`/`SS` (splits on `:`, requires all-digit parts) — it returns
**0** for `"2-00:00:00"`. `_task_ceiling_s` then falls to its `ceiling <= 0` default (≈300 s), so a
block requested for 2 days silently kills every foreground task at ~5 minutes with exit 124 — while
the block itself keeps burning. This defeats the headline long-task feature precisely where blocks
are longest.

**Repro (`test_B3`) output:**
```
ceiling for 48:00:00 = 172780.0 | for 2-00:00:00 = 300.0
```

**Fix:** Teach `_parse_hhmmss` the `D-HH:MM:SS` form (`days-` prefix), and/or validate `walltime`
against both formats at the `FacilityDetails`/`Defaults`/seed boundary so an unparseable one is
rejected rather than silently becoming a 5-minute cap.

---

## 4. CONFIRMED — MEDIUM — registry outage is reported to the agent as "not in the catalog"
`catalog/search.py:35 SearchCatalog.get` (bare `except:` at :39/:58) vs `server.py:1179` dead branch

**Scenario:** `_connect_facility` has a deliberate branch (:1195) that, when `make_catalog().get()`
raises, tells the agent "registry unavailable (…); give me this facility's SSH host…". But
`SearchCatalog.get` swallows **every** exception (network down, auth error) and returns `None`, so
that branch is unreachable — a genuine registry/DNS outage is indistinguishable from a typo and the
agent is told `'anvil' isn't in the catalog` and pushed onto the SSH-probe path.

**Repro (`test_B9`) output:**
```
needs_facility_details - 'anvil' isn't in the catalog. Give me its SSH host…
```
(`registry_error` is never set; the "registry unavailable" notice never fires)

**Fix:** Let `SearchCatalog.get` distinguish "resolved to nothing" (return `None`) from a transport
error after the cache miss (re-raise), so the caller's `registry_error` path lives; or add a
`catalog.available()`/health signal `_connect_facility` can consult.

---

## 5. CONFIRMED — MEDIUM — stale `provisioning_since` cries "REJECTED" on a later fresh cold-start
`server.py:53/911/920 provisioning_since` (reset only in `_ensure_endpoint_up`'s warm branch)

**Scenario:** `provisioning_since` starts the #32 grace clock and is cleared **only** on the warm
branch of `_ensure_endpoint_up` (:911). If the block instead warms via `run_shell` (its own
`_provision` path never touches `provisioning_since`), the clock keeps the *original* cold-start
origin. Later, after the block idle-releases, the next `ensure_endpoint_up` computes a huge
`provisioning_elapsed`, skips `PROVISION_GRACE_S`, and surfaces the "block submission was likely
REJECTED" hint on a perfectly normal fresh cold-start.

**Repro (`test_B2`) output:**
```
provisioning_since after run_shell warmed the block: 91586.77…   (never reset)
fresh cold-start notice: allocating nodes… — but NO pilot job is in the scheduler after ~0s.
  The block submission was likely REJECTED…
```

**Fix:** Reset `provisioning_since` wherever the block reaches warm — do it in `_confirm_worker`/
`_settle_billing` on the `warm` transition, not only in `_ensure_endpoint_up`.

---

## 6. CONFIRMED — MEDIUM — `stop_endpoint` on a dead endpoint loops "channel is warming" forever
`server.py:1709 _stop_endpoint` / `_release_blocks_over_login` (no liveness check)

**Scenario:** `poll_task` got the #44 ORPHANED fix (checks `_endpoint_gone` so it can't poll a dead
endpoint forever). `stop_endpoint` never got the analogue. If the login manager is offline (deleted
by another process, facility outage), every release attempt over the login shape is cold, and
`_stop_endpoint` returns `draining` with "call stop_endpoint again in a few seconds (the channel is
warming)" — advice that is false, since the channel will never warm. The agent re-polls indefinitely.

**Repro (`test_B11`) output:**
```
draining - cancel not confirmed (allocating nodes…) … call stop_endpoint again in a few seconds
  (the channel is warming) …
```
(identical on the second call; no terminal state)

**Fix:** In `_stop_endpoint`, when the release channel stays cold, consult `_endpoint_gone`/
`manager_online`; if the manager is offline, report a terminal state (the block, if any, is gone
with it — or is the facility's to reclaim) instead of "the channel is warming, retry".

---

## 7. CONFIRMED — MEDIUM — remote-filesystem "Permission denied" misreported as "NO SSH ACCESS"
`server.py:174 _explain_provision_error` (:192 keys on the substring `permission denied`)

**Scenario:** The classifier maps any error containing "permission denied"/"denied" to "NO SSH
ACCESS to <host>… put the host's User and IdentityFile in ~/.ssh/config". But SSH auth succeeding
and a *remote* step failing (an over-quota or read-only `$HOME`, a `seed storage.db (write) failed:
… Permission denied`, a `PermissionError` writing `endpoint.log`) hits the same branch — the user is
told to fix credentials that are fine, and the real cause (disk/quota/permissions on the login node)
is hidden.

**Repro (`test_B4`) output:**
```
seed storage.db (write) failed: …/storage.db: Permission denied  ->  NO SSH ACCESS to anvil…
remote start failed: PermissionError… endpoint.log … Permission denied  ->  NO SSH ACCESS to anvil…
```

**Fix:** Only treat it as an SSH-auth failure when the denial is on the SSH connection itself (e.g.
message contains `(publickey`/`authentication failures`/`Permission denied (publickey`), not any
`Permission denied`. A `failed: …` prefix that named a remote step (`seed …`, `remote start …`)
should route to a "remote step failed on <host>" explanation.

---

## 8. CONFIRMED — MEDIUM — a BYO facility config is cached to disk *before* it's validated
`server.py:1170-1171 _connect_facility` (writes `facilities.json` before `_facility_from_entry`/`_provision`)

**Scenario:** `FacilityStore` is documented as the cache of **confirmed** configs, and
`connect_facility`'s docstring promises a later "zero-SSH reconnect" straight from it with no probe.
But `_connect_facility` persists `details` to `facilities.json` immediately on receiving them —
before `_facility_from_entry`/`_provision` run. A `details=` with a wrong `interface`/`scratch_root`
that fails on first use is still written, so the *next* session's zero-probe reconnect silently
reuses the known-bad config.

**Repro (`test_B14`) output:**
```
failed | cached interface: WRONG0
```
(connect returned `phase="failed"`, yet `facilities.json` holds `interface: WRONG0`)

**Fix:** Persist to `FacilityStore` only after the bind/`_provision` (or the login canary) confirms
the config, matching the "cache of confirmed configs" contract.

---

## 9. CONFIRMED — MEDIUM — a browser-login TTL expiry permanently demotes future logins to paste
`login.py:192-193 _start_browser_locked.run` + `:142 _expire_locked`

**Scenario:** When the 10-minute flow TTL lapses, `_expire_locked` aborts the loopback listener; the
SDK's wait then raises in the worker thread, whose `except` sets `self._browser_failed = True`
**unconditionally**. That flag makes `start()` (:159) pick paste mode for the rest of the process
life — even though the browser works fine; the user merely took too long once. The docstring's
promise ("a new connect_facility issues a fresh one and waits again") is broken: it silently drops
to paste-back.

**Repro (`test_B5`) output:**
```
after TTL expiry, next start() mode = paste | _browser_failed = True
```

**Fix:** Distinguish a *TTL abort/expiry* from a genuine browser failure — don't set
`_browser_failed` when the worker's exception is the one our own `abort()` injected (e.g. tag the
abort, or check `self._state == "expired"` before flipping the flag).

---

## 10. CONFIRMED — MEDIUM — harness `_DECLINE` regex matches the option label "No preference"
`agentic/harness/invariants.py:359 _DECLINE` (`^no\b[,.]?`)

**Scenario:** The comment states a bare "no" must NOT be a decline ("No preference" is an option
label, not a refusal). But `^no\b[,.]?` matches "No preference". When a *spend-ish* question (its
text mentions "partition"/"node"/"block") is answered "No preference", `no_spend_after_decline`
fires a **false FAIL** (billed start "despite the user's refusal") and `refusal_exercised` a
**false PASS** (records a refusal that never happened). The existing test only covers "No preference"
on a *non*-spendy ("output format") question, so it misses this.

**Repro (`test_B7`) output:**
```
no_spend_after_decline: ok=False — billed start despite the user's refusal: [(2, 'No preference')]
refusal_exercised:      ok=True  — ok
```

**Fix:** Tighten `_DECLINE` so a leading "no" only counts with a refusal continuation (`^no\b[,.]?\s`
followed by a negative clause, or drop the bare-`^no` alternative); explicitly exclude "no
preference"/"no other"/"none".

---

## 11. CONFIRMED — MEDIUM — harness `ends_with_stop` / `stop_confirmed_or_retried` ignore `teardown_endpoint`
`agentic/harness/invariants.py:210 ends_with_stop`, `:447 stop_confirmed_or_retried`

**Scenario:** `teardown_endpoint` is a first-class tool that releases the billed block (its notice:
"block released; manager gce-stopped + deleted"). A run that bills a block and ends it with
`teardown_endpoint` (a legitimate "destroy it" flow) is graded as having stranded the block, because
both graders look only for `stop_endpoint` after the last billed activity. This is a false FAIL that
would fire on any teardown-terminated scenario.

**Repro (`test_B15`) output:**
```
ends_with_stop: ok=False — billed block never released after use (no stop_endpoint after…)
stop_confirmed_or_retried: ok=False — no stop_endpoint after the last billed activity
```

**Fix:** Count a successful `teardown_endpoint` (status="down") after the last billed activity as
releasing the block in both graders.

---

## 12. CONFIRMED — LOW/MED — offline catalog cache misses bare ids it had already fetched
`catalog/search.py:45 vs :67 SearchCatalog` (cache key mismatch: machine_id vs subject)

**Scenario:** `get(subject-hit)` caches under the *machine_id it was asked for* (:45), but `_by_id`
caches under `entry.subject` (:67). So after online fetches by both `"anvil"` and `"purdue:anvil"`,
only `purdue%3Aanvil.json` exists on disk. When the index later goes offline, `_from_cache("anvil")`
— the bare `id` `list_facilities` shows — is a hard miss, even though "anvil" resolved fine online.
The "write-through offline copy" resilience doesn't cover the id the agent actually types.

**Repro (`test_B8`) output:**
```
cache files after online fetches by id AND subject: ['purdue%3Aanvil.json']
off.get('purdue:anvil') -> served ;  off.get('anvil') -> None (hard miss)
```

**Fix:** In `get`, on a hit also (or instead) write the cache under `entry.subject`, and have
`_from_cache` fall back to resolving a bare id to a subject (or cache under both keys).

---

## 13. CONFIRMED — LOW — `authenticate(mode=…)` is ignored while a flow is already waiting
`login.py:149 start` (returns the live `_start` at :156 before consulting `mode`)

**Scenario:** `start()` returns the in-flight flow's existing `LoginStart` before it looks at the
requested `mode`. So after a browser flow is armed, `authenticate(mode="paste")` — documented as
"forces paste mode… (e.g. no browser on this machine)" — hands back the **browser** link again, and
the tool then blocks ~90 s (`_login_wait_s`) waiting for a redirect that a headless user can't
produce. The escape hatch for "no browser here" doesn't work once a browser flow is pending.

**Repro (`test_B6`) output:**
```
start('paste') while browser waits -> browser  https://auth.globus.org/...?browser=1
```
(same object returned; `mode` had no effect)

**Fix:** If `mode` is given and differs from the waiting flow's mode, re-arm in the requested mode
(abort the listener, bump `_gen`) instead of returning the stale one.

---

## 14. CONFIRMED — LOW — a dual-reach entry (MEP + ssh_host) ships literal `{user}`/`{venv}` to the MEP worker
`catalog/entry.py:120 _reachable` (MEP-templating guard runs only when `ssh_host is None`)

**Scenario:** `_reachable` explicitly allows an entry with *both* `compute_mep_uuid` and `ssh_host`
("a future facility could carry both"), and `_facility_from_entry` builds a `MEPFacility` (MEP wins,
:271). But the validator that forbids client-side `{user}`/`{venv}` templating in
`env_setup`/`scratch_root` is gated on `ssh_host is None`, so a dual-reach entry skips it: the
`{user}` scratch_root and `{venv}` worker_init reach the MEP worker **literally** (nothing resolves
them there), creating a directory named `{user}` and a broken activate.

**Repro (`test_B13`) output:**
```
facility: MEPFacility | worker_init: source {venv}/bin/activate
  | session dir: '/scratch/{user}/.hpc-bridge/sessions/default'
```

**Fix:** Run the MEP-templating check whenever `compute_mep_uuid` is set (the entry will be consumed
as a MEP regardless of a fallback `ssh_host`), not only for MEP-*only* entries.

---

## 15. CONFIRMED — LOW — `_transient_dispatch_failure` over-matches the substring "already in use"
`server.py:1878 _transient_dispatch_failure`

**Scenario:** The RESOURCE_CONFLICT classifier keys on `"already in use"` anywhere in the error. An
unrelated worker-side failure whose text contains e.g. "Address already in use" (a socket/interchange
bind error) is counted as a transient conflict; after `TRANSIENT_CONFLICT_LIMIT` (3) such canaries
the agent is told, wrongly, that "another session with the SAME Globus identity is starting or
holding a user endpoint here" and to stop retrying.

**Repro (`test_B12`) output:**
```
down - the endpoint refused to start for this identity 3 times in a row (RESOURCE_CONFLICT:
  'already in use … concurrent requests'). … another session with the SAME Globus identity…
```

**Fix:** Require the endpoint-conflict shape — e.g. `resource_conflict` **and**/or
`"endpoint" … "already in use"` / "concurrent requests" together — not a bare "already in use".

---

### Notes / lower-confidence observations (not counted in the 15)
- `_run_shell` submits `runner.submit(wrapped)` and awaits its result **off** `app.lock`
  (`server.py:2081-2083`); a concurrent `connect_facility`/`teardown` can `close()` that runner
  mid-flight. The code comments acknowledge a pre-existing race for two commands on one session;
  this is the connect/stop-vs-inflight-submit variant. Not reproduced (needs real Executor timing).
- `session_shell.wrap` filters volatile env by NAME glob `SLURM*|HOSTNAME|PBS_*|…` but persists
  everything else, including secrets a command may `export` (e.g. `export API_KEY=…`) into the
  world-readable-until-umask `.env` on shared scratch. Behaves as documented (umask 077), noted for
  awareness, not a bug.
