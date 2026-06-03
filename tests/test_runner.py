import pytest

from hpc_bridge.runner import GlobusRunner, _escape_for_shellfunction


def test_escape_braces_for_shellfunction_roundtrips():
    # ShellFunction does cmd.format(**kwargs); literal braces must be doubled so they
    # survive as single braces (and don't get read as replacement fields).
    cmd = "mkdir -p x && { echo hi; } && echo ${HOME}"
    escaped = _escape_for_shellfunction(cmd)
    assert "{{" in escaped and "}}" in escaped
    assert escaped.format() == cmd  # collapses back to the original shell command


class FakeFuture:
    def __init__(self, result):
        self._r = result

    def result(self, timeout=None):
        return self._r


class FakeExecutor:
    def __init__(self):
        self.submitted = []
        self.shutdowns = 0

    def submit(self, fn):
        self.submitted.append(fn)
        return FakeFuture("RESULT")

    def shutdown(self):
        self.shutdowns += 1


def test_executor_created_once_and_closed():
    ex = FakeExecutor()
    calls = []

    def factory():
        calls.append(1)
        return ex

    r = GlobusRunner("eid", executor_factory=factory)
    assert r.executor() is ex
    assert r.executor() is ex  # cached, not re-created
    assert calls == [1]
    r.close()
    assert ex.shutdowns == 1


async def test_run_submits_shellfunction_and_returns_result():
    pytest.importorskip("globus_compute_sdk")
    ex = FakeExecutor()
    r = GlobusRunner("eid", executor_factory=lambda: ex)
    res = await r.run("echo hi")
    assert res == "RESULT"
    assert len(ex.submitted) == 1  # one ShellFunction submitted
