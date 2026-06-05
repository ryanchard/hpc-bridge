---
description: How to drive HPC well through hpc-bridge. Before provisioning/starting a compute node on a facility, discover its partitions and present them as a selection gate (don't start blind). You have a persistent per-session shell (relative paths work; call reset_session for a clean slate); redirect verbose output to a file and read it back in bounded chunks (10 MB result cap); a cold first call means nodes are being allocated.
---

# Driving HPC with hpc-bridge

- You have a **persistent session shell**: `cd` and relative paths carry across turns. Call `reset_session` for a clean slate.
- The endpoint may be **cold** on first use — `ensure_endpoint_up` reporting `provisioning` means nodes are being allocated; retry shortly.
- Results are capped at ~10 MB: for verbose commands, redirect to a file and read it back in chunks.

## Before starting a node: discover partitions, then present the selection gate

When asked to **start / provision / spin up a compute node** on a facility, do **not** provision blind. First *discover* what's available and let the user choose — over a read-only login-node probe that costs nothing (no block, no allocation):

1. **Gather** (Slurm facilities) with the `login_shell` tool — it runs on the login node and starts nothing:
   - Partitions: `login_shell("sinfo -h -o '%P|%a|%l|%D|%c|%m|%F'")` → `name|avail|timelimit|nodes|cores|mem_MB|A/I/O/T-nodes`. The **I** (idle) in the last field is *live* availability — a partition with 0 idle will queue.
   - Accounts: `login_shell("sacctmgr -nP show assoc where user=$USER format=Account,QOS,Partition")`.
   - The command is a **recipe, not a rule** — if `sinfo` isn't found (non-Slurm), adapt: `scontrol show partition`, or PBS `qstat -Q`; `module load` first if a binary is missing.
2. **Mind the gotchas:** partition `timelimit` may read `infinite` because the real cap is per-**QOS** — if walltime matters, also `login_shell("sacctmgr -nP show qos format=Name,MaxWall")`.
3. **Present the gate** with `AskUserQuestion`: each option a partition, its description carrying node size + **live idle count** + any caveat (saturated → would queue; a GPU partition needs the `-gpu` account). Recommend the cheapest/fastest sensible default (usually a shared/sub-node partition that has idle nodes now).
4. **Stop at the gate.** Report the user's selection. Provisioning the node *from* the chosen partition is the next milestone — do **not** call `ensure_endpoint_up` from this flow yet.

This is a *policy gate*: discovery surfaces the options, the human picks.
