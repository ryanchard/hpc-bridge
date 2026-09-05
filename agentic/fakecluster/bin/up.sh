#!/usr/bin/env bash
# Bring the fake cluster up: generate the test SSH key (outside the repo) if missing, build + start
# the compose stack, then wait until Slurm is schedulable and sshd answers.
#
#   agentic/fakecluster/bin/up.sh            # first run builds the image (~2-4 min)
#   HPCB_FAKE_SSH_PORT=2223 …/up.sh          # if 2222 is taken
#   HPCB_FAKE_KEY=~/.ssh/other …/up.sh       # a different (host-side) test key path
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$HERE/bin/compose_files.sh" "$@"       # --profile <name> | HPCB_FAKE_PROFILE (default: default)
KEY="${HPCB_FAKE_KEY:-$HOME/.ssh/hpcb-fake}"

if [ ! -f "$KEY" ]; then
  echo "generating test key $KEY (never committed; lives outside the repo)"
  mkdir -p "$(dirname "$KEY")" && chmod 700 "$(dirname "$KEY")"
  ssh-keygen -q -t ed25519 -N '' -C 'hpcb-fake test key' -f "$KEY"
fi
export HPCB_FAKE_KEY_PUB="$KEY.pub"   # compose bind-mounts this into the login node

# Profiles with a facility MEP (profiles/mep) need the MEP OWNER's Globus login and the endpoint version the plugin's
# SDK runs. agentic/.env (gitignored) already holds the harness' test storage.db path — fill from it when unset.
ENV_FILE="$HERE/../.env"
if [ -f "$ENV_FILE" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    k="${line%%=*}"; [ -z "${!k+x}" ] && export "$line"
  done < "$ENV_FILE"
fi
export HPCB_MEP_GLOBUS_DB="${HPCB_MEP_GLOBUS_DB:-${HPCB_TEST_GLOBUS_DB:-}}"
if [ -z "${HPCB_MEP_GCE_VERSION:-}" ]; then
  # the jail installs the repo's locked globus-compute-sdk; the MEP (and its workers) must run that endpoint version
  HPCB_MEP_GCE_VERSION="$("$HERE/../../.venv/bin/python" -c 'import importlib.metadata as m; print(m.version("globus-compute-sdk"))' 2>/dev/null || echo 4.16.0)"
fi
export HPCB_MEP_GCE_VERSION

cd "$HERE"
echo "fake cluster: profile '$PROFILE' ($PROFILE_NODES compute nodes; login: $PROFILE_LOGIN_HOSTS)${PROFILE_CATALOG_CMD:+; facility MEP (gce $HPCB_MEP_GCE_VERSION)}"
"${COMPOSE[@]}" up -d --build --remove-orphans
exec "$HERE/bin/wait-for-cluster.sh" --profile "$PROFILE"
