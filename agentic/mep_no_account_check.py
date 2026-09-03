#!/usr/bin/env python
"""Live check: what a user WITHOUT an account sees on a MEP facility — no agent, raw evidence.

    python agentic/mep_no_account_check.py [--reset] [--not USERNAME ...] [FACILITY_ID]

Runs in a scratch Compute dir (default ~/hpcb-noaccount; --reset wipes it) so nothing of yours is
touched. Steps: (1) in-terminal Globus login — LOG OUT of app.globus.org in the browser first, then
sign in as the identity that has NO account on the facility; (2) prints who you are (and refuses to
continue if it is one of the `--not` usernames, or if that username is in your linked-identity set:
the MEP mapper tries the WHOLE linked set, so a linked mapped identity would silently succeed);
(3) attaches to the facility (registry entry; default globus1), fires the first submit through
hpc-bridge's real path, and prints the RAW error text plus hpc-bridge's verdict — twice, to show the
outcome is stable. PASS = a terminal `down` whose notice says NO ACCOUNT and names the identity.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    args = list(argv)
    reset = "--reset" in args
    if reset:
        args.remove("--reset")
    not_ids: list[str] = []
    while "--not" in args:
        i = args.index("--not")
        not_ids.append(args[i + 1].lower())
        del args[i : i + 2]
    facility = args[0] if args else "globus1"

    fresh = Path(os.environ.get("FRESH", str(Path.home() / "hpcb-noaccount"))).expanduser()
    if reset and fresh.exists():
        shutil.rmtree(fresh)
        print(f"reset: removed {fresh}")
    (fresh / "globus_compute").mkdir(parents=True, exist_ok=True)
    (fresh / "state").mkdir(parents=True, exist_ok=True)
    os.environ["GLOBUS_COMPUTE_USER_DIR"] = str(fresh / "globus_compute")
    os.environ["HPC_BRIDGE_STATE_DIR"] = str(fresh / "state")
    for k in ("HPC_BRIDGE_ENDPOINT_ID", "HPC_BRIDGE_ENDPOINT_NAME", "HPC_BRIDGE_MACHINE", "HPC_BRIDGE_ACCOUNT"):
        os.environ.pop(k, None)

    from hpc_bridge.login import LoginFlow, globus_identity_label

    flow = LoginFlow()
    if flow.login_required():
        print("1. Globus login needed. FIRST log out of app.globus.org in your browser, then sign in as the")
        print("   identity that has NO account on the facility (not a linked one).")
        start = flow.start()
        print(f"   mode={start.mode}  url={start.login_url}")
        if start.mode == "paste":
            code = input("   paste the one-time code: ").strip()
            flow.complete_with_code(code)
        else:
            st = flow.wait(600)
            print(f"   flow: {st}  error={flow.error}")
            if st != "done":
                return 2
    else:
        print(f"1. login present in {fresh} (use --reset for a fresh one)")

    # who am I — and is a mapped identity hiding in my linked set?
    from globus_sdk import AuthClient
    from hpc_bridge.login import _default_app_factory

    app_ = _default_app_factory(None)
    info = AuthClient(authorizer=app_.get_authorizer("auth.globus.org")).oauth2_userinfo()
    me = (info.get("preferred_username") or "").lower()
    linked = sorted({(i.get("username") or "").lower() for i in info.get("identity_set", [])} - {""})
    print(f"2. identity: {me}   linked set: {linked or '(not returned by userinfo)'}   label={globus_identity_label()}")
    bad = [n for n in not_ids if n == me or n in linked]
    if bad:
        print(f"   ABORT: {bad} is your identity or is LINKED to it — the MEP mapper would match it. "
              "Sign in as an UNLINKED identity (or unlink first).")
        return 3

    from hpc_bridge.endpoint import EndpointCLI
    from hpc_bridge.facility.local import LocalFacility
    from hpc_bridge.profile import Profile
    from hpc_bridge.server import (AppCtx, _connect_facility, _ensure_endpoint_up, _shape_runtime,
                                   _teardown_endpoint)

    async def run() -> int:
        app = AppCtx(facility=LocalFacility(EndpointCLI(user_dir=fresh / "globus_compute")), profile=Profile())
        res = await _connect_facility(app, facility)
        print(f"3. connect_facility({facility!r}): phase={res.phase} reused={res.reused}\n   notice={res.notice!r}")
        if res.phase not in ("needs_account", "connected", "provisioning"):
            print("   (did not attach — nothing more to check)")
            return 4
        verdicts = []
        for n in (1, 2):
            st = await _ensure_endpoint_up(app, shape="compute", confirm_spend=True)
            raw = getattr(_shape_runtime(app, "compute").last_canary, "error", None)
            print(f"{3+n}. ensure_endpoint_up #{n}: status={st.status} block={st.block_state}")
            print(f"   RAW canary error: {raw!r}")
            print(f"   notice: {st.notice!r}")
            verdicts.append(st.status == "down" and "NO ACCOUNT" in (st.notice or ""))
        try:
            await _teardown_endpoint(app)
        except Exception as exc:  # noqa: BLE001
            print(f"   (teardown: {type(exc).__name__}: {exc})")
        ok = all(verdicts)
        print("VERDICT:", "PASS — terminal NO ACCOUNT, stable across calls" if ok else "FAIL — see the raw error above")
        return 0 if ok else 1

    rc = asyncio.run(run())
    sys.stdout.flush()
    os._exit(rc)  # the SDK's non-daemon threads would otherwise keep the process alive


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
