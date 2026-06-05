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


def test_parse_canary_extracts_versions():
    from hpc_bridge.runner import _parse_canary

    assert _parse_canary("HPCB_CANARY\n3.11.7 0.3.9 a070.anvil\n") == ("3.11.7", "0.3.9", "a070.anvil")
    assert _parse_canary("HPCB_CANARY\n") == (None, None, None)  # version line absent
    assert _parse_canary("") == (None, None, None)


class _ShellRes:
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 0
        self.stderr = ""


class _CanaryFuture:
    def __init__(self, result=None, exc=None):
        self._r = result
        self._exc = exc

    def result(self, timeout=None):
        if self._exc is not None:
            raise self._exc
        return self._r


class _CanaryExecutor:
    def __init__(self, *, result=None, exc=None):
        self._fut = _CanaryFuture(result, exc)
        self.submitted = []

    def submit(self, fn):
        self.submitted.append(fn)
        return self._fut

    def shutdown(self):
        pass


async def test_canary_ok_parses_worker_versions():
    pytest.importorskip("globus_compute_sdk")
    ex = _CanaryExecutor(result=_ShellRes("HPCB_CANARY\n3.11.7 0.3.9 a070.anvil\n"))
    r = GlobusRunner("eid", executor_factory=lambda: ex)
    res = await r.canary(timeout=2.0)
    assert res.ok is True
    assert (res.worker_python, res.worker_dill, res.worker_host) == ("3.11.7", "0.3.9", "a070.anvil")
    assert len(ex.submitted) == 1  # the canary went through the real Executor path


async def test_canary_timeout_reports_not_ok():
    # No worker answered within the budget -> not warm (block still cold-starting), NOT an exception.
    pytest.importorskip("globus_compute_sdk")
    ex = _CanaryExecutor(exc=TimeoutError())
    r = GlobusRunner("eid", executor_factory=lambda: ex)
    res = await r.canary(timeout=0.5)
    assert res.ok is False and res.error == "timeout"


async def test_canary_ok_even_without_version_line():
    # python/dill missing on the worker (|| true) -> sentinel only: a returned result still
    # proves a worker is live, versions just come back None.
    pytest.importorskip("globus_compute_sdk")
    ex = _CanaryExecutor(result=_ShellRes("HPCB_CANARY\n"))
    r = GlobusRunner("eid", executor_factory=lambda: ex)
    res = await r.canary()
    assert res.ok is True and res.worker_python is None
