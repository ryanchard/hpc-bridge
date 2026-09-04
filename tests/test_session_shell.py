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


def test_quoted_state_dir_expands_home_relative_roots_only():
    # a plain root goes through shlex.quote exactly as before (a safe path needs no quotes at all;
    # one with metachars is quoted whole)
    assert Session("s", "/scratch/u/.hpc-bridge").quoted_state_dir() == "/scratch/u/.hpc-bridge/sessions/s"
    assert Session("s", "/scr atch/.hpc-bridge").quoted_state_dir() == "'/scr atch/.hpc-bridge/sessions/s'"
    # a $HOME-relative root (a facility whose local user we can't know client-side — a multi-user
    # endpoint) must let $HOME expand on the WORKER: "$HOME" unquoted, the remainder quoted
    for root in ("$HOME/.hpc-bridge", "${HOME}/.hpc-bridge", "~/.hpc-bridge"):
        assert Session("s", root).quoted_state_dir() == '"$HOME"/.hpc-bridge/sessions/s', root
    # …and the remainder is still shlex-quoted when it needs to be
    assert Session("s", "$HOME/my dir").quoted_state_dir() == '"$HOME"\'/my dir/sessions/s\''
    # an embedded (non-leading) $HOME is NOT special — quoted whole like any literal
    assert Session("s", "/x/$HOME/y").quoted_state_dir() == "'/x/$HOME/y/sessions/s'"


@bash_only
def test_behaviour_home_relative_root_resolves_on_the_worker(tmp_path):
    # Prove it end-to-end under bash: with HOME pointed at tmp, a "$HOME/…" root lands the session
    # state under tmp (the worker's home), not under a literal '$HOME' directory — the exact failure
    # a whole-quoted root produces ("mkdir -p '$HOME/…'" → a dir literally named '$HOME').
    sess = Session("default", "$HOME/.hpc-bridge")
    r = subprocess.run(
        ["bash", "-c", wrap("echo HB_OK; pwd", sess)], capture_output=True, text=True,
        env={**os.environ, "HOME": str(tmp_path)},
    )
    assert r.returncode == 0 and "HB_OK" in r.stdout, r.stderr
    assert os.path.isfile(tmp_path / ".hpc-bridge" / "sessions" / "default" / ".cwd")
    assert not os.path.exists(tmp_path / "$HOME") and not os.path.exists("$HOME")
    # reset_command uses the same quoting, so it clears the same (expanded) location
    subprocess.run(["bash", "-c", reset_command(sess)], env={**os.environ, "HOME": str(tmp_path)})
    assert not os.path.exists(tmp_path / ".hpc-bridge" / "sessions" / "default" / ".cwd")


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


@bash_only
def test_behaviour_home_root_with_quoted_remainder_keeps_env_fingerprint_intact(tmp_path):
    # Review finding: a $HOME-root whose REMAINDER needs shell quoting ("$HOME"'/sub dir/…') was spliced
    # into `__hb_base="…"` — the single quotes went literal inside the double quotes, the baseline
    # snapshot path broke, and EVERY ambient var got persisted (91 lines seen live). The bare assignment
    # form fixes it: only the command's own export lands in .env, and nothing hits stderr.
    sess = Session("default", "$HOME/sub dir/.hpc-bridge")
    script = wrap("export HB_ONLY=1; echo HB_OK", sess)
    assert '__hb_base="$HOME"' in script and '__hb_base=""' not in script
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={**os.environ, "HOME": str(tmp_path)})
    assert r.returncode == 0 and "HB_OK" in r.stdout
    assert r.stderr.strip() == "", r.stderr
    env_file = tmp_path / "sub dir" / ".hpc-bridge" / "sessions" / "default" / ".env"
    lines = [ln for ln in env_file.read_text().splitlines() if ln.strip()]
    assert "export HB_ONLY=1" in lines
    assert not any(ln.startswith(("export PATH=", "export HOME=")) for ln in lines), lines  # no ambient replay
    assert len(lines) < 5, lines
