---
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

**0. Select the facility.** `list_facilities()` lists the catalogued HPC systems; pick one (or the user names it) and call `connect_facility(facility="<id>")` — the `facility` arg is the `id` or `subject` from the list (e.g. `"anvil"` or `"purdue:anvil"`). This **binds the facility and establishes the endpoint** — standing up, or **reusing with zero SSH**, the manager plus a free login-node worker — then runs the facility's allocation command over the endpoint and returns `needs_account` with your **allocations** (account + balance). It's the only step that may SSH, and only once. `provisioning` ⇒ the login node is still warming; call again shortly. *(If a facility is pinned by env — `HPC_BRIDGE_MACHINE` — you can instead `ensure_endpoint_up(shape="login")` and read the balance yourself in step 1.)*

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

`stop_endpoint` is the explicit exit: it cancels the Slurm block (over the warm login shape) and stops the manager. Its result is **authoritative** — `"endpoint stopped; compute block released"` means the block is gone. **Don't** then `run_shell` a `squeue` to "double-check": that call would cold-start a *brand-new* endpoint (the one you just stopped is down), churning a bootstrap for nothing. If you truly must verify after a stop, use `login_shell("squeue -u $USER")` (raw SSH, stands nothing up) — but normally just trust the stop result.

## When to use `login_shell` (raw SSH) instead

`login_shell` runs on the login node over a **fresh SSH connection** — reserve it for:

- the **cold start** where no endpoint exists yet and you specifically *don't* want to stand one up (a quick one-off peek), or
- a **fallback** if the `login` shape can't come up.

On an MFA facility **every `login_shell` may trigger a re-auth**, so for the provisioning flow above prefer the endpoint path (`run_shell(shape="login")`), which authenticates once at bootstrap and then runs over the network.
