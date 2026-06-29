# Warmth, the canary & cold-start

> [!abstract] In one line
> "Up" means a **worker answered**, not that the manager is online — so before trusting an endpoint we submit a tiny **canary** task through the real Executor; a returned result ⇒ warm, a timeout ⇒ still cold (and the submit itself kicks the block).

## The cold-start gap

`manager_online` is a cheap Globus *web* query that only reflects the **login-node manager**. But in the [[MEP & templated endpoints|MEP model]] the first task forks the UEP and submits the Slurm block, so the manager reads "online" while the next command would still **cold-start** (no worker yet). Trusting `manager_online` makes `run_shell` dispatch into a 124 timeout.

## The canary

`_confirm_worker` ([[server]], `server.py:364`) submits a trivial `ShellFunction` through the *same* long-lived Executor real work uses ([[runner]], `GlobusRunner.canary`). The canary command echoes a sentinel plus the worker's host, Python, and dill versions:

- **returned result** ⇒ a worker is truly live ⇒ `warm`.
- **timeout** (`CANARY_TIMEOUT_S = 8 s`, `server.py:324`) ⇒ still `provisioning` — and the submit has *kicked* the cold block.

A successful canary is trusted for `CANARY_TTL_S = 45 s` (`server.py:321`) so an interactive burst doesn't pay the round-trip every call. (Safe: an idle block needs ≥ `max_idletime`, default 600 s, of silence to release, so a worker seen < 45 s ago can't have vanished.)

> [!warning] dill skew is the real failure mode
> The canary reports the worker's dill version; if it differs from ours, function (de)serialization breaks. `_worker_notice` surfaces that as the warm descriptor's warning — it's the genuine compatibility hazard behind "the worker is up but tasks fail."

This is what makes `ensure_endpoint_up`'s "is it warm?" honest and keeps `run_shell` from dispatching into a hang ([[dispatch]] turns a real timeout into a structured outcome).

## See also
[[server]] · [[runner]] · [[lifecycle]] · [[MEP & templated endpoints]] · [[Cost control]]
