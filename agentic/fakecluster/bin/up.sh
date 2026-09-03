#!/usr/bin/env bash
# Bring the fake cluster up: generate the test SSH key (outside the repo) if missing, build + start
# the compose stack, then wait until Slurm is schedulable and sshd answers.
#
#   agentic/fakecluster/bin/up.sh            # first run builds the image (~2-4 min)
#   HPCB_FAKE_SSH_PORT=2223 …/up.sh          # if 2222 is taken
#   HPCB_FAKE_KEY=~/.ssh/other …/up.sh       # a different (host-side) test key path
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEY="${HPCB_FAKE_KEY:-$HOME/.ssh/hpcb-fake}"

if [ ! -f "$KEY" ]; then
  echo "generating test key $KEY (never committed; lives outside the repo)"
  mkdir -p "$(dirname "$KEY")" && chmod 700 "$(dirname "$KEY")"
  ssh-keygen -q -t ed25519 -N '' -C 'hpcb-fake test key' -f "$KEY"
fi
export HPCB_FAKE_KEY_PUB="$KEY.pub"   # compose bind-mounts this into the login node

cd "$HERE"
docker compose up -d --build
exec "$HERE/bin/wait-for-cluster.sh"
