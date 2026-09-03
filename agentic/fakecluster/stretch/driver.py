#!/usr/bin/env python
"""Stretch proof: drive hpc-bridge's OWN server seams against the fake cluster, end to end.

Runs INSIDE the agentic jail image (`hpc-bridge-agentic`) attached to the fake cluster's compose
network — exactly how the harness would target it — so the login node is plain `login:22` (no port
mapping, no ssh alias). Launch via bin/stretch.sh, which mounts this branch's src, the host's
Globus storage.db and the test key, and sets the HPC_BRIDGE_* env.

Flow (the discover-first BYO path, in the spirit of tests/test_server.py):
  connect_facility(facility, ssh_host)            -> proposed_facility_details (probe over SSH)
  connect_facility(facility, details=proposed)    -> bootstrap: seed creds, configure+start the endpoint
  ensure_endpoint_up(shape="login")               -> login-shape worker (LocalProvider) warm
  run_shell(..., shape="login")
  ensure_endpoint_up(shape="compute", partition="main", confirm_spend=True)  -> sbatch a block
  run_shell("hostname", shape="compute")          -> must answer c1 or c2
  stop_endpoint -> teardown_endpoint              -> block released, manager gce-stopped + deleted

Success line: `STRETCH OK` (exit 0). Anything else exits 1 and dumps the endpoint logs.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path

REPO = Path("/work/hpc-bridge")
T0 = time.monotonic()


def say(msg: str) -> None:
    print(f"[{time.monotonic() - T0:7.1f}s] {msg}", flush=True)


def stage_credentials() -> None:
    """Mirror agentic/entrypoint.sh: owned 0600 copies of the read-only mounted key + storage.db."""
    user_dir = Path(os.environ["HPC_BRIDGE_USER_DIR"])
    user_dir.mkdir(parents=True, exist_ok=True)
    db = Path("/run/secrets/storage.db")
    if db.exists():
        shutil.copy(db, user_dir / "storage.db")
        (user_dir / "storage.db").chmod(0o600)
    else:
        say("WARNING: no /run/secrets/storage.db — bootstrap will fail at credential seeding")
    key = Path(os.environ["HPC_BRIDGE_SSH_KEY"])
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    dst = ssh_dir / "test_key"
    shutil.copy(key, dst)
    dst.chmod(0o600)
    os.environ["HPC_BRIDGE_SSH_KEY"] = str(dst)


def show(label: str, obj) -> None:
    d = obj.model_dump() if hasattr(obj, "model_dump") else obj
    if isinstance(d, dict):
        for k in ("stdout", "stderr_snippet"):
            if d.get(k):
                d[k] = d[k].strip()[:600]
        if d.get("proposed_details") is not None:
            d["proposed_details"] = {k: v for k, v in d["proposed_details"].items() if v is not None}
    say(f"{label}: {d}")


async def diag(app) -> None:
    """On failure: what the login node's endpoint.log and the block's stdout say."""
    name = os.environ.get("HPC_BRIDGE_ENDPOINT_NAME", "")
    cmd = (
        f'for f in "$HOME/.globus_compute/{name}/endpoint.log" "$HOME"/.globus_compute/uep.*/endpoint.log '
        '"$HOME"/.globus_compute/uep.*/submit_scripts/*.std*; do [ -f "$f" ] || continue; '
        'echo "=== $f"; tail -n 60 "$f"; done; echo "=== squeue"; squeue -o "%i %T %N %j %o"; '
        'echo "=== sacct"; sacct -X -o JobID,State,NodeList,Elapsed'
    )
    try:
        rc, out, err = await app.facility.login_exec(cmd)
        print(out or err, flush=True)
    except Exception as exc:  # noqa: BLE001
        say(f"diag failed: {exc}")


async def main() -> int:
    stage_credentials()
    sys.path.insert(0, str(REPO))  # tests.fakes (the FakeFacility placeholder AppCtx starts with)
    from hpc_bridge import server
    from hpc_bridge.profile import Profile
    from tests.fakes import FakeFacility

    facility = "fake"
    ssh_host = os.environ["HPC_BRIDGE_SSH_HOST"]
    app = server.AppCtx(facility=FakeFacility(), profile=Profile())
    say(f"connect_facility({facility!r}, ssh_host={ssh_host!r}) — discovery probe over SSH")
    r = await server._connect_facility(app, facility, ssh_host=ssh_host)
    show("connect#1", r)
    if r.phase != "proposed_facility_details" or r.proposed_details is None:
        say("FAIL: expected proposed_facility_details")
        return 1
    details = r.proposed_details

    # Bootstrap with the proposed config. #39: the first details-connect often trips a registration-lag
    # race ("could not find endpoint … in list output") and the retry succeeds — retry a few times.
    for attempt in range(1, 6):
        say(f"connect_facility(details=…) attempt {attempt}")
        r = await server._connect_facility(app, facility, details=details)
        show(f"connect#details/{attempt}", r)
        if r.phase in ("needs_account", "provisioning"):
            break
        await asyncio.sleep(5)
    else:
        say("FAIL: bootstrap never reached needs_account/provisioning")
        return 1

    # Login shape: the free LocalProvider worker on the login container.
    deadline = time.monotonic() + 600
    while True:
        s = await server._ensure_endpoint_up(app, shape="login")
        show("login status", s)
        if s.status == "up":
            break
        if time.monotonic() > deadline:
            say("FAIL: login shape never warmed")
            await diag(app)
            return 1
        await asyncio.sleep(10)
    out = await server._run_shell(app, "hostname; whoami; sinfo -h -o '%P %T %D'", shape="login")
    show("run_shell[login]", out)

    # Compute shape: a real sbatch'd block on c1/c2 whose worker phones home to the login node.
    deadline = time.monotonic() + 900
    while True:
        s = await server._ensure_endpoint_up(app, shape="compute", partition="main", confirm_spend=True)
        show("compute status", s)
        if s.status == "up":
            break
        if s.status not in ("provisioning", "up"):
            say(f"FAIL: compute shape status={s.status}")
            await diag(app)
            return 1
        if time.monotonic() > deadline:
            say("FAIL: compute block never warmed")
            await diag(app)
            return 1
        await asyncio.sleep(10)
    out = await server._run_shell(
        app, "hostname; echo SLURM_JOB_ID=$SLURM_JOB_ID; squeue -u \"$USER\" -h -o '%i %T %N %j'",
        shape="compute",
    )
    show("run_shell[compute]", out)
    first = (out.stdout or "").strip().splitlines()[:1]
    ok = out.phase == "complete" and out.exit_code == 0 and first and first[0] in ("c1", "c2")
    if not ok:
        await diag(app)

    # Second call on the same block must be a warm hit, and the session shell must persist cwd.
    out2 = await server._run_shell(app, "cd /tmp && export FOO=bar && pwd", shape="compute")
    show("run_shell[compute]#2", out2)
    out3 = await server._run_shell(app, "pwd; echo FOO=$FOO; hostname", shape="compute")
    show("run_shell[compute]#3 (session persistence)", out3)

    st = await server._stop_endpoint(app)
    show("stop_endpoint", st)
    if st.status == "draining":  # channel was cold: re-stop confirms (the agent's prescribed move)
        await asyncio.sleep(5)
        st = await server._stop_endpoint(app)
        show("stop_endpoint#2", st)
    # Leave nothing registered under this identity: full teardown (gce stop + delete over SSH).
    td = await server._teardown_endpoint(app)
    show("teardown_endpoint", td)
    rc, sq, _ = await app.facility.login_exec("squeue -h -o '%i %T %N %j'; echo ---; sacct -X -n -o JobID,State,NodeList")
    say(f"world after teardown — squeue/sacct:\n{sq.strip()}")
    say("STRETCH OK" if ok else "STRETCH FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
