#!/usr/bin/env bash
# `site` profile, controller: QOS + accounts for ENFORCED accounting. Idempotent (sacctmgr errors on existing rows).
set -u
sacctmgr -i add qos normal >/dev/null 2>&1 || true
sacctmgr -i add qos debug MaxWall=00:30:00 >/dev/null 2>&1 || true
sacctmgr -i add account hpcb-gpu description="hpc-bridge GPU pool" organization=hpcb >/dev/null 2>&1 || true
for u in "${POOL_USERS[@]}"; do
  sacctmgr -i add user "$u" account=hpcb-gpu >/dev/null 2>&1 || true
  # every association may use both QOS; `hpcb` stays the default account (set by the base entrypoint's add user)
  sacctmgr -i modify user where name="$u" set qos=normal,debug defaultqos=normal >/dev/null 2>&1 || true
done
echo "[site] QOS normal/debug, accounts hpcb/hpcb-gpu, enforcement on"
