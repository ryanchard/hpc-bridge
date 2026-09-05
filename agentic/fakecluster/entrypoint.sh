#!/usr/bin/env bash
# hpc-bridge fake cluster — role entrypoint. One image, four roles:
#   slurmdbd | slurmctld | slurmd | login
# Shared state between containers is exactly two volumes: /etc/munge (the generated munge key) and
# /home (the test users' homes — the endpoint venv + session scratch must be visible to workers).
set -euo pipefail

ROLE="${1:?role required: slurmdbd|slurmctld|slurmd|login}"
POOL_USERS=(hpcbridge-test $(for i in $(seq -f "%02g" 0 9); do echo "hpcbridge-test-$i"; done))

log() { echo "[$ROLE] $*" >&2; }

wait_tcp() {  # wait_tcp host port [what]
  local host=$1 port=$2 what=${3:-$1:$2}
  log "waiting for $what…"
  until (exec 3<>"/dev/tcp/$host/$port") 2>/dev/null; do sleep 1; done
  log "$what is reachable"
}

# --- munge -----------------------------------------------------------------------------------
# slurmctld generates the key ONCE (atomically) into the shared /etc/munge volume; every other role
# waits for it. Never a real cluster's key — 1 KiB of urandom per `docker compose up`.
munge_key() {
  if [ "$ROLE" = slurmctld ] && [ ! -s /etc/munge/munge.key ]; then
    log "generating a fresh munge key"
    dd if=/dev/urandom of=/etc/munge/.munge.key.tmp bs=1 count=1024 status=none
    mv /etc/munge/.munge.key.tmp /etc/munge/munge.key
  fi
  until [ -s /etc/munge/munge.key ]; do log "waiting for munge key…"; sleep 1; done
  chown -R munge:munge /etc/munge /run/munge /var/log/munge /var/lib/munge
  chmod 700 /etc/munge && chmod 400 /etc/munge/munge.key
  runuser -u munge -- /usr/sbin/munged --force
  until munge -n 2>/dev/null | unmunge >/dev/null 2>&1; do sleep 0.5; done
  log "munged up"
}

# --- roles -----------------------------------------------------------------------------------
role_slurmdbd() {
  munge_key
  sed "s|@@DBPASS@@|${SLURM_DB_PASS:?SLURM_DB_PASS unset}|" /etc/slurm/slurmdbd.conf.tmpl > /etc/slurm/slurmdbd.conf
  chown slurm:slurm /etc/slurm/slurmdbd.conf && chmod 600 /etc/slurm/slurmdbd.conf
  wait_tcp mysql 3306 "mariadb"
  until mariadb -h mysql -u slurm -p"$SLURM_DB_PASS" -e 'select 1' >/dev/null 2>&1; do sleep 1; done
  log "starting slurmdbd"
  exec slurmdbd -D
}

role_slurmctld() {
  munge_key
  wait_tcp slurmdbd 6819 "slurmdbd"
  # Register the cluster + a pool account/users in the accounting DB (idempotent; sacctmgr exits
  # non-zero when the row already exists). No enforcement, so this is for `sacctmgr show` realism.
  for _ in $(seq 1 30); do
    sacctmgr -i add cluster fake >/dev/null 2>&1 && break
    sacctmgr -n show cluster fake 2>/dev/null | grep -q fake && break
    sleep 2
  done
  sacctmgr -i add account hpcb description="hpc-bridge test pool" organization=hpcb >/dev/null 2>&1 || true
  for u in "${POOL_USERS[@]}"; do
    sacctmgr -i add user "$u" account=hpcb >/dev/null 2>&1 || true
  done
  log "starting slurmctld"
  exec slurmctld -D
}

role_slurmd() {
  munge_key
  wait_tcp slurmctld 6817 "slurmctld"
  log "starting slurmd ($(hostname))"
  exec slurmd -D
}

role_login() {
  munge_key
  # Homes live on the shared /home volume; create on first boot. The host's freshly generated test
  # public key is bind-mounted at /run/hpcb/authorized_keys and installed for every pool user.
  for u in "${POOL_USERS[@]}"; do
    h="/home/$u"
    if [ ! -d "$h" ]; then
      mkdir -p "$h" && cp -rT /etc/skel "$h" && chown -R "$u:hpcb" "$h" && chmod 700 "$h"
    fi
    if [ -s /run/hpcb/authorized_keys ]; then
      mkdir -p "$h/.ssh" && install -m 600 /run/hpcb/authorized_keys "$h/.ssh/authorized_keys"
      chown -R "$u:hpcb" "$h/.ssh" && chmod 700 "$h/.ssh"
    fi
  done
  [ -s /run/hpcb/authorized_keys ] || log "WARNING: no /run/hpcb/authorized_keys mounted — nobody can ssh in"
  wait_tcp slurmctld 6817 "slurmctld"
  log "starting sshd ($(hostname), $(ip -o -4 addr show scope global | awk '{print $2"="$4}' | tr '\n' ' '))"
  exec /usr/sbin/sshd -D -e
}

case "$ROLE" in
  slurmdbd)  role_slurmdbd ;;
  slurmctld) role_slurmctld ;;
  slurmd)    role_slurmd ;;
  login)     role_login ;;
  *) log "unknown role $ROLE"; exit 2 ;;
esac
