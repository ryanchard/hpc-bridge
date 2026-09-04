# Review 2026-09-03 — code quality

> [!info] Provenance
> Produced by a read-only code-quality review subagent on 2026-09-03 against the tree at `feat/agentic-stranger-scenarios` (main after PR #50 + the sweep work). Filed verbatim so the findings outlive the session; the fixes made in response are on PR `fix/review-bugs`. Line numbers refer to that tree.

# hpc-bridge — code-quality review (read-only)

Scope: `src/hpc_bridge/**` (centre of gravity `server.py`, 2254 lines, ~110 top-level symbols, 11 MCP tools), lighter pass over `agentic/harness/*.py`. Judged against the vault (`Home.md`, `Happy path.md`, `Modules/server.md`) and `HANDOFF.md`.

Baseline: `python -m pytest -q` → **394 passed, 2 skipped, 7.3 s**. The two skips are `importorskip("globus_compute_sdk")` guards in `tests/test_runner.py:58,106,117,127` that never trip in the dev env. **No linter or type checker is configured anywhere** (no `[tool.ruff]`/`[tool.mypy]` in `pyproject.toml`, no `.github/workflows`), so the 54 `# noqa: BLE001` and 4 `# type: ignore` annotations in `src/` are documentation by convention, not enforced.

Overall: the code is unusually well-explained — rationale comments are the norm and nearly all are worth keeping — and the state machine is well tested. The debt is structural: `server.py` has absorbed six responsibilities that have clean seams, and ~15 patterns are duplicated with small drifts. Nothing below is a correctness emergency; two items (2 and 5 in §6) change observable behaviour slightly, in the correct direction.

---

## 1. Structure — what `server.py` is carrying

By responsibility (all paths `src/hpc_bridge/server.py`):

| Responsibility | Symbols (line) | Lines |
|---|---|---|
| Env/config resolution | `_require_env` 115, `_control_settings` 140, `_short_control_dir` 165, `_env_float` 402, `_env_mode` 413, `_env_endpoint_id` 421, + 26 inline `os.environ.get` reads (116–1547) for 22 distinct `HPC_BRIDGE_*` vars | ~90 |
| Facility / catalog construction | `_ssh_config_user` 122, `_slurm_facility` 213, `_unsupported_entry_reason` 240, `_facility_from_entry` 258, `_catalog_facility` 292, `make_facility` 325, `_resolve_scratch_root` 341, `_make_search_client` 355, `make_catalog` 383, `_session_endpoint_name` 1076, `_facility_store` 1087, `_entry_from_details` 1095 | ~300 |
| Shape runtime / warmth state machine | `_supported_shapes` 486 … `_provision` 738, `_apply_partition` 774, `_apply_account` 800, `_transient_dispatch_failure` 1878, `_no_account_failure` 1906, `_identity_from_error` 1914, `_forget_identity_verdicts` 1885 | ~330 |
| Spend clock | `_bank_warm_interval` 644, `_billable` 653, `_settle_billing` 658, `_session_spend` 671, `_total_session_spend` 680 | 40 (while `cost.py` is 20 lines / 2 functions) |
| Login gate | `_authenticate` 1013, `_complete_login` 1028, `_login_wait_s` 1330, `_start_login_and_wait` 1337, `_login_notice` 1351, `_needs_login_result` 1379, + gate at top of `_connect_facility` 1147–1152 | ~110 |
| Scheduler ops over the login shape | `_release_cmd` 1502, `_release_blocks_over_login` 1526, `_pilot_status_cmd` 1560, `_summarize_pilot` 1591, `_pilot_status_over_login` 1618, `_augment_provisioning_notice` 1630 | ~140 |
| Notices / wording | `_explain_provision_error` 174, `_worker_notice` 694, `_billed_bounds_note` 711, `_needs_confirmation_notice` 821, `_needs_preauth_result` 1390, `_dispatch_error_suffix` 1866, `_no_account_notice` 1920, `_cold_outcome` 1934, `_needs_confirmation_outcome` 1948, `_busy_session_outcome` 1989, `_running_outcome` 2015, `_shape_reject_outcome` 2058, `_orphaned_outcome` 2139, `_error_outcome` 2191, the literal inside `_shape_reject` 500, + ~15 inline multi-line notices in `_ensure_endpoint_up`/`_connect_mep`/`_stop_mep`/`_stop_endpoint`/`_teardown_endpoint`/`_login_shell` | ~450 |
| Task handles | `TaskHandle` 75, `_live_task_handles` 565, `_drain_shape_tasks` 571, `_busy_session` 1978, `_register_task` 2000, `_resolve_task` 2028, `_endpoint_gone` 2126, `_poll_task` 2152 | ~150 |
| Orchestration seams (belong here) | `lifespan` 433, `_ensure_endpoint_up` 839, `_connect_facility` 1139, `_connect_mep` 1282, `_propose_or_ask` 1418, `_stop_mep` 1662, `_stop_endpoint` 1709, `_teardown_endpoint` 1754, `_login_shell` 1823, `_run_shell` 2062, `_reset_session` 2099, tool wrappers | ~650 |

Reading order is also inverted: `_ensure_endpoint_up` (839) calls `_dispatch_error_suffix` (1866), `_no_account_failure` (1906), `_no_account_notice` (1920) — defined ~1000 lines after their only callers; `_stop_endpoint` (1709) calls `_release_blocks_over_login` (1526).

### Proposed split, ranked by value (import direction ← means "imported by")

`config` ← `context` ← {`spend`, `notices`, `warmth`} ← {`scheduler_ops`, `tasks`, `login_gate`, `binding`} ← `connect` ← `server`. No cycles provided the two functions that need `_run_shell` take it as a callable (below).

1. **`context.py`** (leaf, S) — `ShapeRuntime` 44, `TaskHandle` 75, `AppCtx` 89, `DEFAULT_SHAPE` 40. Pure data; unlocks everything else. Fix the misplaced comment at 108 while moving (§3 N7).
2. **`config.py`** (leaf, S) — `_require_env`, `_env_float`, `_env_mode`, `_env_endpoint_id`, `_control_settings`, `_short_control_dir`, `_CONTROL_PATH_BUDGET` 162, `SYNC_WAIT_S` 480, `TASK_CEILING_MARGIN_S` 483, `CANARY_TTL_S` 470, `CANARY_TIMEOUT_S` 474, `TRANSIENT_CONFLICT_LIMIT` 473, `PROVISION_GRACE_S` 1588, plus one typed accessor per env var (`ssh_user()`, `ssh_key()`, `account()`, `release_attempts()`, …). Value: the 26 scattered reads (with their `or None` inconsistency, §2 D7) collapse; the vault's `Reference/Configuration.md` (20 vars) can be diffed against one module (code reads 22: `HPC_BRIDGE_ENDPOINT_NAME` 1107 is undocumented; `HPC_BRIDGE_FACILITY` 330 is a removed-var tripwire).
3. **`notices.py`** (models + read-only context, S/M) — every builder in the "Notices" row plus the inline literals hoisted to named functions (`mep_attach_notice(account_required)`, `mep_stop_draining_notice(idle_s)`, `mep_offline_notice(eid)`, `stop_released_notice(detail)`, `stop_unconfirmed_notice(detail)`, `teardown_notice(...)`, `no_facility_bound_notice()`, `compute_only_no_login_shell_notice()`). ~450 lines out of `server.py`, zero logic risk, and it lets the wording tests target one module (§5).
4. **Fold the spend clock into the existing `cost.py`** (S) — `_billable`, `_settle_billing`, `_bank_warm_interval`, `_session_spend`, `_total_session_spend`. The vault's `Modules/cost.md` is already the documented home.
5. **`binding.py`** (M) — the "Facility / catalog construction" row + `_explain_provision_error`. **Caveat that applies to every step but bites hardest here:** tests monkeypatch `server.make_catalog` (21 sites), `server._facility_from_entry` (15), `server.discover_facility_details` (7), `server._run_shell` (7), `server._control_settings` (5), `server._make_search_client` (4), `server._release_blocks_over_login` (3). Re-exporting a moved name from `server` does **not** make a patch on `server.X` reach a callee inside `binding` that resolved `X` at import — the patch target must move with the function or the tests go vacuous. Grep `monkeypatch.setattr(server,` before each step.
6. **`scheduler_ops.py`** (M) — `_release_cmd`, `_pilot_status_cmd`, `_summarize_pilot` (pure, already directly tested at `tests/test_server.py:143,571,583`), `_release_blocks_over_login`, `_pilot_status_over_login`, `_augment_provisioning_notice`. The two async runners call `_run_shell` (1549, 1624) — give them a `run_login: Callable[[str], Awaitable[ShellOutcome]]` parameter (server passes `partial(_run_shell, app, shape="login")`) to avoid the cycle.
7. **`warmth.py`** (L) — the "Shape runtime" row. Highest value (it is the part a reviewer has to hold in their head) and highest risk (20 functions, all under the `app.lock` discipline; the lock stays on `AppCtx` so semantics don't change, but do this after 1–4 have shrunk the file).
8. **`tasks.py`** (M) — the "Task handles" row.
9. **`login_gate.py`, or simply `login.py`** (S) — the "Login gate" row is SDK-import-free, which is `login.py`'s stated constraint (`login_flow_manager.py:1–3`).
10. **`connect.py`** (M) — `_connect_facility`, `_connect_mep`, `_propose_or_ask`, `_needs_preauth_result`; needs `run_login` injected as in 6 for the allocation listing at 1264.

`server.py` keeps `mcp`, `lifespan`, the tool wrappers, and the orchestration seams (`_ensure_endpoint_up`, `_run_shell`, `_reset_session`, `_stop_*`, `_teardown_endpoint`, `_login_shell`) — which is exactly the role `Modules/server.md` describes. Target ≈ 700–800 lines.

---

## 2. Duplication & drift

- **D1 — two spend-floor notices.** `_needs_confirmation_notice` (821–837, used by `_ensure_endpoint_up` 903) and `_needs_confirmation_outcome` (1948–1962, used by `_run_shell` 2075 / `_reset_session` 2112). Both branch on `_has_login_shape`; one says `e.g. run_shell('mybalance', shape='login')`, the other drops the "e.g."; the MEP tails differ ("every command bills a block, which then stays warm between calls" vs "this facility is compute-only (no free login shape)"). One builder.
- **D2 — the "drop every shape/task/state" block, three copies, three behaviours.** `_connect_facility` 1224–1230 (closes runners, no `_bank_warm_interval`), `_teardown_endpoint` MEP branch 1767–1773 (no banking), `_teardown_endpoint` SSH branch 1795–1802 (banks, and captures `spent` first — with a comment at 1797 explaining why banking before clearing matters). `lifespan`'s `finally` 458–461 is a fourth partial copy. Net effect: **re-binding via `connect_facility` while a billed block is warm drops that block's running warm interval from `session_spend`** — the very under-report `_settle_billing`'s docstring (658–664) says the design closes. One `_drop_all_shapes(app, *, bank: bool) -> float`, called under the lock from all four sites.
- **D3 — `_run_shell` / `_reset_session` preamble.** 2062–2083 and 2099–2118 are a byte-identical 12-line sequence (shape reject → `Session(...)` → lock → `_ensure_warm_runner` → `_busy_session` → three early returns). Extract `_ready_session(app, shape, session_id) -> tuple[GlobusRunner, Session] | ShellOutcome`.
- **D4 — idle-release seconds from two sources.** `_billed_bounds_note` 718 reads `getattr(app.profile, "max_idletime_s", 600)` (client profile; the getattr default is dead — `Profile.max_idletime_s` is a real field, `profile.py:14`), while `_stop_mep` 1693 reads `getattr(app.facility, "max_idletime_s", None) or app.profile.max_idletime_s`. On a MEP the warm-block bounds notice and the stop notice can quote different idle windows. One `_idle_release_s(app)`.
- **D5 — scheduler lookup.** `getattr(getattr(app.facility, "profile", None), "scheduler", "slurm")` at 1544 and 1623, same 4-line justification comment both times.
- **D6 — error-string construction and truncation.** `f"hpc-bridge error: {type(exc).__name__}: {exc}"[:500]` inline at 210, 893, 1222, 1296, 2196; the same shape with other prefixes at 1035 `[:300]`, 1167 `[:300]`, 1453 `[:400]`, 1793 `[:280]`, 1844 `[:300]`; plus `[:160]` 208, `[:200]` 197/205, `[:320]` 1927, `[:1800]` 1465. Eight widths for one purpose. `_err(exc, prefix, limit=NOTICE_LIMIT)`.
- **D7 — env-read idiom drift.** `os.environ.get(X, "").strip()` ×20; **with** `or None` at 286, 287, 321, 1437, 1452 and **without** at 281, 312, 1217. An empty `HPC_BRIDGE_ACCOUNT` therefore reaches `profile_from_catalog_entry(account="")` → `MachineProfile.account=""` → rendered as `account: ""` in the Slurm template (remote.py:687); `MEPFacility.__init__` (mep.py:55–58) has to filter `""` out explicitly — the callee compensating for the caller. `int((os.environ.get(...) or "3").strip())` at 145, 1546, 1547 duplicates what `_env_float` (402) already does for floats. The `HPC_BRIDGE_USER_DIR` default `Path.home()/".globus_compute"` is built twice (337, 444).
- **D8 — the SSH-failure explanation has two calling conventions.** Bootstrap: `_explain_provision_error(exc, fac)` (1243) digs `fac.cli.target.host/user` through four `getattr`s (187–191). Probe: `_explain_provision_error(exc, host=…, user=os.environ.get("HPC_BRIDGE_SSH_USER")…, fallback=…)` (1451–1454) re-reads the env instead of using the `SshTarget` it just built (1435–1441). Both paths have an `SshTarget`; `_explain_ssh_failure(exc, target)` is the single signature. Also 183: `"permission denied" in ln.lower() or "denied" in ln.lower()` — the first clause is subsumed.
- **D9 — "not a queue wait" said four ways.** 1875 (`_dispatch_error_suffix`: "Not a queue wait: fix the config/partition and retry"), 1928 (`_no_account_notice`: "This is TERMINAL, not a queue wait"), 954 (MEP offline: "reports OFFLINE — not a queue wait"), 927–933 (transient-conflict escalation: same intent, no phrase). The harness grader `terminal_refusal_respected` (`agentic/harness/invariants.py:620`) keys on the agent *stopping*; a single `_terminal_suffix(reason)` keeps the phrase the agent learns to recognise identical.
- **D10 — result factories.** `EndpointStatus(status="down", block_state="cold", endpoint_id=…, notice=…)` at 848/852/859/888 (four early rejections in `_ensure_endpoint_up`, differing only in notice and whether partition/account are echoed); `ConnectFacilityResult(phase="failed", facility=facility, notice=…)` at 1164, 1219, 1241, 1293, 1448. Two 3-line factories.
- **D11 — harness re-implements the product's cancel command.** `agentic/harness/cluster_ops.py:30–45 scoped_cancel_cmd` is a copy of `server._release_cmd` (1502–1523): same `uep.<eid>` marker, same `sed ':a;N;$!ba;s/\n\t//g'` PBS unwrap, same awk — and already drifting (`-u "$(whoami)"` vs `-u "$USER"`, `JobID:24` vs `:30`). The harness deliberately imports nothing from `hpc_bridge`; if that isolation is the point, say so in the docstring (it currently says "the same scope the server's `_release_cmd` uses", which reads as shared code).
- **D12 — duplicated test doubles.** `_Res`, `_DoneFuture`, `_FakeRunner` in `tests/test_server.py:14–110` **and** `tests/test_catalog_flow.py:10–58` (the second is a strict subset); `_Status`/`_Client` status fakes in `tests/test_mep_server.py:21` and `tests/test_mep_facility.py:27`; `_FakeGCClient` `tests/test_remote_facility.py:295`. `tests/fakes.py` exists for exactly this.

---

## 3. Naming & readability

- **N1** `SlurmFacility` (`facility/remote.py:634`) and `_slurm_facility` (`server.py:213`) build PBS facilities too (`remote.py:681` template switch, `:804` `cancel_blocks(..., scheduler)`). Name predates PBS (#28). `SshFacility`.
- **N2** `_ensure_warm_runner` (1965) returns the **not-warm** state or `None`-when-warm; callers bind it as `not_warm` (2070, 2107). Inverted name. `_block_not_warm(app, shape) -> str | None`.
- **N3** Three functions do several things each: `_provision` (738; its docstring: "Provision/probe … and update the spend clock"), `_ensure_endpoint_up` (839–977, 142 lines: validate → apply partition/account → provision → assemble one of five notices → pilot probe), `_connect_facility` (1139–1280, 143 lines: login gate → 4-level entry resolution → support check → build → switch → provision → allocation listing). These are the three a newcomer reads first; §1 items 7 and 10 make each a short sequence of named steps.
- **N4** `ShapeRuntime.no_account: str | None` (58) reads as a bool, holds the manager's error text (assigned at 638, tested as truthy at 611). `no_account_error`.
- **N5** `TaskHandle.future: object` (80), comment "opaque, to avoid the SDK import here" — but the SDK returns a stdlib `concurrent.futures.Future`; annotating it costs no import and types `.done()/.cancelled()/.result()` at 568, 2039–2049.
- **N6** `FacilityStore` (`state.py:80`) is "a cache of **CONFIRMED** session (BYO) facility configs", and 1169 comments "(re)remember the confirmed config" — but the `put` at 1171 runs before `_provision` at 1239, so an unverified `details=` is persisted for zero-probe reconnect even if the canary then fails. Either rename ("last-supplied") or move the write after a warm login shape (a behaviour change — the maintainer's call; note the registry-wins rule at 1177–1183 already mitigates the catalogued case).
- **N7** Misplaced comment: 108 "serializes provision / runner-swap / teardown…" describes `lock` (112) but sits above `login_flow` (111).
- **N8** Import block 17–37: `.models` imported at 24 and again at 28–34; `.facility.local` (26) wedged between `.login` and `.lifecycle`. `remote.py:28` puts `from typing import …` after third-party imports. `ruff --select I` fixes both in one pass.
- **N9 — rationale vs history.** The rationale comments are the codebase's best feature (e.g. 152–160 ControlPath budget, 522–530 sanitizer, `remote.py:670–678`, `session_shell.py:63–98`, `shapes.py:22–28`) — keep all of those. The following narrate *removed* code or a stale plan rather than current intent:
  - `_note_dispatch` 726–734: "(the old timeout==124 heuristic is obsolete now …)".
  - `_billed_bounds_note` 711–717 "it is NOT cut at ~110s any more"; the agent-facing text at 720–722 tells an agent "it is NOT cut" about a limit it never saw; `tests/test_server.py:140` asserts `"110" not in low`.
  - `runner.py:97–101` "keeps the old timeout-linked value for back-compat" — the sole production caller (`_runner_for` 596) always passes `walltime`; the fallback is dead.
  - `tests/test_server.py:593 test_release_cmd_slurm_matches_prior_inline_command` pins a byte-for-byte string "the old inline block used to build" — a refactor-safety test that outlived its refactor and now blocks legitimate changes (e.g. matching the harness's `-u "$(whoami)"`).
  - `remote.py:1–8` module docstring: "OTP facilities will later route SSH through the credential broker" — no broker exists; MFA shipped as ControlMaster pre-auth (`NeedsPreauth` 157, `preauth_command` 97). Stale.
  - `_catalog_facility` docstring 293–296: "v1 slice: SSH-bootstrap Slurm/PBS machines only" — the next 15 lines handle MEP entries.
  - `catalog/entry.py:14, 180` "(Plan 2)" — refers to a section of `Planned/Globus index discovery channel.md:28` that is marked built; meaningless in code now.
  - `endpoint.py:59` "hpc-bridge invariant: always a PERSONAL endpoint, never a MEP" — still true for what we *create*; since M1 we *consume* MEPs. Say "never creates".
- **N10 — magic numbers worth naming:** `est_wait_s=60` (1943); poll cap `min(wait, 600.0)` (2161); `max(…, 300.0)` default ceiling (558); `max(…, 5.0)` minimum sync-wait (595); `subprocess.run(..., timeout=10)` (130); `url_ready.wait(timeout=15)` (`login.py:204`); `"limit": 20` (`catalog/search.py:56`); the literals `~10 s` (1874) and `~10 min` (1740) inside notices that should derive from `PROVISION_GRACE_S` / `max_idletime_s`; `1_000_000` as both `AppCtx.max_output_chars` (98) and `dispatch.execute`'s default (`dispatch.py:24`); the eight `[:N]` widths (D6).

---

## 4. Error handling & typing

**Inventory.** 54 `except Exception`/`BaseException` in `src/`; 49 carry `# noqa: BLE001` + a reason. Unannotated: `catalog/search.py:39, 58, 76, 85` and `facility/mep.py:113`. One annotation has no reason: `server.py:2220` (`run_shell` wrapper; its siblings at 2236/2249 do).

**Reasons that hold** (spot-checked): `dispatch.py:34` and `server.py:2090` (the tool contract: every dispatch failure becomes an outcome); `runner.py:61` (shut-down Executor → not-warm, #37); `server.py:436` (boot resilience — the "no tools" failure mode); 1181/1193 (registry unreachable / stale cache — each has a documented next step); 2135 (`_endpoint_gone`: a status hiccup keeps polling — the safe direction); 2171 (re-resolve reads the true state); `login.py:118` (unreadable credential → login required); `remote.py:130` (`BaseException` on cancel: kill + reap, then re-raise — the correct use).

**Reasons weaker than stated:**

- **`_list_facilities` 1006–1010** `except Exception: return []` — "no catalog configured (no index / scope)". But `SearchCatalog.discover` (`search.py:80–86`) already returns `[]` on transport errors, and `make_catalog()` can no longer fail for want of an index (`PUBLIC_REGISTRY_INDEX` baked in, 393). What the outer net now catches is a **bug** (an `AttributeError` in `CatalogEntry.summary()`, an unwritable `CLAUDE_PLUGIN_DATA` in `mkdir` at `search.py:28`) and reports it as "no facilities" — the same silent symptom `MEMORY.md`'s MCP note warns about. Narrow to `OSError`/`GlobusAPIError`, or at minimum `print(..., file=sys.stderr)` before returning.
- **`mep.py:107–118 manager_online`** — any exception → `True`. The docstring's justification (the canary is the real signal) is sound, but note the consequence: on a MEP whose status API *throws*, `_endpoint_gone` (2126) can never orphan a task and the "reports OFFLINE" notice (947–957) never fires. Log the swallowed exception to stderr as `find_online_endpoint` does (`remote.py:839–842`) so an outage is diagnosable from the transcript.
- **`_facility_from_entry` failure 1218** — annotated "surface a missing SSH_USER/KEY as a structured result"; neither is required any more (`_ssh_config_user` falls back to `getpass.getuser()`, 137). What can raise is `profile_from_catalog_entry` / `MEPFacility.from_entry` / `LoginNodeStore` I/O. Update the reason.
- **`_make_search_client` 378** — catches `ImportError` of `globus_compute_sdk` and degrades to anonymous. Fine on its own, but see the SDK boundary below: this is the *only* path that treats the SDK as optional.

**Optional handling — one real bug.** `_ensure_endpoint_up` 910: `notice = _worker_notice(rt.last_canary)` is `None` when `_confirm_worker` short-circuited on a live task (618–620 returns `"warm"` without touching `last_canary`, which is `None` after a runner rebuild — `_runner_for` never sets it). For a non-billable login shape nothing else fills `notice`, and 959–960 `if ignored: notice = f"{notice} (login shape has no partition; …)"` emits the literal `"None (login shape has no partition; ignored 'x')"`. Guard the f-string.

**The optional-dependency boundary is not real.** `pyproject.toml` puts `globus-compute-sdk` only under `[integration]`, and `server.py` imports it lazily with care (80, 216, 272, 276, 687, 1430). But `server.py:20 from .discovery import …` → `discovery.py:13 from .facility.remote import …` → `remote.py:23–26 from globus_compute_sdk.sdk.auth.token_storage import _get_storage_filepath, _resolve_namespace` (a **private** SDK path) and `remote.py:30 from ..credentials import …` → `credentials.py:32 REQUIRED_RESOURCE_SERVERS = _required_resource_servers()` runs an SDK import at module load. Verified: `import hpc_bridge.server` puts `globus_compute_sdk` in `sys.modules`. Decide once: promote the SDK to a core dependency (`.mcp.json` always runs with `--extra integration` anyway) and delete the lazy-import ceremony, or make `discovery` import `remote` lazily and `credentials` compute `REQUIRED_RESOURCE_SERVERS` on first use. Either way, the private-path import at `remote.py:23` is the kind of thing a minor SDK bump breaks *at import* — i.e. the "no hpc-bridge tools" boot crash — and deserves a comment naming the pinned range.

**Typing seams.**

- `Facility` Protocol (`facility/base.py:17–23`) declares three members; the server relies on nine optional capabilities via `getattr`: `supported_shapes` 493, `bootstrap` 755, `teardown` 1788, `login_exec` 1825, `scratch_root` 351, `account_required` 1309, `max_idletime_s` 1693, `profile.scheduler` 1544/1623, `cli.target` 187–189. `config_template` is typed `dict | tuple[str, dict]` and the server does an `isinstance` dance at 522–530 to tell "rendered engine dict" (`LocalFacility`) from "(template, defaults)" (`SlurmFacility`/`MEPFacility`, whose template slot is `""`, `mep.py:120–123`). Give the Protocol optional members with defaults (or split `Facility` / `SshFacility` / `ComputeOnlyFacility`) and return one shape from `config_template`.
- `_provision` (738) returns `str` where the domain is `BlockState | Literal["needs_confirmation"]` (`lifecycle.py:9` already defines `BlockState`); `_confirm_worker` (604) returns `str` for `Literal["warm","provisioning"]`; these feed `EndpointStatus.block_state` (`models.py:36`, a `Literal`). Naming the union lets a checker catch a `"needs_confirmation"` leak.
- Untyped parameters on seams that already have the types in scope: `_explain_provision_error(exc, fac=None…)` 174, `_unsupported_entry_reason(entry)` 240, `_facility_from_entry(entry, …)` 258 (`CatalogEntry` imported at 18), `_connect_mep(app, facility, fac)` 1282, `_needs_preauth_result(facility, target)` 1390, `_make_search_client(_app_factory=None)` 355 and `make_catalog()` 383 (no return types), `_facility_store()` 1087, `_register_task(..., fut, …)` 2000, `MEPFacility.from_entry(cls, entry, …)` `mep.py:66`.
- `Profile(mode=_env_mode())  # type: ignore[arg-type]` 449 — `_env_mode` validates against `("interactive","batch")` and can return that `Literal`.
- `_billed_bounds_note(app: "AppCtx", rt: "ShapeRuntime")` 711 — string annotations are unnecessary under `from __future__ import annotations` (line 1).

**Async hygiene.** `_facility_from_entry` (sync) is called from the async `_connect_facility` at 1217 and runs `_ssh_config_user` → `subprocess.run(["ssh","-G",…], timeout=10)` (130) **on the event loop** — up to 10 s blocking per connect while other tool calls queue behind `app.lock`. Every other blocking call in the file goes through `asyncio.to_thread` (1147, 1338, 2087).

---

## 5. Test coverage gaps (by reading `tests/`)

Coverage is strong where it matters: the warmth/spend state machine (`test_server.py`, 1455 lines, 78 tests), the MEP path (`test_mep_server.py`, 28), SSH transport and templates (`test_remote_facility.py`, 76), login (`test_login.py`, 26), catalog resolution (`test_catalog_flow.py`, 46). The graders in `agentic/harness/test_invariants.py` are hermetic and thorough.

**Behaviours with no test** (each verified by grepping `tests/` for the distinguishing string or symbol):

| Behaviour | Code |
|---|---|
| `_teardown_endpoint` when `facility.teardown` raises ("manager teardown reported") | `server.py:1789–1793` |
| `_env_float` / `_env_mode` invalid-value fallback ("ignoring invalid") | 402–418 |
| `_login_shell` exception path ("login_shell error") | 1842–1844 |
| `_resolve_task` on a **cancelled** future ("cancelled when its block") — `_PendingFuture.cancel()` exists (`test_server.py:70`) but is never called | 2039–2043 |
| `_catalog_facility`: MEP entry + conflicting `HPC_BRIDGE_ENDPOINT_ID` ("conflicts with the entry"); `account_required` with no `HPC_BRIDGE_ACCOUNT` | 303–314 |
| `_summarize_pilot` HELD **notice** (only the category `"held"` is asserted, `test_server.py:158`) | 1607–1610 |
| `_worker_notice` dill-skew warning ("dill skew") | 704–706 |
| `_augment_provisioning_notice` exception path | 1635–1638 |
| `_reset_session` on a busy session / needs_confirmation | 2110–2118 |
| Spend across a `connect_facility` re-bind (D2 — would have caught the dropped interval) | 1224–1230 |
| `_short_control_dir` third fallback and "nothing short" return | 168–171 |
| `discovery._interface` no-candidate `ib0` default; `_env_setup` "already on PATH → `true`" | `discovery.py:189–191, 209–210` |
| `SearchCatalog._from_cache` corrupt-JSON branch | `catalog/search.py:71–78` |
| `lifespan`'s `finally` runner close | 458–461 |

**Tests asserting wording rather than behaviour.** 70 substring-on-`notice` assertions (test_server 26, test_mep_server 21, test_catalog_flow 16, test_login 5, test_dispatch 2). Many are legitimate — on an agent-facing tool the notice *is* the product, and some phrases are contract because the harness graders key on them (`"not confirmed"` → `stop_is_honest`, `invariants.py:437`; `"ORPHANED"`; `"NO ACCOUNT"`). The brittle ones pin incidental phrasing: `test_server.py:137–140` (`"idle"`, `"poll"`, `"detach"`, `"110" not in`), `:283` (`"queued"` + a job id), `:511`/`:565` (`"online for reuse"`), `:661` (`"torn down"`), `:1105` (`"balance"`), and most of the 21 in `test_mep_server.py`. Once `notices.py` exists (§1 item 3), assert identity against the builder (`res.notice == notices.stop_released_notice(detail)`) so a rewording touches one place; keep substrings only for the grader-contract phrases.

**Fixtures duplicated across files.** D12; plus the `AppCtx(facility=FakeFacility(), profile=Profile())` idiom repeated ~40× across `test_server`/`test_catalog_flow`/`test_login`, and `_confirm_slurm(app)` (`test_server.py:8`) re-done inline elsewhere as `_shape_runtime(app, "compute").spend_confirmed = True`. A `conftest.py` `app` fixture and the fakes in `tests/fakes.py` remove both.

**One history-pinned test:** `test_release_cmd_slurm_matches_prior_inline_command` (`test_server.py:593`) — see N9.

---

## 6. Top 10 actionable items

| # | Where | Change | Risk | Est |
|---|---|---|---|---|
| 1 | `server.py:1006–1010` | Narrow `_list_facilities`'s bare `except Exception: return []` to transport errors (`OSError`, `globus_sdk.GlobusAPIError`), or at least log to stderr first — today a bug reads as "no facilities". | None (only widens what's reported) | S |
| 2 | `server.py:1224–1230, 1767–1773, 1795–1802, 458–461` | Extract `_drop_all_shapes(app, *, bank: bool) -> float`; make the `connect_facility` re-bind bank the warm interval like teardown does; add a test that `session_spend` survives a re-bind. | Low; `session_spend` on re-bind becomes correct (a visible change) | M |
| 3 | `server.py:821 / 1948`, then `2062–2083 / 2099–2118` | One spend-floor notice builder; extract the shared `_run_shell`/`_reset_session` preamble. | Low — both callers are tested | S |
| 4 | `pyproject.toml`, `server.py:20`, `discovery.py:13`, `remote.py:23`, `credentials.py:32` | Decide the SDK boundary: promote `globus-compute-sdk` to a core dep and drop the lazy-import ceremony, **or** make `discovery`→`remote` lazy and `REQUIRED_RESOURCE_SERVERS` lazy. Comment the private-path import at `remote.py:23`. | Low either way; today's state is the worst of both | S |
| 5 | `server.py:1217 → 122–137` | Run `_ssh_config_user`'s `subprocess.run` off the event loop (`asyncio.to_thread`). | None | S |
| 6 | `server.py:718 vs 1693` | One `_idle_release_s(app)` (facility's `max_idletime_s`, else profile's) for both `_billed_bounds_note` and `_stop_mep`; drop the dead `getattr(app.profile, …, 600)`. | Low; the MEP bounds notice starts quoting the facility's window (correct) | S |
| 7 | `server.py:910, 959` | Guard the `None` notice on the warm-without-canary path so `"None (login shape has no partition…)"` can't be emitted; add the cancelled-future and teardown-raises tests. | None | S |
| 8 | new `config.py`; `server.py` 26 env reads | Typed accessors for the 22 `HPC_BRIDGE_*` vars with one `or None` idiom (fixes the empty-`HPC_BRIDGE_ACCOUNT` leak, D7); document `HPC_BRIDGE_ENDPOINT_NAME` in `Reference/Configuration.md`. | Low; tests patch functions, not env | M |
| 9 | new `notices.py`; ~70 test asserts | Move every pure text builder + the hoisted inline literals; rewrite brittle substring asserts as identity-against-builder, keep substrings only for grader-contract phrases. | Low, mechanical; ~450 lines leave `server.py` | M |
| 10 | new `context.py` + `warmth.py`; renames | Split the warmth state machine behind a leaf context module (§1 items 1, 7); rename `SlurmFacility`→`SshFacility`, `_ensure_warm_runner`→`_block_not_warm`, `no_account`→`no_account_error`. Do this **last**, and move the `monkeypatch.setattr(server, …)` targets (`make_catalog` ×21, `_facility_from_entry` ×15, `_run_shell` ×7) with their functions — the one way a split silently turns tests vacuous. | Medium | L |

Two decisions for the maintainer rather than fixes: **N6** (`FacilityStore.put` before proof vs the "CONFIRMED" wording) and **D11** (share `_release_cmd` with the harness or keep the copy and say it's deliberate).

Housekeeping that makes the rest stick: add `[tool.ruff] select = ["E","F","I","BLE"]` and run `mypy` on the leaf modules (`config`, `context`, `notices`, `models`, `cost`, `shapes`, `lifecycle`) so the `noqa: BLE001` / `type: ignore` annotations are enforced rather than ornamental — nothing runs them today.
