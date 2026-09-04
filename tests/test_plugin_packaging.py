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
    srv = c["mcpServers"]["endpoint"]
    assert "hpc-bridge" in srv["args"] or srv["command"] == "hpc-bridge"


def test_mcp_config_installs_runtime_deps():
    # The plugin probes and dispatches via globus-compute-sdk at runtime, so the launch
    # MUST request the `integration` extra — base deps alone can neither probe nor
    # dispatch (ModuleNotFoundError: globus_compute_sdk). Accept either launch form
    # (`uv run --extra integration` or a `uvx --from <path>[integration]`).
    c = json.loads((ROOT / ".mcp.json").read_text())
    args = c["mcpServers"]["endpoint"]["args"]
    assert "integration" in " ".join(args), f"launch must request the integration extra; got {args!r}"


def test_hooks_config_valid_and_guard_executable():
    h = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    assert h["hooks"]["PreToolUse"]
    mode = (ROOT / "hooks" / "credential-guard.sh").stat().st_mode
    assert mode & stat.S_IXUSR


def test_credential_guard_matcher_covers_the_actual_tool_names():
    # The guard is keyed by a tool-name REGEX; a server rename silently orphans it (caught
    # in review after the hpc-bridge -> endpoint rename). Pin matcher <-> .mcp.json key.
    import re

    h = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    matcher = h["hooks"]["PreToolUse"][0]["matcher"]
    (key,) = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"].keys()
    for tool in ("run_shell", "login_shell"):
        assert re.search(matcher, f"mcp__{key}__{tool}"), (matcher, key, tool)
        # the plugin-namespaced form Claude Code uses when loaded via --plugin-dir
        assert re.search(matcher, f"mcp__plugin_hpc-bridge_{key}__{tool}"), (matcher, key, tool)


def test_skill_has_frontmatter():
    text = (ROOT / "skills" / "driving-hpc" / "SKILL.md").read_text()
    assert text.startswith("---")
    assert "name: driving-hpc" in text  # skill analyzers expect an explicit name
    assert "description:" in text


def test_mcp_launcher_finds_uv_off_path(tmp_path):
    # The desktop app / IDE extensions start the server with a minimal PATH that lacks uv's install dir
    # (readiness pass 2026-09-04): .mcp.json calls bin/run-with-uv, which locates uv and execs it.
    import os
    import subprocess

    c = json.loads((ROOT / ".mcp.json").read_text())
    assert c["mcpServers"]["endpoint"]["command"] == "${CLAUDE_PLUGIN_ROOT}/bin/run-with-uv"
    launcher = ROOT / "bin" / "run-with-uv"
    assert launcher.stat().st_mode & stat.S_IXUSR
    # a fake HOME with ~/.local/bin/uv, and a PATH with no uv at all
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    stub = home / ".local" / "bin" / "uv"
    stub.write_text("#!/bin/sh\necho \"stub-uv $*\"\n")
    stub.chmod(0o755)
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    r = subprocess.run([str(launcher), "run", "hpc-bridge"], capture_output=True, text=True, env=env, check=False)
    assert r.returncode == 0 and r.stdout.strip() == "stub-uv run hpc-bridge", (r.stdout, r.stderr)
    # nothing anywhere: a clear message, exit 127
    r = subprocess.run([str(launcher), "run"], capture_output=True, text=True,
                       env={"HOME": str(tmp_path / "empty"), "PATH": "/usr/bin:/bin"}, check=False)
    assert r.returncode == 127 and "'uv' was not found" in r.stderr
    assert os.environ  # (keeps the import used on platforms where the assertions above short-circuit)

