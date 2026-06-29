# Plugin packaging

> [!abstract] Role
> How hpc-bridge installs into Claude Code: an MCP stdio server plus the agent-facing skill and command.

## The surfaces

- **`.mcp.json`** — declares the `hpc-bridge` **stdio** server, launched as `uv run --directory ${CLAUDE_PLUGIN_ROOT} --extra integration hpc-bridge`, with `HPC_BRIDGE_USER_DIR=${CLAUDE_PLUGIN_DATA}/globus_compute`. The `hpc-bridge` console script → `main()` ([[server]] `:1075`).
- **`.claude-plugin/plugin.json`** — plugin manifest (name, description, version).
- **`skills/driving-hpc/SKILL.md`** — the **agent recipe**: how to drive HPC well — establish the endpoint, discover via the login shape, present the partition + budget gate, provision with `confirm_spend`, wait by polling `squeue` through the login shape. This is where agent *judgment* lives ([[Discovery today]]).
- **`commands/hpc-connect.md`** — a slash command entry point.

## Install / run

```bash
uv sync --extra dev
uv run pytest -q                 # the test suite
uv run hpc-bridge                # run the MCP server standalone (stdio)
claude --plugin-dir .            # install into Claude Code for local testing
```

> [!note] The `integration` extra
> Core deps are just `mcp` / `pydantic` / `pyyaml`. `globus-compute-sdk` (and, Linux-only, `globus-compute-endpoint`) live in the optional `integration` extra — unit tests are hermetic and don't need it. `globus_sdk` comes transitively (used for the Search index query and `get_endpoints`).

## See also
[[server]] · [[Configuration]] · [[Discovery today]]
