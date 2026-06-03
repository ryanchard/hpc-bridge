from hpc_bridge.session_shell import Session, reset_command, wrap


def test_session_state_dir():
    s = Session("abc", "/scratch/.hpc-bridge")
    assert s.state_dir == "/scratch/.hpc-bridge/sessions/abc"


def test_wrap_rehydrates_and_persists_cwd_and_env():
    s = Session("abc", "/scratch/.hpc-bridge")
    sd = "/scratch/.hpc-bridge/sessions/abc"
    w = wrap("echo hi", s)
    # creates the session dir, rehydrates cwd+env, runs the command, persists cwd+env
    assert f"mkdir -p {sd}" in w
    assert f"{sd}/.cwd" in w  # rehydrate cwd
    assert f"{sd}/.env" in w  # rehydrate env
    assert "echo hi" in w
    assert f"pwd > {sd}/.cwd" in w  # persist cwd
    assert "export -p >" in w  # persist env


def test_wrap_preserves_exit_code():
    w = wrap("false", Session("s", "/r"))
    assert "__hb_rc=$?" in w
    assert "exit $__hb_rc" in w


def test_reset_command_removes_state_files():
    r = reset_command(Session("abc", "/scratch/.hpc-bridge"))
    assert r.startswith("rm -f")
    assert "/scratch/.hpc-bridge/sessions/abc/.cwd" in r
    assert "/scratch/.hpc-bridge/sessions/abc/.env" in r
