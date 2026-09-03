#!/usr/bin/env bash
# Sweep ONE pool user's leftovers by hand: delete every hpc-bridge-* endpoint it owns and cancel ALL
# its scheduler jobs. This is the harness' only deliberately USER-WIDE operation — the automatic
# teardown is run-scoped (see harness/cluster_ops.py) — so run it only when NO run is using the user.
# It refuses if the user's pool claim is currently held by a live harness process.
#   ./agentic/sweep_pool_user.sh hpcbridge-test-03
set -euo pipefail
USER_="${1:?usage: sweep_pool_user.sh <pool-user>}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEY="${HPCB_TEST_SSH_KEY:-$HOME/.ssh/hpcbridge-test}"
HOST="${HPC_BRIDGE_SSH_HOST:-globus1.cs.uchicago.edu}"
CLAIMS="${HPCB_POOL_CLAIMS_DIR:-$REPO_ROOT/agentic/runs/.pool-claims}"
python3 - "$CLAIMS" "$USER_" <<'PY' || { echo "refusing: $USER_ is claimed by a live harness process"; exit 2; }
import fcntl, os, sys
d, u = sys.argv[1], sys.argv[2]; os.makedirs(d, exist_ok=True)
fd = os.open(os.path.join(d, f"{u}.lock"), os.O_RDWR | os.O_CREAT, 0o644)
try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError: sys.exit(1)
PY
GCE='$HOME/hpc-bridge/gce-venv/bin/globus-compute-endpoint'
echo "sweeping $USER_@$HOST: all hpc-bridge-* endpoints + ALL jobs of this user …"
ssh -o BatchMode=yes -o ConnectTimeout=20 -i "$KEY" -o IdentitiesOnly=yes "$USER_@$HOST" \
  "for ep in \$(ls ~/.globus_compute/ 2>/dev/null | grep '^hpc-bridge-'); do $GCE stop \"\$ep\" >/dev/null 2>&1; $GCE delete \"\$ep\" --yes >/dev/null 2>&1; echo \"deleted \$ep\"; done; scancel -u \"\$(whoami)\" 2>/dev/null; echo \"jobs left: \$(squeue -u \"\$(whoami)\" -h | wc -l)\""
