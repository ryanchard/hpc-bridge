#!/usr/bin/env bash
# Block until the fake cluster is usable: both compute nodes idle in `main`, `sacct` answers
# (slurmdbd wired), and sshd on the login node accepts the test key. Exit non-zero after ~3 min.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$HERE/bin/compose_files.sh" "$@"   # --profile: the node count and login hosts to wait for
KEY="${HPCB_FAKE_KEY:-$HOME/.ssh/hpcb-fake}"
PORT="${HPCB_FAKE_SSH_PORT:-2222}"
PORT2="${HPCB_FAKE_SSH_PORT2:-2223}"
USER_="${HPCB_FAKE_USER:-hpcbridge-test-00}"
DEADLINE=$(( $(date +%s) + 180 ))
cd "$HERE"

say() { echo "wait-for-cluster: $*"; }
left() { echo $(( DEADLINE - $(date +%s) )); }

say "waiting for $PROFILE_NODES idle compute nodes (profile $PROFILE)…"
until [ "$("${COMPOSE[@]}" exec -T login sinfo -h -N -o '%N %t' 2>/dev/null | sort -u | grep -c ' idle$' | tr -d '[:space:]')" = "$PROFILE_NODES" ]; do
  [ "$(left)" -gt 0 ] || { say "TIMEOUT — sinfo:"; "${COMPOSE[@]}" exec -T login sinfo -N -l || true; "${COMPOSE[@]}" logs --tail=30 slurmctld c1 c2; exit 1; }
  sleep 2
done
say "nodes: $("${COMPOSE[@]}" exec -T login sinfo -h -N -o '%N:%t:%P' | tr '\n' ' ')"

say "waiting for accounting (sacct)…"
until "${COMPOSE[@]}" exec -T login sacct -n -X >/dev/null 2>&1; do
  [ "$(left)" -gt 0 ] || { say "TIMEOUT — slurmdbd logs:"; "${COMPOSE[@]}" logs --tail=30 slurmdbd; exit 1; }
  sleep 2
done
say "sacct answers"

say "waiting for sshd (port $PORT, user $USER_)…"
SSH=(ssh -p "$PORT" -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=3
     -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$USER_@localhost")
until "${SSH[@]}" true 2>/dev/null; do
  [ "$(left)" -gt 0 ] || { say "TIMEOUT — login logs:"; "${COMPOSE[@]}" logs --tail=30 login; exit 1; }
  sleep 2
done
say "ssh OK: $("${SSH[@]}" 'echo "$(whoami)@$(hostname -f) uid=$(id -u) home=$HOME python=$(python3 --version) uv=$(uv --version)"')"
if [ "$(echo "$PROFILE_LOGIN_HOSTS" | wc -w | tr -d ' ')" -ge 2 ]; then
  SSH2=("${SSH[@]/-p $PORT/-p $PORT2}")
  SSH2=(ssh -p "$PORT2" -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=3
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$USER_@localhost")
  say "waiting for the second login node (port $PORT2)…"
  until "${SSH2[@]}" true 2>/dev/null; do
    [ "$(left)" -gt 0 ] || { say "TIMEOUT — login02 logs:"; "${COMPOSE[@]}" logs --tail=30 login02; exit 1; }
    sleep 2
  done
  say "ssh OK: $("${SSH2[@]}" 'echo "$(whoami)@$(hostname -f)"')  (round-robin name: login)"
fi
say "READY"
