---
description: How to drive HPC well through hpc-bridge. SSH is a one-time bootstrap: stand up (or reuse) a Globus Compute endpoint on the login node ONCE, then do everything — discovery AND compute — THROUGH the endpoint via run_shell, never a fresh SSH (which can force a re-auth on MFA facilities). Flow to bring up a node: establish the endpoint (shape="login") → discover partitions+balance via run_shell(shape="login") → present a selection+budget gate → provision the billed block with confirm_spend=True → wait by polling squeue through the endpoint. Persistent per-session shell; 10 MB result cap; a cold first call means a worker is still warming.
---

# Driving HPC with hpc-bridge

- You have a **persistent session shell**: `cd` and relative paths carry across turns. Call `reset_session` for a clean slate.
- A call may be **cold** on first use — `ensure_endpoint_up`/`run_shell` reporting `provisioning`/`cold_start` means a worker is still warming (a Slurm block may be allocating); retry shortly.
- Results are capped at ~10 MB: for verbose commands, redirect to a file and read it back in bounded chunks.

## SSH once, then work through the endpoint

hpc-bridge stands up a personal Globus Compute endpoint on the login node. Reaching it costs **one** SSH bootstrap — and often **zero**, because a still-running endpoint from a prior session is reused over the network. **Treat SSH as that one-time bootstrap, not a channel:** once the endpoint is up, do *everything* through it — discovery as well as compute — via `run_shell`. Every fresh SSH risks an interactive re-auth on a multi-factor (Duo/MFA) facility, so we avoid it.

Concretely: the **`login` shape** runs commands on the **login node** through the endpoint (a free `LocalProvider` — no allocation, no cost) — that's your no-SSH channel for discovery (`sinfo`, `mybalance`, `squeue`). The **`slurm` shape** runs on a billed compute block. `login_shell` (raw SSH) is a **cold-start escape hatch only** — see the end.

## Bringing up a compute node: establish → discover → gate → provision → wait

A Slurm block **spends your allocation**, so don't provision blind. Discover what's available and what it costs, let the user choose, then provision — all through the endpoint.

**0. Establish the endpoint (the control channel) first.** `ensure_endpoint_up(shape="login")`. This stands up — or **reuses, with zero SSH** — the endpoint manager, plus a free login-node worker. No allocation, no `confirm_spend` (the login shape is free). This is the **only** step that may SSH, and only once (a single bootstrap if no endpoint is already online). Wait for `up`.

**1. Discover THROUGH the endpoint** (over the network, no SSH) with `run_shell(..., shape="login")`:
   - Partitions: `run_shell("sinfo -h -o '%P|%a|%l|%D|%c|%m|%F'", shape="login")` → `name|avail|timelimit|nodes|cores|mem_MB|A/I/O/T-nodes`. The **I** (idle) in the last field is *live* availability — 0 idle will queue.
   - Accounts: `run_shell("sacctmgr -nP show assoc where user=$USER format=Account,QOS,Partition", shape="login")`.
   - **Allocation balance:** `run_shell("mybalance", shape="login")` on Anvil; elsewhere on ACCESS `run_shell("xdusage -p <project>", shape="login")`. Live remaining SUs — the authoritative budget number, read fresh each time.
   - **Recipe, not rule:** if a tool isn't found, adapt (`scontrol show partition` / `qstat -Q`; the facility's own balance tool; `module load` first if a binary is missing; if there's no balance tool, say so and let the human decide). The first `shape="login"` call may return `cold_start` while the login worker warms — just retry.
   - **Gotcha:** partition `timelimit` may read `infinite` because the real cap is per-**QOS** — if walltime matters, also `run_shell("sacctmgr -nP show qos format=Name,MaxWall", shape="login")`.

**2. Present the gate** with `AskUserQuestion`: each option a partition, its description carrying node size + **live idle count** + any caveat (saturated → would queue; a GPU partition needs the `-gpu` account). Put the **remaining balance and the rough block cost** (≈ nodes × walltime × the facility's SU rate) in the question text so the choice is made against the budget. Recommend the cheapest/fastest sensible default (usually a shared/sub-node partition with idle nodes now).

**3. Provision the billed block, confirming spend.** `ensure_endpoint_up(shape="slurm", partition="<choice>", confirm_spend=True)`. The manager is already up from step 0, so this just kicks the Slurm block. The partition and the spend acknowledgement **persist for the session** (later `run_shell`/`ensure_endpoint_up` calls reuse them) until you change the partition or `stop_endpoint`. A first call returning `provisioning` is normal.

**4. Wait THROUGH the endpoint.** Poll `run_shell("squeue -u $USER -h -o '%i|%P|%T|%r'", shape="login")` — over the network, no SSH — for the pilot's state (`PENDING`→`RUNNING`) and *why* it pends (`Priority`, `Resources`). Wait for `RUNNING`, then call `ensure_endpoint_up(shape="slurm")` **once** to confirm the worker registered (`up`).
   - **Between polls, don't foreground-`sleep`** (the harness blocks a standalone `sleep`). The queue state comes from the `run_shell` **MCP tool**, so a `Monitor`/`until <check>` loop can't drive the wait (it tests a *local shell* condition, not a tool call). Instead background a short wait (Claude Code: `run_in_background: true` on a `sleep`) and re-poll when it elapses — relaxed cadence (~20–30 s, escalate if it stays pending), and don't hammer `ensure_endpoint_up`.
   - A pend can be seconds (node booting) or many minutes (queued behind priority) — the `squeue` reason tells you which, so you can tell the user instead of polling blindly.

This is a *policy gate*: discovery surfaces the options + the budget, the human picks, and the pick drives provisioning.

- **The spend floor is enforced, not advisory.** A billed Slurm shape **without** `confirm_spend=True` returns `needs_confirmation` and starts **nothing**. Only set `confirm_spend=True` *after* surfacing the balance to the user — it is your acknowledgement on their behalf, not a default to sprinkle on.
- **Gate, not interrogation:** only the partition + the spend confirmation are gated (the consequential, cost-bearing choices); account/walltime/nodes are sensible-defaulted. Don't prompt for the unambiguous.
- **Headless fallback (can't prompt):** stay on `shape="login"` (free, read-only) rather than spend unattended; only auto-confirm a billed block in autonomous mode with an explicit budget signal.

## When to use `login_shell` (raw SSH) instead

`login_shell` runs on the login node over a **fresh SSH connection** — reserve it for:

- the **cold start** where no endpoint exists yet and you specifically *don't* want to stand one up (a quick one-off peek), or
- a **fallback** if the `login` shape can't come up.

On an MFA facility **every `login_shell` may trigger a re-auth**, so for the provisioning flow above prefer the endpoint path (`run_shell(shape="login")`), which authenticates once at bootstrap and then runs over the network.
