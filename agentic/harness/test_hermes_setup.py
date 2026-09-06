"""Hermetic tests for hermes_setup.build_config — the hermes operator's config generator (no hermes, no network)."""
from __future__ import annotations

import hermes_setup


def test_build_config_defaults(monkeypatch):
    monkeypatch.setenv("HPCB_ALCF_BASE_URL", "https://alcf.example/v1")
    monkeypatch.setenv("HPCB_ALCF_MODEL", "openai/gpt-oss-120b")
    monkeypatch.delenv("HPCB_HERMES_EAGER_TOOLS", raising=False)
    cfg = hermes_setup.build_config()
    assert cfg["model"]["provider"] == "custom"
    assert cfg["model"]["default"] == "openai/gpt-oss-120b"
    assert cfg["model"]["api_key"] == "${ALCF_INFERENCE_TOKEN}"      # never the literal token
    assert "hpc-bridge" in cfg["mcp_servers"]
    assert "tools" not in cfg and "platform_toolsets" not in cfg     # default = deferral on (no eager override)


def test_build_config_eager_tools(monkeypatch):
    monkeypatch.setenv("HPCB_ALCF_BASE_URL", "https://alcf.example/v1")
    monkeypatch.setenv("HPCB_HERMES_EAGER_TOOLS", "1")
    cfg = hermes_setup.build_config()
    assert cfg["tools"]["tool_search"]["enabled"] == "off"           # MCP tools exposed directly (no tool_search)
    assert cfg["platform_toolsets"]["cli"] == ["clarify", "file", "terminal", "todo"]   # trimmed built-in surface


def test_build_config_extra_env_folds_into_server(monkeypatch):
    monkeypatch.setenv("HPCB_ALCF_BASE_URL", "https://alcf.example/v1")
    cfg = hermes_setup.build_config(extra_env={"HPC_BRIDGE_OMIT_INSTRUCTIONS": "1"})
    assert cfg["mcp_servers"]["hpc-bridge"]["env"]["HPC_BRIDGE_OMIT_INSTRUCTIONS"] == "1"


def test_build_config_requires_base_url(monkeypatch):
    monkeypatch.delenv("HPCB_ALCF_BASE_URL", raising=False)
    try:
        hermes_setup.build_config()
    except SystemExit:
        return
    raise AssertionError("expected SystemExit when HPCB_ALCF_BASE_URL is unset")


def test_build_config_no_clarify_routes_asks_to_prose(monkeypatch):
    monkeypatch.setenv("HPCB_ALCF_BASE_URL", "https://alcf.example/v1")
    monkeypatch.setenv("HPCB_HERMES_NO_CLARIFY", "1")
    monkeypatch.delenv("HPCB_HERMES_EAGER_TOOLS", raising=False)
    cfg = hermes_setup.build_config()
    # clarify dropped -> the operator must ask in prose (which the interactive loop routes to the human-sim)
    assert cfg["platform_toolsets"]["cli"] == ["file", "terminal", "todo"]
    assert "clarify" not in cfg["platform_toolsets"]["cli"]
    assert "tools" not in cfg   # deferral is unchanged; only the ask-tool is removed
