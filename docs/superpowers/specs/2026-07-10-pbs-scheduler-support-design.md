# PBS scheduler support for hpc-bridge

**Status:** approved design, pre-implementation
**Date:** 2026-07-10
**Branch:** `feat/pbs-scheduler-support`

## Problem

hpc-bridge can only stand up scheduler compute blocks on **Slurm** facilities. ALCF
Polaris (and many leadership-class systems) run **PBS Pro**. This is a limitation of
hpc-bridge's own wrapper, not of Globus Compute or Parsl — Parsl ships
`PBSProProvider`, and ALCF's own docs use it. Today a PBS facility can only reach the
free login-node shape; its compute nodes are unreachable.

Concretely, on Polaris right now:
- `connect_facility` discovery reports `SCHED=none` (probes `sbatch`, which is absent).
- `server.py:_unsupported_entry_reason` rejects any `scheduler != "slurm"`.
- `FacilityDetails.scheduler` is `Literal["slurm"]`.
- The compute (scheduler) shape is hardwired to `SlurmProvider` + `SrunLauncher` + `partition`.

## Goal

Add PBS Pro as a first-class scheduler alongside Slurm, so a PBS facility discovers,
provisions a scheduler block, runs tasks on compute nodes, and — critically — **stops
spending** through the same tools, validated live on Polaris.

## Decisions (from brainstorming)

1. **Per-scheduler dispatch**, not a registry or a single mega-template.
   `config_template()` returns a clean Slurm-only *or* PBS-only template selected by
   `profile.scheduler`; cancel/poll/discovery branch on scheduler where they must.
2. **Rename the scheduler-block shape `slurm` → `compute`.** The scheduler is a *facility
   property*, not an agent choice. Shapes become `("login", "compute")`.
3. **`qdel`/`qstat` cancel is required**, not optional — the primary stop path must
   actually halt a PBS block.
4. **Extend discovery** to auto-detect PBS (`qsub`, `qstat -Q`), so a fresh PBS
   facility self-discovers like Slurm does.
5. **Validate live on Polaris** as an explicit acceptance step (see §7), in addition
   to unit tests.

## Design

### 1. The seam — thread `profile.scheduler` through

- `MachineProfile` gains `scheduler: Literal["slurm", "pbs"] = "slurm"` (today Slurm is
  implicit).
- `CatalogEntry.compute.scheduler` **already** exists and already allows `"pbs"`.
  `profile_kwargs()` currently drops it ("consumed by the transport layer") — stop
  dropping it so it reaches the profile.
- All scheduler-specific logic dispatches on `profile.scheduler`. No abstraction layer
  — plain dispatch is right for two schedulers (YAGNI on a registry until LSF appears).

### 2. Shapes + templates

- `shapes.py`: `SHAPES = ("login", "compute")`.
- The in-template boolean `is_slurm` becomes **`compute`** — it now means "scheduler-block
  (compute-node) shape vs login node," *not* "is Slurm." Name matches the shape:
  `login` sets `compute=False`, the `compute` shape sets `compute=True`. It stays a
  **bool** (not a string compare) because the endpoint manager's `_sanitize_user_json`
  json.dumps's every string user-opt — the existing documented reason. (Deliberately not
  `billed`: some clusters — Polaris `debug`, campus systems — submit scheduler jobs for
  free, so the flag names the *scheduler submit*, not cost.)
- `shape_config("compute")` no longer injects `provider_type`; the per-scheduler
  template default supplies it (`SlurmProvider` / `PBSProProvider`). `shape_config("login")`
  still pins `provider_type: LocalProvider`.
- `config_template(profile)` returns `_SLURM_TEMPLATE` or `_PBS_TEMPLATE`. Each is
  self-contained: a shared LocalProvider `{% else %}` login branch plus its own compute
  branch. The login branch YAML is duplicated across the two templates by design —
  each template stays readable and whole rather than re-merging into one branched
  template.

**PBS compute branch** (`PBSProProvider`):
```yaml
  provider:
    type: {{ provider_type | default('PBSProProvider') }}
{% if compute | default(true) %}
    account: {{ account | default(@@ACCOUNT@@) }}
    queue: {{ partition | default(@@PARTITION@@) }}
    walltime: {{ walltime | default(@@WALLTIME@@) }}
    nodes_per_block: {{ nodes_per_block | default(@@NODES@@) }}
    cpus_per_node: {{ cpus_per_node | default(@@CPUS@@) }}
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
```
- The generic `partition` value slot renders under the `queue:` key for PBS (one
  internal field, scheduler-appropriate YAML key). This keeps `_apply_partition` and
  the `_VALID_PARTITION` validator working unchanged.
- **`cpus_per_node`** is a new field (see §5). PBSPro defaults it to 1 and would
  under-request a node; it must be explicit.
- **Facility-specific directives stay out of the template.** Polaris needs
  `#PBS -l filesystems=home:eagle` (omit it → the job is *held*) and typically
  `-l place=scatter`; these live in the facility's `scheduler_options`, which already
  flows through. The template stays facility-agnostic.

### 3. Discovery (`discovery.py`)

- Probe gains, alongside the `sbatch` check:
  `if command -v qsub >/dev/null 2>&1; then echo SCHED=pbs; fi` and `qstat -Q 2>/dev/null`
  for queue names (framed `QUEUE=`).
- `parse_probe` branches on `SCHED`: PBS → `scheduler="pbs"`, `partition` from the PBS
  queue list (prefer `debug`, then the existing preference order).
- Interface stays a single low-confidence field, always flagged. Ranking is refined so
  the **compute fabric wins** (`hsn`/`ib` ranked above `bond`): a worker on a PBS
  compute node must reach the interchange over the high-speed fabric (Slingshot
  `hsn0`), and that NIC also exists on the login node where the interchange binds. The
  live run confirms the final value.

### 4. Cancel — the cost-critical path (two spots)

Both dispatch on `profile.scheduler`; both find *our* jobs by the same
`uep.<endpoint_id>` StdOut marker Parsl writes under the UEP dir, so neither touches
another endpoint's jobs.

- `_release_blocks_over_login` (`server.py:940`) — **primary** stop path, runs over the
  login shape via AMQP (no SSH). This is the cost-critical one: if it stays Slurm-only,
  `stop_endpoint` on a PBS facility returns "released none" and the block burns to
  walltime.
- `cancel_blocks` (`remote.py:395`) — SSH teardown fallback.

**PBS command** (vs Slurm's `squeue … | scancel`):
```sh
ids=$(qstat -f -u "$USER" 2>/dev/null \
  | sed ':a;N;$!ba;s/\n\t//g' \
  | awk 'BEGIN{RS="Job Id: "} /uep.<eid>/{print $1}')
[ -n "$ids" ] && qdel $ids; echo "released ${ids:-none}"
```
- `sed ':a;N;$!ba;s/\n\t//g'` unwraps `qstat -f`'s 80-column line continuations before
  matching — otherwise a wrapped `Output_Path` can split the `uep.<eid>` marker. Known
  qstat quirk; confirmed by the live run.
- PBS job-id regex: `^\d+(\.\S+)?$` (e.g. `1234567.polaris-pbs-01`), vs Slurm's
  `^\d+(_\d+)?$`.

### 5. Models, API surface, docs

- `FacilityDetails.scheduler`: `Literal["slurm"]` → `Literal["slurm", "pbs"]`
  (default `"slurm"`); fix the "Only 'slurm' is supported" Field description.
- New `cpus_per_node: int | None = None` on `Defaults` and `MachineProfile`; PBS
  template emits it (Slurm ignores it). Session/catalog details set it per facility.
- `server.py:_unsupported_entry_reason`: allow `pbs`.
- `_apply_partition` / `_apply_account` guards: `is_slurm` → `compute`.
- Tool docstrings + `ShapeStatus.partition` wording: `slurm` → `compute`,
  "Slurm partition" → "scheduler queue/partition."
- **driving-hpc skill**: `shape="slurm"` → `"compute"`; add a PBS note — queue vs
  partition, poll with `qstat` (not `squeue`), and the `filesystems` hold gotcha.

### 6. Non-goals

- LSF (the catalog model lists it, but no implementation this cut).
- MPI-launcher tuning knobs beyond `MpiExecLauncher(bind_threads=true)` — a fixed,
  documented default for now; a `launcher`/`cpus_per_node` override path is a follow-up
  if a facility needs it.
- Auto-discovering `cpus_per_node` from a compute-node resource query — set via details
  for now; discovery may propose it later.

## 7. Validation & acceptance

**Unit tests (no live block):**
- PBS template renders expected YAML: `PBSProProvider`, `queue` (from the `partition`
  slot), `MpiExecLauncher`+`bind_threads`, `cpus_per_node`, `scheduler_options`
  passthrough, `min_blocks: 0`.
- `compute` toggles the login (LocalProvider) vs compute branch correctly in both
  templates.
- Discovery parses a `qstat -Q` fixture → `scheduler="pbs"` + queue list; interface
  ranking prefers `hsn0` over `bond0`.
- Cancel builds the correct `qdel` command and PBS job-id regex from a `qstat -f`
  fixture that includes a **line-wrapped** `Output_Path`.
- `_unsupported_entry_reason` accepts a PBS entry; `_apply_*` guards fire on `compute`.

**Live validation on Polaris (real SU, `debug` queue):**
1. `connect_facility(facility="polaris", ssh_host="polaris")` → PBS auto-discovered
   (`scheduler="pbs"`, queue list), interface confirmed.
2. Gate on allocation + `debug` queue + est. cost; `ensure_endpoint_up(shape="compute",
   account=…, partition="debug", confirm_spend=True)`.
3. Poll the PBS job to `RUNNING` via `qstat` over the login shape; confirm the worker
   registers on a **compute node** (`run_shell(shape="compute")` reports a `xNNNN`
   compute hostname, not a login node).
4. Run a small test task on the compute node.
5. `stop_endpoint` → confirm it returns `down` **and** the PBS job is gone
   (`qstat` shows it `qdel`'d) — i.e. spending actually stopped. This is the acceptance
   crux for the cost-critical path.
6. Record the confirmed Polaris facility details (queue, interface, `cpus_per_node`,
   `scheduler_options` incl. `filesystems`) to memory / a seed catalog entry.

## Touchpoint summary

| File | Change |
|------|--------|
| `catalog/entry.py` | `profile_kwargs()` include `scheduler` (model already allows pbs) |
| `facility/remote.py` | `MachineProfile.scheduler` + `cpus_per_node`; `_PBS_TEMPLATE`; scheduler dispatch in `config_template`; PBS `cancel_blocks` + jobid regex |
| `shapes.py` | `SHAPES=("login","compute")`; `is_slurm`→`compute`; drop `provider_type` from compute shape |
| `discovery.py` | PBS probe (`qsub`,`qstat -Q`); PBS branch in `parse_probe`; interface ranking |
| `models.py` | `FacilityDetails.scheduler` widen; `Defaults.cpus_per_node`; `ShapeStatus` wording |
| `server.py` | `_unsupported_entry_reason` allow pbs; `_apply_*` guard on `compute`; `_release_blocks_over_login` PBS variant; tool docstrings |
| `skills/driving-hpc/SKILL.md` | `slurm`→`compute`; PBS notes |
| `tests/` | template, discovery, cancel unit tests |
