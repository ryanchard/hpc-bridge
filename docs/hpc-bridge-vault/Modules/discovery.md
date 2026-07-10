# discovery.py

> [!abstract] Role
> The raw-SSH **login-node probe** for an **un-indexed** facility: one batched command → a *proposed* [[models|FacilityDetails]] draft the user confirms. The cache-miss path of the [[Facility catalog|catalog]] — the pre-endpoint discovery channel the [[Globus index discovery channel]] reserved, now wired.

## What it does

`discover_facility_details(target)` (`discovery.py:50`) runs **one** batched `ssh_exec` over a bare [[facility-remote|SshTarget]] — just an `ssh_host` plus `~/.ssh/config` credentials, with **no endpoint and no catalog entry needed yet** — then `parse_probe()` (`:63`) turns the output into a `(FacilityDetails, notes)` pair. `notes` names the low-confidence fields the agent must confirm with the user — `interface` above all.

## How it works

- **One probe, framed.** `_PROBE` is a single compound bash script (one SSH round-trip ⇒ MFA-once-friendly) emitting `KEY=value` lines between `HPCB_PROBE_BEGIN`/`END` sentinels (so a login banner can't pollute the parse); multi-valued facts (`PART`/`QUEUE`/`NIC`) repeat their key. `_collect()` reads only the framed block.
- **Scheduler detection.** The probe checks `sbatch` (⇒ `scheduler="slurm"`) then `qsub` (⇒ `"pbs"`); neither ⇒ default `slurm` + a flag. This picks the queue source (`sinfo` vs `qstat -Q`) and, downstream, the [[facility-remote|scheduler template]].
- **Deterministic per-field proposers** (never model inference): `_interface()` (`:181`) picks the worker NIC from `ip -o -4 addr`, ranking the dedicated compute fabric (`_FAST_NIC_ORDER` — `hsn`/`ipogif`/`ib`/`hsi` over a `bond` mgmt NIC), else the single candidate — *always* flagged; `_default_partition()` (`:159`, Slurm `sinfo`) / `_default_queue()` (`:172`, PBS `qstat -Q`) prefer a cheap `debug`/`shared`/`prod`; scratch from `$SCRATCH`/`$WORK`/`$HOME` (templated to `{user}`); `_env_setup()` returns an activate line if gce is already installed, else the idempotent **uv create-venv + install** one-liner (`_UV_ENV_SETUP`) when `uv` is present; `_allocation()` detects `mybalance`/`xdusage`.
- **`{user}` templating** comes from the probe's own `whoami`, so the draft is per-user with no env var.

Reached from `connect_facility`'s index-miss path: `_propose_or_ask` ([[server]] `:871`) builds the bare target and returns `phase="proposed_facility_details"` (or `needs_preauth` when the host needs an interactive login).

> [!note] The full resolution ladder
> This probe is the **last rung**. `connect_facility` walks session → `facilities.json` (local cache) → catalog → probe, first hit wins — the agent never reads the cache itself; it's resolved server-side inside the one call. See the interactive diagram: **[the resolution ladder](../assets/discovery-resolution-ladder.html)** (open in a browser), which also maps where the agent can *deviate* — bypassing `connect_facility`, an inconsistent `ssh_host` key, or a fabricated `details=`.

> [!warning] Propose, don't invent
> Discovery *proposes* discovered facts for the user to confirm; it must not silently commit. A wrong `interface`/`env_setup` is caught by the [[Warmth, the canary & cold-start|canary]] (the worker never registers), so the draft is **checkable, not trusted** — elicit → propose → confirm → validate.

## See also
[[Globus index discovery channel]] · [[server]] · [[models]] · [[facility-remote]] · [[Discovery channel model]] · [[Facility catalog]]
