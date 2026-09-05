#!/usr/bin/env bash
# `mep` overlay, controller: the MAPPED account exists and has an association (site enforces accounting).
set -u
id hpcbmep >/dev/null 2>&1 || useradd -M -u 2100 -g hpcb -s /bin/bash -p '*' hpcbmep
sacctmgr -i add user hpcbmep account=hpcb >/dev/null 2>&1 || true
sacctmgr -i modify user where name=hpcbmep set qos=normal,debug defaultqos=normal >/dev/null 2>&1 || true
echo "[mep] mapped account hpcbmep (uid 2100) in account hpcb"
