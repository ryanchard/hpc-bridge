# Review 2026-09-03 — dependencies & toolchain

> [!info] Provenance
> Produced by a read-only dependency-currency audit subagent on 2026-09-03 against the tree at `feat/agentic-stranger-scenarios` (main after PR #50 + the sweep work). Filed verbatim so the findings outlive the session; the fixes made in response are on PR `fix/review-bugs`. Line numbers refer to that tree.

# hpc-bridge — dependency & toolchain currency audit

Audited 2026-09-03 against `feat/mep-m1` @ `b311b90` (working tree clean before and after; nothing in the repo was modified). `uv.lock` was last regenerated in commit `57b1ee5` (file mtime 2026-06-25) — it is ~10 weeks stale.

**How versions were verified (no memory used for version numbers):**
- Every one of the 73 third-party packages in `uv.lock` (+ `claude-agent-sdk`, `hatchling`, `uv`, `ruff`, `mypy`, `pytest-cov`, and the seed's `globus-compute-endpoint==4.15.0`) was checked live against `https://pypi.org/pypi/<name>/json` (latest + release dates) and `https://pypi.org/pypi/<name>/<locked>/json` (OSV-fed `vulnerabilities`, `requires_dist`). Script: `scratchpad/pypi_audit.py`; raw JSON: `scratchpad/pypi_audit.json`.
- `uv lock --upgrade --dry-run` (no write) to see what a relock changes; `uvx --from uv==0.12.9 uv lock --check` / `--dry-run` to confirm the newest uv keeps the lock byte-identical.
- Two throwaway venvs in the scratchpad on the **latest resolvable** versions (`uv pip install -U -e '.[dev,integration]' globus-compute-endpoint`): **Python 3.11 → 463 passed / 2 skipped**; **Python 3.14.6 → 455 passed / 2 skipped** (`tests/` + `agentic/harness/test_invariants.py` [+ `test_pool_and_cluster_ops.py` on 3.11]). Logs: `scratchpad/latest_deps_test.log`, `scratchpad/py314_test.log`.
- Changelogs / EOL / registry sources are cited inline; the full URL list is in §7.

---

## 1. Headline findings

| # | Finding | Severity | Where |
|---|---------|----------|-------|
| 1 | **7 locked packages carry published advisories** (cryptography ×7 IDs, starlette ×4, mcp ×2, setuptools ×2, python-multipart ×2, click ×1, pydantic-settings ×1). All are **transitive**, none is in an hpc-bridge code path that is reachable over the stdio MCP transport, and **all are cleared by a plain `uv lock --upgrade`** (tested green). | Medium (hygiene) / Low (exploitability) | `uv.lock` — see §2.3 |
| 2 | **Node 20 in the agentic jail is EOL (2026-04-30) and below the Claude Code CLI's `engines.node >=22.0.0`.** Moreover `claude-agent-sdk` now **bundles the CLI** (0.2.152 bundles 2.1.260) and the harness passes no `cli_path`, so the npm-installed CLI is dead weight: the Node + npm layers can be deleted outright. | High for the harness image | `agentic/Dockerfile:12-13,20` |
| 3 | **Client SDK / facility-MEP skew.** We lock `globus-compute-sdk==4.13.0` (wire-protocol package `globus-compute-common==0.7.1`) while the MEP we target runs `globus-compute-endpoint==4.15.0` (`common==1.0.0`). It works today (M1 validated live) and the web service's own floor is only `min_sdk_version 1.0.0a6`, but aligning the client to ≥4.15 removes the last cross-version seam. **The seed pin `==4.15.0` itself must stay** — it must equal the MEP's version exactly (§3.1). | Medium | `pyproject.toml:11`, `src/hpc_bridge/catalog/seed/globus-cluster.yaml:27` |
| 4 | **`mcp` 1.x is now maintenance-only** (2.0.0 shipped 2026-07-28; `FastMCP` → `MCPServer`, `mcp.server.fastmcp` import path removed). Our `<2` pin is correct today; the migration surface is 4 lines in `server.py`. Plan it, don't do it in the V1 sprint. | Low now / Medium in 6 months | `pyproject.toml:6`, `src/hpc_bridge/server.py:15,433,467,2254` |
| 5 | **`asyncio_default_fixture_loop_scope` is unset** → pytest-asyncio emits a `PytestDeprecationWarning` at configure time on every run, which makes `-W error::DeprecationWarning` unusable as a CI gate. One-line fix. | Low | `pyproject.toml:39-41` |
| 6 | **No CI, no linter/type-checker config, no `.python-version`, three unpinned "latest" fetches in Dockerfiles** (`uv:latest` ×2, `@anthropic-ai/claude-code`, `claude-agent-sdk`, `mariadb:11`). Builds are not reproducible across days. | Medium | §3.3, §4 |
| 7 | **Fake-cluster Slurm 23.11.4 (Ubuntu 24.04) is out of upstream support.** SchedMD's 2026-09-02 CVE batch (CVE-2026-65107/-65108/-65109/-65138/-65139/-65140/-65165) was fixed only in 26.05.4 / 25.11.8 / 25.05.9. Exposure is a local throwaway compose network with only sshd published on 2222, so this is a currency note, not an incident. Ubuntu 26.04 LTS ships `slurm-wlm 25.11.2-1ubuntu2`. | Low | `agentic/fakecluster/Dockerfile:10,14` |

---

## 2. Python dependencies — `pyproject.toml` vs `uv.lock` vs latest

### 2.1 Declared dependencies

| Package | Declared (`pyproject.toml`) | `uv.lock` pins | Locked date | Latest | Latest date | Δ that matters | Our usage on latest |
|---|---|---|---|---|---|---|---|
| **mcp** | `>=1.23,<2` (line 6) | **1.27.2** | 2026-05-29 | **1.29.1** (1.x line) / **2.1.1** (2.x) | 2026-08-24 / 2026-08-25 | 1.28.1 fixes GHSA-vj7q-gjh5-988w (deprecated WebSocket transport skipped Host/Origin checks — we use stdio). 1.29.0: `Context.report_progress()` routed to originating stream, request-body limits on HTTP, tool-name validation. 1.29.1: FastMCP `Settings` completed at import time. **2.0.0 (2026-07-28)**: `FastMCP`→`MCPServer`, `mcp.server.fastmcp` raises `ModuleNotFoundError`, transport kwargs moved to `run()`, `Context.client_id` removed, httpx→httpx2, protocol types split into `mcp-types`; "v1.x is in maintenance mode and will only receive security fixes". | Green on 1.29.1 (both venvs). Uses only `FastMCP`, `Context`, `@mcp.tool()`, `ctx.request_context.lifespan_context`, `lifespan=`, `mcp.run()` — all of which the 2.x migration guide lists as unchanged **except the class name/import path**. |
| **pydantic** | `>=2` (line 6) | **2.13.4** | 2026-05-06 | **2.13.5** | 2026-08-28 | Bug-fix only (validator reuse, GC traversal in pydantic-core 2.46.5). 2.14.0a1 (pre-release) drops Python 3.9 and `eval_type_backport()`. No 3.0 exists. | Uses `BaseModel`, `Field(description=…)`, `field_validator`, `model_validator(mode="after")`, `model_dump(mode="json")`, `model_dump_json`, `model_validate` — all stable v2 API (`models.py`, `catalog/entry.py`, `catalog/search.py`, `catalog/bundled.py`, `catalog/ingest.py`, `server.py:1171`). No v1-isms (`.dict()`, `class Config`, `@validator`) found. |
| **pyyaml** | `>=6` (line 6) | **6.0.3** | 2025-09-25 | 6.0.3 | — | Current. | `yaml.safe_load` / `safe_dump` only (`catalog/bundled.py:47`, `facility/local.py:47`, `facility/remote.py:776`). |
| **globus-sdk** | `>=4.4,<5` (line 6) | **4.8.1** | 2026-06-16 | **4.9.0** | 2026-08-10 | 4.9.0: no breaking changes, removals or deprecations; nothing touching `NativeAppAuthClient`, `oauth2_start_flow`, `oauth2_exchange_code_for_tokens`, `AuthClient.userinfo`/`get_identities`, `UserApp`/`GlobusAppConfig`, `ComputeClientV3`, `SearchClient`, `SearchScopes`, `SQLiteTokenStorage`/`TokenStorageData`, `login_flows.LocalServerLoginFlowManager`. **No 5.0 or 5.0 plan mentioned.** Min Python 3.9. | Green on 4.9.0. `oauth2_userinfo`→`userinfo` rename is already handled (`login.py:265`, `agentic/mep_no_account_check.py:76`, guarded by `tests/test_login.py:392-409`). One **private-path import** to watch on every bump: `globus_sdk.login_flows.local_server_login_flow_manager.local_server` (`RedirectHandler`, `RedirectHTTPServer`) at `login_flow_manager.py:38` and `tests/test_login.py:283`, plus the private `_auth_code_queue` at `login_flow_manager.py:63` (already wrapped in `except Exception`). |
| **pytest** (dev) | `>=8` (line 9) | **9.0.3** | 2026-04-07 | **9.1.1** | 2026-06-19 | 9.1.0 breaking: only `--doctest-modules` autouse-fixture double-execution (we don't use it). Deprecations (removal in 10): `getfixturevalue()` in teardown, non-Collection iterables in `parametrize`, `config.inicfg`, `--pastebin`, `pytest.console_main()`. `[tool.pytest.ini_options]` **remains supported** ("both tables cannot be used at the same time"). New: `--max-warnings`. | Green on 9.1.1 (3.11 and 3.14). |
| **pytest-asyncio** (dev) | `>=0.23` (line 9) | **1.4.0** | 2026-05-26 | 1.4.0 | — | Current. 1.0.0 removed the `event_loop` fixture (not used); 1.3.0 dropped Py3.9; **1.4.0 requires pytest ≥ 8.4.0** (so `pytest>=8` is technically too loose) and deprecates overriding `event_loop_policy`. | `asyncio_mode = "auto"` fine. **Missing `asyncio_default_fixture_loop_scope`** → `PytestDeprecationWarning` at configure (§4.2). |
| **jinja2** (dev) | `>=3` (line 9) | **3.1.6** | 2025-03-05 | 3.1.6 | — | Current; 3.1.6 is the CVE-2025-27516 fix release. `globus-compute-endpoint` itself requires `jinja2>=3.1.6,<3.2`. | `tests/test_remote_facility.py:102` renders with `StrictUndefined` exactly like the endpoint manager. |
| **globus-compute-sdk** (integration) | `>=4` (line 11) | **4.13.0** | 2026-06-17 | **4.16.0** | 2026-08-26 | 4.14.0: graceful UEP shutdown; template vars `parent_config`/`user_runtime`/`mapped_identity` deprecated in favour of `_GC.*`. **4.15.0: Python 3.14 support; endpoint moves to Pydantic v2; `globus-compute-common` 0.7.1→1.0.0.** 4.16.0: `_GC.env`, `paths: endpoint_dir/endpoint_log`, **`email` now required for multi-user endpoints (fails to parse on <4.16.0 if present)**, "endpoint and worker Parsl versions no longer have to be strictly in sync", removed dead `log_dir/stdout/stderr` options, parsl pin relaxed to `>=2026.7.27`. `globus-sdk<5,>=4.4.0` and `dill==0.3.9` unchanged across 4.13→4.16. | Green on 4.16.0. Uses `Executor(endpoint_id, user_endpoint_config=…)`, `.submit(ShellFunction(cmd, walltime=…))`, `.shutdown(wait=False, cancel_futures=True)`, `Client(do_version_check=False).app`, `ComputeAuthClient`, `DEFAULT_CLIENT_ID`, `get_token_storage` (`runner.py:53-154`, `server.py:370-374`, `credentials.py:20-54`, `login.py:282-283`, `login_flow_manager.py:21,80,95`). None renamed/removed. Our UEP templates (`facility/remote.py:546-611`) use **none** of the deprecated standalone vars or removed log options. |
| **globus-compute-endpoint** (integration, linux) | `>=4; sys_platform=='linux'` (line 16) | **4.13.0** | 2026-06-17 | **4.16.0** | 2026-08-26 | Same changelog. Its own pins moved: `psutil<6`→`<8`, `click<8.2`→`>=8.3.3,<8.4`, `pyzmq<=26.1`→`<=28`, `parsl==2026.4.20`→`>=2026.7.27`. Always pins `globus-compute-sdk==<same version>`. | Only shelled out to (`endpoint.py:49`, `facility/remote.py:295`). |
| **hatchling** (build) | unpinned (line 24) | — (build-time) | — | **1.32.0** | 2026-08-11 | Unpinned build backend = non-reproducible editable builds. | — |

Locked-vs-latest for the **whole** lock (73 packages): 36 stale, 37 current. `uv lock --upgrade --dry-run` changes exactly these 36 (all resolve inside the current specifiers): annotated-types 0.7.0→0.8.0, anyio 4.13.0→4.15.0, cachetools 7.1.4→7.1.8, certifi 2026.5.20→2026.7.22, cffi 2.0.0→2.1.1, charset-normalizer 3.4.7→3.5.1, click 8.1.8→8.3.3, cryptography 48.0.0→50.0.1, filelock 3.29.0→3.32.5, globus-compute-common 0.7.1→1.0.0, globus-compute-endpoint 4.13.0→4.16.0, globus-compute-sdk 4.13.0→4.16.0, globus-sdk 4.8.1→4.9.0, idna 3.18→3.19, mcp 1.27.2→1.29.1, packaging 26.2→26.3, parsl 2026.4.20→2026.8.10, psutil 5.9.8→7.2.2, pydantic 2.13.4→2.13.5, pydantic-core 2.46.4→2.46.5, pydantic-settings 2.14.1→2.15.0, pygments 2.20.0→2.21.0, pytest 9.0.3→9.1.1, python-dotenv 1.2.2→1.2.3, python-multipart 0.0.30→0.0.32, pywin32 311→312, pyzmq 26.1.0→27.2.0, rpds-py 2026.5.1→2026.6.3, setuptools 82.0.1→84.0.0, sse-starlette 3.4.4→3.4.10, starlette 1.2.1→1.6.0, typeguard 4.5.2→4.6.0, types-cachetools →20260713, typing-extensions 4.15.0→4.16.0, typing-inspection 0.4.2→0.4.4, uvicorn 0.48.0→0.52.4.

Notable transitive packages that stay on old majors *by upstream pin*, not by us: `dill==0.3.9` (SDK pins it; latest 0.4.1), `tblib==1.7.0` (SDK pins; latest 3.2.2), `pika<1.4` (SDK; latest 1.4.4), `python-daemon<3` (endpoint; latest 3.1.2), `rich<15` (SDK; 15.0.0 exists), `marshmallow` 3.26.2 (latest 4.3.1).

### 2.2 Version drift facts about the local environment (not the lock)

- `uv 0.10.4 (2026-02-17)` on this Mac vs latest **0.12.9 (2026-09-01)** — https://github.com/astral-sh/uv/releases/tag/0.12.9. `uv 0.12.9 lock --check` passes and `--dry-run` reports "No lockfile changes detected": the lock (`revision = 3`) is **not** rewritten by the new uv.
- `.venv` is **Python 3.13.12** while `agentic/Dockerfile` builds on 3.11 and the fake cluster runs 3.12.3.
- `.venv` contains `globus-compute-endpoint 4.12.0` next to `globus-compute-sdk 4.13.0` — an inconsistent pair (4.12.0 requires `globus-compute-sdk==4.12.0`) that `uv sync` cannot have produced on macOS (the marker excludes it); it was hand-installed. Harmless for the unit tier, but `uv sync --extra integration` won't fix it and `hpc_bridge/endpoint.py` refuses to run it on non-Linux anyway.

### 2.3 Security advisories in the locked set (all transitive; all fixed by relock)

| Package | Locked | Advisory | Fixed in | Pulled in by | Reachable from hpc-bridge? |
|---|---|---|---|---|---|
| cryptography | 48.0.0 | GHSA-537c-gmf6-5ccf (bundled OpenSSL) | 48.0.1 | globus-sdk, pyjwt[crypto] (via globus-sdk & mcp) | Generic; JWT id-token verification uses it — **bump**. |
| cryptography | 48.0.0 | GHSA-m2h6-j472-rp4c / PYSEC-2026-3554 (name-constraint wildcard bypass), GHSA-jwv3-5hgf-82ww / PYSEC-2026-3553 (exponential chain resolution) | 49.0.0 | same | X.509 verifier — not used by us (TLS is `requests`/OpenSSL). |
| cryptography | 48.0.0 | GHSA-g6cj-pr64-35w5 / PYSEC-2026-3552 (PKCS#7 decrypt oracle) | 50.0.0 | same | Not used. |
| mcp | 1.27.2 | GHSA-vj7q-gjh5-988w / PYSEC-2026-3483 (WebSocket server transport skips Host/Origin validation) | 1.28.1 | direct | **No** — stdio only (`server.py:2254 mcp.run()`). |
| starlette | 1.2.1 | GHSA-jp82-jpqv-5vv3 / PYSEC-2026-248 (`request.url` path not validated), GHSA-82w8-qh3p-5jfq / PYSEC-2026-249 (form limits ignored for urlencoded) | 1.3.0 / 1.3.1 | mcp, sse-starlette | No HTTP server is started. |
| python-multipart | 0.0.30 | GHSA-v9pg-7xvm-68hf / PYSEC-2026-3040 (negative Content-Length) | 0.0.31 | mcp | Same — HTTP form parsing only. |
| pydantic-settings | 2.14.1 | GHSA-4xgf-cpjx-pc3j (`NestedSecretsSettingsSource` path issue) | 2.14.2 | mcp 1.x | Not used by us. |
| click | 8.1.8 | PYSEC-2026-2132 (`click.edit()` command injection) | 8.3.3 | uvicorn (via mcp); globus-compute-endpoint now requires `>=8.3.3` | We never call `click.edit`. |
| setuptools | 82.0.1 | PYSEC-2026-3447 / GHSA-h35f-9h28-mq5c (sdist MANIFEST.in excludes ignored) | 83.0.0 | Linux `integration` extra only (no parent on macOS per `uv tree --invert`) | Build-time only. |

Advisory links: `https://osv.dev/vulnerability/<ID>` for each ID above. No advisories on the latest versions of any locked package, and none on `globus-sdk`, `globus-compute-*`, `pydantic`, `pyyaml`, `jinja2`, `pytest`, `pytest-asyncio`.

---

## 3. Version pins embedded outside `pyproject.toml`

### 3.1 `src/hpc_bridge/catalog/seed/globus-cluster.yaml:27` — `globus-compute-endpoint==4.15.0`

```
env_setup: "[ -d $HOME/hpc-bridge/gce-venv ] || uv venv $HOME/hpc-bridge/gce-venv; . $HOME/hpc-bridge/gce-venv/bin/activate; uv pip install -q globus-compute-endpoint==4.15.0"
```

- **Current endpoint release: 4.16.0 (2026-08-26)** — https://pypi.org/pypi/globus-compute-endpoint/json. 4.15.0 was released 2026-07-15; it is one minor behind.
- **What the compatibility matrix actually says** (no single "must match" sentence exists; this is assembled from the authoritative pieces):
  1. *Service floor* — `https://compute.api.globus.org/v2/version?service=all` returns `{"api":"1.55.1","min_sdk_version":"1.0.0a6","min_ep_version":"1.0.0a0"}`: the web service accepts any 1.x+ client or endpoint. There is **no service-enforced SDK↔endpoint version lock**.
  2. *Endpoint↔its own SDK* — `globus-compute-endpoint` always pins `globus-compute-sdk==<identical version>` (verified on 4.12.0, 4.13.0, 4.15.0, 4.16.0 `requires_dist`), so the remote side is self-consistent by construction.
  3. *Manager↔worker (the failure the seed comment describes)* — the endpoint manager (MEP, 4.15.0) and the worker started by `worker_init` must run the same endpoint code; a skew produces the cryptic `process_worker_pool.py: -P/--port` failure recorded in the seed comment (lines 23-26). The docs concur: "We recommend matching the Python version and globus-compute-endpoint module version on the worker environment and on [the endpoint]" (https://globus-compute.readthedocs.io/en/latest/endpoints/endpoints.html). 4.16.0's changelog says "The endpoint and worker Parsl versions no longer have to be strictly in sync" — that loosens the *Parsl* half of this coupling from 4.16 on, but the endpoint package itself must still match. **Therefore the pin is correct and must track the MEP's version exactly** (currently 4.15.0, set by the globus-cluster admin, not by us).
  4. *Client SDK↔remote endpoint* — the only true cross-version relationship. Three things cross the wire: (a) the task payload serialized by **dill** — `dill==0.3.9` is pinned identically by SDK 4.13.0, 4.14.0, 4.15.0 and 4.16.0, so there is no serializer skew in the 4.13-vs-4.15 pairing; `server.py:687-707` already warns on dill skew via the canary; (b) the AMQP result envelope defined in **globus-compute-common** — 0.7.1 for SDK ≤4.14, **1.0.0 for SDK ≥4.15** (so our locked client is on the old wire package while the MEP is on the new one; it interoperates in practice — M1 validated live — but it is the one seam a relock closes); (c) `user_endpoint_config`, validated by the MEP's schema (unchanged by 4.14-4.16 for the keys we send). The SDK docs' only hard recommendation is Python-level: "We strongly recommend that you use the same Python version as the target [endpoint]… Even a single number difference in Python minor versions (e.g., from 3.12 → 3.13)" can break deserialization (https://globus-compute.readthedocs.io/en/latest/sdk/executor_user_guide.html) — for **ShellFunction** payloads this risk is much smaller than for arbitrary callables, and `runner.py:26` / `server.py:696-707` already surface the worker's Python+dill for exactly this reason.
- **Does `4.15.0` still work with the SDK we lock (4.13.0)?** Yes — nothing in 4.14→4.16 changed the SDK-facing API, dill is identical, and the service floor is 1.0. It is not "wrong", it is one `common` major behind. Recommendation: relock so the client is ≥4.15 (ideally 4.16.0), **keep the seed at `==4.15.0` until the MEP admin upgrades**, and when they move to 4.16.0 (note: 4.16.0 **requires an `email:` field in the MEP's `config.yaml`** and a config containing it "will fail to parse (fail to start!) on versions older than 4.16.0") bump line 27 in the same change (`HANDOFF.md:60` documents the coupling; the Ansible source of truth is in `~/Projects/globus-cluster-docs/globus-admin`).
- Related unpinned installs that self-provision **latest** (4.16.0 today) on a fresh login node: `src/hpc_bridge/discovery.py:21` (`_UV_ENV_SETUP`, used for un-indexed facilities) and `agentic/fakecluster/README.md:103`. For our *own* SSH-provisioned endpoints manager and worker share one NFS venv so they stay consistent with each other; the only skew is client-SDK↔endpoint, which is benign per (4). If you want determinism, template the client SDK version into `_UV_ENV_SETUP` (`globus-compute-endpoint=={globus_compute_sdk.__version__}`) — trade-off: a facility with an older Python could then fail to install.

### 3.2 `src/hpc_bridge/catalog/seed/anvil.yaml:18` — `module load anaconda/2024.02-py311`

A facility module name (Anvil's Python 3.11 Anaconda). Not verifiable offline; it pins the *login-node Python to 3.11*, which matches `requires-python` and the Dockerfile. Nothing to do until Anvil retires the module.

### 3.3 `agentic/Dockerfile`

| Line | Pin | Current | Verdict |
|---|---|---|---|
| 5 | `FROM python:3.11-slim` | tag rebuilt 2026-09-02; resolves to **3.11.16** (latest 3.11 patch, 2026-08-13). 3.11 is **security-only since 2024-04-01, EOL 2027-10-31**; 3.12 EOL 2028-10-31; 3.13 EOL 2029-10-31; 3.14.7 is current (endoflife.date/api/python.json). | OK for V1. Suite is green on 3.14, so `python:3.13-slim` or `3.14-slim` is a free bump when convenient; keep `requires-python>=3.11` as the floor (3.10 EOLs 2026-10-31). |
| 12-13 | `curl … deb.nodesource.com/setup_20.x` + `nodejs` | **Node 20 EOL 2026-04-30** (latest 20.20.2). `@anthropic-ai/claude-code@2.1.260` declares `engines: {node: ">=22.0.0"}` (https://registry.npmjs.org/@anthropic-ai/claude-code). Active LTS: 22 (EOL 2027-04-30), 24 (EOL 2028-04-30); 26 becomes LTS 2026-10-28. | **Remove** (see line 20) or move to `setup_22.x`/`setup_24.x`. |
| 17 | `COPY --from=ghcr.io/astral-sh/uv:latest` | latest = **0.12.9** (2026-09-01). | Pin `ghcr.io/astral-sh/uv:0.12.9` for reproducible images. |
| 20 | `npm install -g @anthropic-ai/claude-code` (unpinned) | npm `latest` **2.1.260** (2026-09-03), `stable` **2.1.236**. Local CLI here is 2.1.259. | **Redundant**: `claude-agent-sdk` README — "The Claude Code CLI is automatically bundled with the package - no separate installation required! The SDK will use the bundled CLI by default." `_cli_version.py` on main = `2.1.260`; 0.2.152's changelog: "Updated bundled Claude CLI to version 2.1.259". `agentic/harness/runner.py:171-178` sets no `cli_path`, so the bundled CLI already wins and the npm one is never used. Delete lines 7-13 (Node) and 20, or keep an explicit `cli_path` if you want to pin the CLI independently. |
| 21 | `pip install claude-agent-sdk` (unpinned) | **0.2.152** (2026-09-02); requires `mcp>=1.23,<3`, Python ≥3.10. Releases are near-daily; 0.2.129 had a "Breaking Changes" section; 0.2.124 refused `.bat/.cmd` CLI shims on Windows. The options the harness uses (`model, allowed_tools, mcp_servers, system_prompt, setting_sources, cwd, max_turns, max_budget_usd, permission_mode, can_use_tool`) are all present in current docs. | Pin `claude-agent-sdk==0.2.152` and bump deliberately. |
| 31 | `uv sync --extra integration` | builds from the (stale) lock — inherits §2.3. | Relock. |

### 3.4 `agentic/fakecluster/`

| Line | Pin | Current | Verdict |
|---|---|---|---|
| `Dockerfile:10` | `FROM ubuntu:24.04` | 24.04 LTS supported to 2029; **26.04 LTS "Resolute Raccoon" is out** (support to 2031-05-29). | Fine to stay; 26.04 is the route to a supported Slurm (below). |
| `Dockerfile:14` | `slurm-wlm slurmdbd slurm-client` (distro) | noble: **23.11.4-1.2ubuntu5**; resolute (26.04): **25.11.2-1ubuntu2**; devel: 25.11.2-1ubuntu3 (https://launchpad.net/ubuntu/+source/slurm-wlm). Upstream newest **26.05.4** (2026-09-02) alongside 25.11.8 / 25.05.9, all carrying CVE-2026-65107, -65108, -65109, -65138, -65139, -65140, -65165 fixes (https://github.com/SchedMD/slurm/releases). 23.11 is not among the fixed series. Ubuntu's tracker for noble shows only CVE-2025-43904 fixed and the 2026 IDs not yet listed (https://ubuntu.com/security/cves?package=slurm-wlm). | Low real exposure (local, private compose network, only `login:22` published on 2222). Moving to `ubuntu:26.04` gets Slurm 25.11 **and** `CgroupPlugin=disabled` (24.05+), removing the `cgroup/v1` workaround documented in `agentic/fakecluster/README.md:204-206` — a small validation project, not a bump. |
| `Dockerfile:17` | `python3` (distro) | 24.04 → 3.12.3 (README:91); 26.04 would move it. | Fine. |
| `Dockerfile:23` | `uv:latest` | 0.12.9 | Pin as above. |
| `docker-compose.yml:24` | `image: mariadb:11` | floating tag currently == **11.8.9** (same digest as `11.8`, pushed 2026-08-24). 11.8 is LTS, EOL 2028-06-04; newest LTS is **12.3.3** (EOL 2029-06-12) (endoflife.date/api/mariadb.json, hub.docker.com). `slurmdbd` 23.11 is happy on 11.x. | Pin `mariadb:11.8` (LTS) so a future `11.x` non-LTS retag can't move under you. |

### 3.5 Other

- **GitHub Actions**: `.github/` does not exist — no CI at all. Nothing runs `pytest -q`, `uv lock --check`, or an advisory scan on push.
- **`.python-version`**: absent and gitignored (`.gitignore:11`). `uv python find` therefore resolves to whatever `.venv` was created with (3.13.12 here) while the jail is 3.11 — contributors silently test on different interpreters.
- **`requires-python = ">=3.11"`** (`pyproject.toml:5`, mirrored in `uv.lock:3`): every direct dep supports it; `globus-compute-*` and `mcp` require ≥3.10, `pydantic` ≥3.9 (2.14 will require ≥3.10). Suite green on 3.14.6.
- `.mcp.json:6` launches via `uv run --extra integration` — inherits whichever uv is on PATH; no version assumption beyond `--directory`/`--extra` (both ancient).
- `hooks/hooks.json`, `.claude-plugin/plugin.json` (`"version": "0.1.0"`), `commands/`, `skills/`: no toolchain pins.

---

## 4. Tooling

### 4.1 uv
Lock `version = 1, revision = 3`; produced by 0.10.x. Newest uv (0.12.9) reads and re-locks it **identically** (`--check` exit 0, `--dry-run`: "No lockfile changes detected"), so upgrading uv locally/in Docker causes no churn. `[tool.uv] cache-keys` (`pyproject.toml:37`) is still the right knob for the `uvx --from <repo>` staleness problem it documents. `hatchling` (`pyproject.toml:24`) is unpinned → latest 1.32.0 at every fresh build; harmless today, but pin (`hatchling>=1.27,<2`) for reproducibility.

### 4.2 pytest / pytest-asyncio
- `pytest 9.0.3 → 9.1.1`: no impact (see §2.1); `[tool.pytest.ini_options]` (`pyproject.toml:39`) is still valid — migrating to the new `[tool.pytest]` table is optional.
- `pytest-asyncio 1.4.0` is current, but `pyproject.toml:39-41` lacks `asyncio_default_fixture_loop_scope`. Observed on both the locked and latest envs: `pytest.PytestDeprecationWarning: The configuration option "asyncio_default_fixture_loop_scope" is unset…` — raised at `pytest_configure`, so a `-W error::DeprecationWarning` run dies with `INTERNALERROR` before collecting. Add `asyncio_default_fixture_loop_scope = "function"`.
- Also tighten `pytest>=8` → `pytest>=8.4` (pytest-asyncio 1.4.0's floor) or simply `>=9`.
- Warnings on both envs are otherwise identical: 8 `ResourceWarning: unclosed file` from `tests/test_session_shell.py` (the `open(f"{sd}/.env").read()` lines) and one from `tests/test_login.py::test_loopback_handler_never_logs_the_request_line` — test hygiene, not dependency-related.

### 4.3 ruff / mypy / coverage — not configured
No `[tool.ruff]`, `[tool.mypy]`, `ruff.toml`, `mypy.ini`, `.pre-commit-config.yaml`, or CI. Evidence of ad-hoc use: `.ruff_cache/0.15.10/` (last touched 2026-07-20; ruff latest is **0.16.6**, 2026-09-03), `# noqa: BLE001` / `A002` comments (flake8-blind-except / builtins rule sets), `# type: ignore[import-not-found|override|arg-type|import-untyped]` (mypy; latest **2.3.1**), a `.coverage` file from 2026-06-10 (pytest-cov, latest **7.1.0**, not declared in `dev`), and an empty `.deepeval/` directory (2026-07-29). Recommendation: declare `ruff`, `mypy`, `pytest-cov` in the `dev` extra with the rule set implied by the existing `noqa`s (`E,F,B,BLE,A` at minimum) so the next contributor reproduces the same lint.

### 4.4 Claude Code CLI / Agent SDK
Local `claude` = 2.1.259; npm `latest` = 2.1.260 (2026-09-03), `stable` = 2.1.236; `claude-agent-sdk` 0.2.152 bundles 2.1.260. The harness assumes nothing about the CLI version (no `--version` check, no `cli_path`), so whichever SDK `pip install` resolves decides the CLI. Pin the SDK (§3.3) and you have pinned the CLI.

---

## 5. Ranked upgrade plan

### A. Bump now — safe, already exercised

1. **`uv lock --upgrade && uv sync --extra dev --extra integration`** (36 packages; the exact set is in §2.1). Verified green: `tests/` + harness unit tiers on Python 3.11 and 3.14 with `globus-sdk 4.9.0`, `globus-compute-sdk/endpoint 4.16.0`, `mcp 1.29.1`, `pydantic 2.13.5`, `pytest 9.1.1`, `parsl 2026.8.10`, `cryptography 50.0.1`, `starlette 1.6.0`. Clears every advisory in §2.3. The one thing to re-run live afterwards is a smoke against the MEP (client `common` 1.0.0 now matches the MEP's) — expected no-op.
2. **`pyproject.toml:40`** — add `asyncio_default_fixture_loop_scope = "function"`; `pyproject.toml:9` — `pytest>=8.4` (or `>=9`) to match pytest-asyncio 1.4.0.
3. **`agentic/Dockerfile`**: delete the Node/npm layers (lines 7-13's `curl … setup_20.x`, `nodejs`, and line 20) — the SDK's bundled CLI is what runs; pin `claude-agent-sdk==0.2.152` (line 21) and `ghcr.io/astral-sh/uv:0.12.9` (line 17). If you prefer an explicit CLI, keep Node but on `setup_22.x`/`24.x` and pin `@anthropic-ai/claude-code@2.1.260` — never Node 20 (EOL, below the CLI's engine floor).
4. **`agentic/fakecluster/docker-compose.yml:24`** → `mariadb:11.8`; **`agentic/fakecluster/Dockerfile:23`** → `uv:0.12.9`.
5. **`pyproject.toml:24`** — `requires = ["hatchling>=1.27,<2"]`.
6. Add a minimal CI workflow: `uv lock --check`, `uv run pytest -q`, `uv run pytest agentic/harness/test_invariants.py -q` on 3.11 and 3.14, plus an OSV/`pip-audit` scan of the lock. (This is the only way §2.3 doesn't silently recur.)

### B. Needs a code or coordination change (list of call sites)

1. **Client SDK floor vs MEP** — `pyproject.toml:11` `globus-compute-sdk>=4` → `>=4.15` (so client and MEP share `globus-compute-common 1.0.0`), and `:16` likewise. No code changes; the relock in A.1 already gets you there.
2. **Seed pin lockstep** — `src/hpc_bridge/catalog/seed/globus-cluster.yaml:27` (`==4.15.0`) must change **only** when the globus-cluster admin upgrades the MEP; 4.16.0 needs `email:` in the MEP's `config.yaml` first. Update `HANDOFF.md:60` and the vault note in the same PR.
3. **`mcp` 2.x migration (defer past V1; 1.x is security-fix-only now)** — exactly four lines: `src/hpc_bridge/server.py:15` `from mcp.server.fastmcp import Context, FastMCP` → `from mcp.server.mcpserver import Context, MCPServer`; `:433` `lifespan(server: FastMCP)` → `MCPServer`; `:467` `FastMCP("endpoint", lifespan=lifespan)` → `MCPServer("endpoint", lifespan=lifespan)` (name must stay "endpoint" — it mirrors the `.mcp.json` key); `:2254` `mcp.run()` unchanged (stdio takes no kwargs). Per the migration guide, `@mcp.tool()`, `Context`, `lifespan=`, `ctx.request_context.lifespan_context`, pydantic-model returns and stdio `run()` are unchanged. Tests import nothing from `mcp`. Then `pyproject.toml:6` → `mcp>=2,<3` (`claude-agent-sdk` allows `<3`). Note 2.x replaces `httpx` with `httpx2` — irrelevant to us, but it changes the lock.
4. **Private globus-sdk paths** — `src/hpc_bridge/login_flow_manager.py:38` (`…login_flows.local_server_login_flow_manager.local_server`), `:63` (`_auth_code_queue`), `tests/test_login.py:283`. Still present in 4.9.0 (suite green). Either keep the `except Exception` guard and re-run `tests/test_login.py` on every globus-sdk bump, or ask upstream to expose a public `handler_class` hook.
5. **Self-provisioned endpoint version** — `src/hpc_bridge/discovery.py:21` and `agentic/fakecluster/README.md:103` install unpinned `globus-compute-endpoint`. Optional: template the client's `globus_compute_sdk.__version__` in for determinism (trade-off in §3.1).
6. **Fake cluster on Ubuntu 26.04 / Slurm 25.11** — `agentic/fakecluster/Dockerfile:10` and `slurm/cgroup.conf` (`CgroupPlugin=disabled` becomes legal, drop the `cgroup/v1` shim). Re-validate the sweep in `README.md:180-195`.
7. **Lint/type toolchain** — add `[tool.ruff]`/`[tool.mypy]` and the `dev` extras (§4.3); first run will surface whatever the ad-hoc 0.15.10 run already saw.

### C. Leave pinned — and why

| Pin | Why leave it |
|---|---|
| `globus-sdk>=4.4,<5` (`pyproject.toml:6`) | No 5.0 exists or is announced; 4.9.0 is drop-in. `<5` matches `globus-compute-sdk`'s own `globus-sdk<5,>=4.4.0`. |
| `mcp>=1.23,<2` (`pyproject.toml:6`) | 2.x removes the `mcp.server.fastmcp` import path; raise the floor to `>=1.28.1` (the advisory fix) if you like, keep `<2` until B.3. |
| `globus-compute-endpoint==4.15.0` (seed line 27) | Must equal the MEP's version — the manager/worker coupling is real (the `process_worker_pool.py -P/--port` failure), and only the MEP admin can move it. |
| `dill==0.3.9`, `tblib==1.7.0`, `pika<1.4`, `rich<15` | Pinned by `globus-compute-sdk`, not by us; identical across 4.13→4.16. |
| `requires-python >= 3.11` | 3.10 EOLs 2026-10-31; 3.11 is the Anvil module Python and the jail image. Plan `>=3.12` after V1 (3.11 EOL 2027-10-31). |
| `jinja2 3.1.6`, `pyyaml 6.0.3`, `pytest-asyncio 1.4.0`, `httpx 0.28.1`, `requests 2.34.2`, `urllib3 2.7.0`, `pyjwt 2.13.0` | Already latest. |
| `python:3.11-slim` | Supported (security-only) until 2027-10-31; suite is green on 3.14 so bumping is optional, not required. |
| `ubuntu:24.04` fake cluster | LTS until 2029; the Slurm-currency argument for 26.04 is real but is a validation project (B.6), not a pin bump. |

---

## 6. Numbers at a glance

- 73 locked third-party packages; **36 stale**, 37 current; lock age ~70 days.
- **7 packages / 19 advisory IDs** in the lock, **0** after relock; **0** on any direct dependency.
- Direct deps behind latest: `mcp` (1.27.2→1.29.1), `globus-sdk` (4.8.1→4.9.0), `globus-compute-sdk/-endpoint` (4.13.0→4.16.0), `pydantic` (2.13.4→2.13.5), `pytest` (9.0.3→9.1.1). Current: `pyyaml`, `jinja2`, `pytest-asyncio`.
- Toolchain behind latest: `uv` 0.10.4→0.12.9 (local), Node **20 (EOL)** → 22/24, Claude CLI 2.1.259→2.1.260 (moot with the bundled CLI), Slurm 23.11.4→25.11 (Ubuntu 26.04) / 26.05.4 (upstream), MariaDB `11`→`11.8` (pin), ruff 0.15.10→0.16.6 (ad hoc).

---

## 7. Sources read

- PyPI JSON (latest, dates, `requires_dist`, OSV vulnerabilities): `https://pypi.org/pypi/<name>/json` and `https://pypi.org/pypi/<name>/<version>/json` for every package in §2 (incl. `globus-compute-endpoint` 4.12.0/4.13.0/4.14.0/4.15.0/4.16.0, `globus-compute-sdk` 4.13.0/4.14.0/4.15.0/4.16.0, `globus-compute-common` 1.0.0, `claude-agent-sdk`, `hatchling`, `uv`, `ruff`, `mypy`, `pytest-cov`).
- Globus Compute: https://globus-compute.readthedocs.io/en/latest/changelog.html · https://globus-compute.readthedocs.io/en/latest/sdk/executor_user_guide.html · https://globus-compute.readthedocs.io/en/latest/endpoints/endpoints.html · https://globus-compute.readthedocs.io/en/latest/endpoints/templates.html · https://globus-compute.readthedocs.io/en/latest/endpoints/installation.html · https://globus-compute.readthedocs.io/en/latest/reference/client.html · https://compute.api.globus.org/v2/version?service=all · https://compute.api.globus.org/v3/version?service=all
- globus-sdk: https://globus-sdk-python.readthedocs.io/en/stable/changelog.html
- mcp: https://github.com/modelcontextprotocol/python-sdk/releases · https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/docs/migration.md
- pydantic: https://github.com/pydantic/pydantic/releases
- pytest: https://docs.pytest.org/en/stable/changelog.html · pytest-asyncio: https://pytest-asyncio.readthedocs.io/en/latest/reference/changelog.html
- Claude Code / Agent SDK: https://registry.npmjs.org/@anthropic-ai/claude-code · https://github.com/anthropics/claude-agent-sdk-python/releases · https://github.com/anthropics/claude-agent-sdk-python/blob/main/README.md · https://raw.githubusercontent.com/anthropics/claude-agent-sdk-python/main/CHANGELOG.md · https://raw.githubusercontent.com/anthropics/claude-agent-sdk-python/main/src/claude_agent_sdk/_cli_version.py
- uv: https://api.github.com/repos/astral-sh/uv/releases/latest (→ https://github.com/astral-sh/uv/releases/tag/0.12.9)
- EOL: https://endoflife.date/api/nodejs.json · https://endoflife.date/api/python.json · https://endoflife.date/api/ubuntu.json · https://endoflife.date/api/mariadb.json
- Images: https://hub.docker.com/v2/repositories/library/mariadb/tags · https://hub.docker.com/v2/repositories/library/python/tags
- Slurm: https://launchpad.net/ubuntu/+source/slurm-wlm · https://github.com/SchedMD/slurm/releases · https://ubuntu.com/security/cves?package=slurm-wlm
- Advisories: https://osv.dev/vulnerability/<ID> for each ID in §2.3
