#!/usr/bin/env bash
# Block until the fake cluster is usable: both compute nodes idle in `main`, `sacct` answers
# (slurmdbd wired), and sshd on the login node accepts the test key. Exit non-zero after ~3 min.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEY="${HPCB_FAKE_KEY:-$HOME/.ssh/hpcb-fake}"
PORT="${HPCB_FAKE_SSH_PORT:-2222}"
USER_="${HPCB_FAKE_USER:-hpcbridge-test-00}"
DEADLINE=$(( $(date +%s) + 180 ))
cd "$HERE"

say() { echo "wait-for-cluster: $*"; }
left() { echo $(( DEADLINE - $(date +%s) )); }

say "waiting for 2 idle nodes in partition main…"
until [ "$(docker compose exec -T login sinfo -h -p main -t idle -o '%D' 2>/dev/null | tr -d '[:space:]')" = "2" ]; do
  [ "$(left)" -gt 0 ] || { say "TIMEOUT — sinfo:"; docker compose exec -T login sinfo -N -l || true; docker compose logs --tail=30 slurmctld c1 c2; exit 1; }
  sleep 2
done
say "nodes idle: $(docker compose exec -T login sinfo -h -p main -o '%N %T' | tr -d '\n')"

say "waiting for accounting (sacct)…"
until docker compose exec -T login sacct -n -X >/dev/null 2>&1; do
  [ "$(left)" -gt 0 ] || { say "TIMEOUT — slurmdbd logs:"; docker compose logs --tail=30 slurmdbd; exit 1; }
  sleep 2
done
say "sacct answers"

say "waiting for sshd (port $PORT, user $USER_)…"
SSH=(ssh -p "$PORT" -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=3
     -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$USER_@localhost")
until "${SSH[@]}" true 2>/dev/null; do
  [ "$(left)" -gt 0 ] || { say "TIMEOUT — login logs:"; docker compose logs --tail=30 login; exit 1; }
  sleep 2
done
say "ssh OK: $("${SSH[@]}" 'echo "$(whoami)@$(hostname) uid=$(id -u) home=$HOME python=$(python3 --version) uv=$(uv --version)"')"
say "READY"
