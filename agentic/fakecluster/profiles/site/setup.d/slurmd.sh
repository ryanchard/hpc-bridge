#!/usr/bin/env bash
# `site` profile, compute nodes: fake GPU device files. Slurm's gres/gpu plugin drops a file-less GPU from the
# node's GRES list ("Ignoring file-less GPU"), so c3 registered 0 < 2 and was drained INVAL. The standard trick for
# a GPU-less test node: create the /dev/nvidia* character devices Slurm expects to see (nothing drives them).
set -u
if [ "$(hostname -s)" = c3 ]; then
  for i in 0 1; do
    [ -e "/dev/nvidia$i" ] || mknod -m 666 "/dev/nvidia$i" c 195 "$i" 2>/dev/null || echo "[site] mknod /dev/nvidia$i failed (CAP_MKNOD?)"
  done
  echo "[site] fake GPU devices: $(ls /dev/nvidia* 2>/dev/null | tr '\n' ' ')"
fi
