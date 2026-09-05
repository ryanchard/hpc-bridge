#!/usr/bin/env bash
# `polaris` overlay, server (after the base pbs queues/nodes): the `filesystems` resource, what the nodes offer, the
# scheduler's knowledge of it, and the queuejob hook that holds jobs which do not request it. Idempotent.
set -u
Q=/opt/pbs/bin/qmgr
# A JOB-WIDE resource (no `flag=h`): Polaris requests it as `-l filesystems=home:eagle`, and PBS only allows `-l name=`
# for job-wide resources — a host-level one must go inside `select` ("-lresource= cannot be used with select", live
# 2026-09-06). What the site offers lives on the server, not the nodes.
if $Q -c "list resource filesystems" 2>/dev/null | grep -q "flag = h"; then $Q -c "delete resource filesystems" >/dev/null 2>&1 || true; fi
$Q -c "create resource filesystems type=string_array" >/dev/null 2>&1 || true
$Q -c "set server resources_available.filesystems = home,eagle,grand" >/dev/null 2>&1 || true
SC=/var/spool/pbs/sched_priv/sched_config
if ! grep -q 'filesystems' "$SC"; then
  sed -i 's/^resources: *"\(.*\)"/resources: "\1, filesystems"/' "$SC"
  pkill -HUP -f pbs_sched 2>/dev/null || true   # the scheduler re-reads sched_config
fi
HOOK=/etc/hpcb/profile/polaris/require_filesystems.py
$Q -c "create hook require_filesystems event=queuejob" >/dev/null 2>&1 || true
$Q -c "import hook require_filesystems application/x-python default $HOOK" \
  && $Q -c "set hook require_filesystems enabled = true" \
  && echo "[polaris] job-wide filesystems resource (server offers home,eagle,grand); queuejob hook holds jobs without -l filesystems" \
  || echo "[polaris] WARNING: the require_filesystems hook could not be imported"
