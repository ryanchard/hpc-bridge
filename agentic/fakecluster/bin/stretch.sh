#!/usr/bin/env bash
# Stretch proof: run hpc-bridge's real bootstrap → compute block → run_shell against the fake cluster,
# from inside the agentic jail image joined to the cluster's network (how the harness would do it).
#
# Needs: the fake cluster up (bin/up.sh), the `hpc-bridge-agentic` image built (run_smoke.sh builds it;
# or `docker build -t hpc-bridge-agentic -f agentic/Dockerfile .`), and a logged-in Globus Compute
# storage.db on the host (default ~/.globus_compute/storage.db; override HPCB_TEST_GLOBUS_DB). The db
# is mounted read-only; the driver copies it into the container like agentic/entrypoint.sh does.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
KEY="${HPCB_FAKE_KEY:-$HOME/.ssh/hpcb-fake}"
DB="${HPCB_TEST_GLOBUS_DB:-$HOME/.globus_compute/storage.db}"
USER_="${HPCB_FAKE_USER:-hpcbridge-test-00}"
RUNID="$(date +%s)"
IMAGE="${HPCB_JAIL_IMAGE:-hpc-bridge-agentic}"
NET="${HPCB_FAKE_NETWORK:-hpcb-fake_default}"

[ -f "$KEY" ] || { echo "missing test key $KEY — run bin/up.sh first"; exit 1; }
[ -f "$DB" ]  || { echo "missing Globus storage.db at $DB — log in with globus-compute-endpoint / set HPCB_TEST_GLOBUS_DB"; exit 1; }
docker image inspect "$IMAGE" >/dev/null 2>&1 || { echo "image $IMAGE not built — docker build -t $IMAGE -f $REPO/agentic/Dockerfile $REPO"; exit 1; }

echo "stretch: user=$USER_ endpoint=hpc-bridge-fake-$RUNID network=$NET"
docker run --rm --network "$NET" \
  -v "$REPO/src":/work/hpc-bridge/src:ro \
  -v "$HERE":/work/fakecluster:ro \
  -v "$DB":/run/secrets/storage.db:ro \
  -v "$KEY":/run/secrets/test_key:ro \
  -e HPC_BRIDGE_SSH_HOST=login \
  -e HPC_BRIDGE_SSH_USER="$USER_" \
  -e HPC_BRIDGE_SSH_KEY=/run/secrets/test_key \
  -e HPC_BRIDGE_ENDPOINT_NAME="hpc-bridge-fake-$RUNID" \
  -e HPC_BRIDGE_USER_DIR=/home/agent/run/fake \
  -e GLOBUS_COMPUTE_USER_DIR=/home/agent/run/fake \
  -e HPC_BRIDGE_STATE_DIR=/home/agent/run/fake/state \
  --entrypoint /work/hpc-bridge/.venv/bin/python \
  "$IMAGE" /work/fakecluster/stretch/driver.py
