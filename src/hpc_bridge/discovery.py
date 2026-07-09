"""Probe an un-indexed facility's login node over raw SSH and propose a FacilityDetails draft.

The cache-miss path of the catalog: a single batched SSH round-trip (MFA-once-friendly) →
deterministic parse → a *proposed* config the user confirms (above all the low-confidence
`interface`). Never trusted — the login-shape canary validates it after the user confirms. Needs only
the SSH transport (`ssh_host` + env creds), not the rest of `FacilityDetails`, so it runs before any
catalog entry or `SlurmFacility` exists.
"""
from __future__ import annotations

import shlex

from .facility.remote import NeedsPreauth, SshTarget, is_interactive_auth_failure, ssh_exec
from .models import FacilityDetails

# uv-bootstrap env_setup (validated live on globus1): idempotent create-venv + install, so the FIRST
# connect provisions the toolchain and every later call (and the compute worker, which replays
# env_setup) short-circuits to a bare activate. `{venv}` is templated by profile_from_catalog_entry.
_UV_ENV_SETUP = (
    "[ -d {venv} ] || uv venv {venv}; . {venv}/bin/activate; "
    "command -v globus-compute-endpoint >/dev/null 2>&1 || uv pip install -q globus-compute-endpoint"
)

# Dedicated HPC high-speed fabrics — a strong `interface` signal vs a management eth.
_FAST_NIC_PREFIXES = ("ib", "hsn", "ipogif", "hsi", "bond")
_VIRTUAL_NIC_PREFIXES = ("docker", "virbr", "veth", "br-", "cni", "flannel", "tap")

# One batched probe. Each command is guarded so one failure can't abort the sweep; output is framed by
# sentinels so a login banner can't pollute the parse. Multi-valued facts (PART/NIC) repeat their key.
_PROBE = r"""
echo HPCB_PROBE_BEGIN
echo "USER=$(whoami 2>/dev/null)"
echo "HOME=${HOME:-}"
echo "SCRATCH=${SCRATCH:-}"
echo "WORK=${WORK:-}"
echo "PSCRATCH=${PSCRATCH:-}"
if command -v sbatch >/dev/null 2>&1; then echo SCHED=slurm; else echo SCHED=none; fi
echo "GCE=$(command -v globus-compute-endpoint 2>/dev/null)"
echo "UV=$(command -v uv 2>/dev/null)"
echo "MYBALANCE=$(command -v mybalance 2>/dev/null)"
echo "XDUSAGE=$(command -v xdusage 2>/dev/null)"
sinfo -h -o 'PART=%P|%a' 2>/dev/null || true
ip -o -4 addr show 2>/dev/null | awk '{print "NIC="$2"|"$4}' || true
echo HPCB_PROBE_END
""".strip()


async def discover_facility_details(target: SshTarget) -> tuple[FacilityDetails, list[str]]:
    """Run the batched probe over `target` and parse it into a proposed FacilityDetails + notes.

    `notes` names the low-confidence / unresolved fields the agent must confirm with the user
    (`interface` above all). Raises RuntimeError if the probe SSH itself can't reach the host."""
    rc, out, err = await ssh_exec(target, f"bash -lc {shlex.quote(_PROBE)}")
    if "HPCB_PROBE_BEGIN" not in out:  # never even ran the script (auth/connect failure)
        if is_interactive_auth_failure(rc, err):  # needs a one-time interactive pre-auth, not broken
            raise NeedsPreauth(target)
        raise RuntimeError(f"discovery probe failed (rc={rc}): {(err or out).strip()[:300]}")
    return parse_probe(out, ssh_host=target.host)


def parse_probe(stdout: str, *, ssh_host: str) -> tuple[FacilityDetails, list[str]]:
    """Pure parse of the probe stdout into a proposed FacilityDetails + low-confidence notes."""
    f = _collect(stdout)
    user = f.get("USER") or ""
    notes: list[str] = []

    if f.get("SCHED") != "slurm":
        notes.append("scheduler: `sbatch` not found — only Slurm is supported; confirm this is a "
                     "Slurm facility before proceeding.")

    scratch_root, scratch_note = _scratch(
        f.get("SCRATCH") or f.get("WORK") or f.get("PSCRATCH"), f.get("HOME"), user)
    if scratch_note:
        notes.append(scratch_note)

    partition = _default_partition(f.get("PART", []))
    if not f.get("PART"):
        notes.append("partition: `sinfo` returned nothing; confirm the queue to use.")

    interface, nic_note = _interface(f.get("NIC", []))
    notes.append(nic_note)  # interface is always flagged — it's the field the canary most often fails on

    env_setup, env_note = _env_setup(f.get("GCE"), f.get("UV"), user)
    notes.append(env_note)

    alloc_cmd, alloc_parser, alloc_note = _allocation(f)
    if alloc_note:
        notes.append(alloc_note)

    draft = FacilityDetails(
        ssh_host=ssh_host,
        interface=interface,
        env_setup=env_setup,
        scratch_root=scratch_root,
        partition=partition,
        scheduler="slurm",
        allocation_command=alloc_cmd,
        allocation_parser=alloc_parser,
    )
    return draft, notes


def _collect(stdout: str) -> dict:
    """Frame-bounded parse of `KEY=value` lines; PART/NIC accumulate into lists."""
    scalars: dict[str, str] = {}
    multi: dict[str, list[str]] = {"PART": [], "NIC": []}
    in_block = False
    for raw in stdout.splitlines():
        line = raw.strip()
        if line == "HPCB_PROBE_BEGIN":
            in_block = True
            continue
        if line == "HPCB_PROBE_END":
            break
        if not in_block or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key in multi:
            multi[key].append(val)
        else:
            scalars[key] = val
    return {**scalars, **multi}


def _templatize(path: str, user: str) -> str:
    """Replace the discovered login name with the `{user}` template so the path is per-user."""
    return path.replace(user, "{user}") if user and user in path else path


def _scratch(scratch_src: str | None, home: str | None, user: str) -> tuple[str, str | None]:
    """Propose `scratch_root` + a low-confidence note. `$SCRATCH` is per-user on many facilities
    (NERSC/Anvil — it contains the login name, so we templatize it) but a SHARED base on others
    (Midway: `/scratch/midway3`). When the login name ISN'T in the path we can't tell which, so we
    append a per-user subdir and flag it — a shared base ⇒ `Permission denied` on every session cd
    (seen live on Midway, where the un-flagged shared path stranded the run)."""
    if scratch_src:
        base = scratch_src.rstrip("/")
        if user and user in base:
            return _templatize(base, user) + "/.hpc-bridge", None
        return base + "/{user}/.hpc-bridge", (
            f"scratch_root: $SCRATCH={base} has no per-user component; proposed {base}/{{user}}/"
            ".hpc-bridge — CONFIRM it's a writable per-user path (a shared base ⇒ Permission denied "
            "on every session cd).")
    return _templatize((home or "/tmp").rstrip("/"), user) + "/.hpc-bridge", (
        "scratch_root: no $SCRATCH/$WORK set; defaulted under $HOME — confirm a shared, non-purged path.")


def _default_partition(part_lines: list[str]) -> str:
    """Pick a sensible default queue: prefer a cheap/quick one, else the Slurm default (`*`), else first."""
    names = [p.split("|", 1)[0] for p in part_lines]
    clean = [n.rstrip("*") for n in names]
    for pref in ("debug", "shared", "test", "standard", "batch"):
        if pref in clean:
            return pref
    starred = [n[:-1] for n in names if n.endswith("*")]
    if starred:
        return starred[0]
    return clean[0] if clean else "debug"


def _interface(nic_lines: list[str]) -> tuple[str, str]:
    """Propose the worker-binding NIC from `ip addr` candidates — always low-confidence."""
    cands: list[str] = []
    for raw in nic_lines:
        iface = raw.split("|", 1)[0]
        if iface == "lo" or iface.startswith(_VIRTUAL_NIC_PREFIXES) or iface in cands:
            continue
        cands.append(iface)
    if not cands:
        return "ib0", ("interface: no candidate NIC found via `ip addr`; defaulted to `ib0` — VERIFY "
                       "(a wrong interface ⇒ workers never register).")
    fast = [c for c in cands if c.startswith(_FAST_NIC_PREFIXES)]
    pick = fast[0] if fast else cands[0]
    return pick, (f"interface: proposed `{pick}` from {cands} — CONFIRM (a wrong interface ⇒ workers "
                  "never register; the login canary catches it).")


def _env_setup(gce: str | None, uv: str | None, user: str) -> tuple[str, str]:
    """Propose how to put globus-compute-endpoint on PATH for the bootstrap's `bash -lc`."""
    if gce:
        if gce.endswith("/bin/globus-compute-endpoint"):
            venv = _templatize(gce[: -len("/bin/globus-compute-endpoint")], user)
            return f"source {venv}/bin/activate", (f"env_setup: globus-compute-endpoint already at "
                    f"{gce}; proposed activating its venv — confirm.")
        return "true", (f"env_setup: globus-compute-endpoint already on PATH ({gce}); using a no-op — "
                        "confirm it's on the *bootstrap* PATH (a non-interactive `bash -lc`).")
    if uv:
        return _UV_ENV_SETUP, ("env_setup: no globus-compute-endpoint found, but `uv` is present — "
                               "proposed an idempotent uv create-venv+install (first connect provisions "
                               "the toolchain).")
    return ("", "env_setup: neither globus-compute-endpoint nor uv found — supply how to put it on "
            "PATH (a `module load` and/or `source <venv>/bin/activate`).")


def _allocation(f: dict) -> tuple[str | None, str | None, str | None]:
    """Detect an allocation-listing command; only `mybalance` has a parser today."""
    if f.get("MYBALANCE"):
        return "mybalance", "mybalance", None
    if f.get("XDUSAGE"):
        return "xdusage", None, ("allocation: found `xdusage` but no parser is implemented for it; "
                                 "left the parser unset — you'll pass account= directly.")
    return None, None, None
