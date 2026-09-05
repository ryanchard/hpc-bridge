#!/usr/bin/env bash
# `pbs` profile, server: queues, limits and node registration (qmgr; idempotent — qmgr errors on existing objects).
set -u
Q=/opt/pbs/bin/qmgr
$Q -c "create queue workq queue_type=execution" >/dev/null 2>&1 || true
$Q -c "set queue workq enabled = true" ; $Q -c "set queue workq started = true"
$Q -c "set queue workq resources_max.walltime = 48:00:00"
$Q -c "set queue workq resources_default.walltime = 01:00:00"
$Q -c "create queue debug queue_type=execution" >/dev/null 2>&1 || true
$Q -c "set queue debug enabled = true" ; $Q -c "set queue debug started = true"
$Q -c "set queue debug resources_max.walltime = 00:30:00"
$Q -c "set queue debug resources_default.walltime = 00:10:00"
$Q -c "set server default_queue = workq"
# the moms register once reachable; retry until both answer (they wait for us first)
for n in c1 c2; do
  for _ in $(seq 1 60); do
    if $Q -c "create node $n" >/dev/null 2>&1 || $Q -c "list node $n" >/dev/null 2>&1; then
      $Q -c "set node $n resources_available.ncpus = 4" >/dev/null 2>&1 || true
      break
    fi
    sleep 2
  done
done
echo "[pbs] queues workq (48h, default) / debug (30 min); nodes: $(/opt/pbs/bin/pbsnodes -a 2>/dev/null | awk '/^[a-z]/{printf "%s ", $1}')"
