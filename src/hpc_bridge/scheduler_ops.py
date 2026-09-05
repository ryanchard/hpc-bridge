"""Scheduler operations over the free login shape (split step 6, 2026-09-03): cancel THIS endpoint's
blocks (Slurm scancel / PBS qdel, scoped by the `uep.<eid>` StdOut marker Parsl writes), and read the
pilot job's status to tell a normal cold-start wait from a rejected/held submission (#32).

The login-shape channel is INJECTED (`run_login`: a coroutine taking the command) rather than imported
from `server`, so this module has no upward import; `server._login_runner(app)` builds it at call time,
which is also why tests that patch `server._run_shell` still reach these paths. Tests that patch the
release call patch `scheduler_ops._release_blocks_over_login`.
"""
from __future__ import annotations

import asyncio
import shlex
from collections.abc import Awaitable, Callable

from . import config
from .config import PROVISION_GRACE_S
from .context import AppCtx
from .models import ShellOutcome

LoginRunner = Callable[[str], Awaitable[ShellOutcome]]

def _release_cmd(scheduler: str, eid: str) -> str:
    """Login-shape shell one-liner that cancels THIS endpoint's scheduler block(s), matched
    precisely by the `uep.<eid>` StdOut marker Parsl writes under the UEP dir. Scheduler-specific:
    Slurm reads squeue/scancel; PBS reads qstat -f (unwrapping its 80-col line continuations so a
    wrapped Output_Path can't split the marker) and qdel."""
    marker = f"uep.{eid}"
    if scheduler == "pbs":
        # NB: `qstat -f -u $USER` yields NOTHING on PBS Pro — the -u filter suppresses full-format
        # output entirely (unlike Slurm's `squeue -u`), which silently no-ops the cancel and lets the
        # block burn to walltime (caught in live Polaris validation). Use bare `qstat -f` (all jobs)
        # and let the endpoint-unique `uep.<eid>` marker scope the match to only our jobs.
        return (
            'ids=$(qstat -f 2>/dev/null '
            "| sed ':a;N;$!ba;s/\\n\\t//g' "
            f"| awk -v m={shlex.quote(marker)} 'BEGIN{{RS=\"Job Id: \"}} index($0,m){{print $1}}'); "
            '[ -n "$ids" ] && qdel $ids; echo "released ${ids:-none}"'
        )
    return (
        'ids=$(squeue -u "$USER" -h -O "JobID:30,StdOut:1024" 2>/dev/null '
        f"| grep -F {shlex.quote(marker)} | awk '{{print $1}}'); "
        '[ -n "$ids" ] && scancel $ids; echo "released ${ids:-none}"'
    )

async def _release_blocks_over_login(app: AppCtx, eid: str, run_login: LoginRunner) -> tuple[bool, str]:
    """Cancel this endpoint's scheduler block(s) by running the scheduler's cancel (scancel/qdel)
    on the **login shape (AMQP)** — never SSH. That's the whole point of the login-node endpoint:
    talk to the cluster over Compute, not a fresh SSH. Matches blocks precisely by the UEP StdOut
    marker (`uep.<eid>`) so it never touches another endpoint's jobs.

    A cold login worker can't dispatch on the first try — it returns cold_start ("allocating
    nodes…"), not `complete`. But that first hit WAKES the worker, so we retry a bounded few times
    to *confirm* the cancel instead of walking away while the block keeps burning. Returns
    `(confirmed, detail)`: `confirmed=False` means the channel stayed cold across the retries and the
    cancel was NOT verified — the caller must report that honestly (never "down"; see #24). An
    unconfirmed cancel is still backstopped by idle-release (`min_blocks=0` + `max_idletime`), and
    re-calling stop (channel now warming) confirms it. Retry budget: HPC_BRIDGE_RELEASE_ATTEMPTS
    (default 3) × HPC_BRIDGE_RELEASE_BACKOFF_S (default 6s)."""
    # The scheduler lives on the facility's MachineProfile (SlurmFacility.profile.scheduler); a
    # facility without one (LocalFacility/dev, or test doubles) has never spoken anything but
    # Slurm's squeue/scancel, so default there instead of assuming an attribute that isn't part
    # of the Facility protocol.
    scheduler = getattr(getattr(app.facility, "profile", None), "scheduler", "slurm")
    cmd = _release_cmd(scheduler, eid)
    attempts = config.release_attempts()
    backoff = config.release_backoff_s()
    detail = "unconfirmed"
    for i in range(attempts):
        out = await run_login(cmd)
        if out.phase == "complete" and out.exit_code == 0:
            line = (out.stdout or "").strip().splitlines()
            return True, (line[-1] if line else "released none")
        detail = out.notice or out.phase or "unconfirmed"
        if i + 1 < attempts and backoff > 0:
            await asyncio.sleep(backoff)  # let the woken login worker register, then re-confirm
    return False, f"cancel not confirmed ({detail}); idle-release will reclaim it"

def _pilot_status_cmd(scheduler: str, eid: str) -> str:
    """Login-shape one-liner that prints THIS endpoint's pilot block(s) as `STATE JOBID` lines,
    matched by the same `uep.<eid>` StdOut marker `_release_cmd` uses (so it never reads another
    endpoint's jobs). Read-only — the diagnostic twin of `_release_cmd`. Empty output ⇒ no pilot is
    in the scheduler (submission rejected, or not yet registered)."""
    marker = f"uep.{eid}"
    if scheduler == "pbs":
        # Bare `qstat -f` (the -u filter suppresses full-format output on PBS Pro); unwrap the 80-col
        # line continuations, split on records, and for records carrying the marker print the
        # job_state letter (R/Q/H) + the job id.
        return (
            # -x: finished jobs too — a pilot that RAN AND DIED (a broken worker_init, a missing module) is otherwise
            # invisible and read as "never submitted"; the summary prefers a live state when both are present.
            "qstat -x -f 2>/dev/null | sed ':a;N;$!ba;s/\\n\\t//g' "
            # …plus the scheduler's `comment` for the record: a HELD pilot's comment is the site's own explanation
            # (a Polaris-style hook: "requires -l filesystems=…"), which the agent otherwise has to dig out of qstat -f.
            f"| awk -v m={shlex.quote(marker)} 'BEGIN{{RS=\"Job Id: \"}} index($0,m){{"
            's="?"; if (match($0,/job_state = [A-Za-z]/)) s=substr($0,RSTART+12,1); '
            'x="-"; if (match($0,/Exit_status = -?[0-9]+/)) x=substr($0,RSTART+14,RLENGTH-14); '
            'c=""; if (match($0,/comment = [^\\n]*/)) c=substr($0,RSTART+10,RLENGTH-10); '
            "print s\" \"$1\" \"x\" \"c}'"
        )
    return (
        # Filter by the marker INSIDE awk (not `grep -F | awk`): grep exits non-zero on no-match,
        # which under a `set -o pipefail` shell would mask an empty result as an error and swallow the
        # "no pilot -> rejected" signal this exists to surface. awk matches AND exits 0 either way.
        'squeue -u "$USER" -h -O "State:20,JobID:24,StdOut:1024" 2>/dev/null '
        f"| awk -v m={shlex.quote(marker)} 'index($0,m){{print $1\" \"$2}}'"
    )

def _summarize_pilot(stdout: str, provisioning_elapsed_s: float) -> tuple[str, str]:
    """(category, notice-suffix) from `_pilot_status_cmd` output. category ∈ {starting, queued, held,
    rejected, finished}. A visible pilot (Q/R/H) is reported at once; a MISSING pilot is only called
    `rejected` once the block has been cold past `PROVISION_GRACE_S` — before that it's a normal
    cold-start gap (empty suffix ⇒ the caller leaves 'allocating nodes…' unchanged)."""
    # Slurm rows are `STATE JOBID`; PBS rows are `STATE JOBID EXIT [comment…]` (EXIT = Exit_status, or `-` when the
    # job never ran). Finished rows are read by WHY they finished: an exit status of its own means the worker died
    # there; `-` (deleted before it ever ran — a held pilot cancelled by a re-bind) or 271 (killed/qdel'd: our own
    # release, or the walltime) is a leftover of an earlier block, not a diagnosis, and is ignored.
    rows = [ln.split(None, 3) for ln in stdout.splitlines() if ln.strip()]
    done_states = {"F", "E", "X"}
    live = [r for r in rows if r and r[0][:1].upper() not in done_states]
    died = [r for r in rows
            if r and r[0][:1].upper() in done_states and len(r) > 2 and r[2] not in ("-", "271", "?")]
    if not live:
        if died:  # every pilot this endpoint submitted has FINISHED with an exit status of its own: it ran and died
            r = died[-1]
            return "finished", (
                f"— pilot {r[1]} already FINISHED (exit status {r[2]}): the block started and its worker exited "
                "(a failed worker_init, an environment the compute node lacks, a network the worker cannot reach). "
                "Not a queue wait: read that job's stdout/stderr in the endpoint's submit_scripts directory "
                "(run_shell shape='login')."
            )
        if provisioning_elapsed_s < PROVISION_GRACE_S:
            return "starting", ""  # normal cold-start window — pilot not visible yet, don't cry wolf
        return "rejected", (
            f"— but NO pilot job is in the scheduler after ~{int(provisioning_elapsed_s)}s. The block "
            "submission was likely REJECTED (e.g. inactive allocation, wrong account, or bad queue) "
            "rather than queued. Check run_shell('qstat -u $USER', shape='login') (squeue on Slurm) "
            "and the endpoint log."
        )
    rows = live
    states = {r[0][:1].upper() for r in rows}
    jid = rows[0][1] if len(rows[0]) > 1 else "?"
    if "H" in states:
        held = next((r for r in rows if r[0][:1].upper() == "H"), rows[0])
        comment = held[3].strip() if len(held) > 3 else ""
        why = (f" The scheduler's comment: {comment[:300]!r}." if comment else
               " A held job usually means a bad scheduler directive (e.g. filesystems/account) — inspect qstat -f / "
               "the #PBS|#SBATCH directives.")
        hjid = held[1] if len(held) > 1 else jid
        return "held", (
            f"— pilot {hjid} is HELD and will not start on its own.{why} Fix the facility's scheduler_options "
            "(connect_facility with details=) or the account/queue, then start again."
        )
    if "R" in states:
        return "starting", f"— pilot {jid} is RUNNING; the worker is starting, retry shortly."
    return "queued", f"— pilot {jid} is queued (PENDING); waiting on the scheduler."

async def _pilot_status_over_login(app: AppCtx, eid: str, elapsed_s: float, run_login: LoginRunner) -> tuple[str, str] | None:  # noqa: E501
    """Ask the scheduler (over the login shape — AMQP, no SSH) what state THIS endpoint's pilot is in.
    Best-effort: returns None when it can't tell (login worker cold, scheduler unreachable) so the
    caller leaves its notice unchanged. `elapsed_s` is how long the block has been provisioning — it
    gates the rejection hint past the cold-start grace."""
    scheduler = getattr(getattr(app.facility, "profile", None), "scheduler", "slurm")
    out = await run_login(_pilot_status_cmd(scheduler, eid))
    if out.phase != "complete" or out.exit_code != 0:
        return None
    return _summarize_pilot(out.stdout or "", elapsed_s)

async def _augment_provisioning_notice(app: AppCtx, eid: str, notice: str, elapsed_s: float, run_login: LoginRunner) -> str:  # noqa: E501
    """Enrich a still-cold BILLED block's 'allocating nodes…' with the pilot's ACTUAL scheduler state,
    so a rejected/held submission isn't silently indistinguishable from a queue wait ([#32]). A
    diagnostic must never break the result it annotates, so any failure — or an empty suffix (the
    normal cold-start window) — leaves the notice as-is."""
    try:
        status = await _pilot_status_over_login(app, eid, elapsed_s, run_login)
    except Exception:  # noqa: BLE001 - the pilot probe is advisory; never fail provisioning on it
        return notice
    suffix = status[1] if status else ""
    return f"{notice} {suffix}" if suffix else notice
