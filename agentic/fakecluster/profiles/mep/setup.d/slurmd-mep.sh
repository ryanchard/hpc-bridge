#!/usr/bin/env bash
# `mep` overlay, compute nodes: the mapped account must resolve where its jobs run.
set -u
id hpcbmep >/dev/null 2>&1 || useradd -M -u 2100 -g hpcb -s /bin/bash -p '*' hpcbmep
