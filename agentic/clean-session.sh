#!/usr/bin/env bash
# clean-session.sh — a pristine Claude Code session for testing hpc-bridge as a BRAND-NEW USER.
#
# Two isolations, and NO behaviour overrides — the agent picks the facility and caching + discovery
# drive everything; nothing is forced (this is the fix for the "globus1 is Aurora" trap, where a
# hardcoded HPC_BRIDGE_SSH_HOST silently redirected the SSH while a leaked cache supplied the config):
#   • CLAUDE_CONFIG_DIR  → throwaway : no auto-memory / your CLAUDE.md / ~/.claude/rules / history /
#                                      other plugins. Only the hpc-bridge plugin + its skill load.
#   • HPC_BRIDGE_STATE_DIR → sandbox : a DEDICATED hpc-bridge state dir (default $HOME/.hpc-bridge-newuser),
#                                      separate from your REAL ~/.hpc-bridge, so a stale facility cache
#                                      can't leak in and mis-resolve. (Isolation, not a behaviour override.)
#
#   ./agentic/clean-session.sh [ssh_host]              # ssh_host = whose ControlMaster to pre-open (default aurora)
#   HPCB_CLEAN_FRESH=1 ./agentic/clean-session.sh      # wipe the sandbox facility cache -> first-connect (discovery)
#
# Caching works normally INSIDE the sandbox: the first connect probes + caches a facility; later runs
# reuse it (passcode-free — the master lives in the sandbox and persists ~1h). HPCB_CLEAN_FRESH=1 (or
# deleting the sandbox) resets to a genuine first-time user. Auth: your Claude SUBSCRIPTION token.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_HOST="${1:-aurora}"
BRANCH="$(git -C "$REPO" branch --show-current 2>/dev/null || echo '?')"

# --- subscription token: caller's env wins, else agentic/.env ---
TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -f "$REPO/agentic/.env" ]; then
  TOKEN="$(grep '^CLAUDE_CODE_OAUTH_TOKEN=' "$REPO/agentic/.env" | head -1 | cut -d= -f2- || true)"
fi
[ -n "$TOKEN" ] || { echo "ERROR: no CLAUDE_CODE_OAUTH_TOKEN (env or agentic/.env). Mint one with: claude setup-token" >&2; exit 1; }

# --- isolated hpc-bridge state sandbox (facility cache + endpoints + ControlMaster) — NOT your real ~/.hpc-bridge ---
STATE="${HPCB_CLEAN_STATE:-$HOME/.hpc-bridge-newuser}"
mkdir -p "$STATE/cm"; chmod 700 "$STATE" "$STATE/cm"
if [ -n "${HPCB_CLEAN_FRESH:-}" ]; then
  rm -f "$STATE/facilities.json" "$STATE/endpoints.json"
  echo "↻ wiped the sandbox facility cache — this run starts as a first-time user (discovery)."
fi

# --- ControlMaster INSIDE the sandbox, so it matches the server's control_dir (= \$STATE/cm) ---
CM="$STATE/cm/%C"
if ssh -o ControlPath="$CM" -O check "$SSH_HOST" >/dev/null 2>&1; then
  echo "✓ ControlMaster for '$SSH_HOST' alive (sandbox) — reusing, no re-auth."
else
  echo "↪ opening ControlMaster for '$SSH_HOST' — enter passcode(s) now (stays ~1h)…"
  ssh -o ControlPath="$CM" -o ControlMaster=yes -o ControlPersist=1h -fN "$SSH_HOST"
fi

# --- throwaway Claude config home: nothing from ~/.claude loads; removed on exit ---
CFG="$(mktemp -d)"; trap 'rm -rf "$CFG"' EXIT

cat <<INFO
────────────────────────────────────────────────────────────────
 NEW-USER hpc-bridge session   (repo branch: $BRANCH)
   plugin            : $REPO
   claude config     : $CFG   (throwaway — no ~/.claude priors)
   hpc-bridge state  : $STATE   (sandbox — no leaked facility cache)
   nothing forced    : no HPC_BRIDGE_SSH_HOST — the agent picks the facility; caching/discovery drive it
   pre-opened master : $SSH_HOST   (name THIS facility in chat; another one just triggers a pre-auth prompt)

 Sanity-check the clean slate first:
   "Do you have any memories or CLAUDE.md instructions? List your tools and skills."
   → want: none, plus mcp__endpoint__* tools + the driving-hpc skill.

 Then drive it as a new user — name the facility you pre-opened, e.g.:
   "Connect to the HPC facility at ssh host '$SSH_HOST' and bring up a login node."
   → first time: it probes + proposes a config to confirm; later runs reuse the sandbox cache.
────────────────────────────────────────────────────────────────
INFO

# neutral cwd (no project CLAUDE.md / .claude settings); env -i so no stray ANTHROPIC_API_KEY outranks
# the token. HOME stays so ~/.ssh/config, the sandbox master, and ~/.globus_compute (your login) resolve.
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
