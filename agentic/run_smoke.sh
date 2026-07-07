#!/usr/bin/env bash
# Build the jail image + run ONE agentic scenario against globus1 with SCOPED creds.
# The admin key (~/.ssh/globus) is NEVER passed into the container.
#
#   CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-... ./agentic/run_smoke.sh [scenario]
#
# Auth — ONE of (subscription preferred; far cheaper than API credits):
#   CLAUDE_CODE_OAUTH_TOKEN   from `claude setup-token` (needs Pro/Max) — PREFERRED
#   ANTHROPIC_API_KEY         API credits — fallback
# Scoped credentials (env):
#   HPCB_TEST_SSH_KEY   (default ~/.ssh/hpcbridge-test)   scoped test PRIVATE key
#   HPCB_TEST_SSH_USER  (default hpcbridge-test)          the non-admin cluster user
#   HPCB_TEST_GLOBUS_DB (optional)                        a Globus storage.db (test identity,
#                                                          or your own for a throwaway smoke)
set -euo pipefail

SCENARIO="${1:-happy_path}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Persisted secrets: agentic/.env (gitignored + dockerignored, chmod 600; plain KEY=value).
# Set CLAUDE_CODE_OAUTH_TOKEN / HPCB_TEST_GLOBUS_DB there ONCE instead of exporting per shell.
ENV_FILE="$REPO_ROOT/agentic/.env"
if [ -f "$ENV_FILE" ]; then set -a; . "$ENV_FILE"; set +a; fi

# Prefer the Claude subscription token; fall back to an API key. PRECEDENCE TRAP:
# ANTHROPIC_API_KEY silently wins over CLAUDE_CODE_OAUTH_TOKEN — so when using the
# subscription we pass an EMPTY ANTHROPIC_API_KEY into the container to block it.
if [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
  echo "auth: Claude subscription (CLAUDE_CODE_OAUTH_TOKEN)"
  AUTH_ARGS=( -e CLAUDE_CODE_OAUTH_TOKEN -e ANTHROPIC_API_KEY= )
elif [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  echo "auth: API key (ANTHROPIC_API_KEY) — billed as API credits"
  AUTH_ARGS=( -e ANTHROPIC_API_KEY )
else
  echo "ERROR: set CLAUDE_CODE_OAUTH_TOKEN ('claude setup-token', needs Pro/Max) or ANTHROPIC_API_KEY"; exit 1
fi

KEY="${HPCB_TEST_SSH_KEY:-$HOME/.ssh/hpcbridge-test}"
SSH_USER="${HPCB_TEST_SSH_USER:-hpcbridge-test}"
GLOBUS_DB="${HPCB_TEST_GLOBUS_DB:-}"
RUNID="$(date +%s)-$$"
USER_DIR="/home/agent/run/$RUNID"   # agent-writable (the entrypoint mkdir's it + stages the db)
RUNS_HOST="$REPO_ROOT/agentic/runs" # per-run provenance bundles land here (gitignored)
mkdir -p "$RUNS_HOST"
GIT_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"

[ -f "$KEY" ] || { echo "missing scoped test key: $KEY  (generate one + register its .pub on globus1)"; exit 1; }

if [ -z "${HPCB_SKIP_BUILD:-}" ]; then   # the suite runner builds once, then sets this
  echo "building jail image (hpc-bridge-agentic)…"
  docker build --provenance=false -t hpc-bridge-agentic -f "$REPO_ROOT/agentic/Dockerfile" "$REPO_ROOT" >/dev/null
fi

ARGS=(
  --rm
  "${AUTH_ARGS[@]}"
  -e HPCB_RUNID="$RUNID"
  -e HPC_BRIDGE_SSH_USER="$SSH_USER"
  -e HPC_BRIDGE_SSH_KEY=/run/secrets/test_key
  -e HPC_BRIDGE_SSH_HOST=globus1.cs.uchicago.edu   # FQDN — the container has no ~/.ssh/config alias
  -e HPC_BRIDGE_USER_DIR="$USER_DIR"
  -e GLOBUS_COMPUTE_USER_DIR="$USER_DIR"   # so the MCP process's Globus SDK finds the mounted db
  -e HPCB_RUNS_DIR=/work/hpc-bridge/agentic/runs
  -e HPCB_GIT_SHA="$GIT_SHA"
  -v "$RUNS_HOST":/work/hpc-bridge/agentic/runs   # provenance bundles survive the --rm container
  -v "$KEY":/run/secrets/test_key:ro
)
if [ -n "$GLOBUS_DB" ]; then
  ARGS+=( -v "$GLOBUS_DB":/run/secrets/storage.db:ro )   # staged read-only; entrypoint copies to a writable owned path
else
  echo "WARN: HPCB_TEST_GLOBUS_DB unset — endpoint registration/dispatch will fail without a Globus login."
fi

RUN_ARGS=("$SCENARIO")
[ -n "${HPCB_MODEL:-}" ]   && RUN_ARGS+=(--model "$HPCB_MODEL")      # pin an Anthropic model version
[ -n "${HPCB_EFFORT:-}" ]  && RUN_ARGS+=(--effort "$HPCB_EFFORT")    # pin a reasoning level (low..max)
[ -n "${HPCB_PERSONA:-}" ] && RUN_ARGS+=(--persona "$HPCB_PERSONA")  # interactive: simulated-human persona
[ -n "${HPCB_NO_SKILL:-}" ] && RUN_ARGS+=(--no-skill)                # ablation: withhold SKILL.md
echo "running '$SCENARIO'${HPCB_MODEL:+ model=$HPCB_MODEL}${HPCB_EFFORT:+ effort=$HPCB_EFFORT}${HPCB_PERSONA:+ persona=$HPCB_PERSONA}${HPCB_NO_SKILL:+ ABLATED:skill}  (user $SSH_USER, endpoint hpc-bridge-globus1-$RUNID)…"
docker run "${ARGS[@]}" hpc-bridge-agentic "${RUN_ARGS[@]}"
