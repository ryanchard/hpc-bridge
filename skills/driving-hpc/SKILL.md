---
description: How to drive HPC well through hpc-bridge. Before provisioning/starting a compute node on a facility, discover its partitions AND the allocation balance, present them as a selection+budget gate, then provision with confirm_spend=True (a billed Slurm block won't start without it). You have a persistent per-session shell (relative paths work; call reset_session for a clean slate); redirect verbose output to a file and read it back in bounded chunks (10 MB result cap); a cold first call means nodes are being allocated.
---

# Driving HPC with hpc-bridge

- You have a **persistent session shell**: `cd` and relative paths carry across turns. Call `reset_session` for a clean slate.
- The endpoint may be **cold** on first use — `ensure_endpoint_up` reporting `provisioning` means nodes are being allocated; retry shortly.
- Results are capped at ~10 MB: for verbose commands, redirect to a file and read it back in chunks.

## Before starting a node: discover partitions + budget, then gate, then provision

When asked to **start / provision / spin up a compute node** on a facility, do **not** provision blind. A Slurm block **spends your allocation**, so first *discover* what's available **and what it costs you**, let the user choose, then provision. Discovery is a read-only login-node probe that costs nothing (no block, no allocation):

1. **Gather** (Slurm facilities) with the `login_shell` tool — it runs on the login node and starts nothing:
   - Partitions: `login_shell("sinfo -h -o '%P|%a|%l|%D|%c|%m|%F'")` → `name|avail|timelimit|nodes|cores|mem_MB|A/I/O/T-nodes`. The **I** (idle) in the last field is *live* availability — a partition with 0 idle will queue.
   - Accounts: `login_shell("sacctmgr -nP show assoc where user=$USER format=Account,QOS,Partition")`.
   - **Allocation balance** (so the human spends with the number in view): `login_shell("mybalance")` on Anvil; elsewhere on ACCESS `login_shell("xdusage -p <project>")`. This is **live remaining SUs** — the authoritative budget number, read fresh each time (not cached, not a server API).
   - Each command is a **recipe, not a rule** — if `sinfo`/`mybalance` isn't found (non-Slurm, or a facility with a different balance tool), adapt: `scontrol show partition` / `qstat -Q`; the facility's own allocation tool; `module load` first if a binary is missing; if there's no balance tool, say so and let the human decide.
2. **Mind the gotchas:** partition `timelimit` may read `infinite` because the real cap is per-**QOS** — if walltime matters, also `login_shell("sacctmgr -nP show qos format=Name,MaxWall")`.
3. **Present the gate** with `AskUserQuestion`: each option a partition, its description carrying node size + **live idle count** + any caveat (saturated → would queue; a GPU partition needs the `-gpu` account). Put the **remaining balance and the rough cost of the block** (≈ nodes × walltime × the facility's SU rate) in the question text so the choice is made against the budget. Recommend the cheapest/fastest sensible default (usually a shared/sub-node partition with idle nodes now).
4. **Provision onto the selection, confirming spend.** Once the human has seen the balance and chosen, call `ensure_endpoint_up(partition="<choice>", confirm_spend=True)`. Both **persist for the session** (later `run_shell`/`ensure_endpoint_up` calls reuse them) until you change the partition or `stop_endpoint`. A first call returning `provisioning` is normal — the block is allocating; retry shortly. The status echoes the active `partition`.

This is a *policy gate*: discovery surfaces the options + the budget, the human picks, and the pick drives provisioning.

- **The spend floor is enforced, not advisory.** `ensure_endpoint_up`/`run_shell` on a billed Slurm shape **without** `confirm_spend=True` return `needs_confirmation` and start **nothing** — that's the deterministic budget floor. Only set `confirm_spend=True` *after* surfacing the balance to the user; it is your acknowledgement on their behalf, not a default to sprinkle on.
- **Gate, not interrogation:** only the partition + the spend confirmation are gated (the consequential, cost-bearing choices); account/walltime/nodes are sensible-defaulted. Don't prompt for the unambiguous.
- **Headless fallback (can't prompt):** prefer `shape="login"` — a free login-node `LocalProvider` that never needs `confirm_spend`. Only auto-confirm a billed block in autonomous mode if you have an explicit budget signal; otherwise stay on the free login shape rather than spend unattended.

## Waiting for the block to warm (don't busy-wait)

After `ensure_endpoint_up` returns `provisioning`, the Slurm block is queued. Wait cheaply:

- **Poll the queue, not the endpoint.** `login_shell("squeue -u $USER -h -o '%i|%P|%T|%r'")` is a fast login-node read showing the pilot's state (`PENDING`→`RUNNING`) and *why* it pends (`Priority`, `Resources`). Re-calling `ensure_endpoint_up` instead pays an ~8 s worker-canary timeout per poll. Wait for `RUNNING`, then call `ensure_endpoint_up` **once** to confirm the worker registered (`up`).
- **Between polls, wait *without* a foreground `sleep`** (the harness blocks a standalone `sleep`). The queue state is read through the `login_shell` **MCP tool**, so a `Monitor`/`until <check>` loop **can't drive this wait** — that loop tests a *local shell* condition, not a tool call. Instead run a short wait in the background (Claude Code: `run_in_background: true` on a `sleep`), then re-poll `squeue` when it elapses. Use a relaxed cadence — start ~20–30 s, escalate if it stays pending — and don't hammer `ensure_endpoint_up`.
- A pend can be seconds (node booting) or many minutes (queued behind priority) — the `squeue` reason tells you which, so you can tell the user instead of polling blindly.
