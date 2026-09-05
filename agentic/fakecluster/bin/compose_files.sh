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
PROFILE_DIR="$HERE_FC/.merged/$PROFILE"
eval "$(python3 "$HERE_FC/bin/profile.py" build "$PROFILE" "$PROFILE_DIR")" || exit 2
export HPCB_FAKE_PROFILE="$PROFILE" HPCB_FAKE_PROFILE_DIR="$PROFILE_DIR"
COMPOSE=(docker compose -f "$HERE_FC/docker-compose.yml")
for ov in $PROFILE_OVERLAYS; do COMPOSE+=(-f "$ov"); done
