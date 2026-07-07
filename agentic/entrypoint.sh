#!/usr/bin/env bash
# Runs as the non-root 'agent' user (Claude Code refuses --dangerously-skip-permissions
# under root). Stages the injected — root-owned, read-only — scoped credentials into
# agent-owned copies with the perms their consumers demand, then runs the scenario.
set -euo pipefail

# SSH key: ssh rejects a private key it can't own / that others could read. Copy to an
# owned 0600 file and re-point the var the MCP server reads.
if [ -n "${HPC_BRIDGE_SSH_KEY:-}" ] && [ -f "$HPC_BRIDGE_SSH_KEY" ]; then
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  install -m 600 "$HPC_BRIDGE_SSH_KEY" "$HOME/.ssh/test_key"
  export HPC_BRIDGE_SSH_KEY="$HOME/.ssh/test_key"
fi

# Globus storage.db: the SDK opens it read-write (token refresh), so copy the mounted
# read-only db into a writable, owned location under HPC_BRIDGE_USER_DIR.
if [ -n "${HPC_BRIDGE_USER_DIR:-}" ]; then
  mkdir -p "$HPC_BRIDGE_USER_DIR"
  [ -f /run/secrets/storage.db ] && install -m 600 /run/secrets/storage.db "$HPC_BRIDGE_USER_DIR/storage.db"
fi

# Run the scenario, then harvest the CLI's NATIVE session transcripts (the operator's AND the
# human-sim's — both actors) into the run's provenance bundle before the container dies.
rc=0
python /work/hpc-bridge/agentic/harness/run.py "$@" || rc=$?
if [ -n "${HPCB_RUNS_DIR:-}" ] && [ -n "${HPCB_RUNID:-}" ]; then
  d=$(ls -d "$HPCB_RUNS_DIR/$HPCB_RUNID"-* 2>/dev/null | head -1)
  if [ -n "$d" ] && [ -d "$HOME/.claude/projects" ]; then
    cp -r "$HOME/.claude/projects" "$d/claude-session" 2>/dev/null || true
  fi
fi
exit "$rc"
