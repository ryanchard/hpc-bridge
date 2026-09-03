#!/usr/bin/env bash
# Tear the fake cluster down. Default keeps the volumes (homes, accounting DB, munge key) so the
# next `up.sh` is a warm restart; `--wipe` also removes them (a truly fresh cluster).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
if [ "${1:-}" = "--wipe" ]; then
  docker compose down -v --remove-orphans
else
  docker compose down --remove-orphans
fi
