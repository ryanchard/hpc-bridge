"""In-session one-time code (complete_preauth) — hermetic, with a fake `ssh` that emulates OpenSSH's askpass
behaviour: it calls $SSH_ASKPASS with a prompt, compares the answer with the expected code, and honours
`-O check` against a marker file it writes on success."""
from __future__ import annotations

import stat

import pytest

from hpc_bridge import preauth
from hpc_bridge.facility.remote import NeedsPreauth, SshTarget, offers_one_time_code
from hpc_bridge.notices import _needs_preauth_result

FAKE_SSH = r'''#!/bin/sh
# fake ssh: -O check -> 0 iff marker exists; otherwise a keyboard-interactive login answered via $SSH_ASKPASS
marker="$FAKE_MARKER"
for a in "$@"; do [ "$a" = "-O" ] && { [ -f "$marker" ] && exit 0 || exit 255; }; done
[ -n "$SSH_ASKPASS" ] || { echo "no askpass" >&2; exit 255; }
[ "$SSH_ASKPASS_REQUIRE" = "force" ] || { echo "askpass not forced" >&2; exit 255; }
answer=$("$SSH_ASKPASS" "$FAKE_PROMPT") || { echo "Permission denied (keyboard-interactive)." >&2; exit 255; }
if [ "$answer" = "$FAKE_CODE" ]; then touch "$marker"; exit 0; fi
echo "Permission denied (keyboard-interactive)." >&2; exit 255
'''


@pytest.fixture
def fake_ssh(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text(FAKE_SSH)
    ssh.chmod(ssh.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_MARKER", str(tmp_path / "master.sock"))
    monkeypatch.setenv("FAKE_CODE", "123456")
    monkeypatch.setenv("FAKE_PROMPT", "Verification code: ")
    return str(ssh)


def _target(tmp_path):
    cd = tmp_path / "cm"
    cd.mkdir(exist_ok=True)
    return SshTarget(host="login.expanse.sdsc.edu", user="u", control_dir=str(cd))


def test_valid_code_opens_the_master(tmp_path, fake_ssh):
    ok, why = preauth.open_master_with_code(_target(tmp_path), "123456", state_dir=tmp_path, ssh_bin=fake_ssh)
    assert ok, why
    assert "123456" not in why and "one-time code was accepted" in why
    assert not list(tmp_path.glob("otp-*"))  # the code file is gone


def test_wrong_or_expired_code_is_reported_without_echoing_it(tmp_path, fake_ssh):
    ok, why = preauth.open_master_with_code(_target(tmp_path), "999999", state_dir=tmp_path, ssh_bin=fake_ssh)
    assert not ok and "not accepted" in why and "999999" not in why


def test_password_prompt_is_refused_and_routed_to_the_terminal(tmp_path, fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_PROMPT", "u@login.expanse.sdsc.edu's password: ")
    ok, why = preauth.open_master_with_code(_target(tmp_path), "123456", state_dir=tmp_path, ssh_bin=fake_ssh)
    assert not ok and "PASSWORD" in why and "own terminal" in why


@pytest.mark.parametrize("bad", ["hunter2!", "correct horse battery", "", "x" * 40, "12 34"])
def test_non_codes_are_rejected_before_any_ssh(tmp_path, bad):
    assert not preauth.looks_like_code(bad)
    ok, why = preauth.open_master_with_code(_target(tmp_path), bad, state_dir=tmp_path, ssh_bin="/nonexistent/ssh")
    assert not ok and "never sends passwords" in why


def test_multiplexing_off_cannot_share_a_connection(tmp_path, fake_ssh):
    t = SshTarget(host="h", user="u")  # no control_dir
    ok, why = preauth.open_master_with_code(t, "123456", state_dir=tmp_path, ssh_bin=fake_ssh)
    assert not ok and "multiplexing is off" in why


def test_needs_preauth_result_offers_the_code_path_only_when_keyboard_interactive():
    t = SshTarget(host="h", user="u", control_dir="/tmp/cm")
    res = _needs_preauth_result("f", t, otp_ok=True)
    assert res.preauth_code_ok and "complete_preauth(code)" in res.notice and "NEVER ask for a password" in res.notice
    res2 = _needs_preauth_result("f", t)
    assert not res2.preauth_code_ok and "complete_preauth" not in res2.notice and "THEIR OWN terminal" in res2.notice


def test_offers_one_time_code_reads_the_denial():
    assert offers_one_time_code("Permission denied (gssapi-with-mic,keyboard-interactive,hostbased).")
    assert not offers_one_time_code("Permission denied (publickey,password).")
    assert NeedsPreauth(SshTarget(host="h"), otp_ok=True).otp_ok is True


async def test_complete_preauth_tool_flow(tmp_path, fake_ssh, monkeypatch):
    from hpc_bridge import server, state
    from hpc_bridge.context import AppCtx
    from hpc_bridge.profile import Profile
    from tests.fakes import FakeFacility

    monkeypatch.setattr(state, "_state_dir", lambda: tmp_path)
    real = preauth.open_master_with_code
    monkeypatch.setattr(preauth, "open_master_with_code",
                        lambda target, code, *, state_dir, **kw: real(target, code, state_dir=state_dir, ssh_bin=fake_ssh))
    app = AppCtx(facility=FakeFacility(), profile=Profile())
    res = await server._complete_preauth(app, "123456")
    assert res.phase == "failed" and "connect_facility first" in res.notice  # nothing pending
    app.pending_preauth = ("expanse", _target(tmp_path))
    res = await server._complete_preauth(app, "not a code!")
    assert res.phase == "failed" and "never sends passwords" in res.notice and res.preauth_command
    res = await server._complete_preauth(app, "123456")
    assert res.phase == "opened" and app.pending_preauth is None and "connect_facility('expanse')" in res.notice
    assert "123456" not in (res.notice or "")

