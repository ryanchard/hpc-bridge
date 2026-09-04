# Review 2 2026-09-03 — bugs

> [!info] Provenance
> Second review round (evening of 2026-09-03), after the server split (#58–#66), the 15 bug fixes (#54), the relock (#55) and the quick wins (#56/#57): read-only subagents against `main` @ 064f141. Filed verbatim; fixes on PR `fix/review2` and the typing PR that follows.

# Adversarial review 2 — hpc-bridge `62337f0..HEAD` (2026-09-03)

Scope: the server split (#58–#66), the proven-cache / dead-pin feature (#57), the review-fix PR (#54), the six
new-user scenarios + graders (#51), CI (#56). Every CONFIRMED item below has a runnable reproduction in this
directory (`repro_product.py`, `repro_product2.py`, `repro_harness.py`; run with
`uv run --project /Users/gusellerm/Projects/hpc-bridge python -m pytest <file> -q -p no:cacheprovider -s`,
the harness one from `agentic/harness`). Baseline: `pytest -q` 415 passed; CI harness step 70 passed.

**Split verification (no bugs found):** an AST diff of every moved function against `62337f0:server.py`
(`ast_diff.py`) shows only the intended edits (config accessors, `_drop_all_shapes`, `_idle_release_s`,
`_spend_floor_guidance`, the injected `run_login`, `_endpoint_gone` in stop, the pending-cache). Every old
name resolves in some new module; every module imports standalone (no cycles, no import-time I/O: no state dir
is created on `import hpc_bridge.server`); the only `server.*` monkeypatch left in tests is `_run_shell`, which
`_login_runner` resolves at call time. `_parse_hhmmss`'s day branch is right; `_cache_file` escapes `/` and `%`
(no traversal on POSIX); the endpoint-name allowlist and the MEP `{user}/{venv}` guard hold; `login.py`'s
mode-switch/TTL `_gen` guards are sound; `_runner_for`'s deferred swap does rebuild once the task's future
resolves (`repro_product2.py::test_deferred_swap_reports_up_without_canary`).

---

## 1. HIGH — CONFIRMED · `CANNOT REACH` never fires on macOS, so `_drop_dead_pin` is dead on the primary client
`src/hpc_bridge/notices.py:62-65`, `src/hpc_bridge/connect.py:157`

The unreachable-host classifier matches Linux strerror text only (`connection timed out`, `no route to host`, …).
macOS's `ETIMEDOUT` reads **"Operation timed out"**, so a dead login-node pin (the #57 feature's whole target)
falls to the raw fallback and the pin is kept forever; the agent sees an internal step name instead of the remedy.

Reproduction (live ssh on this Mac):
```
$ ssh -o BatchMode=yes -o ConnectTimeout=2 u@10.255.255.1 true
ssh: connect to host 10.255.255.1 port 22: Operation timed out
notice: hpc-bridge error: RuntimeError: seed storage.db (mkdir) failed: ssh: connect to host 10.255.255.1 port 22: Operation timed out
startswith CANNOT REACH: False | pin would be dropped: False
```
Fix: add `"operation timed out"`, `"host is down"` (macOS `EHOSTDOWN`) and `"timed out during banner exchange"`
to the list; better, classify on the `ssh: connect to host … port …:` prefix, which is platform-independent.

## 2. HIGH — CONFIRMED · a canary timeout never resets `transient_conflicts`, so a warming block is reported as the terminal "NO LONGER transient"
`src/hpc_bridge/warmth.py:134-144`, `src/hpc_bridge/server.py:269`

The counter increments on RESOURCE_CONFLICT and resets only on a canary **success** or a non-timeout,
non-conflict failure. A **timeout** (= the submit was ACCEPTED; the block is cold-starting, the normal case right
after another session's start completes) leaves it untouched. After 3 conflicts every later poll whose canary
merely times out returns `status="down"` + "refused to start … 3 times in a row … Stop retrying", and the agent
abandons a block that is in fact allocating (on a MEP: billing, and idle-releasing only 600 s later).
```
after an accepted (timed-out) canary: down | the endpoint refused to start for this identity 3 times in a row (RESOURCE_CONFLICT: ...
```
(`repro_product.py::test_transient_conflict_verdict_sticks_after_accepted_submit`)
Fix: in `_confirm_worker`, `rt.transient_conflicts = 0` on `result.error == "timeout"` (an accepted submit is
the end of the conflict), and also in `_note_dispatch` on `complete`/`running`.

## 3. MEDIUM-HIGH — CONFIRMED · `scenario_knobs.py` resolves names differently from `run.py`, so knobs are silently dropped — the "no account"/"logged-out" scenarios then run as the mapped, logged-in identity
`agentic/harness/scenario_knobs.py:23`, `agentic/harness/run.py:241-247`, `agentic/run_smoke.sh:52-61`

`run.py._resolve_scenario` forgives `saturation.`, `x.py` and `agentic/scenarios/x.py`; `scenario_knobs.py`
only strips `.py`, and an unknown name exits 0 with no output. `run_smoke.sh` `eval`s that empty output and
proceeds, mounting the **default** `HPCB_TEST_GLOBUS_DB`. For `mep_no_account` that is the MAPPED identity —
the prompt authorises confirming spend, so a real block starts and bills on the wrong identity, and the run is
graded against a world it was never meant to see (FAIL for the wrong reason). For `needs_login_paste` the
"logged-out stranger" runs logged in. `SERIAL`/`COOLDOWN_S` are lost the same way (run_suite calls `_knobs`
too, but through the same script).
```
needs_login_paste                        knobs=[HPCB_KNOB_NO_GLOBUS_DB=1 ]  run.py->needs_login_paste
needs_login_paste.                       knobs=[]                          run.py->needs_login_paste
agentic/scenarios/needs_login_paste.py   knobs=[]                          run.py->needs_login_paste
```
Fix: share one resolver (move `_resolve_scenario` into a tiny host-importable module both use) and make
`scenario_knobs.py` exit non-zero on an unknown scenario, with `run_smoke.sh` failing on it
(`knobs="$(python3 …)" || exit 1` — the current `eval "$(…)"` discards the status even under `set -e`).

## 4. MEDIUM — CONFIRMED · chain runs lose every agent text: `_combine` builds `Trace(calls)` without `texts`
`agentic/harness/run.py:57-73`

On a `PHASES` scenario the combined trace has `texts == []`, so the word-reading graders (#51) are
vacuous (`never_asks_for_password` PASSES on a password ask) or spurious (`login_link_surfaced`,
`texts_mention`, `identity_quoted_from_refusal` FAIL). `trace_from_bundle` keeps texts, so a live grade and an
offline regrade of the same run disagree. No current chain scenario uses a text grader — it is a trap for the
next one.
(`repro_harness.py::test_combine_drops_texts`)
Fix: `Trace(calls, [t for r in results for t in r.trace.texts])`.

## 5. MEDIUM — CONFIRMED · `_asks_for_password` misgrades realistic phrasing in both directions
`agentic/harness/invariants.py:557-576`

False positives — the agent correctly telling the user to type the password **into the browser** is graded as
soliciting it (2/2): "Open that link in your browser and enter your password on the Globus page; nothing comes
back to me." / "On the Duo page, type your password and approve the push — I never see it."
False negatives — any `_NEGATED` word anywhere earlier in the sentence disarms the detector (3/3): "I can't
proceed **without** your password — please enter it below…", "**Don't** worry, just paste your password here…",
"I will **not** store it, so please tell me your Globus password now."
(`repro_harness.py::test_password_grader_*`)
Fix: scope negation to the verb's clause (e.g. within ~4 tokens before the verb, not the whole prefix), and
whitelist the browser/IdP destination ("on the Globus page", "in your browser", "into Duo") as not-an-ask.
Given the FN rate this grader is a weaker gate than `EXPECT_OK` treats it as.

## 6. MEDIUM — CONFIRMED · the "proven" BYO cache is committed only inside `connect_facility`, and a reused endpoint "proves" details it never rendered
`src/hpc_bridge/connect.py:33-39, 89-90, 160-161`

(a) The common flow — `connect(details=…)` → `provisioning` → the agent warms the login shape via
`ensure_endpoint_up(shape="login")`/`run_shell(shape="login")` and never calls connect again — leaves
`pending_facility_cache` populated and `facilities.json` empty, although the canary answered (the vault's own
definition of proven). Next session: probe again, Duo again on an MFA facility. (`repro_product.py::test_proven_login_shape_outside_connect_is_never_cached`)
(b) "Proven" is judged by a canary on whichever endpoint is **online**. With `find_online_endpoint` reusing an
endpoint built from an earlier config, a corrected-but-wrong `details=` (interface `does-not-exist0`) is
committed as proven with `reused=True` — the interface the docstring says this step exercises was never
rendered. (`repro_product2.py::test_reused_endpoint_proves_unexercised_details`)
Fix: (a) commit from the login-shape success path in `warmth._confirm_worker`/`_provision` (or call
`_commit_proven_facility` from `_ensure_endpoint_up`/`_ready_session` when `shape=="login"` warms); (b) skip the
commit when `app.state.reused` is True, or record the reused endpoint's config hash and compare.

## 7. MEDIUM — CONFIRMED (mechanism) / PLAUSIBLE (impact) · `_drop_dead_pin` drops the pin on a client-side outage
`src/hpc_bridge/connect.py:41-55, 157`

Any `CANNOT REACH` qualifies — including "No route to host"/"Network is unreachable" with the VPN off, when the
node is fine. The pin is gone for good; after the user fixes the VPN, `find_online_endpoint` reattaches over
the web (so nothing looks wrong), but `login_shell`/`teardown` now go to the round-robin **alias** and can
`gce stop` on a node that isn't running the manager — the orphan the pin exists to prevent.
(`repro_product.py::test_pin_dropped_on_client_side_outage`)
Fix: only drop when the pin is unreachable **and** the alias answers (one cheap `ssh -O check`/`true` to the
alias), or mark the pin suspect and let the next successful alias bootstrap overwrite it.

## 8. MEDIUM-LOW — CONFIRMED · re-binding TO a MEP discards the previous facility's banked spend
`src/hpc_bridge/connect.py:145-152, 164-165`

`prior_spend = _drop_all_shapes(bank=True)` runs, then the MEP branch `return await _connect_mep(...)` never
reads it; the shapes are cleared so `_total_session_spend` no longer sees the number either. A user who held a
warm Anvil block for an hour and then says "connect me to globus1" loses the ≈1.0 node-hour from every later
status/outcome without a word. (`repro_product.py::test_rebind_to_mep_drops_prior_spend_note`)
Fix: pass `prior_spend` into `_connect_mep` and prefix its notice the same way (or carry banked spend on
`AppCtx` across re-binds instead of dropping it with the shapes).

## 9. MEDIUM-LOW — CONFIRMED · `_list_facilities`'s "re-raise a BUG" path is unreachable
`src/hpc_bridge/catalog/search.py:104-110`, `src/hpc_bridge/server.py:351-373`

The #54 fix classifies exceptions in `_list_facilities`, but `SearchCatalog.discover` still wraps
`post_search` in a blanket `except Exception: return []`, so an `AttributeError` in the search call reads as
"no facilities" exactly as before; only errors raised by `make_catalog()` itself reach the classifier.
(`repro_product.py::test_list_facilities_bug_hidden_as_empty`)
Fix: let `discover` propagate (the caller already classifies), keeping only the per-entry skip.

## 10. LOW — CONFIRMED · `globus_identity_label` caches the first identity for the process lifetime; the `sub` fallback is unreachable
`src/hpc_bridge/login.py:254-282`

`_forget_identity_verdicts` runs on every new login, but `_IDENTITY_LABEL` is never cleared, so after
`authenticate(force=True)` as a different user the NO ACCOUNT notice tells the user to quote the **old**
identity to support (the error-text identity is preferred, but the 422 does not always carry it). Separately,
`get_identities` needs `view_identities`, which the minimal scope set does not request; it raises, the whole
try aborts, and the documented `… or sub` fallback never runs (returns None).
(`repro_product.py::test_identity_label_cache_survives_relogin_and_sub_fallback_unreachable`)
Fix: reset `_IDENTITY_LABEL = None` from `_forget_identity_verdicts` (or key the cache on `sub`); wrap
`get_identities` in its own try so `sub` survives.

## 11. LOW — CONFIRMED · a bare-number walltime is Slurm MINUTES but parsed as SECONDS → a 10 s task ceiling
`src/hpc_bridge/config.py:157-180`, `src/hpc_bridge/models.py:113`

Slurm accepts `30` (= 30 min) and `days-hours`; `_parse_hhmmss("30") == 30` and
`_task_ceiling_s({"walltime": "30"}) == 10.0`, so every task is killed with exit 124 after 10 s and the sync-wait
clamps to 5 s. `FacilityDetails.walltime` is a free string (no validator), so a BYO `details=` can carry it.
The new day branch is correct (`2-00:30` → 2 d 30 min). Pre-existing in the single-field case, but now the only
walltime parser. (`repro_product.py::test_bare_minutes_walltime_misparsed`)
Fix: treat a single field as minutes (Slurm) and validate `walltime` on `FacilityDetails`/`Defaults` with a
regex that admits `[D-]HH:MM[:SS]`.

## 12. LOW — CONFIRMED · a test patches `hpc_bridge.server._ssh_config_user`, which nothing calls any more
`tests/test_catalog_flow.py:682`

`binding._facility_from_entry` reads its own module global, so the "a MEP must never consult ~/.ssh/config"
guard cannot fire even when the lookup runs — shown by running an SSH entry under the same patch: the real
lookup ran, the AssertionError never did. Violates the split's own patch-target rule (HANDOFF §server split).
(`repro_product.py::test_server_patch_of_ssh_config_user_is_vacuous`)
Fix: `monkeypatch.setattr(binding, "_ssh_config_user", …)`. Worth a grep-guard test that no test patches a
re-exported name on `server` except `_run_shell`.

## 13. LOW — PLAUSIBLE · `fresh_user_session.sh` strips only some hpc-bridge overrides
`scripts/fresh_user_session.sh:49-54`

`HPC_BRIDGE_SSH_HOST`, `_PARTITION`, `_REMOTE_VENV`, `_SCRATCH`, `_CHARGE_FACTOR`, `_PROFILE`,
`_SSH_CONTROL_PERSIST` leak from the maintainer's shell into the "brand-new user" session. `SSH_HOST` is the
one that changes behaviour: `_propose_or_ask` falls back to it, so a stranger's `connect_facility("x")` would
probe the maintainer's host instead of returning `needs_facility_details`. (`${INDEX:+…}` quoting is fine.)
Fix: `env -u` the whole `HPC_BRIDGE_*` family (`env -i` + an explicit allowlist, or a loop over `env | grep`).

## 14. LOW — PLAUSIBLE · CI harness step runs without `-W error::DeprecationWarning`
`.github/workflows/ci.yml:41,44`

The unit tier promotes deprecations to errors; the harness tier does not, so a deprecation surfacing only
through `agentic/harness` imports would pass silently. Verified the step currently passes **with** the flag
(70 passed), so this is a hardening gap, not a live failure. `uv run --project ../..` does resolve the synced
env (confirmed locally); pip-audit's `-e .` line is harmless from the repo root.

---
Not pursued (would need live creds/cluster): whether the Globus web service ever answers a 5xx whose text contains
"404" (`_not_found`'s substring test), and the real-world frequency of finding 2's conflict→timeout sequence.
