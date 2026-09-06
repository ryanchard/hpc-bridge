# Per-test runtime jail for the hpc-bridge agentic harness.
# The headless agent + hpc-bridge run here; SCOPED creds (test SSH key, test Globus
# identity, API key) are injected at RUN time (see run_smoke.sh) — NEVER baked into the
# image, and the admin key is never present. Design: Planned/Agentic testing - Plan B.
FROM python:3.11-slim

# System deps: ssh client (hpc-bridge's control plane), git/curl/ca-certs. No Node: the Python Agent
# SDK BUNDLES the Claude Code CLI it spawns (the npm CLI on Node 20 — EOL 2026-04 — was dead weight;
# dependency audit 2026-09-03).
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client git curl ca-certificates \
        build-essential python3-dev \
    && rm -rf /var/lib/apt/lists/*

# uv (hpc-bridge launches its MCP server via `uv run`) — pinned: a floating `latest` made builds drift by the day
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv

# The Python Agent SDK (bundles the Claude Code CLI it drives) — pinned; it releases near-daily
RUN pip install --no-cache-dir claude-agent-sdk==0.2.152

# NousResearch hermes-agent — a SECOND operator harness, so the same scenarios can be driven by a non-Claude model
# (via ALCF inference) as well as by Claude Code. Only used when HPCB_OPERATOR=hermes; a no-op for the default Claude path.
# PINNED to the exact upstream commit the maintainer runs locally (v0.21.0). NOT the PyPI build: PyPI's latest is 0.19.0,
# whose MCP client reads the removed camelCase `CallToolResult.isError` and crashes on EVERY tool result with
# "AttributeError: 'CallToolResult' object has no attribute 'isError'" — surfacing to the model as "server unreachable".
# hermes blocks non-editable wheel builds by design, so clone + editable-install (its MCP types are vendored; core deps only).
RUN git clone -q https://github.com/NousResearch/hermes-agent.git /opt/hermes-agent \
    && git -C /opt/hermes-agent checkout -q 245e48008fa814b3251f50755eb656bd9fb86cb1 \
    && pip install --no-cache-dir -e /opt/hermes-agent

# The hpc-bridge repo (plugin + MCP server + skill + harness). Build context is the
# repo root (run_smoke.sh builds from there); .dockerignore trims it.
WORKDIR /work/hpc-bridge
# Deps layer — copy ONLY what `uv sync` needs (pyproject + lock + the package src, per
# hatchling packages=["src/hpc_bridge"]), so editing harness/scenario code below doesn't
# bust this cache and recompile psutil every iteration.
COPY pyproject.toml uv.lock ./
COPY src ./src
# The wheel force-includes skills/driving-hpc/SKILL.md (the guidance resource), so it must be present when `uv sync`
# builds hpc-bridge — copy it in the deps layer (it changes rarely, so it barely affects caching).
COPY skills/driving-hpc/SKILL.md ./skills/driving-hpc/SKILL.md
RUN uv sync --extra integration
# The rest of the repo (harness, scenarios, skill, .mcp.json, entrypoint).
COPY . /work/hpc-bridge
# What this image is built from (`git describe --always --dirty`), so a run's bundle pins the CODE it exercised —
# the launch-time HEAD on the host can move under a long suite (one 2026-09-03 image was recorded under two SHAs).
ARG GIT_DESCRIBE=unknown
RUN echo "$GIT_DESCRIBE" > /work/hpc-bridge/BUILD_INFO

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
