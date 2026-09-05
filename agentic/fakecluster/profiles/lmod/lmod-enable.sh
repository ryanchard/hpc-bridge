#!/usr/bin/env bash
# Sourced by the lmod profile's per-role setup on login AND compute nodes: enable Lmod (restore the parked
# /etc/profile.d hooks), install the modulefiles at /opt/modulefiles, and take uv OFF the default PATH (it becomes
# `module load uv/0.12.9`). Idempotent.
set -u
if ls /opt/lmod-profile.d/*lmod* >/dev/null 2>&1; then cp -f /opt/lmod-profile.d/*lmod* /etc/profile.d/; fi
mkdir -p /opt/modulefiles && cp -rT "$PROFILE_DIR/modulefiles" /opt/modulefiles
# Lmod reads MODULEPATH from its init; point every shell at ours (the package's default is /etc/lmod/modulespath)
mkdir -p /etc/lmod && echo /opt/modulefiles > /etc/lmod/modulespath
# uv's managed CPython ships only `python3.11`: a module user (and `python3 -m venv`) expects python3/python too
for n in python3 python; do [ -e /opt/python/3.11/bin/$n ] || ln -s python3.11 /opt/python/3.11/bin/$n 2>/dev/null || true; done
if [ -x /usr/local/bin/uv ] && [ ! -x /opt/uv/0.12.9/bin/uv ]; then
  mkdir -p /opt/uv/0.12.9/bin && mv /usr/local/bin/uv /usr/local/bin/uvx /opt/uv/0.12.9/bin/ 2>/dev/null || true
fi
echo "[lmod] module system enabled (MODULEPATH=/opt/modulefiles: $(ls /opt/modulefiles | tr '\n' ' ')); uv is module-only"
