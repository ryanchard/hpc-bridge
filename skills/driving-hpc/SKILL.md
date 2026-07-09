---
name: driving-hpc
description: How to drive HPC well through hpc-bridge. SSH is a one-time bootstrap: stand up (or reuse) a Globus Compute endpoint on the login node ONCE, then do everything — discovery AND compute — THROUGH the endpoint via run_shell, never a fresh SSH (which can force a re-auth on MFA facilities). Flow to bring up a node: select the machine (list_facilities → connect_facility, which brings up the free login shape and lists your allocations) → discover partitions via run_shell(shape="login") → present an allocation+partition+budget gate → provision the billed block with account and confirm_spend=True → wait by polling squeue through the endpoint. Persistent per-session shell; 10 MB result cap; a cold first call means a worker is still warming.
---

# Driving HPC with hpc-bridge

- You have a **persistent session shell**: `cd` and relative paths carry across turns. Call `reset_session` for a clean slate.
- A call may be **cold** on first use — `ensure_endpoint_up`/`run_shell` reporting `provisioning`/`cold_start` means a worker is still warming (a Slurm block may be allocating); retry shortly.
- Results are capped at ~10 MB: for verbose commands, redirect to a file and read it back in bounded chunks.

## SSH once, then work through the endpoint

hpc-bridge stands up a personal Globus Compute endpoint on the login node. Reaching it costs **one** SSH bootstrap — and often **zero**, because a still-running endpoint from a prior session is reused over the network. **Treat SSH as that one-time bootstrap, not a channel:** once the endpoint is up, do *everything* through it — discovery as well as compute — via `run_shell`. Every fresh SSH risks an interactive re-auth on a multi-factor (Duo/MFA) facility, so we avoid it.

Concretely: the **`login` shape** runs commands on the **login node** through the endpoint (a free `LocalProvider` — no allocation, no cost) — that's your no-SSH channel for discovery (`sinfo`, `mybalance`, `squeue`). The **`slurm` shape** runs on a billed compute block. `login_shell` (raw SSH) is a **cold-start escape hatch only** — see the end.

## Bringing up a compute node: select → discover → gate → provision → wait

A Slurm block **spends your allocation**, so don't provision blind. Pick a machine, discover what's available and what it costs, let the user choose, then provision — all through the endpoint.

**0. Select the facility.** `list_facilities()` lists the catalogued HPC systems; pick one (or the user names it) and call `connect_facility(facility="<id>")` — the `facility` arg is the `id` or `subject` from the list (e.g. `"anvil"` or `"purdue:anvil"`). This **binds the facility and establishes the endpoint** — standing up, or **reusing with zero SSH**, the manager plus a free login-node worker — then runs the facility's allocation command over the endpoint and returns `needs_account` with your **allocations** (account + balance). It's the only step that may SSH, and only once. `provisioning` ⇒ the login node is still warming; call again shortly.
   - **`needs_facility_details` ⇒ the facility isn't catalogued — discover it, don't interrogate.** The user supplies only *access*; you discover the config. Pass the login host — `connect_facility(facility, ssh_host="<login-host-or-alias>")` (SSH user + key come from your `~/.ssh/config`, or optional env overrides — not from you) — and the tool **probes the login node** and returns **`proposed_facility_details`** with a filled-in draft plus a `notice` flagging the low-confidence fields. **Review that draft *with the user* via `AskUserQuestion` — above all confirm `interface`** (a wrong NIC ⇒ workers never register) and anything the notice flags — fix what's wrong, then call `connect_facility(facility, details=<confirmed>)`. That registers a session-local facility and brings up the login shape, whose canary validates the values. Use `{user}`/`$USER` in paths (both resolve to the SSH login name). You pass `details` once — re-calls remember it.
   - **Can't discover** (no SSH host yet, or the probe can't reach the node) ⇒ fall back to filling `details` from the **user** directly — the schema's field descriptions are the question list. Offer option values only for the genuinely common fields (`interface`: ib0 / hsn0; `partition`: the queues); for facility-specific free-text (`ssh_host`, `env_setup`, `scratch_root`) ask — do **not** invent a plausible-but-wrong value (a guessed `env_setup` they rubber-stamp just wastes a bootstrap).
   - **`needs_preauth` ⇒ the host needs a one-time interactive login (a password and/or Duo/MFA) that you must NOT handle.** The result carries a `preauth_command` (an `ssh -fN …` line). **Relay it for the user to run in THEIR OWN terminal** — they type the password / approve Duo there, directly with ssh. **Never ask for, type, or run a command containing their password or passcode** — a secret must never reach you or a tool call. It opens a reusable connection; when the user confirms it's up, call `connect_facility` again and the whole session rides it with no further SSH auth. (A non-secret Duo *push* choice you may relay; a **password or passcode is a secret** — hand it off.)
   - **`env_setup` must leave `globus-compute-endpoint` on `PATH`** — a site `module load` and/or `source <venv>/bin/activate`. If the facility has no pre-installed endpoint but has `uv` (or `pip`), `env_setup` can self-provision so the **first connect installs the toolchain**, kept idempotent: `[ -d {venv} ] || uv venv {venv}; . {venv}/bin/activate; command -v globus-compute-endpoint >/dev/null || uv pip install -q globus-compute-endpoint`. The guards make every later call — and the compute worker, which replays `env_setup` — a no-op activate.
   - *(If a facility is pinned by env — `HPC_BRIDGE_MACHINE` — you can instead `ensure_endpoint_up(shape="login")` and read the balance yourself in step 1.)*

**1. Discover partitions THROUGH the endpoint** (over the network, no SSH) with `run_shell(..., shape="login")`. Your **allocations already came back from `connect_facility`** (step 0); here you fill in the partition picture:
   - Partitions: `run_shell("sinfo -h -o '%P|%a|%l|%D|%c|%m|%F'", shape="login")` → `name|avail|timelimit|nodes|cores|mem_MB|A/I/O/T-nodes`. The **I** (idle) in the last field is *live* availability — 0 idle will queue.
   - Accounts/QOS: `run_shell("sacctmgr -nP show assoc where user=$USER format=Account,QOS,Partition", shape="login")` — the allocation→partition/QOS mapping behind the balances.
   - **Re-read balance** (optional): balances came from `connect_facility`; to refresh, `run_shell("mybalance", shape="login")` on Anvil (elsewhere on ACCESS `xdusage -p <project>`).
   - **Recipe, not rule:** if a tool isn't found, adapt (`scontrol show partition` / `qstat -Q`; `module load` first if a binary is missing). A `cold_start` while the login worker warms — just retry.
   - **Gotcha:** partition `timelimit` may read `infinite` because the real cap is per-**QOS** — if walltime matters, also `run_shell("sacctmgr -nP show qos format=Name,MaxWall", shape="login")`.

**2. Present the gate** with `AskUserQuestion`: the **allocation** (when there's a real choice — e.g. a CPU vs `-gpu` account, each with its balance) and the **partition** (each option carrying node size + **live idle count** + any caveat: saturated → would queue; a GPU partition needs the `-gpu` account). Put the **chosen allocation's balance and the rough block cost** (≈ nodes × walltime × the facility's SU rate) in the question text so the choice is made against the budget. Recommend the cheapest/fastest sensible default (usually a shared/sub-node partition with idle nodes now).

**3. Provision the billed block, confirming spend.** `ensure_endpoint_up(shape="slurm", account="<allocation>", partition="<choice>", confirm_spend=True)`. The manager is already up from step 0, so this just kicks the Slurm block. The account, partition, and spend acknowledgement **persist for the session** (later calls reuse them) until you change them or `stop_endpoint`. A first call returning `provisioning` is normal.

**4. Wait for the block, then confirm.** The pilot starts `PENDING` and flips to `RUNNING` once Slurm gives it a node; watch its state with `run_shell("squeue -u $USER -h -o '%i|%P|%T|%r'", shape="login")` — over the endpoint, no SSH. Once `RUNNING`, call `ensure_endpoint_up(shape="slurm")` once to confirm the worker registered. A `PENDING` job spends no SU.

This is a *policy gate*: discovery surfaces the options + the budget, the human picks, and the pick drives provisioning.

- **The spend floor is enforced, not advisory.** A billed Slurm shape **without** `confirm_spend=True` returns `needs_confirmation` and starts **nothing**. Only set `confirm_spend=True` *after* surfacing the balance to the user — it is your acknowledgement on their behalf, not a default to sprinkle on.
- **Gate, not interrogation:** the consequential, cost-bearing choices are gated — the **allocation** (only when there's a real choice), the partition, and the spend confirmation; walltime/nodes are sensible-defaulted. Don't prompt for the unambiguous (a single allocation → just use it).
- **Headless fallback (can't prompt):** stay on `shape="login"` (free, read-only) rather than spend unattended; only auto-confirm a billed block in autonomous mode with an explicit budget signal.

## Stopping

`stop_endpoint` means **stop spending**, not tear the endpoint down: it cancels the billed Slurm block over the login endpoint (AMQP, no SSH) and **leaves the login-node endpoint online** for reuse. **Read the status, don't assume:** `status="down"` means the cancel was *confirmed* — the block is gone, no more SU accrue. `status="draining"` means the cancel dispatched but the login release channel was cold, so **spend is not confirmed stopped** — the block may still be burning. On `draining`, **call `stop_endpoint` again after a few seconds** (the channel is now warming) until you get `down`; idle-release (~10 min) is only the backstop, not a substitute for confirming. Don't tell the user spending stopped until you've seen `down`. The endpoint stays up either way, so reconnecting later (`connect_facility`/`ensure_endpoint_up`) is **zero-SSH**; a quick `run_shell(shape="login")` after a stop just reuses the still-online endpoint.

## When to use `login_shell` (raw SSH) instead

`login_shell` runs on the login node over a **fresh SSH connection** — reserve it for:

- the **cold start** where no endpoint exists yet and you specifically *don't* want to stand one up (a quick one-off peek), or
- a **fallback** if the `login` shape can't come up.

On an MFA facility **every `login_shell` may trigger a re-auth**, so for the provisioning flow above prefer the endpoint path (`run_shell(shape="login")`), which authenticates once at bootstrap and then runs over the network.
