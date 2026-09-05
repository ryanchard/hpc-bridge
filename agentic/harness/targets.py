#!/usr/bin/env python3
"""The cluster a run targets — `globus1` (the lab cluster) or `fake` (agentic/fakecluster, a compose Slurm cluster).

One preset carries every target-specific fact together (ssh host as the JAIL reaches it, node count, the endpoint
name prefix, the pool key, the docker network the jail must join, how the HOST reaches the cluster for probes and
cleanup), so the two targets can never be half-mixed. Selected by HPCB_TARGET (default globus1); run_suite's
`--target` sets it for every cell. Importable on the host (no SDK) and in the jail; also a CLI that prints shell
assignments for run_smoke.sh:

    eval "$(python3 agentic/harness/targets.py fake)"   # HPCB_T_SSH_HOST=login HPCB_T_NETWORK=hpcb-fake_default …

Prompts name the login host as `{ssh_host}`; `fill_prompt` substitutes it (and `{facility}`) literally — never
str.format, a prompt may embed braces of its own.
"""
from __future__ import annotations

import json
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TARGET = "globus1"
REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES_DIR = REPO_ROOT / "agentic" / "fakecluster" / "profiles"

# What the lab cluster offers, in the same vocabulary as a fake-cluster profile's [capabilities].
GLOBUS1_CAPABILITIES = {
    "scheduler": "slurm", "nodes": 3, "login_nodes": 1, "login_hosts": ["globus1.cs.uchicago.edu"],
    "partitions": ["main", "backfill"], "default_partition": "main", "accounting": "none", "accounts": ["lab"],
    "balance_tool": "none", "nics": 1, "qos": [], "gres": "none", "module_system": "none", "mfa": "none",
}


@dataclass(frozen=True)
class Target:
    name: str
    ssh_host: str            # the login host as the JAIL (and the product inside it) reaches it
    nodes: int               # compute nodes in the `main` partition (saturation's sleeper count / node need)
    endpoint_prefix: str     # this target's endpoints in the shared Globus identity: <prefix>-<runid>
    default_key: str         # host path of the pool users' private key
    docker_network: str | None   # the jail joins this network (fake: reach `login:22` with no port mapping)
    probe_argv: tuple[str, ...]  # host-side: one shell command on the cluster (node probes) — argv prefix
    cleanup_host: str        # host-side ssh destination host for per-pool-user cleanup (sweep, abandoned cells)
    cleanup_ssh_opts: tuple[str, ...]
    profile: str | None = None                        # fake: the active cluster profile (profiles/<name>/)
    capabilities: dict = field(default_factory=dict)  # what this cluster offers (a scenario's REQUIRES is matched against it)

    def cleanup_argv(self, user: str, key: str) -> list[str]:
        """Host-side ssh as the pool `user` with `key` (the run-scoped cleanup channel)."""
        return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-i", key, "-o", "IdentitiesOnly=yes",
                *self.cleanup_ssh_opts, f"{user}@{self.cleanup_host}"]


def load_profile(name: str) -> dict:
    """A fake-cluster profile's manifest (profiles/<name>/profile.toml) — its [capabilities] are the vocabulary a
    scenario's REQUIRES is matched against."""
    p = PROFILES_DIR / name / "profile.toml"
    if not p.is_file():
        have = sorted(d.name for d in PROFILES_DIR.iterdir() if (d / "profile.toml").is_file()) if PROFILES_DIR.is_dir() else []
        raise SystemExit(f"targets: unknown fake-cluster profile {name!r} (have: {have})")
    with p.open("rb") as fh:
        return tomllib.load(fh)


def meets(requires: dict | None, caps: dict) -> tuple[bool, str]:
    """Does a cluster with `caps` satisfy a scenario's REQUIRES? Keys: scheduler (==), min_nodes (<= nodes),
    login_nodes (<= login_nodes), accounting (==), balance_tool (==), gres (==), module_system (==), mfa (==),
    min_partitions (<= len(partitions)), min_nics (<= nics). Returns (ok, the first unmet requirement)."""
    for k, want in (requires or {}).items():
        if k == "min_nodes":
            ok = int(caps.get("nodes", 0)) >= int(want)
        elif k == "login_nodes":
            ok = int(caps.get("login_nodes", 1)) >= int(want)
        elif k == "min_partitions":
            ok = len(caps.get("partitions", [])) >= int(want)
        elif k == "min_nics":
            ok = int(caps.get("nics", 1)) >= int(want)
        else:
            ok = str(caps.get(k)) == str(want)
        if not ok:
            return False, f"{k}={want!r} (cluster has {caps.get(k.removeprefix('min_'), caps.get(k))!r})"
    return True, ""


def get(name: str | None = None) -> Target:
    name = (name or os.environ.get("HPCB_TARGET") or DEFAULT_TARGET).strip().lower()
    home = str(Path.home())
    if name == "globus1":
        return Target(
            name="globus1", ssh_host="globus1.cs.uchicago.edu", nodes=3, endpoint_prefix="hpc-bridge-globus1",
            default_key=f"{home}/.ssh/hpcbridge-test", docker_network=None,
            # the operator's own ssh alias (admin identity) — sinfo/squeue for the node gate
            probe_argv=("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", os.environ.get("HPCB_NODE_PROBE_SSH", "globus1")),
            cleanup_host="globus1.cs.uchicago.edu", cleanup_ssh_opts=(), capabilities=dict(GLOBUS1_CAPABILITIES),
        )
    if name == "fake":
        profile = os.environ.get("HPCB_FAKE_PROFILE", "default")
        caps = dict(load_profile(profile).get("capabilities", {}))
        port = os.environ.get("HPCB_FAKE_SSH_PORT", "2222")
        key = os.environ.get("HPCB_FAKE_KEY", f"{home}/.ssh/hpcb-fake")
        nohostkey = ("-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR")
        return Target(
            name="fake", ssh_host="login", nodes=int(caps.get("nodes", 2)), endpoint_prefix="hpc-bridge-fake",
            default_key=key, docker_network=os.environ.get("HPCB_FAKE_NETWORK", "hpcb-fake_default"),
            # the (first) login container's sshd on the host port, as a pool user
            probe_argv=("ssh", "-p", port, "-i", key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                        "-o", "ConnectTimeout=10", *nohostkey, "hpcbridge-test-00@localhost"),
            cleanup_host="localhost", cleanup_ssh_opts=("-p", port, *nohostkey),
            profile=profile, capabilities=caps,
        )
    raise SystemExit(f"targets: unknown target {name!r} (globus1 | fake)")


def fill_prompt(text: str, *, facility: str, ssh_host: str) -> str:
    """Literal substitution of the two per-run tokens a scenario may use in PROMPT / PHASES / USER_GOAL."""
    return text.replace("{facility}", facility).replace("{ssh_host}", ssh_host)


def main(argv: list[str]) -> int:
    t = get(argv[0] if argv else None)
    for k, v in (("NAME", t.name), ("SSH_HOST", t.ssh_host), ("NODES", t.nodes), ("EP_PREFIX", t.endpoint_prefix),
                 ("KEY_DEFAULT", t.default_key), ("NETWORK", t.docker_network or ""), ("PROFILE", t.profile or "")):
        print(f"HPCB_T_{k}={v}")
    print(f"HPCB_T_CAPS_JSON={json.dumps(t.capabilities, separators=(',', ':'))!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
