#!/usr/bin/env bash
# Sourced by up.sh / down.sh / wait-for-cluster.sh: resolve the active PROFILE and the compose -f set.
#   --profile <name> on the command line, else $HPCB_FAKE_PROFILE, else `default`.
# Sets: PROFILE, PROFILE_DIR, HPCB_FAKE_PROFILE_DIR (for compose), COMPOSE (array: docker compose -f … [-f overlay]),
# and reads the profile's node count / login hosts for the readiness wait (python3 tomllib — 3.11+).
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
PROFILE_DIR="$HERE_FC/profiles/$PROFILE"
[ -s "$PROFILE_DIR/profile.toml" ] || { echo "unknown fake-cluster profile '$PROFILE' (have: $(ls "$HERE_FC/profiles" | tr '\n' ' '))" >&2; exit 2; }
export HPCB_FAKE_PROFILE="$PROFILE" HPCB_FAKE_PROFILE_DIR="$PROFILE_DIR"
COMPOSE=(docker compose -f "$HERE_FC/docker-compose.yml")
[ -s "$PROFILE_DIR/compose.override.yml" ] && COMPOSE+=(-f "$PROFILE_DIR/compose.override.yml")
_cap() { python3 -c 'import sys,tomllib; d=tomllib.load(open(sys.argv[1],"rb"))["capabilities"]; v=d[sys.argv[2]]; print(" ".join(map(str,v)) if isinstance(v,list) else v)' "$PROFILE_DIR/profile.toml" "$1"; }
PROFILE_NODES="$(_cap nodes)"
PROFILE_LOGIN_HOSTS="$(_cap login_hosts)"
