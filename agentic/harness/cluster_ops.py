"""Pure builders for the harness' cluster-side commands (SETUP/teardown/log capture).

Everything here is a string — no SSH, no SDK — so it is unit-testable and so the ONE rule this
module enforces is auditable: **the harness never touches jobs or endpoints that aren't this
run's.** Teardown used to `scancel -u $(whoami)` and delete every `hpc-bridge-*` endpoint the pool
user owned, on the assumption that concurrent runs use distinct users. That assumption broke
(see pool.py) and one run's teardown killed another's blocks mid-task. Now:

- endpoints are stopped/deleted BY NAME — this run's `HPC_BRIDGE_ENDPOINT_NAME` only;
- pilot blocks are cancelled by the `uep.<eid>` StdOut marker Parsl writes under the UEP dir —
  the same scope the server's `_release_cmd` uses — for this run's endpoint uuid(s) only;
- with NO endpoint uuid known, cancel NOTHING (and say so) rather than fall back to user-wide.
"""
from __future__ import annotations

import shlex

GCE = "$HOME/hpc-bridge/gce-venv/bin/globus-compute-endpoint"


def endpoint_uuid_cmd(endpoint_name: str) -> str:
    """Print this endpoint's registered uuid (from its endpoint.json), or nothing if unregistered."""
    ep = shlex.quote(endpoint_name)
    return (
        f'grep -o \'"endpoint_id": *"[^"]*"\' "$HOME/.globus_compute/"{ep}/endpoint.json 2>/dev/null '
        "| grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1"
    )


def scoped_cancel_cmd(scheduler: str, eids: list[str]) -> str:
    """Cancel ONLY the pilot blocks of `eids` (matched by their `uep.<eid>` marker). Never `-u`."""
    if not eids:
        return 'echo "cancel: no endpoint uuid known for this run — cancelling NOTHING (never user-wide)"'
    markers = shlex.quote(" ".join(f"uep.{e}" for e in eids))
    # awk matches ANY of the markers and exits 0 on no-match (a `grep` would exit 1 under pipefail).
    pick = f"awk -v m={markers} 'BEGIN{{n=split(m,a,\" \")}} {{for(i=1;i<=n;i++) if(index($0,a[i])){{print $1; break}}}}'"
    if scheduler == "pbs":
        listing = (
            "qstat -f 2>/dev/null | sed ':a;N;$!ba;s/\\n\\t//g' "
            f"| awk -v m={markers} 'BEGIN{{RS=\"Job Id: \"; n=split(m,a,\" \")}} "
            "{for(i=1;i<=n;i++) if(index($0,a[i])){print $1; break}}'"
        )
        return f'ids=$({listing}); [ -n "$ids" ] && qdel $ids 2>/dev/null; echo "cancelled: ${{ids:-none}}"'
    listing = f'squeue -u "$(whoami)" -h -O "JobID:24,StdOut:1024" 2>/dev/null | {pick}'
    return f'ids=$({listing}); [ -n "$ids" ] && scancel $ids 2>/dev/null; echo "cancelled: ${{ids:-none}}"'


def delete_endpoint_cmd(endpoint_name: str) -> str:
    """Stop + delete exactly ONE endpoint by name (this run's), best-effort."""
    ep = shlex.quote(endpoint_name)
    return f"{GCE} stop {ep} >/dev/null 2>&1; {GCE} delete {ep} --yes >/dev/null 2>&1; true"


def capture_logs_cmd(endpoint_name: str, eids: list[str], *, tail_lines: int = 2000) -> str:
    """Print (with `=== path` headers) the tail of the manager's endpoint.log, each UEP's
    endpoint.log, and the blocks' submit-script stdout/stderr — the evidence a post-mortem needs
    and which `delete` erases. Bounded per file so a chatty log can't bloat the bundle."""
    ep = shlex.quote(endpoint_name)
    globs = [f'"$HOME/.globus_compute/"{ep}/endpoint.log']
    for e in eids:
        globs.append(f'"$HOME/.globus_compute/uep."{shlex.quote(e)}.*/endpoint.log')
        globs.append(f'"$HOME/.globus_compute/uep."{shlex.quote(e)}.*/submit_scripts/*.std*')
    files = " ".join(globs)
    n = int(tail_lines)
    return (
        f"for f in {files}; do [ -f \"$f\" ] || continue; "
        f'echo "=== $f ($(wc -l < "$f") lines; last {n})"; tail -n {n} "$f"; done; true'
    )
