# MEP block rejection visibility (planned)

> [!abstract] In one line
> On a facility multi-user endpoint hpc-bridge cannot see a scheduler REJECTING its block: the pilot-rejection and
> finished-pilot probes (0.1.13/0.1.14) run over the login shape, and a MEP has none — so a rejected submission reads
> as "allocating nodes…" until the caller gives up. Seen live on NCSA Delta, 2026-09-09.

## The gap

`warmth` learns a block is alive when a worker answers the canary. When the submit was rejected at `sbatch` (missing
`--account` on an account-required facility, an unknown partition, a QOS the user lacks), no worker ever comes, and on an
SSH facility the login shape lets the probe run `sacct`/`squeue` and say so (`REJECTED`, `slurm_worker_died`). A
[[facility-mep|MEPFacility]] has `supported_shapes = ("compute",)`: no login shape, no scheduler channel, no probe. The
endpoint status stays "online" (the manager is fine), the block stays "provisioning", and `_allocating_notice` keeps
saying nodes are being allocated. Stop is draining-only (no cancel channel), so nothing can be confirmed either way.

The 0.1.17 account floor removes the most common cause on Delta (no account → nothing submitted), but not the class:
a wrong partition, an expired allocation or a QOS refusal still look like a queue wait.

## Options

1. **A provisioning deadline on MEPs.** After N minutes (per partition, from the catalog `defaults` or a facility
   `max_queue_wait_s`) with `block_state == "provisioning"` and the manager online, return `status="down"` with a
   notice: *"no worker after N min on `<partition>` — on this facility hpc-bridge cannot see the scheduler, so this is
   either a long queue or a REJECTED submission (account/partition/QOS); check the facility's queue yourself
   (`squeue -u $USER`) or try another partition."* Cheap, honest, no new channel.
2. **Ask the MEP manager.** The Globus Compute web service exposes per-UEP state to the owner; if the manager
   surfaces the provider's submit failure (some do: the 422 "no account" path already does), read it. Needs
   investigation per endpoint version.
3. **A facility-side hint in the catalog.** Entries could carry `known_queue_wait_s` so the deadline in (1) is
   informed rather than guessed.

Recommended: (1) now, (2) as an enhancement. Test on the fake `mep` profile by pointing a block at a partition the
fake scheduler rejects (a `submit_policy_rejected` sibling for MEPs).

## See also

- [[ACP interactive benchmark driver]] — the live session that surfaced this (the "no login node" reading, the
  username-as-account confirm) and the account floor that followed
- [[Resource shapes & the spend floor]] — the spend floor the account floor sits beside
