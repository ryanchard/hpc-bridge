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
# PRECEDENCE: the caller's environment WINS — .env only fills in unset vars. (Sourcing it
# blindly let a persisted HPCB_TEST_SSH_USER/HPCB_MODEL silently clobber run_suite.py's
# per-job values — same cluster user across parallel runs, mislabelled matrix cells.)
ENV_FILE="$REPO_ROOT/agentic/.env"
if [ -f "$ENV_FILE" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|\#*) continue ;; esac
    k="${line%%=*}"
    # per-cell knobs are run_suite's to set: a persisted HPCB_NO_SKILL/HPCB_EFFORT/... must never fill a cell
    case "$k" in HPCB_MODEL|HPCB_EFFORT|HPCB_PERSONA|HPCB_NO_SKILL|HPCB_RUNID) continue ;; esac
    if [ -z "${!k+x}" ]; then export "$line"; fi
  done < "$ENV_FILE"
fi

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

# The cluster this run targets (harness/targets.py): globus1 (default) or fake (agentic/fakecluster). One preset
# carries the jail-side ssh host, the docker network, the pool key default and the endpoint-name prefix together.
TARGET="${HPCB_TARGET:-globus1}"
eval "$(python3 "$REPO_ROOT/agentic/harness/targets.py" "$TARGET")" || { echo "ERROR: unknown HPCB_TARGET '$TARGET'"; exit 1; }
KEY="${HPCB_TEST_SSH_KEY:-$HPCB_T_KEY_DEFAULT}"
SSH_USER="${HPCB_TEST_SSH_USER:-hpcbridge-test}"
GLOBUS_DB="${HPCB_TEST_GLOBUS_DB:-}"
# Per-scenario HOST knobs (read from the scenario module): run with NO Globus store (a logged-out
# stranger: needs_login), or with an ALTERNATE store (a second identity — e.g. one the MEP does not map).
KNOBS="$(python3 "$REPO_ROOT/agentic/harness/scenario_knobs.py" "$SCENARIO")" || { echo "ERROR: scenario knobs could not be read for '$SCENARIO' (unknown scenario?) — refusing to guess what to mount"; exit 1; }
eval "$KNOBS"
if [ -n "${HPCB_KNOB_GLOBUS_DB_SECRET:-}" ]; then
  GLOBUS_DB="${!HPCB_KNOB_GLOBUS_DB_SECRET:-}"
  [ -n "$GLOBUS_DB" ] || { echo "ERROR: scenario '$SCENARIO' needs \$$HPCB_KNOB_GLOBUS_DB_SECRET (path to a storage.db) — set it in agentic/.env"; exit 1; }
  echo "globus store: from \$$HPCB_KNOB_GLOBUS_DB_SECRET (an alternate identity)"
fi
if [ -n "${HPCB_KNOB_NO_GLOBUS_DB:-}" ]; then
  GLOBUS_DB=""
  echo "globus store: NONE — the scenario runs logged out (expects needs_login)"
fi
RUNID="${HPCB_RUNID:-$(date +%s)-$$}"   # run_suite mints it (so it can clean up an abandoned cell); solo runs mint their own
USER_DIR="/home/agent/run/$RUNID"   # agent-writable (the entrypoint mkdir's it + stages the db)
RUNS_HOST="$REPO_ROOT/agentic/runs" # per-run provenance bundles land here (gitignored)
mkdir -p "$RUNS_HOST"
GIT_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"          # the HOST's head at launch
GIT_DESCRIBE="$(git -C "$REPO_ROOT" describe --always --dirty --abbrev=12 2>/dev/null || echo unknown)"  # what the image is built from

[ -f "$KEY" ] || { echo "missing scoped test key: $KEY  (globus1: generate one + register its .pub; fake: agentic/fakecluster/bin/up.sh creates it)"; exit 1; }

if [ -z "${HPCB_SKIP_BUILD:-}" ]; then   # the suite runner builds once, then sets this
  echo "building jail image (hpc-bridge-agentic)…"
  docker build --provenance=false -t hpc-bridge-agentic --build-arg "GIT_DESCRIBE=$GIT_DESCRIBE" \
    -f "$REPO_ROOT/agentic/Dockerfile" "$REPO_ROOT" >/dev/null
fi
IMAGE_ID="$(docker image inspect -f '{{.Id}}' hpc-bridge-agentic 2>/dev/null || echo unknown)"   # pins the code the cell ran

ARGS=(
  --rm
  --stop-timeout 120                  # `docker stop`: give run.py's teardown (SIGTERM -> its finally) time to finish
  "${AUTH_ARGS[@]}"
  -e HPCB_RUNID="$RUNID"
  -e HPCB_IMAGE_ID="$IMAGE_ID"
  -e HPC_BRIDGE_SSH_USER="$SSH_USER"
  -e HPC_BRIDGE_SSH_KEY=/run/secrets/test_key
  -e HPC_BRIDGE_SSH_HOST="$HPCB_T_SSH_HOST"        # the login host as the JAIL reaches it (globus1 FQDN; `login` on the fake cluster's network)
  -e HPC_BRIDGE_ENDPOINT_NAME="$HPCB_T_EP_PREFIX-$RUNID"   # per-run isolation: runs share ONE Globus identity, so a unique NAME keeps their registrations distinct; the prefix names the TARGET
  -e HPCB_TARGET="$TARGET"
  -e HPCB_TARGET_NODES="$HPCB_T_NODES"
  -e HPC_BRIDGE_USER_DIR="$USER_DIR"
  -e GLOBUS_COMPUTE_USER_DIR="$USER_DIR"   # so the MCP process's Globus SDK finds the mounted db
  -e HPCB_RUNS_DIR=/work/hpc-bridge/agentic/runs
  -e HPCB_GIT_SHA="$GIT_SHA"
  -v "$RUNS_HOST":/work/hpc-bridge/agentic/runs   # provenance bundles survive the --rm container
  -v "$KEY":/run/secrets/test_key:ro
)
if [ -n "$HPCB_T_NETWORK" ]; then
  ARGS+=( --network "$HPCB_T_NETWORK" )   # the fake cluster's compose network: the jail reaches `login:22` directly
fi
if [ -n "$GLOBUS_DB" ]; then
  ARGS+=( -v "$GLOBUS_DB":/run/secrets/storage.db:ro )   # staged read-only; entrypoint copies to a writable owned path
elif [ -z "${HPCB_KNOB_NO_GLOBUS_DB:-}" ]; then
  echo "WARN: HPCB_TEST_GLOBUS_DB unset — endpoint registration/dispatch will fail without a Globus login."
fi
# OPTIONAL: the Globus Search catalog (list_facilities / catalogued connect). Forwarded only when the
# host sets it — nothing changes when unset (the suite stays on BYO discovery). The mounted storage.db
# must then already carry the Search scope (grant it ONCE on the host: `hpc-bridge-catalog <index> …`),
# or catalog calls fail with "Globus Search scope not granted" inside the jail.
if [ -n "${HPC_BRIDGE_SEARCH_INDEX:-}" ]; then
  echo "catalog: forwarding HPC_BRIDGE_SEARCH_INDEX=$HPC_BRIDGE_SEARCH_INDEX (storage.db needs the Search scope)"
  ARGS+=( -e HPC_BRIDGE_SEARCH_INDEX )
fi

RUN_ARGS=("$SCENARIO")
[ -n "${HPCB_MODEL:-}" ]   && RUN_ARGS+=(--model "$HPCB_MODEL")      # pin an Anthropic model version
[ -n "${HPCB_EFFORT:-}" ]  && RUN_ARGS+=(--effort "$HPCB_EFFORT")    # pin a reasoning level (low..max)
[ -n "${HPCB_PERSONA:-}" ] && RUN_ARGS+=(--persona "$HPCB_PERSONA")  # interactive: simulated-human persona
[ -n "${HPCB_NO_SKILL:-}" ] && RUN_ARGS+=(--no-skill)                # ablation: withhold SKILL.md
echo "running '$SCENARIO'${HPCB_MODEL:+ model=$HPCB_MODEL}${HPCB_EFFORT:+ effort=$HPCB_EFFORT}${HPCB_PERSONA:+ persona=$HPCB_PERSONA}${HPCB_NO_SKILL:+ ABLATED:skill}  (target $TARGET, user $SSH_USER, facility $TARGET-$RUNID)…"
docker run "${ARGS[@]}" hpc-bridge-agentic "${RUN_ARGS[@]}"
