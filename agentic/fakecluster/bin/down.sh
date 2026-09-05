#!/usr/bin/env bash
# Tear the fake cluster down. Default keeps the volumes (homes, accounting DB, munge key) so the
# next `up.sh` is a warm restart; `--wipe` also removes them (a truly fresh cluster — no stale endpoints, worker
# dirs or processes under any pool user). `--profile <name>` selects the overlay so its extra services come down too
# (`--remove-orphans` catches a switch between profiles).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$HERE/bin/compose_files.sh" "$@"
cd "$HERE"
if [ "${ARGS_LEFT[0]:-}" = "--wipe" ]; then
  # A profile that registers things with a remote service (the MEP profile's managers) ships a deregister.sh: run it
  # BEFORE the volumes holding the registrations' state are removed, else the records orphan under the owner identity.
  "${COMPOSE[@]}" down --remove-orphans
  [ -x "$PROFILE_DIR/deregister.sh" ] && "$PROFILE_DIR/deregister.sh"
  "${COMPOSE[@]}" down -v --remove-orphans
else
  "${COMPOSE[@]}" down --remove-orphans
fi
