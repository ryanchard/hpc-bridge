# PBS Pro scheduler support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add PBS Pro as a first-class scheduler alongside Slurm so ALCF Polaris (and other PBS sites) can provision scheduler compute blocks, run tasks on compute nodes, and stop spending through the same tools.

**Architecture:** Per-scheduler dispatch. `MachineProfile.scheduler` (`"slurm"|"pbs"`) selects a clean single-scheduler Jinja template in `config_template()`; discovery, the cancel/poll path, and the `_apply_*` guards branch on scheduler where they must. The billed shape is renamed `slurm`→`compute` and its in-template discriminator `is_slurm`→`compute` (scheduler is a *facility* property, not an agent choice).

**Tech Stack:** Python 3.10–3.13, pydantic v2, Jinja2, Parsl (`PBSProProvider`, `MpiExecLauncher`), pytest.

## Global Constraints

- Line length 88 (ruff + black + isort + flake8 + mypy). Run `make lint` before each commit.
- The in-template discriminator MUST be a **bool** named `compute` (never a string compare): the endpoint manager's `_sanitize_user_json` json.dumps's every *string* user-opt, so a `{% if x == 'PBSProProvider' %}` compare sees `'"PBSProProvider"'` and silently drops the block. Bools pass through unchanged.
- Do NOT `| tojson` a string value in the template (worker_init, scheduler_options): the sanitizer already quotes strings; a second encode embeds literal quotes and breaks the worker.
- Never rename the scheduler *value* `scheduler="slurm"` (e.g. `test_discovery.py`, `test_catalog_profile_bridge.py`, `catalog/seed/anvil.yaml`) — only the *shape* name `"slurm"`→`"compute"`.
- Facility-specific PBS directives (Polaris `#PBS -l filesystems=home:eagle`, `-l place=scatter`) live in the facility's `scheduler_options`, NOT the template.
- Tests split `unit`/`integration`; these tasks are all unit-level except Task 8 (live). Run `cd /Users/ryan/Work/Projects/Compute/hpc-bridge` first; all paths below are repo-relative.
- Branch: `feat/pbs-scheduler-support` (already created). Commit after each task.

---

## File structure

| File | Responsibility | Tasks |
|------|----------------|-------|
| `src/hpc_bridge/facility/remote.py` | `MachineProfile` fields; `config_template` scheduler dispatch; `_SLURM_TEMPLATE`/`_PBS_TEMPLATE`; PBS `cancel_blocks` + jobid regex | 1,2,3,5 |
| `src/hpc_bridge/catalog/entry.py` | `Defaults.cpus_per_node`; `profile_kwargs()` passes `scheduler`+`cpus_per_node` | 1 |
| `src/hpc_bridge/models.py` | `FacilityDetails.scheduler` widen; `ShapeStatus` wording | 1,6 |
| `src/hpc_bridge/shapes.py` | `SHAPES`; `is_slurm`→`compute`; drop `provider_type` from compute shape | 2 |
| `src/hpc_bridge/server.py` | `DEFAULT_SHAPE`; `_apply_*` guards; `_unsupported_entry_reason`; `_release_blocks_over_login` PBS; tool docstrings | 2,5,6 |
| `src/hpc_bridge/discovery.py` | PBS probe + parse + interface ranking | 4 |
| `skills/driving-hpc/SKILL.md` | `slurm`→`compute`; PBS notes | 7 |
| `tests/test_*.py` | unit tests for each | 1–6 |
| `docs/.../plans` runbook | live Polaris validation | 8 |

---

## Task 1: Seam — thread `scheduler` + `cpus_per_node` through the profile

**Files:**
- Modify: `src/hpc_bridge/facility/remote.py` (`MachineProfile` ~181-202)
- Modify: `src/hpc_bridge/catalog/entry.py` (`Defaults` ~38-46, `profile_kwargs` ~118-133)
- Modify: `src/hpc_bridge/models.py` (`FacilityDetails.scheduler` ~92-94)
- Test: `tests/test_profile.py`, `tests/test_models.py`

**Interfaces:**
- Produces: `MachineProfile.scheduler: Literal["slurm","pbs"] = "slurm"`, `MachineProfile.cpus_per_node: int | None = None`, `Defaults.cpus_per_node: int | None = None`, `FacilityDetails.scheduler: Literal["slurm","pbs"]`. `profile_from_catalog_entry` carries `scheduler` and `cpus_per_node` onto the profile.

- [ ] **Step 1: Write the failing test** — append to `tests/test_profile.py`:

```python
def test_profile_carries_scheduler_and_cpus_per_node():
    from hpc_bridge.catalog.entry import (
        CatalogEntry, Compute, Defaults, Allocation,
    )
    from hpc_bridge.facility.remote import profile_from_catalog_entry
    import datetime

    entry = CatalogEntry(
        id="polaris", facility_key="alcf", facility="ALCF",
        description="d", display_name="Polaris", ssh_host="polaris",
        compute=Compute(
            scheduler="pbs", interface="hsn0",
            env_setup="source {venv}/bin/activate",
            scratch_root="/home/{user}/.hpc-bridge",
        ),
        defaults=Defaults(partition="debug", cpus_per_node=32),
        last_validated=datetime.date(2026, 7, 10),
    )
    prof = profile_from_catalog_entry(entry, user="rchard", account="acct")
    assert prof.scheduler == "pbs"
    assert prof.cpus_per_node == 32
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/ryan/Work/Projects/Compute/hpc-bridge && python -m pytest tests/test_profile.py::test_profile_carries_scheduler_and_cpus_per_node -v`
Expected: FAIL — `TypeError: Defaults() got an unexpected keyword argument 'cpus_per_node'` (or `MachineProfile` has no `scheduler`).

- [ ] **Step 3: Add the fields.** In `src/hpc_bridge/facility/remote.py`, add to `MachineProfile` (after `scratch_root`, keeping the dataclass field order valid — these have defaults so they go among the defaulted fields):

```python
    scheduler: str = "slurm"          # "slurm" | "pbs" — selects the config_template branch
    cpus_per_node: int | None = None  # PBS: emitted as PBSProProvider.cpus_per_node; Slurm: unused
```

In `src/hpc_bridge/catalog/entry.py` `Defaults`, add after `nodes_per_block`:

```python
    cpus_per_node: int | None = None  # PBSProProvider.cpus_per_node; Slurm ignores it
```

In `profile_kwargs()` (`entry.py`), add two keys to the returned dict:

```python
            "scheduler": self.compute.scheduler,
            "cpus_per_node": self.defaults.cpus_per_node,
```

Update the `profile_kwargs` docstring line that says `compute.scheduler` is "absent — consumed by the transport layer": change to note `scheduler` is now passed through to the profile (the template dispatches on it); `ssh_host` alone stays transport-only.

- [ ] **Step 4: Widen `FacilityDetails.scheduler`.** In `src/hpc_bridge/models.py` replace the `scheduler` field (~92-94):

```python
    scheduler: Literal["slurm", "pbs"] = Field(
        default="slurm",
        description="Batch scheduler: 'slurm' or 'pbs' (PBS Pro). Picks the provider/launcher "
        "and the queue vs partition wording; LSF is not supported yet.",
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_profile.py tests/test_models.py tests/test_catalog_entry.py -v`
Expected: PASS (new test green; existing profile/model/entry tests still green — new fields are optional with Slurm-preserving defaults).

- [ ] **Step 6: Add a FacilityDetails PBS-acceptance test** — append to `tests/test_models.py`:

```python
def test_facility_details_accepts_pbs_scheduler():
    from hpc_bridge.models import FacilityDetails
    d = FacilityDetails(
        ssh_host="polaris", interface="hsn0",
        env_setup="source {venv}/bin/activate",
        scratch_root="/home/{user}/.hpc-bridge", partition="debug",
        scheduler="pbs",
    )
    assert d.scheduler == "pbs"
```

Run: `python -m pytest tests/test_models.py::test_facility_details_accepts_pbs_scheduler -v`
Expected: PASS

- [ ] **Step 7: Lint + commit**

```bash
make lint
git add src/hpc_bridge/facility/remote.py src/hpc_bridge/catalog/entry.py src/hpc_bridge/models.py tests/test_profile.py tests/test_models.py
git commit -m "feat(pbs): thread scheduler + cpus_per_node through profile; allow scheduler=pbs in FacilityDetails"
```

---

## Task 2: Rename the compute shape `slurm`→`compute` and the flag `is_slurm`→`compute`

Pure refactor — still Slurm-only behaviour. Renames the agent-facing shape and the in-template discriminator to scheduler-neutral names, keeping the whole suite green.

**Files:**
- Modify: `src/hpc_bridge/shapes.py`
- Modify: `src/hpc_bridge/facility/remote.py` (template `{% if is_slurm %}` ~547; docstring ~519)
- Modify: `src/hpc_bridge/server.py` (`DEFAULT_SHAPE` :38; `_apply_partition` :511; `_apply_account` :526; `_ensure_endpoint_up` ignored-check :560; tool signature defaults :614,:1191,:1209; run_shell docstring :1195; the `slurm = app.shapes.pop(...)` var :990)
- Test: `tests/test_shapes.py`, `tests/test_server.py`, `tests/test_remote_facility.py`

**Interfaces:**
- Produces: `SHAPES = ("login", "compute")`; `shape_config("login") = {"provider_type":"LocalProvider","max_workers_per_node":1,"compute":False}`; `shape_config("compute") = {"compute":True}` (no `provider_type`). `DEFAULT_SHAPE = "compute"`. Template branch is `{% if compute | default(true) %}`. `_apply_*` guards read `.get("compute")`.

- [ ] **Step 1: Rewrite the failing tests first** — replace the three assertions in `tests/test_shapes.py`:

```python
def test_known_shapes():
    assert set(SHAPES) == {"login", "compute"}


def test_login_shape_selects_localprovider():
    cfg = shape_config("login")
    assert cfg["provider_type"] == "LocalProvider"
    assert cfg["compute"] is False  # bool discriminator survives the manager's json sanitizer


def test_compute_shape_sets_compute_flag_without_pinning_provider():
    cfg = shape_config("compute", partition="debug", account="ACC", walltime="00:30:00")
    assert cfg["compute"] is True  # gates the scheduler block (not a string compare)
    assert "provider_type" not in cfg  # provider comes from the per-scheduler template default
    assert cfg["partition"] == "debug" and cfg["account"] == "ACC"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_shapes.py -v`
Expected: FAIL (`SHAPES` still `("login","slurm")`; `shape_config("compute")` raises unknown-shape).

- [ ] **Step 3: Rewrite `shapes.py`.** Replace the body of `src/hpc_bridge/shapes.py` from `SHAPES` onward:

```python
SHAPES = ("login", "compute")


def shape_config(shape: str, **overrides: Any) -> dict[str, Any]:
    """Return the user_endpoint_config dict for `shape`, merging caller overrides.

    The keys here MUST match the Jinja variables in the UEP template (see
    SlurmFacility.config_template). Raises ValueError for an unknown shape.

    `compute` is a BOOLEAN discriminator the template branches on (scheduler block vs
    login node). It must be a bool, not a string compared in-template: the endpoint
    manager runs user_opts through `_sanitize_user_json`, which json.dumps's every
    string (so "PBSProProvider" becomes '"PBSProProvider"') — a template
    `{% if provider_type == 'PBSProProvider' %}` then silently drops the provider block.
    Bools pass through the sanitizer unchanged. The `compute` shape does NOT pin a
    provider_type: the per-scheduler template supplies it (SlurmProvider / PBSProProvider)."""
    if shape == "login":
        base: dict[str, Any] = {
            "provider_type": "LocalProvider",
            "max_workers_per_node": 1,
            "compute": False,
        }
    elif shape == "compute":
        base = {"compute": True}
    else:
        raise ValueError(f"unknown shape {shape!r}: expected one of {SHAPES}")
    base.update({k: v for k, v in overrides.items() if v is not None})
    return base
```

Update the module docstring line to read: ``login`` runs on the login node (LocalProvider); ``compute`` requests a scheduler block.

- [ ] **Step 4: Update the template + docstring in `remote.py`.** Line ~547: change `{% if is_slurm | default(true) %}` to `{% if compute | default(true) %}`. In the `config_template` docstring (~519) change "Branch on the BOOLEAN `is_slurm`" to "Branch on the BOOLEAN `compute`".

- [ ] **Step 5: Update `server.py` references.**

- Line 38: `DEFAULT_SHAPE = "compute"`.
- `_apply_partition` (:511) and `_apply_account` (:526): change `rt.user_endpoint_config.get("is_slurm")` → `.get("compute")`. Update their docstrings ("Slurm-only" → "compute-shape only").
- `_ensure_endpoint_up` ignored check (:560): `not rt.user_endpoint_config.get("is_slurm")` → `.get("compute")`.
- Tool signature defaults still hardcoding `"slurm"` (:614, :1191, :1209) and `shape: str = DEFAULT_SHAPE` (:537): set each to `DEFAULT_SHAPE`.
- `run_shell` docstring (:1195): `"slurm" runs on a scheduler block` → `"compute" runs on a scheduler block`.
- Local var `slurm = app.shapes.pop(DEFAULT_SHAPE, None)` (:990): rename to `compute` for clarity (and the two follow-on lines).

Bulk-verify no stragglers: `grep -rn 'is_slurm\|shape=.slurm.\|"slurm"' src/hpc_bridge/ | grep -v 'scheduler'` should return nothing but comments; fix any code hit.

- [ ] **Step 6: Update `test_server.py` + `test_remote_facility.py` shape usages** (NOT `scheduler="slurm"`):

```bash
# test_server.py: rename the shape key/kwarg/flag, leave scheduler= untouched (none here)
sed -i '' \
  -e 's/app\.shapes\["slurm"\]/app.shapes["compute"]/g' \
  -e 's/_shape_runtime(app, "slurm")/_shape_runtime(app, "compute")/g' \
  -e 's/shape="slurm"/shape="compute"/g' \
  -e 's/{"login", "slurm"}/{"login", "compute"}/g' \
  -e 's/"slurm" not in app.shapes/"compute" not in app.shapes/g' \
  -e 's/{"is_slurm": True}/{"compute": True}/g' \
  tests/test_server.py
# rename local fixture vars slurm_runner/`slurm =`/`slurm.` are fine to leave (cosmetic),
# but the dict-literal `{"slurm": slurm, ...}` on line ~678 must change:
sed -i '' -e 's/{"slurm": slurm, "login": login}/{"compute": slurm, "login": login}/g' tests/test_server.py
```

In `tests/test_remote_facility.py`: rename the three template tests that call `shape_config("slurm", ...)` to `shape_config("compute", ...)` and drop any `prov["type"] == "SlurmProvider"` assertion that now depends on the template default (keep it — the Slurm template default is still `SlurmProvider`, so `_render` yields it). Specifically update `test_template_renders_slurmprovider_for_slurm_shape`, `test_template_defaults_to_slurm_account_from_profile`, `test_slurm_provider_params_survive_the_manager_sanitizer`, `test_worker_init_not_double_encoded_through_sanitizer`, `test_template_max_blocks_and_nodes_per_block_default_and_override` to pass `shape_config("compute", ...)`. The comment at :121 "`is_slurm` guard" → "`compute` guard".

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest tests/test_shapes.py tests/test_server.py tests/test_remote_facility.py tests/test_cost.py tests/test_dispatch.py -v`
Expected: PASS. If `test_cost.py`/`test_dispatch.py` reference the `"slurm"` shape, apply the same shape-only rename there.

- [ ] **Step 8: Lint + commit**

```bash
make lint
git add -u   # tracked modifications only — Task 2 creates no new files, and this never stages untracked dirs (e.g. .claude/)
git commit -m "refactor(pbs): rename compute shape slurm->compute and flag is_slurm->compute (scheduler-neutral)"
```

---

## Task 3: PBS template + `config_template` scheduler dispatch

**Files:**
- Modify: `src/hpc_bridge/facility/remote.py` (`config_template` ~503-595)
- Test: `tests/test_remote_facility.py`

**Interfaces:**
- Consumes: `MachineProfile.scheduler`, `MachineProfile.cpus_per_node` (Task 1); the `compute` flag (Task 2).
- Produces: `config_template(profile)` returns the PBS template when `profile.scheduler == "pbs"`, rendering `PBSProProvider`, `queue` (from the `partition` slot), `MpiExecLauncher(bind_threads)`, optional `cpus_per_node`, `min_blocks: 0`, `scheduler_options` passthrough.

- [ ] **Step 1: Write failing tests** — add a PBS `_profile` helper and tests to `tests/test_remote_facility.py`:

```python
def _pbs_profile():
    return remote.MachineProfile(
        name="polaris", endpoint_name="hpc-bridge-polaris",
        display_name="Polaris",
        env_setup="module load conda && source /home/u/gce/bin/activate",
        interface="hsn0", partition="debug", account="ACC",
        worker_init="module load conda && source /home/u/gce/bin/activate",
        scratch_root="/home/u/.hpc-bridge",
        scheduler="pbs", cpus_per_node=32,
        scheduler_options="#PBS -l filesystems=home:eagle",
    )


def test_pbs_template_renders_pbsproprovider_compute_shape():
    f = SlurmFacility(_pbs_profile(), cli=None)
    tmpl, defaults = f.config_template(Profile(mode="interactive"))
    prov = _render(tmpl, {**defaults, **shape_config("compute")})["engine"]["provider"]
    assert prov["type"] == "PBSProProvider"
    assert prov["queue"] == "debug"           # partition slot renders under the queue key
    assert prov["account"] == "ACC"
    assert prov["cpus_per_node"] == 32
    assert prov["min_blocks"] == 0
    assert prov["launcher"]["type"] == "MpiExecLauncher"
    assert prov["launcher"]["bind_threads"] is True
    assert "partition" not in prov            # PBS uses queue, not partition
    assert prov["scheduler_options"] == "#PBS -l filesystems=home:eagle"


def test_pbs_template_login_shape_is_localprovider():
    f = SlurmFacility(_pbs_profile(), cli=None)
    tmpl, _defaults = f.config_template(Profile(mode="interactive"))
    cfg = _render(tmpl, shape_config("login"))
    assert cfg["engine"]["provider"]["type"] == "LocalProvider"


def test_pbs_provider_params_survive_the_manager_sanitizer():
    f = SlurmFacility(_pbs_profile(), cli=None)
    tmpl, defaults = f.config_template(Profile(mode="interactive"))
    prov = _render(tmpl, {**defaults, **shape_config("compute")})["engine"]["provider"]
    assert prov["queue"] == "debug" and prov["account"] == "ACC"  # full block, not else branch


def test_slurm_template_still_selected_for_slurm_profile():
    f = SlurmFacility(_profile(), cli=None)  # _profile() defaults scheduler="slurm"
    tmpl, defaults = f.config_template(Profile(mode="interactive"))
    prov = _render(tmpl, {**defaults, **shape_config("compute")})["engine"]["provider"]
    assert prov["type"] == "SlurmProvider" and prov["partition"] == "debug"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_remote_facility.py -k pbs_template -v`
Expected: FAIL — the Slurm template renders `SlurmProvider`/`partition`, not `PBSProProvider`/`queue`.

- [ ] **Step 3: Refactor `config_template` into scheduler dispatch.** In `src/hpc_bridge/facility/remote.py`, keep the existing method signature and the `subs`/`defaults` machinery, but select the template string by `self.profile.scheduler`. Replace the single `template = """..."""` assignment with:

```python
        template = _PBS_TEMPLATE if p.scheduler == "pbs" else _SLURM_TEMPLATE
```

Move the current Slurm template body to a module-level constant `_SLURM_TEMPLATE` (unchanged except the Task 2 `{% if compute %}` edit). Add a module-level `_PBS_TEMPLATE`:

```python
_PBS_TEMPLATE = """\
engine:
  type: GlobusComputeEngine
  run_in_sandbox: true
  max_workers_per_node: {{ max_workers_per_node | default(@@MAXW@@) }}
  address:
    type: address_by_interface
    ifname: {{ interface | default(@@IFACE@@) }}
{% if available_accelerators is defined and available_accelerators %}
{% if available_accelerators is iterable and available_accelerators is not string %}
  available_accelerators: [{{ available_accelerators | join(', ') }}]
{% else %}
  available_accelerators: {{ available_accelerators }}
{% endif %}
{% endif %}
  job_status_kwargs:
    max_idletime: @@IDLE@@
    strategy_period: 30
  provider:
    type: {{ provider_type | default('PBSProProvider') }}
{% if compute | default(true) %}
    account: {{ account | default(@@ACCOUNT@@) }}
    queue: {{ partition | default(@@PARTITION@@) }}
    walltime: {{ walltime | default(@@WALLTIME@@) }}
    nodes_per_block: {{ nodes_per_block | default(@@NODES@@) }}
{% if cpus_per_node is defined and cpus_per_node %}
    cpus_per_node: {{ cpus_per_node }}
{% endif %}
    worker_init: {{ worker_init | default(@@WORKER_INIT@@) }}
    launcher:
      type: MpiExecLauncher
      bind_threads: true
    init_blocks: {{ init_blocks | default(@@EAGER@@) }}
    min_blocks: 0
    max_blocks: {{ max_blocks | default(@@MAXBLK@@) }}
{% if scheduler_options is defined and scheduler_options %}
    scheduler_options: {{ scheduler_options }}
{% endif %}
{% else %}
    init_blocks: 1
    min_blocks: 0
    max_blocks: {{ max_blocks | default(@@MAXBLK@@) }}
{% endif %}
"""
```

The `@@...@@` sentinel substitution loop already runs on `template` after selection, so `@@ACCOUNT@@`, `@@PARTITION@@`, etc. are filled for whichever template was chosen (the PBS template reuses `@@PARTITION@@` for the queue value — one slot, scheduler-appropriate key). Add `cpus_per_node` to the `defaults` dict only when set (mirrors `available_accelerators`):

```python
        if p.cpus_per_node is not None:
            defaults["cpus_per_node"] = p.cpus_per_node
```

(No `@@CPUS@@` sub is needed — `cpus_per_node` is emitted only via the conditional `{% if cpus_per_node %}` from the merged defaults, exactly like `available_accelerators`/`scheduler_options`.)

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_remote_facility.py -v`
Expected: PASS (PBS tests green; all Slurm template tests still green — the Slurm path is byte-identical).

- [ ] **Step 5: Lint + commit**

```bash
make lint
git add src/hpc_bridge/facility/remote.py tests/test_remote_facility.py
git commit -m "feat(pbs): PBSProProvider template selected by profile.scheduler"
```

---

## Task 4: Discovery — auto-detect PBS + rank the compute fabric

**Files:**
- Modify: `src/hpc_bridge/discovery.py` (`_PROBE` ~30-45, `parse_probe` ~61-100, `_collect` multi-keys ~106, `_interface` ~161-175, add `_default_queue`)
- Test: `tests/test_discovery.py`

**Interfaces:**
- Consumes: `FacilityDetails.scheduler` accepting `"pbs"` (Task 1).
- Produces: a probe that emits `SCHED=pbs` when `qsub` is present and `QUEUE=<name>` lines from `qstat -Q`; `parse_probe` returns a draft with `scheduler="pbs"` and a PBS queue as `partition`. `_interface` ranks `hsn`/`ib` above `bond`.

- [ ] **Step 1: Write failing tests** — add to `tests/test_discovery.py`:

```python
# Polaris-shaped: PBS (qsub, no sbatch), Slingshot hsn0 beside the bond0 mgmt NIC, conda env.
_POLARIS = """\
HPCB_PROBE_BEGIN
USER=rchard
HOME=/home/rchard
SCRATCH=
WORK=
PSCRATCH=
SCHED=pbs
GCE=
UV=
MYBALANCE=
XDUSAGE=
QUEUE=debug
QUEUE=prod
NIC=lo|127.0.0.1/8
NIC=bond0|10.140.56.127/24
NIC=hsn0|10.201.3.11/16
HPCB_PROBE_END
"""


def test_parse_probe_detects_pbs_and_queue():
    draft, notes = parse_probe(_POLARIS, ssh_host="polaris")
    assert draft.scheduler == "pbs"
    assert draft.partition == "debug"       # preferred PBS queue


def test_parse_probe_prefers_compute_fabric_over_bond():
    draft, _notes = parse_probe(_POLARIS, ssh_host="polaris")
    assert draft.interface == "hsn0"        # Slingshot fabric, not the bond0 mgmt NIC


def test_probe_script_checks_qsub_and_qstat():
    assert "qsub" in discovery._PROBE and "qstat -Q" in discovery._PROBE
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_discovery.py -k "pbs or fabric or qsub" -v`
Expected: FAIL — probe has no `qsub`/`qstat`; `_collect` drops `QUEUE`; `parse_probe` hardcodes `scheduler="slurm"`; `_interface` may pick `bond0`.

- [ ] **Step 3: Extend the probe.** In `_PROBE`, replace the scheduler line and add a queue query (place beside the `sinfo` line):

```sh
if command -v sbatch >/dev/null 2>&1; then echo SCHED=slurm; elif command -v qsub >/dev/null 2>&1; then echo SCHED=pbs; else echo SCHED=none; fi
sinfo -h -o 'PART=%P|%a' 2>/dev/null || true
qstat -Q 2>/dev/null | awk 'NR>2 && $1!="" {print "QUEUE="$1}' || true
```

- [ ] **Step 4: Collect `QUEUE` as multi-valued.** In `_collect`, add `QUEUE` to the multi dict: `multi: dict[str, list[str]] = {"PART": [], "NIC": [], "QUEUE": []}`.

- [ ] **Step 5: Branch `parse_probe` on scheduler.** Replace the scheduler note + draft construction:

```python
    sched = f.get("SCHED")
    if sched == "pbs":
        scheduler = "pbs"
        partition = _default_queue(f.get("QUEUE", []))
        if not f.get("QUEUE"):
            notes.append("queue: `qstat -Q` returned nothing; confirm the PBS queue to use.")
    elif sched == "slurm":
        scheduler = "slurm"
        partition = _default_partition(f.get("PART", []))
        if not f.get("PART"):
            notes.append("partition: `sinfo` returned nothing; confirm the queue to use.")
    else:
        scheduler = "slurm"
        partition = _default_partition(f.get("PART", []))
        notes.append("scheduler: neither `sbatch` nor `qsub` found; defaulted scheduler='slurm' — "
                     "confirm the facility's scheduler before proceeding.")
```

Then pass `scheduler=scheduler` into the `FacilityDetails(...)` draft (replacing the hardcoded `scheduler="slurm"`). Remove the old unconditional `if f.get("SCHED") != "slurm":` note block and the old `partition = _default_partition(...)` lines now folded above.

Add the helper beside `_default_partition`:

```python
def _default_queue(queue_lines: list[str]) -> str:
    """Pick a sensible default PBS queue: prefer a debug/short one, else the first listed."""
    names = [q.strip() for q in queue_lines if q.strip()]
    for pref in ("debug", "debug-scaling", "prod", "batch"):
        if pref in names:
            return pref
    return names[0] if names else "debug"
```

- [ ] **Step 6: Rank the compute fabric in `_interface`.** Replace the `fast`/`pick` selection:

```python
_FAST_NIC_ORDER = ("hsn", "ipogif", "ib", "hsi", "bond")
```

(replace `_FAST_NIC_PREFIXES` with `_FAST_NIC_ORDER`; update the `.startswith(_FAST_NIC_ORDER)` membership test to use it) and pick by priority:

```python
    fast = [c for c in cands if c.startswith(_FAST_NIC_ORDER)]
    def _rank(nic: str) -> int:
        return next((i for i, p in enumerate(_FAST_NIC_ORDER) if nic.startswith(p)), len(_FAST_NIC_ORDER))
    pick = min(fast, key=_rank) if fast else cands[0]
```

- [ ] **Step 7: Run tests to verify pass**

Run: `python -m pytest tests/test_discovery.py -v`
Expected: PASS (new PBS tests green; the existing `_GLOBUS`/`_ANVIL` Slurm tests still green — `SCHED=slurm` path unchanged, and `enP7s7`/`ib0` interface picks are unaffected by the ranking since no `bond` competes).

- [ ] **Step 8: Lint + commit**

```bash
make lint
git add src/hpc_bridge/discovery.py tests/test_discovery.py
git commit -m "feat(pbs): discover PBS facilities (qsub/qstat -Q) and rank the compute fabric"
```

---

## Task 5: Cancel — `qdel`/`qstat` on the cost-critical stop path

**Files:**
- Modify: `src/hpc_bridge/server.py` (`_release_blocks_over_login` ~940-959 — the primary AMQP stop path)
- Modify: `src/hpc_bridge/facility/remote.py` (`_JOBID` ~249, `cancel_blocks` ~395-427 — the SSH teardown fallback)
- Test: `tests/test_server.py`, `tests/test_remote_facility.py`

**Interfaces:**
- Consumes: `app.profile.scheduler` / `self.profile.scheduler`.
- Produces: `_release_cmd(scheduler, eid) -> str` (module-level in `server.py`) building the correct `scancel`/`qdel` one-liner; `_JOBID_PBS` regex; scheduler-branched `cancel_blocks`.

- [ ] **Step 1: Write failing tests** — add to `tests/test_server.py`:

```python
def test_release_cmd_pbs_uses_qstat_and_qdel():
    from hpc_bridge.server import _release_cmd
    cmd = _release_cmd("pbs", "abc-123")
    assert "qstat -f" in cmd and "qdel" in cmd
    assert "uep.abc-123" in cmd
    assert "scancel" not in cmd and "squeue" not in cmd


def test_release_cmd_slurm_uses_squeue_and_scancel():
    from hpc_bridge.server import _release_cmd
    cmd = _release_cmd("slurm", "abc-123")
    assert "squeue" in cmd and "scancel" in cmd and "uep.abc-123" in cmd
```

And to `tests/test_remote_facility.py`:

```python
def test_pbs_jobid_regex_matches_dotted_ids():
    from hpc_bridge.facility.remote import _JOBID_PBS
    assert _JOBID_PBS.match("1234567.polaris-pbs-01")
    assert _JOBID_PBS.match("1234567")
    assert not _JOBID_PBS.match("garbage")
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_server.py -k release_cmd tests/test_remote_facility.py -k jobid_regex -v`
Expected: FAIL — `_release_cmd`/`_JOBID_PBS` do not exist.

- [ ] **Step 3: Add `_release_cmd` and branch `_release_blocks_over_login`.** In `src/hpc_bridge/server.py`, add above `_release_blocks_over_login`:

```python
def _release_cmd(scheduler: str, eid: str) -> str:
    """Login-shape shell one-liner that cancels THIS endpoint's scheduler block(s), matched
    precisely by the `uep.<eid>` StdOut marker Parsl writes under the UEP dir. Scheduler-specific:
    Slurm reads squeue/scancel; PBS reads qstat -f (unwrapping its 80-col line continuations so a
    wrapped Output_Path can't split the marker) and qdel."""
    marker = f"uep.{eid}"
    if scheduler == "pbs":
        return (
            'ids=$(qstat -f -u "$USER" 2>/dev/null '
            "| sed ':a;N;$!ba;s/\\n\\t//g' "
            f"| awk -v m={shlex.quote(marker)} 'BEGIN{{RS=\"Job Id: \"}} index($0,m){{print $1}}'); "
            '[ -n "$ids" ] && qdel $ids; echo "released ${ids:-none}"'
        )
    return (
        'ids=$(squeue -u "$USER" -h -O "JobID:30,StdOut:1024" 2>/dev/null '
        f"| grep -F {shlex.quote(marker)} | awk '{{print $1}}'); "
        '[ -n "$ids" ] && scancel $ids; echo "released ${ids:-none}"'
    )
```

Then in `_release_blocks_over_login`, replace the inline `marker = ...` + `cmd = (...)` block with:

```python
    cmd = _release_cmd(app.profile.scheduler, eid)
```

(Keep the retry loop and docstring; update the docstring's "`scancel`" mention to "the scheduler's cancel (scancel/qdel)".)

- [ ] **Step 4: Add `_JOBID_PBS` and branch `cancel_blocks`.** In `src/hpc_bridge/facility/remote.py`, beside `_JOBID` (:249):

```python
_JOBID_PBS = re.compile(r"^\d+(\.\S+)?$")  # PBS: bare or host-qualified (1234567.polaris-pbs-01)
```

In `cancel_blocks`, branch on `self.profile.scheduler`. Keep the Slurm path as-is; add before it:

```python
        if self.profile.scheduler == "pbs":
            return await self._cancel_blocks_pbs(marker)
```

and add the PBS helper method on the CLI class:

```python
    async def _cancel_blocks_pbs(self, marker: str) -> list[str]:
        """PBS variant of cancel_blocks: qstat -f -> unwrap continuations -> match marker -> qdel."""
        try:
            q = "qstat -f -u \"$USER\" 2>/dev/null | sed ':a;N;$!ba;s/\\n\\t//g'"
            rc, out, _err = await ssh_exec(
                self.target, f"bash -lc {shlex.quote(q)}", timeout=_TEARDOWN_SSH_S
            )
        except Exception:  # noqa: BLE001 - scheduler unreachable -> nothing to cancel
            return []
        if rc != 0:
            return []
        ids: list[str] = []
        for record in out.split("Job Id: ")[1:]:
            jid = record.split(None, 1)[0] if record.split() else ""
            if marker in record and _JOBID_PBS.match(jid):
                ids.append(jid)
        if ids:
            try:
                await ssh_exec(
                    self.target,
                    f"bash -lc {shlex.quote('qdel ' + ' '.join(ids))}",
                    timeout=_TEARDOWN_SSH_S,
                )
            except Exception:  # noqa: BLE001 - best-effort
                pass
        return ids
```

Note `marker` in `cancel_blocks` is already `f"uep.{endpoint_id}"`.

- [ ] **Step 5: Run tests to verify pass**

Run: `python -m pytest tests/test_server.py -k release_cmd tests/test_remote_facility.py -k jobid -v && python -m pytest tests/test_server.py tests/test_remote_facility.py -v`
Expected: PASS (new cancel tests green; existing stop/teardown tests still green — the Slurm branch is behaviourally unchanged).

- [ ] **Step 6: Lint + commit**

```bash
make lint
git add src/hpc_bridge/server.py src/hpc_bridge/facility/remote.py tests/test_server.py tests/test_remote_facility.py
git commit -m "feat(pbs): qdel/qstat cancel on the primary stop path and SSH teardown fallback"
```

---

## Task 6: Flip the runtime gate on + finish the wording

**Files:**
- Modify: `src/hpc_bridge/server.py` (`_unsupported_entry_reason` :157; tool docstrings)
- Modify: `src/hpc_bridge/models.py` (`ShapeStatus.partition`/`account` comments :34-39; `ShellOutcome`/`EndpointStatus` "billed (Slurm)" wording)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: a PBS catalog/session entry stands up (no longer `unsupported`).

- [ ] **Step 1: Write failing test** — add to `tests/test_server.py`:

```python
def test_pbs_entry_is_supported():
    from hpc_bridge.server import _unsupported_entry_reason
    from hpc_bridge.catalog.entry import CatalogEntry, Compute, Defaults
    import datetime
    entry = CatalogEntry(
        id="polaris", facility_key="alcf", facility="ALCF", description="d",
        display_name="Polaris", ssh_host="polaris",
        compute=Compute(scheduler="pbs", interface="hsn0",
                        env_setup="x", scratch_root="/home/{user}/.hpc-bridge"),
        defaults=Defaults(partition="debug"),
        last_validated=datetime.date(2026, 7, 10),
    )
    assert _unsupported_entry_reason(entry) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_server.py::test_pbs_entry_is_supported -v`
Expected: FAIL — returns `"scheduler 'pbs' not supported yet (slurm only)"`.

- [ ] **Step 3: Allow PBS.** In `src/hpc_bridge/server.py` `_unsupported_entry_reason` (:157), replace the scheduler check:

```python
    if entry.compute.scheduler not in ("slurm", "pbs"):
        return f"scheduler {entry.compute.scheduler!r} not supported yet (slurm/pbs only)"
```

- [ ] **Step 4: Finish the wording.** In `models.py`: `ShapeStatus.partition` comment "Slurm partition this shape will provision onto" → "Scheduler queue/partition this shape will provision onto"; "None for non-Slurm shapes" → "None for the login shape". In `ShellOutcome`/`EndpointStatus` the `needs_confirmation` comments "billed (Slurm) shape/block" → "scheduler compute shape/block". In `server.py` any remaining `run_shell`/`ensure_endpoint_up` docstring text saying "Slurm block" → "scheduler block", and the `needs_confirmation` notice strings that say "billed Slurm" → "scheduler compute block". Grep to confirm: `grep -rn 'billed Slurm\|Slurm block\|non-Slurm' src/hpc_bridge/`.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/ -q`
Expected: PASS (whole unit suite green).

- [ ] **Step 6: Lint + commit**

```bash
make lint
git add src/hpc_bridge/server.py src/hpc_bridge/models.py tests/test_server.py
git commit -m "feat(pbs): accept pbs entries for stand-up; scheduler-neutral status wording"
```

---

## Task 7: Update the driving-hpc skill docs

**Files:**
- Modify: `skills/driving-hpc/SKILL.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Update shape name + add PBS guidance.** In `skills/driving-hpc/SKILL.md`:
  - Replace every `shape="slurm"` / `shape=slurm` / "the **`slurm`** shape" with `shape="compute"` / the **`compute`** shape.
  - In the "Bringing up a compute node" section, add a note: "The facility's **scheduler** (Slurm or PBS) is discovered at connect time and drives how the `compute` block is realized — you don't choose it. On a **PBS** facility (e.g. ALCF Polaris): the selection gate's 'partition' is the PBS **queue**; poll the pilot with `qstat -u $USER` (not `squeue`) through the login shape; and the facility's `env_setup`/`scheduler_options` must carry any site directive such as `#PBS -l filesystems=home:eagle` (omit it and the job is **held**, never runs)."
  - In the partition-discovery bullet, add the PBS recipe: "PBS: `run_shell(\"qstat -Q\", shape=\"login\")` for queues; `run_shell(\"qstat -u $USER\", shape=\"login\")` to watch the pilot."
  - In the Stopping section, note that `stop_endpoint` cancels via the scheduler's own command (`scancel`/`qdel`) — behaviour is identical, only the underlying command differs.

- [ ] **Step 2: Sanity-check no stale `slurm` shape references remain**

Run: `grep -n 'shape=.*slurm\|`slurm` shape' skills/driving-hpc/SKILL.md`
Expected: no matches.

- [ ] **Step 3: Commit**

```bash
git add skills/driving-hpc/SKILL.md
git commit -m "docs(pbs): driving-hpc skill uses the compute shape and documents PBS"
```

---

## Task 8: Live validation on ALCF Polaris (real SU — runbook, no unit test)

Do this LAST, interactively, once Tasks 1–7 are merged and the endpoint code is live in the running MCP server (restart / `/mcp reconnect` so the new `compute` shape + PBS template load). This step **spends a small amount of real SU** on the `debug` queue — get the user's go-ahead at the spend gate.

**Precondition:** the Polaris login-only endpoint from earlier is still online → reconnect is zero-SSH. Polaris `ssh_host="polaris"`, user `rchard`, MFA (preauth may be needed if the master expired).

- [ ] **Step 1: Reconnect + confirm PBS discovery.** `connect_facility(facility="polaris", ssh_host="polaris")`. Expect the draft/entry to carry `scheduler="pbs"` and a queue list; confirm `interface` (expect `hsn0`) and set `cpus_per_node` (Polaris: 32) + `scheduler_options="#PBS -l filesystems=home:eagle"` in the session `details`. Re-call with `details=` to register.

- [ ] **Step 2: Discover through the login shape.** `run_shell("qstat -Q", shape="login")` (queues) and `run_shell("qstat -u $USER", shape="login")` (baseline). Surface the allocation balance.

- [ ] **Step 3: Spend gate.** Present allocation + `debug` queue + est. cost via `AskUserQuestion`; on approval `ensure_endpoint_up(shape="compute", account=<acct>, partition="debug", confirm_spend=True)`.

- [ ] **Step 4: Wait for the pilot.** Poll `run_shell("qstat -u $USER", shape="login")` until the job is `R` (running). `PENDING`/`Q` spends no SU.

- [ ] **Step 5: Prove it ran on a compute node.** `ensure_endpoint_up(shape="compute")` to confirm the worker registered, then `run_shell("hostname; nproc", shape="compute")`. Expect a Polaris **compute** hostname (`xNNNN…`), not `polaris-login-*`. Run one small compute task.

- [ ] **Step 6: Prove spending stops — the acceptance crux.** `stop_endpoint`; require `status="down"` (retry on `draining`). Then `run_shell("qstat -u $USER", shape="login")` and confirm the pilot job is **gone** (qdel'd). If it lingers, the cancel path failed — do NOT call this validated.

- [ ] **Step 7: Record the confirmed facility.** Write the validated Polaris details (queue, `hsn0`, `cpus_per_node`, `env_setup`, `scheduler_options`) to memory (`polaris-alcf-facility.md`) and, if promoting beyond session-local, a `catalog/seed/polaris.yaml` entry with `scheduler: pbs`.

- [ ] **Step 8: Land the branch.** With unit tests green and the live run confirmed: `scriv create --edit` (changelog fragment — required on PRs), then open the PR from `feat/pbs-scheduler-support`.

---

## Self-review notes

- **Spec coverage:** §1 seam→T1; §2 shapes/templates→T2+T3; §3 discovery→T4; §4 cancel (both spots)→T5; §5 models/API/docs→T1(FacilityDetails)+T6(gate/wording)+T7(skill); §6 non-goals respected (no LSF; fixed `MpiExecLauncher`; `cpus_per_node` via details); §7 validation→per-task unit tests + T8 live runbook.
- **Ordering/green:** FacilityDetails widening (T1) precedes discovery's `scheduler="pbs"` draft (T4); the runtime `unsupported` gate flips only in T6 after template (T3) and cancel (T5) exist, so no half-enabled PBS stand-up is reachable earlier.
- **`cpus_per_node`:** emitted conditionally (like `available_accelerators`), so unset → Parsl's own default; no arbitrary global constant. This refines the spec's `| default(@@CPS@@)` sketch.
- **Type consistency:** `compute` (bool flag) and `_release_cmd(scheduler, eid)`/`_JOBID_PBS`/`_default_queue`/`_cancel_blocks_pbs` names are used identically across the tasks that define and consume them.
