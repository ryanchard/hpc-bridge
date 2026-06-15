import os
import shutil
import subprocess

import pytest

from hpc_bridge.session_shell import Session, reset_command, wrap

bash_only = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash to execute the wrapper")


def _run_session(steps, root):
    """Execute a sequence of (command, ambient_env_overrides) through the wrapper IN BASH
    (ShellFunction runs under /bin/bash). Returns [(stdout, persisted_.env_text), ...]."""
    sess = Session("default", root)
    sd = f"{root}/sessions/default"
    os.makedirs(sd, exist_ok=True)
    out = []
    for cmd, amb in steps:
        r = subprocess.run(
            ["bash", "-c", wrap(cmd, sess)], capture_output=True, text=True,
            env={**os.environ, **amb},
        )
        env_text = open(f"{sd}/.env").read() if os.path.exists(f"{sd}/.env") else ""
        out.append((r.stdout, env_text))
    return out


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
    assert f"> {sd}/.env 2>/dev/null" in w  # env persisted


def test_wrap_first_call_defaults_into_session_dir():
    # empty/missing .cwd must deterministically land in the session dir (not silently
    # keep the worker start dir via a no-op `cd ""`).
    w = wrap("pwd", Session("abc", "/scratch/.hpc-bridge"))
    assert "/scratch/.hpc-bridge/sessions/abc" in w
    assert '-n "$__hb_cwd"' in w  # guards the empty-string case


def test_wrap_persists_only_command_changed_env_not_runtime_vars():
    # Scheduler-injected runtime vars (SLURM_JOB_ID, HOSTNAME, ...) must NOT be frozen into
    # .env and replayed into a later, different allocation. The wrapper fingerprints the
    # ambient env first and persists only what differs (and never the volatile names).
    w = wrap("echo hi", Session("s", "/r"))
    assert '__hb_snap > "$__hb_base"' in w  # ambient fingerprint captured...
    assert w.index('__hb_snap > "$__hb_base"') < w.index(". /r/sessions/s/.env")  # ...before sourcing
    assert "compgen -A export" in w  # per-var enumeration (record-safe, not a line diff)
    assert 'grep -qxF "$__hb_n=$__hb_v" "$__hb_base"' in w  # skip vars unchanged vs ambient
    assert "SLURM*|HOSTNAME" in w  # drop scheduler runtime vars by name
    assert r"printf 'export %s=%q\n'" in w  # single-line, re-sourceable (multi-line safe)
    assert 'rm -f "$__hb_base"' in w  # baseline snapshot cleaned up


@bash_only
def test_behaviour_scheduler_vars_dropped_user_vars_persist(tmp_path):
    # A user var persists across calls; the live $SLURM_JOB_ID flows through (not frozen);
    # scheduler vars never land in .env.
    (out1, env1), (out2, out2env) = _run_session(
        [
            ('export DEMO=keep; echo "JOB=$SLURM_JOB_ID"', {"SLURM_JOB_ID": "L1", "HOSTNAME": "n1"}),
            ('echo "JOB=$SLURM_JOB_ID DEMO=$DEMO"', {"SLURM_JOB_ID": "L2", "HOSTNAME": "n2"}),
        ],
        str(tmp_path),
    )
    assert "SLURM_JOB_ID" not in env1 and "HOSTNAME" not in env1  # scheduler vars dropped
    assert "DEMO" in env1  # user var kept
    assert "JOB=L2" in out2  # live value, not the frozen L1
    assert "DEMO=keep" in out2  # user var persisted


@bash_only
def test_behaviour_multiline_var_mutation_does_not_corrupt_env(tmp_path):
    # Regression: mutating a MULTI-LINE ambient var used to leave an orphan line that broke
    # the next `. .env` (silently swallowed), dropping the WHOLE persisted session env.
    ml = "line1\nline2\nline3"
    res = _run_session(
        [
            ("export USERVAR=keep; echo set", {"ML": ml}),
            ('export ML="line1\nCHANGED"; echo mutated', {"ML": ml}),
            ('echo "USERVAR=[$USERVAR]"', {"ML": ml}),
        ],
        str(tmp_path),
    )
    assert "USERVAR=[keep]" in res[2][0]  # survived the multi-line mutation (was empty pre-fix)


@bash_only
def test_behaviour_user_modified_ambient_var_persists(tmp_path):
    # A user change to an ambient var (PATH) must carry forward, even though PATH exists in
    # the baseline — the diff is by value, not just name.
    res = _run_session(
        [
            ('export PATH="$PATH:/hpcb-demo"; echo set', {}),
            ('echo "P=$PATH"', {}),
        ],
        str(tmp_path),
    )
    assert "/hpcb-demo" in res[1][0]  # the PATH change persisted


def test_wrap_preserves_exit_code():
    w = wrap("false", Session("s", "/r"))
    assert "__hb_rc=$?" in w
    assert "exit $__hb_rc" in w


def test_reset_command_removes_state_files():
    r = reset_command(Session("abc", "/scratch/.hpc-bridge"))
    assert r.startswith("rm -f")
    assert "/scratch/.hpc-bridge/sessions/abc/.cwd" in r
    assert "/scratch/.hpc-bridge/sessions/abc/.env" in r
    assert "/scratch/.hpc-bridge/sessions/abc/.env.base.*" in r  # sweep leaked snapshots
