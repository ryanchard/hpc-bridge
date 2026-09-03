#!/usr/bin/env bash
# fresh_user_session.sh — launch Claude Code with hpc-bridge loaded as a plugin, AS A FRESH USER:
# no Globus tokens, no cached facilities/endpoints, no repo-local settings. Nothing of yours is
# touched: the Compute SDK and hpc-bridge are pointed at scratch dirs under $FRESH (default
# ~/hpcb-fresh), and the session is launched from there so the repo's project-level .mcp.json and
# .claude/settings.local.json do not apply — the plugin is the only hpc-bridge in the session,
# exactly like an installed one.
#
#   scripts/fresh_user_session.sh           # 1st run: expect needs_login (browser), then the MEP attach
#   scripts/fresh_user_session.sh           # 2nd run, same dirs: NO login (refresh tokens) -> straight to attach
#   scripts/fresh_user_session.sh --reset   # wipe the scratch dirs first -> a brand-new user again
#
# In the session, say:  connect me to globus1
#
# DRY_RUN=1 prints the launch command instead of running it.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRESH="${FRESH:-$HOME/hpcb-fresh}"
INDEX="${HPC_BRIDGE_SEARCH_INDEX:-6ff95fb8-1113-42be-a811-3d1cb5a67bd5}"   # the hpc-bridge-test registry

if [[ "${1:-}" == "--reset" ]]; then
  rm -rf "$FRESH"
  echo "reset: removed $FRESH"
  shift
fi
mkdir -p "$FRESH/globus_compute" "$FRESH/state"

command -v claude >/dev/null || { echo "error: 'claude' is not on PATH" >&2; exit 1; }
command -v uv >/dev/null || { echo "error: 'uv' is not on PATH (the plugin launches the server with 'uv run')" >&2; exit 1; }
[[ -f "$REPO/.mcp.json" && -f "$REPO/.claude-plugin/plugin.json" ]] || { echo "error: $REPO is not an hpc-bridge checkout" >&2; exit 1; }

echo "repo:        $REPO  (branch: $(git -C "$REPO" branch --show-current 2>/dev/null || echo '?'))"
echo "registry:    $INDEX"
echo "compute sdk: $FRESH/globus_compute   (tokens land here, not in ~/.globus_compute)"
echo "hpc-bridge:  $FRESH/state            (no cached facilities/endpoints)"
if [[ -f "$FRESH/globus_compute/storage.db" ]]; then
  echo "tokens:      PRESENT -> expect NO login this run (use --reset to be a brand-new user again)"
else
  echo "tokens:      none    -> expect connect_facility to return needs_login (a browser opens; or paste the code back)"
fi
echo
echo ">>> in the session, say:  connect me to globus1"
echo

# Stray hpc-bridge overrides from this shell must not leak in (an endpoint id would even be refused for a MEP).
CMD=(env -u HPC_BRIDGE_ENDPOINT_ID -u HPC_BRIDGE_ENDPOINT_NAME -u HPC_BRIDGE_MACHINE -u HPC_BRIDGE_ACCOUNT
     -u HPC_BRIDGE_SSH_USER -u HPC_BRIDGE_SSH_KEY -u HPC_BRIDGE_USER_DIR
     GLOBUS_COMPUTE_USER_DIR="$FRESH/globus_compute"
     HPC_BRIDGE_STATE_DIR="$FRESH/state"
     HPC_BRIDGE_SEARCH_INDEX="$INDEX"
     claude --plugin-dir "$REPO" "$@")

cd "$FRESH"
if [[ -n "${DRY_RUN:-}" ]]; then
  printf 'would run (from %s):\n  ' "$FRESH"; printf '%q ' "${CMD[@]}"; echo
  exit 0
fi
exec "${CMD[@]}"
