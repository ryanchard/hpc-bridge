#!/usr/bin/env python
"""Live check of a facility MEP through hpc-bridge's REAL path — no agent, raw evidence.

    python agentic/mep_live_check.py SEED.yaml ENTRY_ID --account ACCT [--partition P] [--walltime HH:MM:SS]
                                     [--fresh DIR] [--keep]

Loads ENTRY_ID from the seed file (so an entry can be tested BEFORE it is in the registry), binds it as a
session facility, attaches (connect_facility: reads the facility's template contract), then submits the
compute canary (ensure_endpoint_up confirm_spend=True) until the block is up or the verdict is terminal,
runs `hostname` on the compute shape, and stops the endpoint (draining on a MEP). Runs against the scratch
Compute dir (default ~/hpcb-fresh: the identity logged in there), never ~/.globus_compute.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("seed")
    ap.add_argument("entry_id")
    ap.add_argument("--account", default=None)
    ap.add_argument("--partition", default=None)
    ap.add_argument("--walltime", default=None)
    ap.add_argument("--fresh", default=str(Path.home() / "hpcb-fresh"))
    ap.add_argument("--wait-s", type=int, default=900, help="how long to wait for the block (default 15 min)")
    ap.add_argument("--keep", action="store_true", help="don't stop the endpoint at the end")
    a = ap.parse_args(argv)

    fresh = Path(a.fresh).expanduser()
    (fresh / "globus_compute").mkdir(parents=True, exist_ok=True)
    (fresh / "state").mkdir(parents=True, exist_ok=True)
    os.environ["GLOBUS_COMPUTE_USER_DIR"] = str(fresh / "globus_compute")
    os.environ["HPC_BRIDGE_STATE_DIR"] = str(fresh / "state")
    for k in ("HPC_BRIDGE_ENDPOINT_ID", "HPC_BRIDGE_ENDPOINT_NAME", "HPC_BRIDGE_MACHINE", "HPC_BRIDGE_ACCOUNT", "HPC_BRIDGE_SSH_HOST"):
        os.environ.pop(k, None)

    import yaml

    from hpc_bridge.catalog.entry import CatalogEntry
    from hpc_bridge.endpoint import EndpointCLI
    from hpc_bridge.facility.local import LocalFacility
    from hpc_bridge.login import LoginFlow, globus_identity_label
    from hpc_bridge.profile import Profile
    from hpc_bridge.server import (
        AppCtx,
        _connect_facility,
        _ensure_endpoint_up,
        _run_shell,
        _shape_runtime,
        _stop_endpoint,
    )

    if LoginFlow().login_required():
        print(f"no Globus login in {fresh}: run scripts/fresh_user_session.sh first (or log in there)")
        return 2
    print(f"identity: {globus_identity_label()}  (store: {fresh / 'globus_compute'})")

    with open(a.seed) as fh:
        docs = yaml.safe_load(fh)
    entry = next((CatalogEntry.model_validate(d) for d in docs if d.get("id") == a.entry_id), None)
    if entry is None:
        print(f"no entry {a.entry_id!r} in {a.seed}")
        return 2
    if a.partition:
        entry.defaults.partition = a.partition
    if a.walltime:
        entry.defaults.walltime = a.walltime
    print(f"entry: {entry.subject}  mep={entry.compute_mep_uuid}  partition={entry.defaults.partition} "
          f"walltime={entry.defaults.walltime} extra={entry.defaults.extra}")

    async def run() -> int:
        app = AppCtx(facility=LocalFacility(EndpointCLI(user_dir=fresh / "globus_compute")), profile=Profile())
        app.session_facilities[entry.id] = entry  # a session-local entry wins over the registry
        res = await _connect_facility(app, entry.id)
        print(f"1. connect: phase={res.phase} reused={res.reused}\n   notice={res.notice!r}")
        if res.phase not in ("needs_account", "connected", "provisioning"):
            return 4
        fac = app.facility
        print(f"   facility endpoint: {getattr(fac, 'display_name', None)!r} v{getattr(fac, 'endpoint_version', None)}; notes={getattr(fac, 'template_notes', None)}")
        t0 = time.monotonic()
        n = 0
        while True:
            n += 1
            kw = {"shape": "compute", "confirm_spend": True}
            if a.account:
                kw["account"] = a.account
            st = await _ensure_endpoint_up(app, **kw)
            raw = getattr(_shape_runtime(app, "compute").last_canary, "error", None)
            print(f"{1+n}. ensure_endpoint_up #{n} (+{int(time.monotonic()-t0)}s): status={st.status} block={st.block_state}"
                  f"\n   notice={(st.notice or '')[:300]!r}" + (f"\n   RAW canary error: {str(raw)[:300]!r}" if raw else ""))
            if n == 1:
                print(f"   user_endpoint_config sent: {_shape_runtime(app, 'compute').user_endpoint_config}")
            if st.status in ("up", "down") or time.monotonic() - t0 > a.wait_s:
                break
            await asyncio.sleep(30)
        ok = False
        if st.status == "up":
            out = await _run_shell(app, "hostname; whoami; echo SLURM_JOB_ID=$SLURM_JOB_ID; nproc", shape="compute")
            print(f"{2+n}. run_shell(compute): phase={out.phase} exit={out.exit_code}\n   stdout={out.stdout!r}\n   stderr={out.stderr_snippet!r}")
            ok = out.phase == "complete" and out.exit_code == 0
        if not a.keep:
            try:
                stp = await _stop_endpoint(app)
                print(f"{3+n}. stop: status={stp.status} notice={(stp.notice or '')[:200]!r}")
            except Exception as exc:  # noqa: BLE001
                print(f"   (stop: {type(exc).__name__}: {exc})")
        print("VERDICT:", "PASS — a worker answered on the facility MEP" if ok else f"FAIL/INCOMPLETE — final status {st.status}")
        return 0 if ok else 1

    rc = asyncio.run(run())
    sys.stdout.flush()
    os._exit(rc)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
