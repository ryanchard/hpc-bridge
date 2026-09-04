# Review 2 2026-09-03 — static analysis

> [!info] Provenance
> Second review round (evening of 2026-09-03), after the server split (#58–#66), the 15 bug fixes (#54), the relock (#55) and the quick wins (#56/#57): read-only subagents against `main` @ 064f141. Filed verbatim; fixes on PR `fix/review2` and the typing PR that follows.

# Static-analysis pass — hpc-bridge after the server split (2026-09-03)

Tools: ruff 0.16.6, mypy 2.3.1 (`uv run --with mypy`), vulture (uvx), Python via `uv run`. Nothing tracked was modified; configs and scratch copies live under the scratchpad (`mypy_A2.toml`, `ruff_proposed.toml`, `import_graph.py`, `xref.py`, `reexports.py`).

Baselines: `uvx ruff check src agentic/harness agentic/scenarios tests` → `All checks passed!`; `uv run python -m pytest -q` → `415 passed, 2 skipped in 11.20s`; every one of the 36 modules imports standalone (`uv run python -c "import hpc_bridge.<m>"` → 36× OK).

---

## 1. Real findings first

### 1A. Type lies and bugs (mypy default: `Found 28 errors in 10 files (checked 36 source files)`)

Per-file: warmth 6, server 5, dispatch 5, catalog/ingest 3, notices 2, facility/remote 2, discovery 2, login_gate 1, connect 1, catalog/entry 1. 26 of 36 files are already clean at default strictness. Triage:

**1. The `str`-typed block-state chain — the leak the review predicted (10 of the 28 errors).**
```
warmth.py:105  async def _confirm_worker(app, shape, *, force) -> str:        # returns only "warm" | "provisioning"
warmth.py:174  async def _provision(...) -> str:                               # domain: BlockState | "needs_confirmation"
warmth.py:280  async def _ensure_warm_runner(app, shape) -> str | None:
notices.py:249 def _cold_outcome(block: str, canary=None) -> ShellOutcome:
dispatch.py:23/39/51  block_state: str
```
mypy output (the same complaint ×10):
```
src/hpc_bridge/dispatch.py:47: error: Argument "block_state" to "ShellOutcome" has incompatible type "str"; expected "Literal['warm', 'cold', 'provisioning']"  [arg-type]   (also :56 :67 :77 :84)
src/hpc_bridge/notices.py:253 / :257 (same)
src/hpc_bridge/warmth.py:197: error: Incompatible types in assignment (expression has type "str", variable has type "Literal['warm', 'cold', 'provisioning']")  [assignment]
src/hpc_bridge/server.py:315: error: Argument "status" to "EndpointStatus" has incompatible type "str"; expected "Literal['up', 'provisioning', 'down', 'needs_confirmation', 'draining']"
src/hpc_bridge/server.py:316: error: Argument "block_state" to "EndpointStatus" has incompatible type "str"; expected "Literal['warm', 'cold', 'provisioning']"
```
Runtime is correct today only because `server.py:674` tests `not_warm == "needs_confirmation"` before `:677 _cold_outcome(not_warm, …)`; nothing stops the next caller from leaking `"needs_confirmation"` into `ShellOutcome.block_state`. **Fix (one alias + 6 signatures):** `lifecycle.py`: `ProvisionResult = BlockState | Literal["needs_confirmation"]`; `_confirm_worker -> BlockState`; `_provision -> ProvisionResult`; `_ensure_warm_runner -> ProvisionResult | None`; `_cold_outcome(block: BlockState, …)`; dispatch `block_state: BlockState`; in `_ensure_endpoint_up` declare `status: Literal[...]` (or add `EndpointStatusKind` next to `EndpointStatus` in models.py). Clears 10 errors.

**2. `TaskHandle.future: object` — 6 errors, and the Future API is unchecked.** `context.py:63`: `future: object  # concurrent.futures.Future from the Executor (opaque, to avoid the SDK import here)`.
```
src/hpc_bridge/warmth.py:67:  error: "object" has no attribute "done"      (also :75 :328)
src/hpc_bridge/warmth.py:322: error: "object" has no attribute "cancelled"
src/hpc_bridge/warmth.py:332: error: "object" has no attribute "result"
src/hpc_bridge/server.py:743: error: "object" has no attribute "result"
```
`Executor.submit` returns a *stdlib* `concurrent.futures.Future`; `from concurrent.futures import Future` + `future: Future[Any]` needs no SDK import. Clears 6.

**3. `connect.py:92` — the registry-lookup branch is invisible to the checker.**
```
src/hpc_bridge/connect.py:92: error: Incompatible types in assignment (expression has type "CatalogEntry | None", variable has type "CatalogEntry")  [assignment]
```
`entry` is first bound at `:81` (`binding._entry_from_details`) so mypy types it non-Optional; after the `:92` error it treats `entry is None` as impossible. With `warn_unreachable` the consequence is explicit: `connect.py:98, :107, :114, :115, :121: error: Statement is unreachable` — i.e. lines 94–125 (registry-wins lookup, `_facility_store` local-discovery cache, both `_propose_or_ask` fallbacks) are never type-checked. **Fix:** declare `entry: CatalogEntry | None` before `if details is not None:` (`:75`).

**4. `EndpointRecord.user: str` / `key_path: str` are lies (state.py:30-31).**
```
src/hpc_bridge/facility/remote.py:751: error: Argument "user" to "EndpointRecord" has incompatible type "str | None"; expected "str"
src/hpc_bridge/facility/remote.py:752: error: Argument "key_path" to "EndpointRecord" has incompatible type "str | None"; expected "str"
```
`SshTarget.user/key_path` are `str | None` by design (remote.py:53-56, "when None, defer to ~/.ssh/config"); the dataclass doesn't validate, so `null` round-trips through `endpoints.json`. Benign today — the only reader is `binding.py:64 rec.login_host` — but a future `f"{rec.user}@{host}"` gives `None@host`. **Fix:** `str | None`.

**5. `authenticate(mode: str | None)` silently coerces bad input to paste mode.**
```
src/hpc_bridge/login_gate.py:29: error: Argument 2 to "to_thread" has incompatible type "str | None"; expected "Literal['browser', 'paste'] | None"
```
Chain: `server.py:373 authenticate(ctx, force, mode: str | None)` → `login_gate.py:38 _authenticate(app, force, mode: str | None)` → `login.py:149 LoginFlow.start(mode: LoginMode | None)` whose body (`:168 if mode == "browser": … else: paste`) turns `"Browser"`/`"auto"` into paste mode with no error. **Fix:** annotate the tool parameter `Literal["browser", "paste"] | None` (reuse `login.LoginMode`, login.py:35) — FastMCP/pydantic then publishes an enum in the tool schema and rejects bad values; the mypy error disappears.

**6. `Client().app` may be `None` (catalog/ingest.py:49-53; binding.py:203).**
```
src/hpc_bridge/catalog/ingest.py:51: error: Item "None" of "GlobusApp | None" has no attribute "add_scope_requirements"  [union-attr]   (also :52 :53)
--check-untyped-defs adds: src/hpc_bridge/binding.py:203: error: Item "None" of "Any | GlobusApp | None" has no attribute "login_required"
```
The curator CLI would die with AttributeError if the SDK ever hands back `None` (client-credentials env). binding's is inside `except Exception → SearchClient()` so it degrades to anonymous. **Fix:** `if app is None: raise SystemExit("…")` in ingest; `if app is None or not app.login_required()` in binding.

**7. `_ready_session` returns an Optional runner it declares non-Optional (server.py:660-680).**
```
src/hpc_bridge/server.py:680: error: Incompatible return value type (got "tuple[GlobusRunner | None, Session]", expected "tuple[GlobusRunner, Session] | ShellOutcome")
```
`runner = _shape_runtime(app, shape).runner` (`:671`) is `GlobusRunner | None`; correct at runtime (an `_ensure_warm_runner` → None means `_runner_for` bound one) but unprovable, and `_run_shell:691 runner.submit(...)` is checked against an Optional. **Fix:** make `_ensure_warm_runner` return the runner on success (`GlobusRunner | ProvisionResult`), or assert with the invariant stated.

**8. Annotation-only (b), one line each.** `catalog/entry.py:191` `access` (inferred `str`, values `"mep"|"ssh"`) → `access: Literal["mep","ssh"]`; `discovery.py:107/:109` → `scheduler: Literal["slurm","pbs"]` at `:74` and `_allocation -> tuple[str | None, Literal["sbank","iris","mybalance"] | None, str | None]` (`:219`); `server.py:750` rebinding `handle = app.tasks.get(task_id)` on a `TaskHandle`-typed name → `handle: TaskHandle | None` (or a new name).

**9. `warn_unreachable` extras worth two lines each** (Config B: `Found 41 errors in 14 files`):
```
src/hpc_bridge/login_flow_manager.py:64: error: Statement is unreachable   # `_server = None` (:24) inferred as type None -> abort() body never checked; annotate `_server: RedirectHTTPServer | None`
src/hpc_bridge/runner.py:154: error: Statement is unreachable             # `self._ex = None` (:103) -> close()'s `self._ex.shutdown(wait=False, cancel_futures=True)` is never checked; annotate `self._ex: Any | None`
src/hpc_bridge/scheduler_ops.py:74: error: Right operand of "or" is never evaluated   # `out.notice or out.phase or "unconfirmed"` — phase is a non-empty Literal; harmless
src/hpc_bridge/warmth.py:58: error: Statement is unreachable               # `if not isinstance(defaults, dict)` given `defaults: dict = {}`; defensive, fine
src/hpc_bridge/endpoint.py:48: error: Statement is unreachable             # sys.platform check evaluated on darwin -> noise (set `platform = "linux"` or don't enable warn_unreachable)
```

**10. Strict-only, on the 7 leaves** (`--strict`: `Found 9 errors in 3 files (checked 7 source files)` — context 2, config 1, notices 6):
```
src/hpc_bridge/config.py:182:  Missing type arguments for generic type "dict"   [type-arg]   (uec: dict)
src/hpc_bridge/context.py:30:  (user_endpoint_config: dict)   src/hpc_bridge/context.py:92: (dict[str, tuple[str, dict]])
src/hpc_bridge/notices.py:29:  Function is missing a type annotation for one or more parameters  (fac=None)
src/hpc_bridge/notices.py:171: (target)   — both on the review's untyped-seams list
src/hpc_bridge/notices.py:73:  Unused "type: ignore" comment   (under ignore_missing_imports)
src/hpc_bridge/notices.py:75:  Returning Any from function declared to return "str | None"   (dill.__version__ -> str(...))
src/hpc_bridge/notices.py:253/:257  (item 1)
```

### 1B. Ruff — real issues (everything else is style; see §3)

- **`config.py:96` S108** `f"/tmp/hpcb-cm-{os.getuid()}"` — predictable third-fallback ControlMaster socket dir; `:89-90 os.makedirs(cd, mode=0o700, exist_ok=True); os.chmod(cd, 0o700)`. On a shared host a pre-created dir owned by another user makes `chmod` raise `PermissionError` (crash inside `_control_settings`) or hosts our socket in their directory. Reached only when both `$STATE/cm` and `~/.hpc-bridge/cm` exceed the ControlPath budget, so low likelihood — but `tempfile.mkdtemp(prefix="hpcb-cm-")` or an `os.stat(cd).st_uid == os.getuid()` check is a two-line hardening. (`discovery.py:155` S108 is a *remote* path default in a proposed config → noise.)
- **`binding.py:37` PLW1510** `subprocess.run(["ssh", "-G", host], capture_output=True, text=True, timeout=10)` — no `check=`; intentional (falls back to `getpass.getuser()`), so write `check=False`.
- **`facility/remote.py:369` ASYNC240** `Path(local_db).read_bytes()` inside `async def seed_storage_db` — a blocking read on the event loop; the trimmed storage.db is KBs, so note only.
- **Shell-injection posture:** `grep -rn "shell=True" src agentic/harness agentic/scenarios tests` → 0 hits; `os.system|os.popen` → 0; S602/S604/S605/S606 → 0 findings. Remote commands go through `ssh_exec → asyncio.create_subprocess_exec(*argv)` with `shlex.quote` (remote.py:465, :496); the injection surface is the *remote* shell, guarded by allowlists `_VALID_PARTITION`/`_VALID_ACCOUNT` (warmth.py:205-206) and `SAFE_ENDPOINT_NAME` (entry.py:39-47). Bandit cannot see that layer; the tests are the gate.
- Tests: `RUF059` unused unpacked ×3 (`test_discovery.py:75 notes`, `test_session_shell.py:124 out1, out2env`), `RUF034` useless if-else (`test_remote_facility.py:610`), `SIM115` open without context manager (`test_session_shell.py:24`), `RUF043` ×2 regex-looking `match=` (`test_credentials.py:83`, `test_remote_facility.py:858`).
- `ISC004` ×5 (`discovery.py:215`, `agentic/harness/provenance.py:81`, `agentic/scenarios/saturation.py:49`, `test_remote_facility.py:592/:636`) — all inspected: intentionally wrapped long strings, no missing comma. Worth enabling anyway (it is the missing-comma tripwire) — 5 parenthesisations.

### 1C. Dead code

Method: vulture at 80/60 + an AST cross-reference (`xref.py`) counting word-boundary refs to every top-level def/class/method in other src files, `tests/`, and `agentic/ skills/ scripts/ hooks/ commands/`, then in-file counts for anything with zero outside refs.

- **`RemoteEndpointCLI.hostname_fqdn` (remote.py:519-527) — dead in production.** In-file refs = 1 (the def); no other src module references it. `provision()` learns the node from `start`'s `echo HPCB_HOST=$(hostname -f)` (remote.py:399 → :791 `eid, host = await self.cli.start(name)`). Only `tests/test_remote_facility.py:862-879` (2 tests) and a fake at `:332` use it. Delete method + 2 tests.
- **`EndpointCLI.config_path` (endpoint.py:28-29) — dead.** In-file 1; `tests/test_local_facility.py:13` defines its *own* `config_path` on a fake and never calls ours. Delete.
- **`CatalogProvider` (catalog/base.py) — orphaned Protocol.** No src module imports `catalog.base` (import edge list: only `catalog.base -> catalog.entry`; `catalog/__init__.py` exports `Allocation, CatalogEntry, CatalogSummary, Compute, Defaults` only). Referenced by `tests/test_catalog_bundled.py:15 isinstance(c, CatalogProvider)` and a docstring in `tests/fakes.py:82`. Give it a job — `make_catalog() -> CatalogProvider` (also fixes ANN201 / `no-untyped-def` at binding.py:209) — or delete it.
- **`TaskHandle.submitted_at` (context.py:67) — write-only.** Written at `warmth.py:306`, `.submitted_at` read 0 times across src/tests/agentic. Either surface it (poll_task's `_running_outcome` could say "running for N s") or drop. `EndpointRecord.provisioned_at` (state.py:33) is likewise never read, but it is on-disk metadata → keep.
- **Dead re-exports in `server.py` — 25 names** that nothing outside `src/` imports from `server` (AST over tests/agentic/skills/scripts/hooks/commands for `from hpc_bridge.server import …`, `server.<name>`, `srv.<name>`, `monkeypatch.setattr(server, "<name>", …)`; `reexports.py` output: `names imported from sibling modules into server.py: 112 / of which UNUSED in server.py body (pure re-exports): 59`):
  ```
  from .binding:       _slurm_facility
  from .config:        CANARY_TIMEOUT_S, CANARY_TTL_S, SYNC_WAIT_S, TASK_CEILING_MARGIN_S, _env_float, _require_env
  from .connect:       _commit_proven_facility, _connect_mep, _drop_dead_pin, _propose_or_ask
  from .cost:          _bank_warm_interval
  from .login_gate:    _start_login_and_wait
  from .notices:       _GLOBUS_USERNAME_RE, _NO_ACCOUNT_MARKERS, _SSH_AUTH_DENIED, _login_notice, _login_wait_s,
                       _needs_login_result, _needs_preauth_result, _spend_floor_guidance
  from .scheduler_ops: _augment_provisioning_notice, _pilot_status_over_login
  from .warmth:        _drop_compute_shape, _provision
  ```
  The other **34 pure re-exports are still imported from `server` by tests** (removal breaks them until the imports move to the leaf module): `PROVISION_GRACE_S, ShapeRuntime, TaskHandle, _authenticate, _catalog_facility, _complete_login, _confirm_worker, _control_settings, _drain_shape_tasks, _entry_from_details, _explain_provision_error, _facility_from_entry, _facility_store, _forget_identity_verdicts, _identity_from_error, _local_dill, _make_search_client, _parse_hhmmss, _pilot_status_cmd, _release_blocks_over_login, _release_cmd, _resolve_scratch_root, _runner_for, _session_endpoint_name, _session_spend, _settle_billing, _short_control_dir, _ssh_config_user, _summarize_pilot, _supported_shapes, _transient_dispatch_failure, _unsupported_entry_reason, make_catalog, make_facility` — in `test_server.py, test_catalog_flow.py, test_review_bugs.py, test_mep_server.py, test_login.py, test_catalog_make.py, agentic/harness/test_pool_and_cluster_ops.py` (`_release_cmd`). Migrate those imports, then the whole re-export block can go.
- **`login_gate.py:17-21`** re-exports `_needs_login_result` with the comment `# noqa: F401 - _needs_login_result is used by connect` — but `connect.py:28` imports it from `.notices` directly. Stale re-export + stale comment.
- **66 redundant per-alias `# noqa: F401` in server.py** (RUF100 under the *project* config, `--extend-select RUF100`: 68 total = 66 F401 in server.py + `server.py:362` and `facility/remote.py:134` unused `BLE001`, both on `except` blocks that re-raise). Ruff applies the opener line's `# noqa: F401` to the whole parenthesized import, so the ones on lines 13-24, 38, 41, 44-47, 54-56, 60, 63-64, 72-74, 88-92, 98-99, 102-106, 109, 114-119, 123-142 are dead directives. `ruff check --extend-select RUF100 --fix` clears all 68.
- **vulture:** `--min-confidence 80` → exactly one finding, a false positive: `login_flow_manager.py:44: unused variable 'format' (100% confidence)` (the stdlib `log_message(self, format, *args)` override). `--min-confidence 60` → 33 findings: 11 MCP tools (`@mcp.tool()`-registered), 8 pydantic fields/validators (`_safe_endpoint_name`, `_valid_uuid`, `_reachable` are `@field_validator`/`@model_validator`), 3 SDK overrides (`background_local_server`, `log_message`, `_get_authorize_url`) — all false positives; the genuine items above came from the cross-reference, not vulture.
- The previous review's `LoginNodeStore.remove` is now called (`connect._drop_dead_pin`). All other 47 defs with zero outside references have in-file counts ≥ 2 (used privately).

---

## 2. Proposed `[tool.mypy]` — passes today

Two facts learned empirically: (i) `strict = true` inside `[[tool.mypy.overrides]]` is applied **globally** by mypy 2.3.1 (Config A produced `Found 57 errors in 12 files` including `state.py`, `binding.py`, `facility/local.py`, none of which were in the override), so the override must spell out the flags; (ii) `warn_unused_ignores` trips `login_flow_manager.py:26/:32` (`# type: ignore[override]` on the SDK subclass — unused, the overrides are compatible) and `notices.py:73`.

```toml
[tool.mypy]
python_version = "3.11"
files = ["src/hpc_bridge"]
ignore_missing_imports = true      # mcp / globus_sdk / globus_compute_sdk stubs are partial
warn_unused_ignores = true
warn_redundant_casts = true
# warn_unreachable deliberately off: endpoint.py:48 is platform-dependent (sys.platform on darwin)

# Strict on the pure leaves (mypy has no per-module `strict`; these are its flags minus one)
[[tool.mypy.overrides]]
module = ["hpc_bridge.context", "hpc_bridge.config", "hpc_bridge.cost", "hpc_bridge.models",
          "hpc_bridge.shapes", "hpc_bridge.lifecycle", "hpc_bridge.profile"]
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
disallow_untyped_calls = true
disallow_untyped_decorators = true
no_implicit_optional = true
warn_return_any = true
strict_equality = true
extra_checks = true
disallow_subclassing_any = true
# disallow_any_generics OFF: 3 bare `dict` annotations (context.py:30, :92; config.py:182) — turn on after fixing them

# Incremental gate: the modules that fail at default level TODAY (28 errors, §1A) — shrink as each is fixed
[[tool.mypy.overrides]]
module = ["hpc_bridge.server", "hpc_bridge.warmth", "hpc_bridge.dispatch", "hpc_bridge.notices",
          "hpc_bridge.connect", "hpc_bridge.login_gate", "hpc_bridge.discovery",
          "hpc_bridge.catalog.entry", "hpc_bridge.catalog.ingest", "hpc_bridge.facility.remote",
          "hpc_bridge.login_flow_manager"]   # last one only for the 2 unused `type: ignore[override]` at :26/:32
ignore_errors = true
```
Result today: `Success: no issues found in 36 source files` (**0 errors**). Proof the per-module flags bite: moving `hpc_bridge.notices` from the ignore list into the strict list yields exactly its 6 known strict errors (`Found 6 errors in 1 file (checked 36 source files)`).

Why these exclusions: `notices` (2 arg-type, item 1), `dispatch` (5, item 1), `warmth` (6 = item 1 + item 2), `server` (5 = items 1, 2, 7, 8), `connect` (1, item 3), `login_gate` (1, item 5), `discovery` (2, item 8), `catalog.entry` (1, item 8), `catalog.ingest` (3, item 6), `facility.remote` (2, item 4). All 28 are ~15 one-line edits (§1A items 1-8); after that PR the second override block is deleted and the whole package is gated at default level with the 7 leaves strict. Variant: drop `warn_unused_ignores` and `login_flow_manager` leaves the list (also 0 errors: `Success: no issues found in 36 source files`).

Counts for the record: default 28/10 files; `--check-untyped-defs` 29 (+`binding.py:203`); `--strict` on 7 leaves 9/3 files; Config B (default + `warn_unused_ignores` + `warn_unreachable`) 41/14 files; Config A2 above 0.

---

## 3. Ruff — stricter families (counts are what each family ADDS on top of today's `E,F,I,B,BLE,A` config; `--select ALL` total is `Found 5559 errors`, of which S101 1127, ANN 1356, D ~1200, PT018 281, PLC0415 293)

| Family | total | in `src` | Verdict |
|---|---|---|---|
| **UP** (pyupgrade, py311) | 14 | 7 | Enable — all autofixable: UP035 ×6 `typing.Callable`→`collections.abc`, UP037 ×5 quoted annotations, UP017 ×3 `datetime.UTC`. |
| **RET, PIE, PLE, T10, ICN, PGH, G, LOG, PYI, SLOT, ERA** | 0 | 0 | Enable free — zero findings today, so they cost nothing and become tripwires (ERA = commented-out code: 0 is worth locking in). |
| **SIM** | 11 | 7 | Enable with `SIM105` ignored — 7 of 11 are "use `contextlib.suppress`", which would delete the codebase's reasoned `except Exception:  # noqa: BLE001 - <why>` comments. Rest: SIM108 ×1, SIM300 ×2 (autofix), SIM115 ×1 (real, test). |
| **RUF** | 86 | 73 | Enable with `RUF001-003` ignored (6 deliberate `×`/`–`/`−` in docstrings). Leaves 80: RUF100 ×68 (autofix — the ornamental noqa), RUF005 ×5 (harness list concat, style), RUF059 ×3, RUF043 ×2, RUF034 ×1, RUF022 ×1 (autofix). |
| **PLW1510** | 6 | 1 | Enable — explicit `check=` on `subprocess.run` (binding.py:37 + 5 in tests/harness). The rest of PL is noise here: PLC0415 ×293 (lazy imports are the SDK-boundary discipline), PLR2004 ×73, PLR09xx complexity (`_connect_facility` 24 branches / 12 returns / 58 statements — informative, not a gate). |
| **S** (bandit) | 1127 S101 + 13 S110 + 11 S108 + 6 S603 + 5 S607 + 3 S105 + 2 S112 + 2 S106 + 1 S701 | 13 S110, 2 S108, 2 S112, 1 S603, 1 S607 | Enable only the zero-cost tripwires `S102, S602, S604, S605, S606` (all 0 today) plus `S108` (2 in src, one worth fixing — §1B; 9 in tests are `/tmp/k` fixtures → per-file-ignore). Skip S101 (tests), S110/S112 (every one of the 15 sites already carries a reasoned `# noqa: BLE001`; S110 duplicates BLE001's job), S603/S607 (list argv + PATH lookup of `ssh` is the intent), S105/S106/S701 (test fixtures). |
| **ISC** | 5 | 1 | Optional — ISC004 is the missing-comma tripwire; all 5 hits today are wrapped strings (parenthesise them). |
| **ASYNC** | 9 | 6 | Skip — ASYNC109 ×7 is the `timeout=` parameter convention (`ssh_exec`, `_gce`, `_probe_executor`, `canary`) feeding `asyncio.wait_for`/`to_thread(fut.result, t)`; ASYNC240 ×2 trivial (§1B). |
| **TC** (typing-only imports) | 32 | 29 | Skip for now — mechanical, and pydantic models need `runtime-evaluated-base-classes = ["pydantic.BaseModel"]`; the cycle-avoidance benefit is marginal since the graph is already acyclic (§5). |
| **PTH** | 14 | 7 | Skip — `os.path`/`Path` mix is style (expanduser ×3, chmod ×3, makedirs ×1). |
| **ARG** | 295 | 5 | Skip — the 5 src hits are Protocol/override params (`mep.py:102/:120 profile`, `login_flow_manager.py:44 format,*args`, `server.py:147 server`); tests 290 are fixtures/lambda stubs. |
| **PERF, FURB, C4, FLY, DTZ** | 1/6/3/1/2 | 0/0/0/0/1 | Harmless; FURB/C4 fine to add (3 autofixes in harness). DTZ011 `date.today()` at binding.py:283 for a session entry is the intent. |
| **D, ANN, COM812, CPY, INP001, TID252, FBT, PT, T201, SLF001, EM, TRY** | thousands | — | Noise for this codebase. Specific checks: T201 ×7 in src — all `print(..., file=sys.stderr)` (verified each; an MCP stdio server must never print to stdout, which is what T201 would guard, but all 7 are correct). ANN in src = 39 (the untyped-seams backlog; mypy `disallow_untyped_defs` covers it better). COM812 conflicts with the formatter. TID252: relative imports are the package convention. |

**E501 (line length 120) now:** 71 lines total — `src` 13, `tests` 39, `agentic/harness` 9, `agentic/scenarios` 10. In src: server.py ×5 (:275 :277 :278 :313 :587), connect.py ×3 (:158 :165 :171), scheduler_ops.py ×2 (:129 :140), state.py:83, discovery.py:38 (the probe shell script, 144 chars), catalog/entry.py:60 (159 chars, the longest). Roughly 30 of the 71 are string literals/comments, 41 code. **Small fix:** enabling E501 with `per-file-ignores = {"tests/*" = ["E501"], "agentic/*" = ["E501"]}` leaves 13 lines to wrap; fixing all 71 is under an hour.

**Concrete proposal** (tested as `ruff_proposed.toml`):
```toml
[tool.ruff.lint]
select = [
  "E", "F", "I", "B", "BLE", "A",           # today's set, E501 now ON
  "UP", "RET", "PIE", "PLE", "PLW1510",
  "SIM", "RUF", "ISC", "ERA", "T10", "PGH",
  "S102", "S602", "S604", "S605", "S606", "S108",
]
ignore = ["SIM105", "RUF001", "RUF002", "RUF003"]

[tool.ruff.lint.per-file-ignores]
"tests/*" = ["E501", "S108"]
"agentic/*" = ["E501"]
```
Measured against the tree today: `Found 124 errors. [*] 85 fixable with the --fix option`; after `--fix` on a scratch copy: `Found 133 errors (94 fixed, 39 remaining)` — the 39 = 13 E501 (src) + 6 PLW1510 + 5 RUF005 + 5 ISC004 + 3 RUF059 + 2 S108 + 2 RUF043 + SIM108 + SIM115 + RUF034 (residual list by file is in the scratchpad run log). One sitting.

---

## 4. Dead code — summary table

| What | Where | Evidence | Action |
|---|---|---|---|
| `hostname_fqdn` | remote.py:519 | in-file refs 1; node comes from `start`'s `HPCB_HOST` echo (:399/:791); only tests call it | delete + 2 tests |
| `EndpointCLI.config_path` | endpoint.py:28 | 0 callers (test fake defines its own) | delete |
| `CatalogProvider` | catalog/base.py:10 | no src importer; 1 test isinstance | type `make_catalog() -> CatalogProvider` or delete |
| `TaskHandle.submitted_at` | context.py:67 | written warmth.py:306, 0 reads | surface in running notice or drop |
| 25 dead re-exports | server.py:12-143 | AST: nothing outside src imports them from `server` | delete; migrate the other 34 test imports to leaf modules, then drop the block |
| `_needs_login_result` re-export | login_gate.py:17-21 | connect.py:28 imports from `.notices` | delete + fix comment |
| 66 `# noqa: F401` + 2 `BLE001` | server.py, remote.py:134 | RUF100 under project config: 68 | `ruff --extend-select RUF100 --fix` |
| vulture ≥80 | — | 1 finding, false positive | nothing |

---

## 5. Import graph verdict — the layering holds

Method: `import_graph.py` (AST; resolves relative and `from . import x` submodule imports; separates top-level vs in-function vs `TYPE_CHECKING` edges; Tarjan SCC). `edge count: 124 modules: 36`.

- **Nobody imports `server`:** `=== who imports server? === (none)` — the only textual hits are the two docstrings (`server.py:497`, `context.py:7`).
- **Cycles among top-level runtime edges: `none`.** Including lazy edges, exactly one SCC: `hpc_bridge.login <-> hpc_bridge.login_flow_manager` — both directions are function-local imports (`login.py:173/:221/:234` → lfm; `login_flow_manager.py:83/:98` → `login.required_scopes` / `_default_app_factory`), deliberate per lfm's docstring ("kept separate from login.py so that module stays SDK-import-free"). No import-time cycle; breakable later by passing `required_scopes`/`_default_app_factory` as parameters.
- **Layering among the new split modules (top-level edges):**
  ```
  context        -> (no leaf deps)          # depends only on pre-existing: catalog.entry, facility.base, lifecycle, login, profile, runner, shapes
  config         -> (no leaf deps)          # -> catalog.search (PUBLIC_REGISTRY_INDEX), state (lazy)
  cost           -> context
  notices        -> config, context, cost   # + login (lazy, :251), models, runner
  scheduler_ops  -> config, context
  binding        -> config                  # + catalog.*, endpoint, facility.*, models, state
  warmth         -> config, context, cost, notices
  login_gate     -> context, notices, warmth
  connect        -> binding, config, context, login_gate, notices, scheduler_ops, warmth
  server         -> all of the above
  ```
  A clean DAG: models/profile/shapes/runner/login/credentials/state/endpoint/session_shell → lifecycle, facility.*, catalog.* → context, config → cost → notices → warmth, scheduler_ops, binding → login_gate → connect → server.
- **Two edges worth a note (not violations):** `config -> catalog.search` is a top-level import of an implementation module by a "config" leaf just to get the `PUBLIC_REGISTRY_INDEX` constant (`config.py:12`) — reverse it (constant lives in config, search imports it). `notices -> login` at top level (for `LoginStart`) plus a lazy `globus_identity_label` import — fine, `login.py` is SDK-free.
- **Lazy imports (14)** are all SDK-boundary or cycle-avoidance: binding → facility.remote/mep/state/catalog.search, config → state, login ↔ login_flow_manager, notices/server → login. One `TYPE_CHECKING` edge: `facility.remote:37 -> catalog.entry`.
- **Standalone import:** all 36 modules import in a fresh interpreter (`OK` ×36).

---

### Priority order if this becomes a PR
1. §1A items 1-3 (ProvisionResult alias; `Future[Any]`; `entry: CatalogEntry | None`) — 3 changes, 17 of 28 mypy errors, and the connect lookup branch becomes checked.
2. §1A items 4-8 — the remaining 11 errors, ~10 one-liners; then delete the `ignore_errors` block and gate the whole package.
3. `ruff --extend-select RUF100 --fix` + the 25 dead re-exports + the `login_gate` stale re-export; migrate the 34 test imports to leaf modules and drop the block.
4. Adopt the ruff config in §3 (94 autofixed, 39 manual incl. 13 E501).
5. Delete `hostname_fqdn`, `config_path`; decide `CatalogProvider` and `submitted_at`.
6. `config.py:96` `/tmp` fallback hardening.
