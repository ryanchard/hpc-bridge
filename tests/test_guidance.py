"""The cross-harness guidance mechanism (0.1.15): the env-gated MCP `instructions` pointer and the SKILL.md resource.

Claude Code loads the driving-hpc skill itself and opts out via HPC_BRIDGE_OMIT_INSTRUCTIONS; every other MCP host
gets a small pointer telling it to read `hpcbridge://guidance/operations`, which serves SKILL.md verbatim on demand.
"""
from __future__ import annotations

from hpc_bridge import config, server


def test_omit_instructions_env_parsing(monkeypatch):
    monkeypatch.delenv("HPC_BRIDGE_OMIT_INSTRUCTIONS", raising=False)
    assert config.omit_instructions() is False
    for on in ("1", "true", "TRUE", "yes", "on", "anything"):
        monkeypatch.setenv("HPC_BRIDGE_OMIT_INSTRUCTIONS", on)
        assert config.omit_instructions() is True, on
    for off in ("0", "false", "False", "no", "off", ""):
        monkeypatch.setenv("HPC_BRIDGE_OMIT_INSTRUCTIONS", off)
        assert config.omit_instructions() is False, off


def test_server_instructions_gated_by_env(monkeypatch):
    monkeypatch.delenv("HPC_BRIDGE_OMIT_INSTRUCTIONS", raising=False)
    ptr = server._server_instructions()
    assert ptr and server._GUIDANCE_URI in ptr          # non-CC hosts get a pointer that names the resource
    assert len(ptr) < 1200                               # it's a POINTER, not the whole skill (kept small/always-on)
    monkeypatch.setenv("HPC_BRIDGE_OMIT_INSTRUCTIONS", "1")
    assert server._server_instructions() is None         # Claude Code opts out — no pointer, no duplication


def test_guidance_resource_serves_skill_verbatim():
    txt = server._guidance_text()
    assert "Driving HPC with hpc-bridge" in txt           # the real SKILL.md content
    assert txt == server._operations_guidance()           # the resource function returns it
    p = server._skill_path()
    assert p is not None and txt == p.read_text()          # verbatim: byte-for-byte the resolved file, so zero drift


def test_skill_resolves_from_source_tree_today():
    # running from the repo, the source-tree candidate exists (the wheel copy won't in a dev checkout)
    p = server._skill_path()
    assert p is not None and p.is_file() and p.name == "SKILL.md"


def test_guidance_text_falls_back_when_skill_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_SKILL_CANDIDATES", (tmp_path / "does-not-exist.md",))
    assert server._skill_path() is None
    txt = server._guidance_text()
    assert "unavailable" in txt.lower()                   # graceful, never crashes the server
