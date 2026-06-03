import json
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_plugin_manifest_valid():
    m = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert m["name"] == "hpc-bridge"
    assert m["description"]


def test_mcp_config_launches_server():
    c = json.loads((ROOT / ".mcp.json").read_text())
    srv = c["mcpServers"]["hpc-bridge"]
    assert "hpc-bridge" in srv["args"] or srv["command"] == "hpc-bridge"


def test_mcp_config_installs_runtime_deps():
    # The plugin probes and dispatches via globus-compute-sdk at runtime, so the launch
    # MUST request the `integration` extra — base deps alone can neither probe nor
    # dispatch (ModuleNotFoundError: globus_compute_sdk). Accept either launch form
    # (`uv run --extra integration` or a `uvx --from <path>[integration]`).
    c = json.loads((ROOT / ".mcp.json").read_text())
    args = c["mcpServers"]["hpc-bridge"]["args"]
    assert "integration" in " ".join(args), f"launch must request the integration extra; got {args!r}"


def test_hooks_config_valid_and_guard_executable():
    h = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    assert h["hooks"]["PreToolUse"]
    mode = (ROOT / "hooks" / "credential-guard.sh").stat().st_mode
    assert mode & stat.S_IXUSR


def test_skill_has_frontmatter():
    text = (ROOT / "skills" / "driving-hpc" / "SKILL.md").read_text()
    assert text.startswith("---")
    assert "description:" in text
