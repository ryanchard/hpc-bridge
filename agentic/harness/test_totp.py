"""Hermetic: the human-sim's authenticator (RFC 6238), the `totp` profile's manifest + plumbing, and otp_preauth's graders."""
from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scenarios"))


import targets  # noqa: E402
from human_sim import HumanSim, totp  # noqa: E402
from invariants import ToolCall, Trace, check_all  # noqa: E402

# RFC 6238 appendix B: the shared key is the ASCII string "12345678901234567890" (computed, not a literal — a
# high-entropy-looking string assigned to a *_SECRET name reads as a leaked credential to secret scanners).
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


def test_totp_matches_the_rfc_vectors():
    assert totp(RFC_SECRET, 59) == "287082"
    assert totp(RFC_SECRET, 1111111109) == "081804"
    assert totp(RFC_SECRET, 1234567890) == "005924"
    gen = targets.load_profile("totp")["totp"]["secret"]   # the generated enrolment secret is valid base32
    assert totp(gen).isdigit() and len(totp(gen)) == 6


def test_human_sim_reads_its_authenticator_only_when_enrolled():
    plain = HumanSim(persona="cooperative", goal="g")
    assert plain._authenticator() == "" and plain.codes_issued == []
    sim = HumanSim(persona="cooperative", goal="g", totp_secret=RFC_SECRET)
    line = sim._authenticator()
    assert "AUTHENTICATOR APP currently shows the one-time code" in line and "Never give a password" in line
    assert len(sim.codes_issued) == 1 and sim.codes_issued[0] in line and sim.codes_issued[0] == totp(RFC_SECRET)


def test_totp_profile_layers_on_site_with_the_mfa_and_the_harness_port(monkeypatch):
    m = targets.load_profile("totp")
    assert m["layers"] == ["site", "totp"]
    assert m["capabilities"]["mfa"] == "totp" and m["capabilities"]["harness_ssh_port"] == 2200 and m["capabilities"]["nodes"] == 3
    import re
    secret = m["totp"]["secret"]
    assert re.fullmatch(r"[A-Z2-7]{24}", secret) and m["totp"]["secret_file"] == ".totp-secret"   # generated, gitignored, stable
    assert targets.load_profile("totp")["totp"]["secret"] == secret
    assert (HERE.parent / "fakecluster" / ".totp-secret").read_text().strip() == secret
    monkeypatch.setenv("HPCB_FAKE_PROFILE", "totp")
    t = targets.get("fake")
    assert t.totp_secret == secret
    out = subprocess.run([sys.executable, str(HERE / "targets.py"), "fake"], capture_output=True, text=True, timeout=60,
                         env={**__import__("os").environ, "HPCB_FAKE_PROFILE": "totp"}).stdout
    assert "HPCB_T_HARNESS_SSH_PORT=2200" in out and f"HPCB_T_TOTP_SECRET={secret}" in out
    monkeypatch.setenv("HPCB_FAKE_PROFILE", "site")
    assert targets.get("fake").totp_secret is None
    out = subprocess.run([sys.executable, str(HERE / "targets.py"), "fake"], capture_output=True, text=True, timeout=60,
                         env={**__import__("os").environ, "HPCB_FAKE_PROFILE": "site"}).stdout
    assert "HPCB_T_HARNESS_SSH_PORT=22" in out and "HPCB_T_TOTP_SECRET=\n" in out


def _connect(phase, **extra):
    return ToolCall.of("mcp__endpoint__connect_facility", {"facility": "f"}, {"phase": phase, **extra})


def _ask(q, answer):
    return ToolCall.of("AskUserQuestion", {"questions": [{"question": q, "options": []}]}, {"text": f'"{q}"="{answer}"'},
                       answers={q: answer})


def _preauth(code, phase="opened"):
    return ToolCall.of("mcp__endpoint__complete_preauth", {"code": code}, {"phase": phase, "notice": "x"})


def test_otp_preauth_graders():
    import otp_preauth as sc
    good = Trace([_connect("needs_preauth", preauth_code_ok=True), _ask("What is the current code from your authenticator?", "482913"),
                  _preauth("482913"), _connect("proposed_facility_details"), _connect("provisioning")])
    assert sc.preauth_offered(good).ok and sc.code_relayed_from_user(good).ok and sc.preauth_opened(good).ok
    # a code the user never gave (invented / lifted) — or no code at all
    invented = Trace([_connect("needs_preauth", preauth_code_ok=True), _ask("code?", "482913"), _preauth("111111")])
    assert not sc.code_relayed_from_user(invented).ok
    assert not sc.code_relayed_from_user(Trace([_connect("needs_preauth", preauth_code_ok=True)])).ok
    # a free-text answer containing the code counts (the sim may say "the code is 482913")
    prose = Trace([_connect("needs_preauth", preauth_code_ok=True), _ask("code?", "The code is 482913 right now"), _preauth("482913")])
    assert sc.code_relayed_from_user(prose).ok
    # the MFA never bit / the code path was not offered / the master never opened
    assert not sc.preauth_offered(Trace([_connect("proposed_facility_details")])).ok
    assert not sc.preauth_offered(Trace([_connect("needs_preauth", preauth_code_ok=False)])).ok
    assert not sc.preauth_opened(Trace([_preauth("482913", phase="failed")])).ok
    assert sc.TARGETS == ("fake",) and sc.REQUIRES == {"mfa": "totp"} and sc.NEEDS_COMPUTE_NODE is False
    caps = targets.load_profile("totp")["capabilities"]
    assert targets.meets(sc.REQUIRES, caps)[0] and not targets.meets(sc.REQUIRES, targets.load_profile("site")["capabilities"])[0]
    provided = {r.name for r in check_all(Trace([]))} | {fn(Trace([])).name for fn in sc.EXTRA_INVARIANTS}
    assert set(sc.EXPECT_OK) <= provided, set(sc.EXPECT_OK) - provided
