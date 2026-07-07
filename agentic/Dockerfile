# Per-test runtime jail for the hpc-bridge agentic harness.
# The headless agent + hpc-bridge run here; SCOPED creds (test SSH key, test Globus
# identity, API key) are injected at RUN time (see run_smoke.sh) — NEVER baked into the
# image, and the admin key is never present. Design: Planned/Agentic testing - Plan B.
FROM python:3.11-slim

# System deps: ssh client (hpc-bridge's control plane), git/curl/ca-certs, and node
# (the Agent SDK spawns the Claude Code CLI as a subprocess).
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client git curl ca-certificates gnupg \
        build-essential python3-dev \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# uv (hpc-bridge launches its MCP server via `uv run`)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# The Claude Code CLI (spawned by the Agent SDK) + the Python SDK
RUN npm install -g @anthropic-ai/claude-code \
    && pip install --no-cache-dir claude-agent-sdk

# The hpc-bridge repo (plugin + MCP server + skill + harness). Build context is the
# repo root (run_smoke.sh builds from there); .dockerignore trims it.
WORKDIR /work/hpc-bridge
# Deps layer — copy ONLY what `uv sync` needs (pyproject + lock + the package src, per
# hatchling packages=["src/hpc_bridge"]), so editing harness/scenario code below doesn't
# bust this cache and recompile psutil every iteration.
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --extra integration
# The rest of the repo (harness, scenarios, skill, .mcp.json, entrypoint).
COPY . /work/hpc-bridge

# Non-root user — Claude Code refuses --dangerously-skip-permissions (bypassPermissions)
# under root/sudo. Own the repo + venv so `uv run` can use it as this user.
RUN useradd --create-home --uid 1000 agent \
    && chmod +x /work/hpc-bridge/agentic/entrypoint.sh \
    && chown -R agent:agent /work/hpc-bridge
USER agent
# Harness modules are flat (invariants / trace_adapter / runner import each other).
ENV HOME=/home/agent \
    PYTHONPATH=/work/hpc-bridge/agentic/harness
ENTRYPOINT ["/work/hpc-bridge/agentic/entrypoint.sh"]
