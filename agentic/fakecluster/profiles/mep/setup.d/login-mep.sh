#!/usr/bin/env bash
# `mep` overlay, login nodes (sourced as root before sshd; the entrypoint runs `set -euo pipefail`, so every step
# that may fail is guarded — a broken MEP must not take the login node down with it).
#
# Every login node: the mapped account + its home (shared /home). login01 ONLY: the two MEP managers — the endpoint
# software pinned to the version the plugin's SDK runs (HPCB_MEP_GCE_VERSION), the owner's Globus login staged into
# /root/.globus_compute (the `mep-state` volume, so UUIDs survive a restart), one endpoint dir per manager
# (config.yaml, identity_mapping.json, user_config_template.yaml.j2, user_config_schema.json), each started in the
# background. `hpcb-mep-catalog` prints the plugin-side catalog once endpoint.json (the minted UUID) exists.
set -u
MEPUSER=hpcbmep
id "$MEPUSER" >/dev/null 2>&1 || useradd -M -u 2100 -g hpcb -s /bin/bash -p '*' "$MEPUSER"
h="/home/$MEPUSER"
if [ ! -d "$h" ]; then mkdir -p "$h" && cp -rT /etc/skel "$h" && chown -R "$MEPUSER:hpcb" "$h" && chmod 700 "$h"; fi
install -m 755 "$PROFILE_DIR/mep/hpcb-mep-catalog" /usr/local/bin/hpcb-mep-catalog

if [ "$(hostname -s)" = login01 ]; then
  VER="${HPCB_MEP_GCE_VERSION:-4.16.0}"
  IDENTITY="${HPCB_MEP_IDENTITY:-gusellerm@uchicago.edu}"
  EMAIL="${HPCB_MEP_EMAIL:-hpcb-fake-mep@example.com}"   # the manager's contact address (required by 4.16; a fixture)
  # The venv's interpreter must be readable by the MAPPED user: the manager forks each user endpoint as that account
  # and execs the same globus-compute-endpoint. uv's default python store is /root/.local (0700) — the child died with
  # EX_NOPERM (77) — so the managed python lives under /opt too.
  export UV_PYTHON_INSTALL_DIR=/opt/uv-python
  case "$(readlink /opt/gce/bin/python 2>/dev/null)" in /root/*) echo "[mep] /opt/gce points into /root — rebuilding"; rm -rf /opt/gce ;; esac
  have="$(/opt/gce/bin/python -c 'import importlib.metadata as m; print(m.version("globus-compute-endpoint"))' 2>/dev/null || true)"
  if [ "$have" != "$VER" ]; then
    echo "[mep] installing globus-compute-endpoint==$VER into /opt/gce (had: ${have:-none})"
    uv venv -q --python 3.11 /opt/gce && uv pip install -q --python /opt/gce/bin/python "globus-compute-endpoint==$VER" \
      || echo "[mep] WARNING: endpoint install failed"
    chmod -R a+rX /opt/uv-python /opt/gce 2>/dev/null || true
  fi
  mkdir -p /root/.globus_compute && chmod 700 /root/.globus_compute
  if [ ! -s /root/.globus_compute/storage.db ]; then
    if [ -s /run/hpcb/mep-storage.db ]; then install -m 600 /run/hpcb/mep-storage.db /root/.globus_compute/storage.db
    else echo "[mep] WARNING: no /run/hpcb/mep-storage.db mounted — the MEP cannot log in"; fi
  fi
  for kind in strict open; do
    name="hpcb-mep-$kind"; d="/root/.globus_compute/$name"
    mkdir -p "$d" && chmod 700 "$d"
    sed -e "s/@@KIND@@/$kind/g" -e "s/@@EMAIL@@/$EMAIL/g" "$PROFILE_DIR/mep/config.yaml" > "$d/config.yaml"
    python3 "$PROFILE_DIR/mep/idmap.py" "$IDENTITY" "$MEPUSER" > "$d/identity_mapping.json" && chmod 600 "$d/identity_mapping.json"
    sed "s/@@VER@@/$VER/g" "$PROFILE_DIR/mep/user_config_template.yaml.j2" > "$d/user_config_template.yaml.j2"
    cp "$PROFILE_DIR/mep/schema-$kind.json" "$d/user_config_schema.json"
    rm -f "$d/daemon.pid"   # a stale pidfile from a killed container blocks the start for 90 s
    ( cd / && nohup /opt/gce/bin/globus-compute-endpoint start "$name" >"/var/log/$name.log" 2>&1 & ) || true
    echo "[mep] started $name (log /var/log/$name.log)"
  done
fi
