#!/usr/bin/env bash
# hpc-bridge fake cluster — PBS role entrypoint (OpenPBS). One image, three roles:
#   server (pbs_server + pbs_sched + pbs_comm + the datastore) | mom (pbs_mom) | login (sshd, PBS clients only)
# Shared state: the /home volume (pool users' homes — endpoint venv + session scratch must be visible to the moms).
# The mounted PROFILE (/etc/hpcb/profile: profiles/<name>/, merged) supplies setup.d/<role>[-*].sh — for the server
# role that is the qmgr configuration (queues, node registration, limits) — exactly like the Slurm entrypoint.
set -euo pipefail
ROLE="${1:?role required: server|mom|login}"
POOL_USERS=(hpcbridge-test $(for i in $(seq -f "%02g" 0 9); do echo "hpcbridge-test-$i"; done))
PBS_SERVER_HOST="${PBS_SERVER_HOST:-pbsserver}"
PROFILE_DIR=/etc/hpcb/profile
log() { echo "[$ROLE] $*" >&2; }

run_setup() {  # run_setup <role> — setup.d/<role>.sh, then setup.d/<role>-*.sh, sorted
  local f
  for f in "$PROFILE_DIR/setup.d/$1.sh" $(ls "$PROFILE_DIR/setup.d/$1"-*.sh 2>/dev/null | sort); do
    if [ -s "$f" ]; then log "profile setup: $(basename "$f")"; # shellcheck disable=SC1090
      source "$f"; fi
  done
}
wait_tcp() {  # wait_tcp host port [what]
  local host=$1 port=$2 what=${3:-$1:$2}
  log "waiting for $what…"
  until (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null; do sleep 1; done
  log "$what is reachable"
}
pbs_conf() {  # pbs_conf <start_server> <start_sched> <start_comm> <start_mom>
  cat > /etc/pbs.conf <<CONF
PBS_EXEC=/opt/pbs
PBS_HOME=/var/spool/pbs
PBS_SERVER=$PBS_SERVER_HOST
PBS_START_SERVER=$1
PBS_START_SCHED=$2
PBS_START_COMM=$3
PBS_START_MOM=$4
PBS_CORE_LIMIT=1
PBS_SCP=/usr/bin/scp
CONF
}
ensure_homes() {
  for u in "${POOL_USERS[@]}"; do
    h="/home/$u"
    if [ ! -d "$h" ]; then mkdir -p "$h" && cp -rT /etc/skel "$h" && chown -R "$u:hpcb" "$h" && chmod 700 "$h"; fi
    if [ -s /run/hpcb/authorized_keys ]; then
      mkdir -p "$h/.ssh" && install -m 600 /run/hpcb/authorized_keys "$h/.ssh/authorized_keys"
      chown -R "$u:hpcb" "$h/.ssh" && chmod 700 "$h/.ssh"
    fi
  done
}
log "profile: $(sed -n 's/^name *= *"\(.*\)"/\1/p' "$PROFILE_DIR/profile.toml" 2>/dev/null || echo unknown)"

role_server() {
  pbs_conf 1 1 1 0
  # first start: pbs_habitat creates the datastore (PostgreSQL under PBS_HOME) and the default workq
  /etc/init.d/pbs start >/tmp/pbs-start.log 2>&1 || { cat /tmp/pbs-start.log >&2; log "pbs start failed"; exit 1; }
  until /opt/pbs/bin/qstat -B >/dev/null 2>&1; do sleep 1; done
  log "pbs_server up: $(/opt/pbs/bin/qstat -B -f 2>/dev/null | awk -F'= ' '/pbs_version/{print $2}')"
  /opt/pbs/bin/qmgr -c "set server flatuid = true" >/dev/null 2>&1 || true   # same uids everywhere; no per-host user map
  /opt/pbs/bin/qmgr -c "set server job_history_enable = true" >/dev/null 2>&1 || true   # parsl polls `qstat -x -f -F json`
  /opt/pbs/bin/qmgr -c "set server scheduling = true" >/dev/null 2>&1 || true
  run_setup server   # the profile's queues, node registration, limits
  log "ready: queues $(/opt/pbs/bin/qstat -Q -f 2>/dev/null | awk '/^Queue:/{printf "%s ", $2}')"
  # pbs runs as daemons; keep PID 1 alive and forward a stop
  trap '/etc/init.d/pbs stop >/dev/null 2>&1; exit 0' TERM INT
  while pgrep -f pbs_server >/dev/null; do sleep 5; done   # the daemon is pbs_server.bin (pbs_server is its wrapper)
  log "pbs_server exited"; exit 1
}

role_mom() {
  pbs_conf 0 0 0 1
  mkdir -p /var/spool/pbs/mom_priv
  # the server may talk to this mom; output files are on the shared /home — copy locally, never scp
  printf '$clienthost %s\n$usecp *:/home /home\n' "$PBS_SERVER_HOST" > /var/spool/pbs/mom_priv/config
  # The JOB environment: OpenPBS's default pbs_environment is PATH=/bin:/usr/bin, so a job could not find uv
  # (/usr/local/bin) — the plugin's worker_init self-provision exited 127 and the endpoint saw "workers failed to
  # register" (live 2026-09-06). Real sites carry a fuller PATH into jobs; so does this one.
  printf 'PATH=/usr/local/bin:/usr/bin:/bin:/opt/pbs/bin\nLANG=C.UTF-8\n' > /var/spool/pbs/pbs_environment
  wait_tcp "$PBS_SERVER_HOST" 15001 "pbs_server"
  /etc/init.d/pbs start >/tmp/pbs-start.log 2>&1 || { cat /tmp/pbs-start.log >&2; log "pbs_mom start failed"; exit 1; }
  run_setup mom
  log "pbs_mom up ($(hostname))"
  trap '/etc/init.d/pbs stop >/dev/null 2>&1; exit 0' TERM INT
  while pgrep -f pbs_mom >/dev/null; do sleep 5; done
  log "pbs_mom exited"; exit 1
}

role_login() {
  pbs_conf 0 0 0 0   # clients only: qsub/qstat/qdel/pbsnodes reach PBS_SERVER
  ensure_homes
  [ -s /run/hpcb/authorized_keys ] || log "WARNING: no /run/hpcb/authorized_keys mounted — nobody can ssh in"
  run_setup login
  wait_tcp "$PBS_SERVER_HOST" 15001 "pbs_server"
  log "starting sshd ($(hostname), $(ip -o -4 addr show scope global | awk '{print $2"="$4}' | tr '\n' ' '))"
  exec /usr/sbin/sshd -D -e
}

case "$ROLE" in
  server) role_server ;;
  mom)    role_mom ;;
  login)  role_login ;;
  *) log "unknown role $ROLE"; exit 2 ;;
esac
