#!/usr/bin/env bash
# clean-session.sh — a pristine Claude Code session for testing hpc-bridge as a BRAND-NEW USER.
#
# It launches Claude with the hpc-bridge plugin and NOTHING ELSE — no ~/.claude priors, an isolated
# facility-cache sandbox, no forced overrides. It does NOT connect to any facility: the AGENT connects
# when you tell it to in the chat, and on an MFA facility it returns a `needs_preauth` command for YOU
# to run once (the authentic new-user flow). Two isolations:
#   • CLAUDE_CONFIG_DIR  → throwaway : no auto-memory / your CLAUDE.md / ~/.claude/rules / history /
#                                      other plugins. Only the hpc-bridge plugin + its skill load.
#   • HPC_BRIDGE_STATE_DIR → sandbox : a DEDICATED hpc-bridge state dir (default $HOME/.hpc-bridge-newuser)
#                                      so a stale facility CACHE (facilities.json/endpoints.json) can't
#                                      leak in and mis-resolve. Its `cm/` is symlinked to your real
#                                      ~/.hpc-bridge/cm, so any SSH ControlMaster you open (via the
#                                      agent's needs_preauth command, or a normal `ssh <host>`) is shared.
#
#   ./agentic/clean-session.sh                     # launch a clean session
#   HPCB_CLEAN_FRESH=1 ./agentic/clean-session.sh  # also wipe the sandbox facility cache -> first-connect
#
# Auth: your Claude SUBSCRIPTION token (CLAUDE_CODE_OAUTH_TOKEN — env or agentic/.env).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="$(git -C "$REPO" branch --show-current 2>/dev/null || echo '?')"

# --- subscription token: caller's env wins, else agentic/.env ---
TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -f "$REPO/agentic/.env" ]; then
  TOKEN="$(grep '^CLAUDE_CODE_OAUTH_TOKEN=' "$REPO/agentic/.env" | head -1 | cut -d= -f2- || true)"
fi
[ -n "$TOKEN" ] || { echo "ERROR: no CLAUDE_CODE_OAUTH_TOKEN (env or agentic/.env). Mint one with: claude setup-token" >&2; exit 1; }

# --- isolated hpc-bridge state sandbox: the facility CACHE is dedicated (nothing stale leaks in), but
#     its cm/ is symlinked to your real ~/.hpc-bridge/cm so any ControlMaster is SHARED with normal ssh
#     (an SSH master is auth transport, not a hpc-bridge prior). The wrapper opens NO connection itself. ---
STATE="${HPCB_CLEAN_STATE:-$HOME/.hpc-bridge-newuser}"
CM_DIR="$HOME/.hpc-bridge/cm"   # where ~/.ssh/config's ControlPath + a normal `ssh <host>` open the master
mkdir -p "$STATE" "$CM_DIR"; chmod 700 "$STATE" "$CM_DIR"
# The server's control dir is _state_dir()/cm = $STATE/cm; symlink it at the shared master dir so a
# master you (or the agent's needs_preauth) open is reused, and the sandbox isolates only the cache.
if [ ! -L "$STATE/cm" ]; then rm -rf "$STATE/cm" 2>/dev/null || true; fi
ln -sfn "$CM_DIR" "$STATE/cm"
if [ -n "${HPCB_CLEAN_FRESH:-}" ]; then
  rm -f "$STATE/facilities.json" "$STATE/endpoints.json"
  echo "↻ wiped the sandbox facility cache — this run starts as a first-time user (discovery)."
fi

# --- throwaway Claude config home: nothing from ~/.claude loads; removed on exit ---
CFG="$(mktemp -d)"; trap 'rm -rf "$CFG"' EXIT

cat <<INFO
────────────────────────────────────────────────────────────────
 NEW-USER hpc-bridge session   (repo branch: $BRANCH)
   plugin           : $REPO
   claude config    : $CFG   (throwaway — no ~/.claude priors)
   hpc-bridge state : $STATE   (sandbox — no leaked facility cache)
   nothing forced   : no HPC_BRIDGE_SSH_HOST; the agent picks the facility, discovery/caching drive it
   no connection    : the wrapper does NO ssh — the agent connects only when you ask it to

 Sanity-check the clean slate first:
   "Do you have any memories or CLAUDE.md instructions? List your tools and skills."
   → want: none, plus mcp__endpoint__* tools + the driving-hpc skill.

 Then drive it as a new user — name the facility, e.g.:
   "Connect to the HPC facility at ssh host 'aurora' and bring up a login node."
   → key facility (globus1, Anvil): connects straight away. MFA facility (Aurora): the agent returns a
     needs_preauth 'ssh -fN …' command for YOU to run once in your own terminal, then it multiplexes.
────────────────────────────────────────────────────────────────
INFO

# neutral cwd (no project CLAUDE.md / .claude settings); env -i so no stray ANTHROPIC_API_KEY outranks
# the token. HOME stays so ~/.ssh/config, the shared master, and ~/.globus_compute (your login) resolve.
cd /tmp
env -i \
  HOME="$HOME" PATH="$PATH" TERM="${TERM:-xterm-256color}" \
  CLAUDE_CONFIG_DIR="$CFG" \
  CLAUDE_CODE_OAUTH_TOKEN="$TOKEN" \
  ANTHROPIC_API_KEY= \
  CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 \
  HPC_BRIDGE_STATE_DIR="$STATE" \
  claude --plugin-dir "$REPO" || true

echo "session ended — throwaway Claude config removed (sandbox state kept at $STATE)."
