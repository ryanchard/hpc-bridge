#!/usr/bin/env bash
# Step-2 sweep: can the login node run hpc-bridge's bootstrap? Over SSH as a pool user, check the
# tools the bootstrap/discovery/teardown shell out to, the NIC name (address_by_interface), outbound
# HTTPS + AMQPS (the endpoint talks to Globus), srun inside a batch job (Parsl's SrunLauncher), the
# shared /home, and time a real `uv venv` + `uv pip install globus-compute-endpoint` (what the
# discover-first env_setup does on the first connect).
set -euo pipefail
KEY="${HPCB_FAKE_KEY:-$HOME/.ssh/hpcb-fake}"
PORT="${HPCB_FAKE_SSH_PORT:-2222}"
USER_="${HPCB_FAKE_USER:-hpcbridge-test-00}"
SSH=(ssh -p "$PORT" -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes
     -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$USER_@localhost")

"${SSH[@]}" <<'REMOTE'
set -u
echo "== whoami/hostname: $(whoami)@$(hostname) hostname-f=$(hostname -f 2>&1)"
echo "== tools:"; for t in bash base64 scancel squeue sacct sinfo sacctmgr sbatch srun uv python3 ip curl git; do printf '  %-10s %s\n' "$t" "$(command -v $t 2>/dev/null || echo MISSING)"; done
echo "== python: $(python3 --version)  uv: $(uv --version)"
echo "== login shell PATH (bash -lc): $(bash -lc 'echo $PATH')"
echo "== NICs (address_by_interface candidates):"; ip -o -4 addr show | awk '{print "  "$2" "$4}'
echo "== outbound internet (Globus Compute API / PyPI):"
curl -sS -o /dev/null -w '  compute.api.globus.org  HTTP %{http_code} in %{time_total}s\n' https://compute.api.globus.org/v2/version || echo "  compute API: FAILED"
curl -sS -o /dev/null -w '  pypi.org                HTTP %{http_code} in %{time_total}s\n' https://pypi.org/simple/globus-compute-endpoint/ || echo "  pypi: FAILED"
echo "== AMQPS 443 reachability (compute.amqps.globus.org):"
timeout 5 bash -c 'exec 3<>/dev/tcp/compute.amqps.globus.org/443' && echo "  tcp 443 open" || echo "  tcp 443 FAILED"
echo "== sinfo:"; sinfo -o '  %P %a %l %D %T %N'
echo "== sacctmgr show cluster / user:"; sacctmgr -n show cluster format=Cluster,ControlHost | sed 's/^/  /'; sacctmgr -n show user "$(whoami)" format=User,DefaultAccount | sed 's/^/  /'
echo "== srun inside a batch job (the SrunLauncher shape):"
cd "$HOME"
jid=$(sbatch --parsable -p main -N1 --wrap 'srun -n1 -N1 bash -c "echo step-on \$(hostname) uid=\$(id -u) home=\$HOME uv=\$(command -v uv)"')
for i in $(seq 1 30); do st=$(sacct -X -n -j "$jid" -o State | tr -d '[:space:]'); case "$st" in COMPLETED|FAILED|CANCELLED*) break;; esac; sleep 1; done
echo "  job $jid -> $st"; sed 's/^/  /' "slurm-$jid.out"
echo "== shared /home (write on login, read on compute):"
echo "hello-from-login-$(date +%s)" > "$HOME/.hpcb-shared-test"
# </dev/null: srun forwards stdin to the task, and stdin here IS the rest of this heredoc script.
srun -p main -N1 -n1 cat "$HOME/.hpcb-shared-test" </dev/null | sed 's/^/  compute reads: /'
echo "== uv venv self-provision timing (what env_setup does on the first connect):"
time (uv venv -q "$HOME/hpcb-uvtest" && . "$HOME/hpcb-uvtest/bin/activate" && uv pip install -q globus-compute-endpoint && globus-compute-endpoint version) 2>&1 | sed 's/^/  /'
rm -rf "$HOME/hpcb-uvtest" "$HOME/.hpcb-shared-test"
echo "== CHECK DONE"
REMOTE
