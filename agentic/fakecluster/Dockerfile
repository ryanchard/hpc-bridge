# hpc-bridge "fake cluster" — ONE image for every Slurm role (slurmdbd / slurmctld / slurmd / login).
#
# One image (not one per role) is load-bearing: the Globus Compute endpoint venv lives under the
# test user's $HOME on a volume shared between the login node and the compute nodes, and a venv's
# python symlink resolves to a system interpreter path — so login and compute MUST carry the same
# python at the same path. Same image also ⇒ identical uid/gid for `slurm`, `munge` and the test
# users on every node, which munge/Slurm require.
#
# arm64-native: Ubuntu 24.04 ships slurm-wlm 23.11 + munge for arm64 (no x86 emulation).
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        slurm-wlm slurmdbd slurm-client munge libmunge2 \
        openssh-server openssh-client \
        mariadb-client \
        python3 python3-venv python3-dev build-essential \
        curl ca-certificates git iproute2 iputils-ping procps less nano \
    && rm -rf /var/lib/apt/lists/*

# uv — hpc-bridge's discover-first flow proposes an env_setup that self-provisions the
# globus-compute-endpoint venv with `uv venv` + `uv pip install` (see discovery._UV_ENV_SETUP).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Slurm / munge / sshd runtime dirs. /etc/munge is a shared volume at run time (the generated
# munge.key); the entrypoint re-chowns it once mounted.
RUN mkdir -p /var/spool/slurmctld /var/spool/slurmd /var/log/slurm \
             /etc/munge /run/munge /var/log/munge /var/lib/munge /run/sshd \
    && chown slurm:slurm /var/spool/slurmctld /var/log/slurm \
    && chown -R munge:munge /etc/munge /run/munge /var/log/munge /var/lib/munge

COPY slurm/slurm.conf slurm/cgroup.conf /etc/slurm/
COPY slurm/slurmdbd.conf.tmpl /etc/slurm/slurmdbd.conf.tmpl
COPY sshd_config /etc/ssh/sshd_config.d/00-hpcb-fake.conf
# Host keys baked at build time — throwaway (the recommended ssh alias disables known_hosts pinning).
RUN ssh-keygen -A

# The test-user pool the agentic harness expects: hpcbridge-test (run_smoke.sh's default) and
# hpcbridge-test-00..02 (run_suite.py's pool). Fixed uids so they match across containers. `-M`: the
# home dirs live on the shared /home volume and are created by the login entrypoint. `-p '*'`
# UNLOCKS the account for pubkey login without setting a password (a fresh useradd leaves `!` in
# shadow, which sshd treats as locked when UsePAM is off).
RUN groupadd -g 2000 hpcb \
    && useradd -M -u 1999 -g hpcb -s /bin/bash -p '*' hpcbridge-test \
    && for i in 00 01 02; do \
         useradd -M -u 20$i -g hpcb -s /bin/bash -p '*' "hpcbridge-test-$i"; \
       done

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
