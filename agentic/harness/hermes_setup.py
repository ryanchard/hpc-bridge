"""Write hermes-agent's config.yaml for the jail's hermes operator: the ALCF (OpenAI-compatible) model
provider plus hpc-bridge as an MCP server. hermes filters the environment for stdio MCP servers to a
static ``env:`` allowlist in the config, but the jail injects the scoped hpc-bridge credentials at RUN
time — so we generate the config here (at container start, or from ``hermes_runner``), folding the
current env into the mcp_servers env block.

The ALCF bearer is NOT written to the file: ``api_key: ${ALCF_INFERENCE_TOKEN}`` is interpolated by
hermes from the env (minted on the host, passed with ``-e``). We deliberately do NOT set
``HPC_BRIDGE_OMIT_INSTRUCTIONS`` for hpc-bridge here — hermes has no skill system, so it SHOULD get the
guidance pointer + resource (unlike Claude Code, which opts out). The config lands at
``$HERMES_HOME/config.yaml`` (``~/.hermes/config.yaml`` when HERMES_HOME is unset), matching hermes'
own home resolution, so the runner can point HERMES_HOME at a per-run dir for an isolated state.db.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

# Credentials / config the hpc-bridge MCP server needs, passed through hermes' allowlist (only those actually set).
# NB: HPC_BRIDGE_OMIT_INSTRUCTIONS is intentionally excluded from the default — hermes wants the guidance; the
# skill-ablation path (hermes_runner) adds it via extra_env when measuring the guidance's value.
_PASSTHROUGH = (
    "HOME", "HPC_BRIDGE_USER_DIR", "GLOBUS_COMPUTE_USER_DIR", "HPC_BRIDGE_SSH_USER", "HPC_BRIDGE_SSH_KEY",
    "HPC_BRIDGE_SSH_HOST", "HPC_BRIDGE_ENDPOINT_NAME", "HPC_BRIDGE_MACHINE", "HPC_BRIDGE_SEARCH_INDEX",
    "HPC_BRIDGE_CATALOG_FILE", "HPCB_HARNESS_SSH_PORT",
)


def hermes_home() -> Path:
    """hermes' home dir, matching its own resolution (_startup_fast): $HERMES_HOME, else ~/.hermes.
    config.yaml + state.db live directly under it."""
    return Path(os.environ.get("HERMES_HOME", "").strip() or (Path.home() / ".hermes"))


def build_config(extra_env: dict[str, str] | None = None) -> dict:
    env = {k: os.environ[k] for k in _PASSTHROUGH if k in os.environ}
    if extra_env:
        env.update({k: v for k, v in extra_env.items() if v is not None})
    base_url = os.environ.get("HPCB_ALCF_BASE_URL") or ""
    if not base_url:
        raise SystemExit("hermes_setup: HPCB_ALCF_BASE_URL is unset (the ALCF OpenAI-compatible base URL)")
    return {
        "model": {
            "provider": "custom",
            "base_url": base_url,
            "api_key": "${ALCF_INFERENCE_TOKEN}",       # interpolated by hermes from the env; never written here
            "default": os.environ.get("HPCB_ALCF_MODEL", "openai/gpt-oss-120b"),
            "streaming": False,                          # ALCF gateway SSE isn't clean → 'empty stream' without this
            "context_length": 131072,                    # ALCF 404s /v1/models → auto-detect fails; set explicitly
            "max_tokens": 4096,
        },
        "mcp_servers": {
            "hpc-bridge": {
                "command": "/usr/local/bin/uv",          # absolute: hermes' env filter must not hide uv on PATH
                "args": ["run", "--directory", "/work/hpc-bridge", "--extra", "integration", "hpc-bridge"],
                "env": env,
                "connect_timeout": 180.0,
            }
        },
    }


def write_config(extra_env: dict[str, str] | None = None) -> Path:
    """Build + write config.yaml under hermes' home; return the path."""
    cfg = build_config(extra_env)
    out = hermes_home() / "config.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return out


def main() -> int:
    out = write_config()
    cfg = build_config()
    print(f"hermes config written: {out} (model {cfg['model']['default']}, hpc-bridge env keys "
          f"{sorted(cfg['mcp_servers']['hpc-bridge']['env'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
