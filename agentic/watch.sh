#!/usr/bin/env bash
# watch.sh — one screen for in-flight agentic runs: live jail containers, the newest run bundles with their
# RESULT lines, and the tail of a log (default: the newest agentic/runs/*.log). Refreshes every 10 s; Ctrl-C to quit.
#   agentic/watch.sh                 # newest sweep/smoke log
#   agentic/watch.sh /path/to/log    # a specific log
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${1:-$(ls -t "$REPO"/agentic/runs/*.log 2>/dev/null | head -1)}"
while true; do
  clear
  echo "== $(date +%T)  jail containers =="
  docker ps --filter ancestor=hpc-bridge-agentic --format '  {{.ID}}  up {{.RunningFor}}  {{.Command}}' 2>/dev/null | sed 's/ ago//' || echo "  (docker not running)"
  echo; echo "== newest run bundles (agentic/runs) =="
  for d in $(ls -td "$REPO"/agentic/runs/*/ 2>/dev/null | head -8); do
    r=$(grep -h -m1 '^RESULT:' "$d/transcript.md" 2>/dev/null || grep -h -o '"result": *"[^"]*"' "$d/record.json" 2>/dev/null | head -1)
    printf '  %-45s %s\n' "$(basename "$d")" "${r:-(running / no result yet)}"
  done
  echo; echo "== log: ${LOG:-none} =="
  [ -n "$LOG" ] && [ -f "$LOG" ] && tail -n 18 "$LOG" | cut -c1-160
  sleep 10
done
