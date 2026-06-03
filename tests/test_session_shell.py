import pytest

from hpc_bridge.session_shell import Session, reset_command, wrap


def test_session_state_dir():
    s = Session("abc", "/scratch/.hpc-bridge")
    assert s.state_dir == "/scratch/.hpc-bridge/sessions/abc"


def test_session_rejects_path_traversal_and_metachars():
    # session_id is an untrusted MCP parameter; must not allow escaping the sessions root.
    for bad in ("../../etc", "a/b", "..", "", "a b", "x;rm -rf /", "a" * 65):
        with pytest.raises(ValueError):
            Session(bad, "/scratch")


def test_session_accepts_safe_ids():
    assert Session("default", "/r").session_id == "default"
    assert Session("my-sess_1", "/r").state_dir == "/r/sessions/my-sess_1"


def test_wrap_carries_command_inertly_no_raw_brace_group():
    # A top-level '}' must not break out of the wrapper; command is carried as base64.
    w = wrap("echo a; }", Session("s", "/r"))
    assert "{ echo a; }" not in w  # no raw brace group the command could close
    assert "base64 -d" in w  # command decoded+eval'd in the current shell
    assert "echo a; }" not in w  # command body not present literally


def test_wrap_hardens_permissions_and_persists_state():
    s = Session("abc", "/scratch/.hpc-bridge")
    sd = "/scratch/.hpc-bridge/sessions/abc"
    w = wrap("echo hi", s)
    assert "umask 077" in w  # .cwd/.env created 0600, dir 0700
    assert f"mkdir -p {sd}" in w
    assert f"{sd}/.cwd" in w and f"{sd}/.env" in w
    assert f"pwd > {sd}/.cwd" in w
    assert "export -p >" in w


def test_wrap_first_call_defaults_into_session_dir():
    # empty/missing .cwd must deterministically land in the session dir (not silently
    # keep the worker start dir via a no-op `cd ""`).
    w = wrap("pwd", Session("abc", "/scratch/.hpc-bridge"))
    assert "/scratch/.hpc-bridge/sessions/abc" in w
    assert '-n "$__hb_cwd"' in w  # guards the empty-string case


def test_wrap_preserves_exit_code():
    w = wrap("false", Session("s", "/r"))
    assert "__hb_rc=$?" in w
    assert "exit $__hb_rc" in w


def test_reset_command_removes_state_files():
    r = reset_command(Session("abc", "/scratch/.hpc-bridge"))
    assert r.startswith("rm -f")
    assert "/scratch/.hpc-bridge/sessions/abc/.cwd" in r
    assert "/scratch/.hpc-bridge/sessions/abc/.env" in r
