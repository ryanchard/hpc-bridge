#!/usr/bin/env python
"""Live check of the bring-your-own SSH path through hpc-bridge's REAL functions — no agent, raw evidence.

    python agentic/byo_live_check.py SSH_HOST FACILITY_ID [--fresh DIR] [--keep] [--no-teardown]

connect (probe → proposal) → accept the proposal as-is (details=) → connect until the login shape is up →
run `hostname; whoami` on the LOGIN shape (free) → teardown (unless --no-teardown). Globus tokens come from the
scratch dir (default ~/hpcb-fresh, the identity logged in there); hpc-bridge STATE stays at the default
(~/.hpc-bridge) so an SSH ControlMaster the user pre-opened with the plugin's `preauth_command` is shared —
that is the MFA handoff this driver exercises on a facility that demands a one-time code.
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
    ap.add_argument("ssh_host")
    ap.add_argument("facility_id")
    ap.add_argument("--fresh", default=str(Path.home() / "hpcb-fresh"))
    ap.add_argument("--wait-s", type=int, default=600)
    ap.add_argument("--no-teardown", action="store_true")
    a = ap.parse_args(argv)

    fresh = Path(a.fresh).expanduser()
    (fresh / "globus_compute").mkdir(parents=True, exist_ok=True)
    os.environ["GLOBUS_COMPUTE_USER_DIR"] = str(fresh / "globus_compute")
    for k in ("HPC_BRIDGE_ENDPOINT_ID", "HPC_BRIDGE_ENDPOINT_NAME", "HPC_BRIDGE_MACHINE", "HPC_BRIDGE_ACCOUNT",
              "HPC_BRIDGE_SSH_HOST", "HPC_BRIDGE_SEARCH_INDEX", "HPC_BRIDGE_STATE_DIR"):
        os.environ.pop(k, None)

    from hpc_bridge.endpoint import EndpointCLI
    from hpc_bridge.facility.local import LocalFacility
    from hpc_bridge.login import LoginFlow, globus_identity_label
    from hpc_bridge.profile import Profile
    from hpc_bridge.server import AppCtx, _connect_facility, _run_shell, _teardown_endpoint

    if LoginFlow().login_required():
        print(f"no Globus login in {fresh}: run scripts/fresh_user_session.sh first")
        return 2
    print(f"identity: {globus_identity_label()}  | state dir: ~/.hpc-bridge (shared ControlMaster)")

    async def run() -> int:
        app = AppCtx(facility=LocalFacility(EndpointCLI(user_dir=fresh / "globus_compute")), profile=Profile())
        res = await _connect_facility(app, a.facility_id, ssh_host=a.ssh_host)
        print(f"1. connect(ssh_host={a.ssh_host!r}): phase={res.phase}\n   notice={(res.notice or '')[:600]!r}")
        if res.phase == "needs_preauth":
            print(f"   preauth_command: {res.preauth_command}")
            print("   -> run that in YOUR terminal (password/code once), then rerun this driver.")
            return 5
        if res.phase != "proposed_facility_details":
            return 4
        draft = res.proposed_details
        print(f"   proposal: scheduler={draft.scheduler} interface={draft.interface} partition={draft.partition} "
              f"scratch={draft.scratch_root} env_setup={draft.env_setup[:120]!r}")
        res = await _connect_facility(app, a.facility_id, details=draft)
        print(f"2. connect(details=…): phase={res.phase}\n   notice={(res.notice or '')[:400]!r}")
        t0 = time.monotonic()
        n = 2
        while res.phase == "provisioning" and time.monotonic() - t0 < a.wait_s:
            await asyncio.sleep(20)
            n += 1
            res = await _connect_facility(app, a.facility_id)
            print(f"{n}. connect (+{int(time.monotonic()-t0)}s): phase={res.phase} notice={(res.notice or '')[:200]!r}")
        ok = False
        if res.phase in ("needs_account", "connected", "up"):
            out = await _run_shell(app, "hostname; whoami; echo SLURM=$(command -v sbatch)", shape="login")
            print(f"{n+1}. run_shell(login): phase={out.phase} exit={out.exit_code}\n   stdout={out.stdout!r}\n   stderr={out.stderr_snippet[:200]!r}")
            ok = out.phase == "complete" and out.exit_code == 0
        if not a.no_teardown:
            try:
                td = await _teardown_endpoint(app)
                print(f"{n+2}. teardown: status={td.status} notice={(td.notice or '')[:220]!r}")
            except Exception as exc:  # noqa: BLE001
                print(f"   (teardown: {type(exc).__name__}: {exc})")
        print("VERDICT:", "PASS — login shape ran on the bring-your-own facility" if ok else f"FAIL/INCOMPLETE — final phase {res.phase}")
        return 0 if ok else 1

    rc = asyncio.run(run())
    sys.stdout.flush()
    os._exit(rc)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
