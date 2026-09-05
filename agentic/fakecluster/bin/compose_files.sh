#!/usr/bin/env bash
# Sourced by up.sh / down.sh / wait-for-cluster.sh: resolve the active PROFILE and the compose -f set.
#   --profile <name> on the command line, else $HPCB_FAKE_PROFILE, else `default`.
# A profile may LAYER on another (`base = "site"` in its profile.toml): bin/profile.py materialises the merged dir
# under .merged/<name>/ (gitignored) — THAT is what compose mounts as /etc/hpcb/profile — and lists every
# compose.override.yml in the chain (base first) for `-f`.
# Sets: PROFILE, PROFILE_DIR (the merged dir), HPCB_FAKE_PROFILE_DIR (for compose), COMPOSE (array), PROFILE_NODES,
# PROFILE_LOGIN_HOSTS, PROFILE_CATALOG_CMD (a host command printing this cluster's local catalog, or empty).
HERE_FC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${HPCB_FAKE_PROFILE:-default}"
ARGS_LEFT=()
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --profile=*) PROFILE="${1#--profile=}"; shift ;;
    *) ARGS_LEFT+=("$1"); shift ;;
  esac
done
# The MEP overlay's compose file needs the MEP owner's Globus login path and the endpoint version to INTERPOLATE at all
# (up AND down), so every script that composes reads agentic/.env here (unset vars only) and fills the defaults.
ENV_FILE_FC="$HERE_FC/../.env"
if [ -f "$ENV_FILE_FC" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    k="${line%%=*}"; [ -z "${!k+x}" ] && export "$line"
  done < "$ENV_FILE_FC"
fi
export HPCB_MEP_GLOBUS_DB="${HPCB_MEP_GLOBUS_DB:-${HPCB_TEST_GLOBUS_DB:-/dev/null}}"   # /dev/null lets `down` interpolate without a login
if [ -z "${HPCB_MEP_GCE_VERSION:-}" ]; then
  HPCB_MEP_GCE_VERSION="$("$HERE_FC/../../.venv/bin/python" -c 'import importlib.metadata as m; print(m.version("globus-compute-sdk"))' 2>/dev/null || echo 4.16.0)"
fi
export HPCB_MEP_GCE_VERSION
export HPCB_MEP_EMAIL="${HPCB_MEP_EMAIL-}"   # the managers' contact address (required to START them; empty interpolates for down)
PROFILE_DIR="$HERE_FC/.merged/$PROFILE"
eval "$(python3 "$HERE_FC/bin/profile.py" build "$PROFILE" "$PROFILE_DIR")" || exit 2
export HPCB_FAKE_PROFILE="$PROFILE" HPCB_FAKE_PROFILE_DIR="$PROFILE_DIR"
export HPCB_FAKE_SSHD_PORT="$PROFILE_HARNESS_SSH_PORT"   # the container port the published ssh port maps to (22; an MFA profile's key-only sshd otherwise)
export HPCB_TOTP_SECRET="$PROFILE_TOTP_SECRET"          # an MFA profile's fixture secret (enrolled at boot; the human-sim's authenticator)
COMPOSE=(docker compose -f "$HERE_FC/docker-compose.yml")
for ov in $PROFILE_OVERLAYS; do COMPOSE+=(-f "$ov"); done
