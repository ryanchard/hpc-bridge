#!/usr/bin/env bash
# Step-1 proof: over SSH as a pool user, sbatch a job onto `main`, then show it COMPLETED on a
# compute container in sacct (i.e. sshd + munge + slurmctld + slurmd + slurmdbd all agree).
set -euo pipefail
KEY="${HPCB_FAKE_KEY:-$HOME/.ssh/hpcb-fake}"
PORT="${HPCB_FAKE_SSH_PORT:-2222}"
USER_="${HPCB_FAKE_USER:-hpcbridge-test-00}"
SSH=(ssh -p "$PORT" -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes
     -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$USER_@localhost")

"${SSH[@]}" 'set -e
  cd "$HOME"
  out=$(sbatch --parsable --wrap "hostname; sleep 5" -p main -N1 -J hpcb-proof)
  jid=${out%%;*}
  echo "submitted job $jid as $(whoami) from $(hostname)"
  for i in $(seq 1 40); do
    st=$(sacct -X -n -j "$jid" -o State | tr -d "[:space:]")
    case "$st" in COMPLETED|FAILED|CANCELLED*|TIMEOUT|NODE_FAIL) break;; esac
    sleep 1
  done
  echo "--- squeue (all users):"; squeue -o "%.8i %.9P %.12j %.10u %.8T %.10M %R"
  echo "--- sacct -X -o JobID,JobName,State,NodeList,Elapsed:"
  sacct -X -j "$jid" -o JobID,JobName,State,NodeList,Elapsed
  echo "--- job stdout (slurm-$jid.out on the shared /home):"; cat "slurm-$jid.out"
  [ "$st" = COMPLETED ] || { echo "PROOF FAILED: state=$st"; exit 1; }
  echo "PROOF OK: job $jid COMPLETED on $(sacct -X -n -j "$jid" -o NodeList | tr -d "[:space:]")"'
