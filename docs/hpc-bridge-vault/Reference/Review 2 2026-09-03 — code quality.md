# Review 2 2026-09-03 — code quality

> [!info] Provenance
> Second review round (evening of 2026-09-03), after the server split (#58–#66), the 15 bug fixes (#54), the relock (#55) and the quick wins (#56/#57): read-only subagents against `main` @ 064f141. Filed verbatim; fixes on PR `fix/review2` and the typing PR that follows.

# hpc-bridge — code-quality re-read after the server split (read-only)

Tree: `main` @ `064f141` (split 10/10, #66). Baseline: `python -m pytest -q` → **415 passed, 2 skipped, 11.7 s**; `ruff check .` with the CI rule set (`E,F,I,B,BLE,A`, `pyproject.toml:52`) → clean. `RUF100` (unused `noqa`) is not enabled — see §1.4.

Line counts: `server.py` **818** (HANDOFF.md:162 and `Modules/server.md:61` both say "≈1000"), `warmth` 350, `notices` 314, `connect` 293, `binding` 284, `config` 194, `scheduler_ops` 150, `context` 116, `cost` 68, `login_gate` 62.

Judged against the first review (`Reference/Review 2026-09-03 — code quality.md`), `CLAUDE.md`, HANDOFF's "server split" section and the ten `Modules/*.md` pages.

---

## 1. The new boundaries

### 1.1 Does each module hold what its docstring says, and nothing else?

| Module | Docstring claim | Verdict | Evidence |
|---|---|---|---|
| `context` | "pure data, no behaviour" (`context.py:1`; vault `context.md`) | **Slightly over-claims** | `_supported_shapes` / `_has_login_shape` / `_idle_release_s` are behaviour (`context.py:101–116`). Fine to keep them here — fix the docstring. Lines 5–6 ("as the split proceeds") are stale. |
| `config` | "every environment variable … ONE idiom (`env()`)" (`config.py:1–4`, `config.md`) | **Mostly true; five internal exceptions + one external** | `_require_env` (`:71`), `_control_settings` (`:81`), `_env_mode` (`:112`), `_env_endpoint_id` (`:125` — literally re-implements `env()`), `_env_float` (`:102`) all read `os.environ` directly. `HPC_BRIDGE_STATE_DIR` is read in `state.py:22`, not here (it is in `Reference/Configuration.md`). `_control_settings` also does `mkdir`/`chmod` (`:89–90`) — a config module with filesystem side effects. `_parse_hhmmss`/`_task_ceiling_s` (`:157–194`) are walltime maths, not configuration. `config.md`'s sentence "tests that patch `server._control_settings` keep working" is now **false** — callers are `binding.py:52` / `connect.py:260` and tests patch `config._control_settings` (×5). |
| `cost` | spend clock + two helpers | **Clean** | `cost.py:30–68`. |
| `notices` | "every pure notice/outcome builder … No I/O, no state mutation" (`notices.py:1–7`) | **Every *builder function* moved; the inline literals did not** | ≈49 agent-facing literals remain outside: `server.py` ≈20 (`:200, :207, :261–262, :268, :274–278, :299–301, :471–476, :486–491, :512, :524–525, :534–536, :545–547, :567, :581–585, :588, :594, :602–603, :623–625, :630–633`), `connect.py` ≈14 (`:86, :117–118, :123–125, :142, :158, :171, :179–180, :187–188, :196, :213, :221–224, :227–231, :238–242, :283–287`), `warmth.py` 5 (`:36–40, :226–227, :246–247, :319, :326`), `scheduler_ops.py` 5 (`:77, :112–127`), `login_gate.py` 5. The first review's §1.3 hoist list (`mep_stop_draining_notice`, `stop_released_notice`, `teardown_notice`, `no_facility_bound_notice`, …) was not executed. Also holds three **classifiers**, not wording — `_no_account_failure` (`:225`), `_transient_dispatch_failure` (`:209`), `_identity_from_error` (`:231`) + `_NO_ACCOUNT_MARKERS`/`_GLOBUS_USERNAME_RE` — which `warmth.py:25` imports, putting the state machine *above* the wording module. And a config shim: `_login_wait_s` (`:128–132`) is a pass-through to `config.login_wait_s()` with the same docstring. |
| `binding` | how a machine is reached | **Clean** | Residue: `_facility_store` docstring still says "confirmed" (`binding.py:238`) after the "PROVEN" decision (`state.py:80–83`); `make_catalog()` / `_make_search_client()` still untyped returns (`:182, :209`); `entry` untyped at `:72, :89`. |
| `scheduler_ops` | login-shape scheduler ops, runner injected | **Clean** | D5 duplicate at `:64` and `:134` (below). |
| `warmth` | state machine + task handles; "Every function here runs under `app.lock`, held by the caller" (`warmth.py:10`) | **Lock claim is false for two functions** | `_drop_compute_shape` takes the lock itself (`:262`); `_endpoint_gone` is a web call deliberately made *off* the lock (`server.py:527, :754`). Say so in the docstring — the next person will add a lock acquisition inside one of them and deadlock. Also `_shape_reject`/`_apply_*`/`_resolve_task` return wording (above). |
| `login_gate` | the gate as the tools see it | **Clean but thin (62 lines), one dead import** | `login_gate.py:17–21` imports `_needs_login_result` with `# noqa: F401 - _needs_login_result is used by connect` — `connect.py:28` imports it from `.notices`; nothing imports `login_gate._needs_login_result`. The `noqa` reason is false. `_start_login_and_wait(flow, mode)` touches no runtime and could be `LoginFlow.start_and_wait` in `login.py` (the stated constraint is only "no server/runtime imports", `login_flow_manager.py:1–3`); the two tool bodies are 25 lines. Acceptable as is; not worth a PR on its own. |
| `connect` | the connect flow | **Clean** | `_drop_dead_pin` (`connect.py:41–55`) does facility-internals surgery through five `getattr`s (`:46–49`) — it is a `SlurmFacility` method wearing a module function's clothes (the Protocol gap, §2). `LoginRunner` is imported from `scheduler_ops` (`:29`) — a type alias living in a sibling; `context.py` is its natural home. |
| `server` | app, lifespan, tool wrappers, orchestration | **Holds three things that aren't orchestration** | (a) 132 lines of re-exports (`server.py:12–143`, 17% of the file); (b) `_registry_transport_error` (`:351–356`) — Globus exception-class knowledge that belongs in `binding` next to `make_catalog`; (c) the `_VALID_PARTITION`/`_VALID_ACCOUNT` validation (`:195–208`) — belongs in `warmth` beside `_apply_partition`/`_apply_account` (`warmth.py:206–207` define the regexes). |

### 1.2 `login_gate` vs `login`
Right cut, given the constraint: `login.py` stays free of `AppCtx`/`warmth`; the one runtime-touching action (forget sticky verdicts on a new identity, `login_gate.py:46, :61`) is exactly what the gate module exists for. The only thing I'd move is `_start_login_and_wait` into `login.py` (no runtime import needed) — then `login_gate` is two tool bodies, and the maintainer can decide whether a 40-line module earns its file.

### 1.3 The injected seams — callable per call vs a `Channels`/`Runtime` object
**Keep the callable.** Reasons, verified:
- There is **one** channel (`run_login`), consumed by 4 functions (`scheduler_ops.py:46, :129, :140`; `connect.py:59`), built at 4 sites (`server.py:313, :412, :516, :587`).
- The closure resolves `_run_shell` from `server`'s globals **at call time** (`server.py:499–500`), which is what keeps the 7 `monkeypatch.setattr(server, "_run_shell", …)` tests reaching the release/pilot/allocation paths (`tests/test_server.py:502, :536, :564, :633, :661, :1078, :1260`). A `Channels` object built in `lifespan` would break the 104 tests that construct `AppCtx(...)` directly (57 in `test_server.py`, 26 `test_catalog_flow.py`, 11 `test_login.py`, 8 `test_review_bugs.py`, 2 `test_mep_server.py`) unless it kept the same late-binding trick — at which point it is the closure with a class around it.
- The precedent for "inject via `AppCtx`" already exists — `AppCtx.runner_factory: Callable[..., GlobusRunner] = GlobusRunner` (`context.py:93`). If a **second** channel appears, add `run_login` there with a default that late-binds, and delete `_login_runner`. Until then a class is ceremony.

Two nits: `_login_runner` is defined at `server.py:495`, after its first two uses (`:313`, `:412`) — the reading-order inversion the first review flagged, in miniature; and `_ensure_endpoint_up` constructs a fresh closure on every provisioning poll (`:313`) — harmless, but it reads as if something is being set up.

### 1.4 The per-line `# noqa: F401` re-export blocks
**Wrong mechanism even on its own terms.** `ruff --extend-select RUF100` flags **66 of the 88** per-line `noqa: F401` in `server.py:12–143` as *unused* (lines 13–24, 38, 41, 44–47, 54–56, 60, 63, 64, 72–74, 88–92, 98–99, 102–106, 109, 114–119, 123–142). Cause, verified: ruff applies a `# noqa` on the **first line** of a parenthesised import to the whole statement — the `config` block (`:27–42`) has a header `noqa` and no per-line ones, yet `CANARY_TIMEOUT_S`, `CANARY_TTL_S`, `SYNC_WAIT_S`, `TASK_CEILING_MARGIN_S`, `_control_settings`, `_env_float`, `_require_env`, `_short_control_dir` are all unused in the body and CI passes. So in the seven header-`noqa`'d blocks every per-line `noqa` is dead, and in the two blocks *without* a header (`cost` `:58`, `notices` `:84`) the per-line ones on *used* names (`_billable`, `_total_session_spend`, `_with_spend`, `_billed_bounds_note`, …) are dead too. Only 22 do anything.

Options, cheapest first: (a) header `noqa` on the two remaining blocks, delete all 88 per-line ones, add `RUF100` to `select` so this can't recur — 10 minutes; (b) `[tool.ruff.lint.per-file-ignores] "src/hpc_bridge/server.py" = ["F401"]` — one line that also documents intent; (c) delete the re-exports and point imports at the owning modules.

**Who still imports each name from `server`** (so (c) is costed honestly; `grep` over `tests/`, `agentic/`, `skills/`, `commands/` — the last two reference no internals):

| Owning module | Names imported from `server` | Where |
|---|---|---|
| `context` | `AppCtx` (6 files + `agentic/mep_no_account_check.py:95`, `agentic/fakecluster/stretch/driver.py:86–92`), `ShapeRuntime` (test_server ×8, test_review_bugs), `TaskHandle` (test_server:1244), `_shape_runtime` (4 files + agentic), `_supported_shapes`, `_idle_release_s` (test_review_bugs:293), `EndpointState` via `server.EndpointState` | |
| `config` | `PROVISION_GRACE_S` (test_server:154, 253 + ×3 attr), `_env_endpoint_id` (:455), `_parse_hhmmss`/`_task_ceiling_s` (:1324; test_review_bugs:21, 27), `_control_settings`, `_short_control_dir` (test_catalog_flow:793) | |
| `binding` | `make_facility` (test_server ×6), `make_catalog` (×4 attr), `_facility_from_entry` (test_catalog_flow:655, 680, 698 + ×2), `_facility_store` (×8), `_resolve_scratch_root` (×4), `_catalog_facility` (×4), `_make_search_client` (×3), `_ssh_config_user` (test_server:822, 835), `_unsupported_entry_reason` (test_server:1168; test_catalog_flow:669), `_session_endpoint_name` (:493), `_entry_from_details` (:609, 640) | |
| `scheduler_ops` | `_release_cmd` (test_server:574, 586, 597; **`agentic/harness/test_pool_and_cluster_ops.py:96`**), `_pilot_status_cmd`, `_summarize_pilot` (test_server:147) | |
| `warmth` | `_runner_for` (test_server:1342; test_review_bugs:24), `_confirm_worker`, `_forget_identity_verdicts` (test_mep_server:346; test_review_bugs:17, 20), `_shape_reject`, `_register_task`, `_drain_shape_tasks` (test_server:1390–1395), `_settle_billing`, `_total_session_spend`, `_worker_notice`, `_note_dispatch` (test_server:960–1000 via `srv.`) | |
| `notices` | `_no_account_failure` (test_mep_server:156), `_cold_outcome` (:165), `_identity_from_error` (:288), `_dispatch_error_suffix`, `_transient_dispatch_failure` (:339; test_review_bugs:28), `_explain_provision_error` (test_catalog_flow:827; test_review_bugs:19) | |
| `login_gate` | `_authenticate`, `_complete_login` (test_login:11) | |
| `connect` | none directly — the 36 `server._connect_facility` calls hit the wrapper (legit) | |

≈110 import sites in 6 test files + 3 agentic scripts. Mechanical, but it is a whole-suite touch; do (a) before V1 and (c) after.

### 1.5 Import graph sanity
No cycles, no leaf importing upward. Actual layering: `config` ← `context` ← `cost` ← `notices` ← {`warmth`, `scheduler_ops`, `binding`} ← `login_gate` ← `connect` ← `server` (plan §1 of the first review, with `tasks` folded into `warmth` — the vault's reason, "a live task IS warmth", is sound). Three mild oddities, none a cycle: `config.py:12` imports `catalog.search` for one constant (`PUBLIC_REGISTRY_INDEX` — the constant should live in `config`, the leaf); `context.py:18` imports the whole `login` module for the `LoginFlow` annotation (make it `TYPE_CHECKING`-only); `warmth.py:25` imports `notices` for two classifiers (§1.1).

---

## 2. What the split left behind in `server.py`

Function sizes now: `_ensure_endpoint_up` **139** lines (`server.py:184–322`), `_stop_mep` 45 (`:448–492`), `_stop_endpoint` 45 (`:504–548`), `_teardown_endpoint` 45 (`:560–604`), `_poll_task` 37 (`:725–761`), `_login_shell` 27, `_run_shell` 24, `_ready_session` 21. Over in `connect.py`, `_connect_facility` is still **141** lines (`connect.py:57–197`).

### 2.1 `_ensure_endpoint_up` is the obvious next decomposition — yes. Named steps:
1. **`_validate_selection(app, shape, partition, account) -> EndpointStatus | None`** — `:191–208`: three early rejections that differ only in notice. Give them one `_down(app, notice, *, rt=None)` factory (D10) and move the `_VALID_*` check to `warmth` beside `_apply_*`.
2. **`_apply_selection(app, shape, rt, partition, account) -> tuple[str | None, bool]`** — `:211–224` (the `ignored` flag + the two applies + the live-task rejection).
3. **`_provision_status(app, shape, confirm_spend) -> str | EndpointStatus`** — `:227–249` (the try/except and the `needs_confirmation` return).
4. **`_warm_notice(app, rt)`** — `:254–262` (worker notice + bounds + the charge-factor caveat → a `notices` builder).
5. **`_cold_status(app, rt, eid, …) -> EndpointStatus | str`** — `:263–303`: three exits (conflict-limit `:269–279`, NO ACCOUNT `:280–291`, MEP-OFFLINE `:292–302`) whose text moves to `notices` as `_conflict_limit_notice(rt)`, `_mep_offline_notice(eid)`, `_allocating_notice(partition)`.
6. The off-lock pilot augmentation `:306–313` stays as the tail.

One **new** finding inside it: `server.py:283–285` awaits `asyncio.to_thread(globus_identity_label)` — a userinfo network round-trip (`login.py:257–262`, "silent refresh through the SDK app's authorizer") — **while holding `app.lock`** (taken `:209`, the return at `:287` is inside the block). Every other tool call queues behind it. `_cold_outcome` does the same lookup with `fetch=False` (cache only, `notices.py:254`). Either use `fetch=False` here too or fetch after the lock is released.

### 2.2 `_stop_mep` / `_stop_endpoint` / `_teardown_endpoint`
Three 45-line siblings, eight inline notices. Residual D2 drift:
- `server.py:572–574` (MEP teardown) calls `warmth._drop_compute_shape(app)` **then** `_drop_all_shapes(app, bank=True)` and adds the two — `_drop_all_shapes(bank=True)` alone banks the compute interval and returns the same total (`warmth.py:153–161`); the pair is a leftover of the pre-`_drop_all_shapes` copy.
- `lifespan`'s `finally` (`server.py:171–175`) is still an inline fourth copy of "clear tasks, close runners" — `_drop_all_shapes(app, bank=False)` is the one-liner.

### 2.3 First review's D-items and N-items — status

| Item | Status | Where now |
|---|---|---|
| D1 two spend-floor notices | **DONE** | `notices.py:116–126` `_spend_floor_guidance`, used by `:114` and `:268` |
| D2 four "drop everything" copies | **DONE** (2 residues) | `warmth.py:148–162`; callers `connect.py:145`, `server.py:574, :596`. Residues §2.2 (`server.py:171–175`, `:572–574`). Re-bind now banks — pinned by `tests/test_review_bugs.py:276` |
| D3 `_run_shell`/`_reset_session` preamble | **DONE** | `server.py:660–680` `_ready_session` |
| D4 idle-release from two sources | **DONE** | `context.py:113–116`; used `notices.py:102`, `server.py:479`; test `test_review_bugs.py:292` |
| D5 scheduler lookup ×2 | **STILL OPEN** | `scheduler_ops.py:64` and `:134`, byte-identical |
| D6 error-string truncation widths | **STILL OPEN** | 18 sites, 8 widths: `server.py:238` [:500], `:594` [:280], `:638` [:300]; `connect.py:86` [:300], `:142` [:500], `:213` [:500], `:280` [:400], `:292` [:1800]; `notices.py:52/59/64` [:200], `:67` [:160], `:69` [:500], `:243` [:320], `:313` [:500]; `login_gate.py:59` [:300] |
| D7 env-read idiom drift | **DONE** (residues) | `config.py:15–18` `env()`; residues `config.py:71, :81, :112, :125`; `state.py:22` |
| D8 `_explain_provision_error` two conventions | **STILL OPEN** | `connect.py:156` `(exc, fac)` vs `connect.py:278–281` `(exc, host=, user=, fallback=)`; the four-`getattr` dig `notices.py:42–46`; the subsumed clause `notices.py:39` |
| D9 "not a queue wait" said four ways | **STILL OPEN** | `notices.py:207`, `:244`, `server.py:299`, `server.py:274–278` (same intent, no phrase) |
| D10 result factories | **STILL OPEN** | `EndpointStatus(status="down", block_state="cold", …)` ×8: `server.py:192, :196, :203, :232, :271, :287, :512, :567`; `ConnectFacilityResult(phase="failed", …)` ×7: `connect.py:83, :139, :159, :184, :211, :218, :275` |
| D11 harness cancel copy | **RESOLVED AS DELIBERATE** | `agentic/harness/cluster_ops.py:32–36` says so; pinned by `test_pool_and_cluster_ops.py:96–104` |
| D12 duplicated test doubles | **STILL OPEN** | §4.2 |
| N1 `SlurmFacility` builds PBS too | **STILL OPEN** | `facility/remote.py:640`; `binding.py:46` `_slurm_facility`; 12 src + 42 test + 18 vault refs |
| N2 `_ensure_warm_runner` inverted name | **STILL OPEN** | `warmth.py:281`; caller binds it as `not_warm` (`server.py:670`). 3 src refs, 0 test refs — a 2-minute rename |
| N3 three functions doing several things | **PARTIAL** | `_ensure_endpoint_up` 139 lines, `_connect_facility` 141, `_provision` unchanged (`warmth.py:175–200`) |
| N4 `ShapeRuntime.no_account: str \| None` | **STILL OPEN** | `context.py:46`; 4 src + 4 test refs |
| N5 `TaskHandle.future: object` | **STILL OPEN** | `context.py:63` — the "avoid the SDK import" reason never applied (`concurrent.futures.Future` is stdlib) |
| N6 `FacilityStore` "CONFIRMED" before proof | **DONE** | `state.py:80–83` "PROVEN"; `connect.py:33–39` `_commit_proven_facility`; residue wording `binding.py:238` |
| N7 misplaced lock comment | **DONE** | `context.py:97–98` |
| N8 import ordering | **DONE** | ruff `I` in CI (`pyproject.toml:52`) |
| N9 history-narrating comments (8) | **STILL OPEN, all 8** | `warmth.py:167` ("old timeout==124 heuristic is obsolete"); `notices.py:99` ("NOT cut at ~110s any more") + `tests/test_server.py:141` (`"110" not in`); `runner.py:100` (dead back-compat fallback — every production caller passes `walltime`, `warmth.py:98–100`); `tests/test_server.py:592` `test_release_cmd_slurm_matches_prior_inline_command`; `facility/remote.py:7` ("credential broker"); `binding.py:125` ("v1 slice: SSH-bootstrap … only" above code that handles MEPs); `catalog/entry.py:15, :197` ("Plan 2"); `endpoint.py:59` ("never a MEP") |
| N10 magic numbers | **STILL OPEN** | `notices.py:258` `est_wait_s=60`; `server.py:734` `600.0`; `config.py:190` `300.0`; `warmth.py:97` `5.0`; `binding.py:37` `timeout=10`; `login.py:212` `timeout=15`; `catalog/search.py:82` `"limit": 20`; `notices.py:206` "~10 s"; `server.py:546` "~10 min" (should derive from `_idle_release_s`); `context.py:81` vs `dispatch.py:24` `1_000_000` |

§4 of the first review: `_list_facilities` net **DONE** (`server.py:351–369`, test `test_review_bugs.py:259`); `_ssh_config_user` off the loop **DONE** (`connect.py:137`); `None` notice **DONE** (`server.py:255, :305`; test `:301`); SDK boundary **DONE** (`pyproject.toml:6` promotes `globus-compute-sdk` to core with the reason) — the lazy-import ceremony (`binding.py:49, :103, :107, :194–197`) is now just noise; string annotations on `_billed_bounds_note` **DONE** (`notices.py:95`). **Still open:** `mep.py:113–117` swallows the status-API exception without a stderr line; `connect.py:138` reason comment "surface a missing SSH_USER/KEY" is stale (neither is required, `binding.py:112`); `Facility` Protocol still three members (`facility/base.py:17–23`) against ≥12 `getattr` probes (`context.py:108, :116`; `warmth.py:52, :192`; `server.py:589, :619`; `connect.py:46–49, :226`; `scheduler_ops.py:64, :134`; `binding.py:179`; `notices.py:42–46`); `_provision`/`_confirm_worker` return `str` (`warmth.py:177, :106`); `Profile(mode=_env_mode())  # type: ignore[arg-type]` (`server.py:163`); `server.py:784` `# noqa: BLE001` with no reason (siblings `:800`, `:813` have one).

---

## 3. Naming

Still recommended, all four — and cheaper than before because the split localised them:
- `SlurmFacility` → `SshFacility` (+ `_slurm_facility` → `_ssh_facility`): 72 refs incl. 18 vault pages; **M**, after V1.
- `_ensure_warm_runner` → `_block_not_warm`: 3 refs, 0 tests; **S**, any time.
- `ShapeRuntime.no_account` → `no_account_error`: 8 refs; **S**.
- `TaskHandle.future: object` → `concurrent.futures.Future`: one line, and it types `.done()/.cancelled()/.result()` at `warmth.py:67, :76, :323–333`, `server.py:693, :743`; **S**.

New names from the split: `binding`, `warmth`, `scheduler_ops`, `connect`, `login_gate` all say what they hold. `notices` slightly misleads only because it also hosts the three *classifiers* (§1.1) — move them and the name is exact. `_login_runner` is fine (it returns a runner for the login shape); `LoginRunner` as a type alias is fine but lives in the wrong file (`scheduler_ops.py:21`). `context` under-sells itself ("pure data") — either accept "the runtime context and its derived reads" or move the three functions to `warmth`.

---

## 4. Tests

### 4.1 Vacuous patches — three found (the trap the vault warns about, on `main`)
The rule: a `monkeypatch.setattr(M, "name")` only reaches a caller that resolves `name` as a bare global **in `M`**.

1. **`tests/test_server.py:987`** `monkeypatch.setattr(srv, "_local_dill", lambda: "0.3.9")` — the only caller is `notices._worker_notice` (`notices.py:90`), which resolves `notices._local_dill`. The patch is a no-op; the test passes because the dev env's real `dill.__version__` **happens to be 0.3.9** (verified). It will fail the day `dill` bumps, and it will look like a product regression. Patch `notices._local_dill`.
2. **`tests/test_server.py:1407`** `monkeypatch.setattr(srv, "_release_blocks_over_login", _released)` — `_teardown_endpoint` calls `scheduler_ops._release_blocks_over_login` (`server.py:587`). The stub even has the pre-split two-arg signature `(app_, eid)`; the real function runs against the `_FakeRunner` and answers rc=0, so the test passes for the wrong reason. Patch `scheduler_ops._release_blocks_over_login` (as `test_server.py:616–617` already does elsewhere).
3. **`tests/test_catalog_flow.py:682`** `monkeypatch.setattr("hpc_bridge.server._ssh_config_user", <raises AssertionError>)` — the guard is meant to prove a MEP entry never consults `~/.ssh/config`; the caller is `binding._facility_from_entry` → `binding._ssh_config_user` (`binding.py:112`). The assertion can never fire. Patch `binding._ssh_config_user` (as `test_review_bugs` does).

Everything else checks out: `binding.make_catalog` ×26 (bare at `binding.py:126`; module-attr at `server.py:361`, `connect.py:99`), `binding._facility_from_entry` ×19 (`binding.py:145, :148`; `connect.py:137`), `connect.discover_facility_details` ×7 (`connect.py:269`), `warmth._provision` ×6 (`server.py:230`, `connect.py:154`, bare at `warmth.py:285`), `config._control_settings` ×5 (`binding.py:52`, `connect.py:260`), `binding._make_search_client` ×4 (`binding.py:220`), `scheduler_ops._release_blocks_over_login` ×3, `warmth._drop_compute_shape` ×1, `connect._propose_or_ask` ×1 (`connect.py:115, :121`), `binding._ssh_config_user` ×1, the three string patches of `hpc_bridge.login.globus_identity_label` (lazy imports at `server.py:283`, `notices.py:251` resolve at call time), and the 7 `server._run_shell` patches (reach via `_login_runner`, §1.3). The 4 `srv.time.monotonic` patches work because `srv.time` *is* the `time` module — fine, but reads as if patching `server`.

### 4.2 Fixtures (D12) — still duplicated; a `conftest.py` `app` fixture is cheap now
- `_Res`, `_DoneFuture`, `_FakeRunner` defined in `tests/test_server.py:15–110` **and** `tests/test_catalog_flow.py:11–58` (subset); `tests/test_review_bugs.py:31` imports them **from `tests.test_server`** — a test module importing another test module. `tests/fakes.py` exists for exactly this and already holds `FakeFacility`, `FakeCatalog`, `fake_entry`, `fake_mep_entry`.
- `tests/conftest.py:5–12` has one autouse fixture; `tests/test_review_bugs.py:36–40` re-declares the same `HPC_BRIDGE_STATE_DIR` isolation plus the release knobs.
- `AppCtx(facility=FakeFacility(), profile=Profile())` is built **104×** (57/26/11/8/2 across five files); `test_review_bugs.py:43–50` `_warm_app()` is the `warm_app` fixture waiting to be promoted. `_confirm_slurm` (`test_server.py:9`) is repeated inline as `spend_confirmed = True` 5×.
Cost of fixing: one PR, ~200 mechanical line changes, zero product risk.

### 4.3 The first review's 14 untested behaviours — 4 now covered, 10 still untested
**Covered:** spend across a re-bind (`test_review_bugs.py:276`), `_idle_release_s` (`:292`), the `None` notice (`:301`), `_list_facilities` transport classification (`:259`).
**Still untested** (grepped for the distinguishing string/symbol):
1. `_teardown_endpoint` when `facility.teardown` raises — `server.py:593–594` ("manager teardown reported").
2. `_env_float`/`_env_mode` invalid-value fallback — `config.py:108, :114` ("ignoring invalid").
3. `_login_shell` exception path — `server.py:637–638`.
4. `_resolve_task` on a **cancelled** future — `warmth.py:323–327`; `_PendingFuture.cancel()` (`test_server.py:74–75`) is still never called anywhere.
5. `_catalog_facility` MEP entry + conflicting `HPC_BRIDGE_ENDPOINT_ID` — `binding.py:136–141`.
6. `_summarize_pilot` HELD notice text — `scheduler_ops.py:121–124` (only the category is asserted).
7. `_worker_notice` dill skew — has a test but it is the vacuous one (§4.1).
8. `_augment_provisioning_notice` exception path — `scheduler_ops.py:147`.
9. `_reset_session` on busy / needs_confirmation — `server.py:709–722` via `_ready_session`.
10. `_short_control_dir` fallbacks ("nothing short") — `config.py:96–99` (only the short-path identity is tested, `test_catalog_flow.py:793`); `discovery._interface` default / `_env_setup` "already on PATH"; `SearchCatalog._from_cache` corrupt JSON; `lifespan`'s `finally` close (`test_server.py:780` enters `lifespan` but asserts nothing about `runner.close`).

---

## 5. Top 10 actionable items

### Before the V1 tag (all S, all behaviour-neutral or strictly safer)

| # | Where | Change | Risk | Est |
|---|---|---|---|---|
| 1 | `tests/test_server.py:987`, `:1407`; `tests/test_catalog_flow.py:682` | Repoint the three vacuous patches to `notices._local_dill`, `scheduler_ops._release_blocks_over_login` (fix the stub's signature), `binding._ssh_config_user`. | None — makes three tests real; #1 is a latent CI break on a `dill` bump | S |
| 2 | `server.py:283–285` | Take the identity lookup off `app.lock`: use `globus_identity_label(fetch=False)` as `notices.py:254` does, or fetch after the `async with` block. | None functionally; removes a network round-trip under the global lock | S |
| 3 | `server.py:12–143`; `pyproject.toml:52`; `login_gate.py:17–21`; `notices.py:128–132` | Header `# noqa: F401` on the `cost` (`:58`) and `notices` (`:84`) blocks, delete the 88 per-line ones (66 already dead), add `RUF100` to `select`; drop the dead `_needs_login_result` import; delete the `_login_wait_s` shim (callers `connect.py:71`, `login_gate.py:32, :50` → `config.login_wait_s()`). | None | S |
| 4 | `HANDOFF.md:162`; `Modules/server.md` (`:61` and its 46 pre-split `:NNN` refs); `Home.md:26`; `Modules/context.md`; `Modules/config.md`; `context.py:1–8`; `warmth.py:10`; `connect.py:138`; `binding.py:125, :238` | Bring the docs in step (a `CLAUDE.md` rule): 818 not ≈1000; server.md's body still locates `_provision`, `_connect_mep`, etc. in `server`; Home.md's module index omits the nine new modules; config.md's "patch `server._control_settings`" sentence is now wrong; the lock claim in `warmth`; the four stale comments. | None | S |
| 5 | `warmth.py:323–327`; `server.py:593–594`; `server.py:709–722`; `scheduler_ops.py:121–124` | Add the four cheapest missing tests (cancelled future, teardown raises, reset on a busy/unconfirmed session, HELD text). | None | S |
| 6 | `server.py:572–574`, `:171–175`; `scheduler_ops.py:64, :134` | Single `_drop_all_shapes(bank=True)` in the MEP teardown; `_drop_all_shapes(app, bank=False)` in `lifespan`; one `_scheduler_of(app)` for D5. | Low — teardown spend is pinned by `test_server.py:1396` | S |

### After the V1 tag

| # | Where | Change | Risk | Est |
|---|---|---|---|---|
| 7 | `server.py` (≈20 literals), `connect.py` (≈14), `warmth.py` (5), `scheduler_ops.py` (5), `login_gate.py` (5) → `notices.py`; classifiers `notices.py:209–234` → `runner.py` or a `verdicts` leaf | Finish `notices`: hoist every inline agent-facing literal to a named builder (the first review's §1.3 list); move the three classifiers so `warmth` stops importing wording; then rewrite the brittle substring asserts (`test_server.py:137–141`, `test_mep_server.py` ×21) as identity-against-builder, keeping substrings only for the grader-contract phrases (`NO ACCOUNT`, `not confirmed`, `ORPHANED`). | Low, mechanical; touches agent-facing text so do it with the harness graders green | M |
| 8 | `server.py:184–322`; `connect.py:57–197` | Decompose `_ensure_endpoint_up` into the six steps of §2.1 with a `_down()` factory (D10) and validation moved to `warmth`; split `_connect_facility` into `_resolve_entry` (`:91–126`), `_bind_and_warm` (`:144–161`), `_list_allocations` (`:162–197`). | Medium — the two most-exercised paths; 36 `_connect_facility` call sites and ~30 `_ensure_endpoint_up` tests are the net | M |
| 9 | `warmth.py:281`; `context.py:46, :63`; `facility/remote.py:640`, `binding.py:46` + 42 test / 18 vault refs | Renames: `_block_not_warm`, `no_account_error`, `future: Future` (S each); `SlurmFacility`→`SshFacility` (M). While there: `_provision`/`_confirm_worker` return `BlockState \| Literal["needs_confirmation"]` (`lifecycle.py:9` has `BlockState`), and `_env_mode` returns the `Literal` so `server.py:163` loses its `type: ignore`. | Low | S+M |
| 10 | `tests/fakes.py`, `tests/conftest.py`, then the ≈110 `from hpc_bridge.server import …` sites in 6 test files + `agentic/mep_no_account_check.py:95`, `agentic/fakecluster/stretch/driver.py:86`, `agentic/harness/test_pool_and_cluster_ops.py:96` | Move `_Res/_DoneFuture/_PendingFuture/_FakeRunner` to `fakes.py`; `app`/`warm_app` fixtures; drop `test_review_bugs.py:36–40`; then point imports at the owning modules and delete `server`'s re-export blocks. | Low, mechanical, one whole-suite PR | M |

Also-after, not top-10: widen the `Facility` Protocol (or split `SshFacility`/`ComputeOnlyFacility`) so `_drop_dead_pin` becomes a method and the 12 `getattr` probes go; D6 `_err(exc, prefix, limit)`; D8 `_explain_ssh_failure(exc, target)`; D9 `_terminal_suffix(reason)`; the N9 comment sweep and N10 constants; a stderr line in `mep.py:113–117`; delete the now-pointless lazy SDK imports in `binding.py`.

Two decisions for the maintainer, as before: whether `login_gate` stays a module (§1.2), and whether `context` is "data" or "context + derived reads" (§1.1).
