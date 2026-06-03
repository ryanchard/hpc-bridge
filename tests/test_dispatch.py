from dataclasses import dataclass

from hpc_bridge.dispatch import execute


@dataclass
class FakeResult:
    returncode: int
    stdout: str
    stderr: str


class FakeRunner:
    def __init__(self, result):
        self.result = result
        self.commands = []

    async def run(self, command):
        self.commands.append(command)
        return self.result


async def test_execute_builds_complete_outcome():
    runner = FakeRunner(FakeResult(0, "hi\n", ""))
    out = await execute("echo hi", runner)
    assert out.phase == "complete"
    assert out.exit_code == 0
    assert out.stdout == "hi\n"
    assert out.block_state == "warm"
    assert runner.commands == ["echo hi"]


async def test_execute_preserves_nonzero_exit_and_stderr():
    runner = FakeRunner(FakeResult(2, "", "boom\n"))
    out = await execute("false", runner)
    assert out.exit_code == 2
    assert out.stderr_snippet == "boom\n"


async def test_execute_caps_large_output():
    runner = FakeRunner(FakeResult(0, "y" * 5000, ""))
    out = await execute("big", runner, max_output_chars=100)
    assert out.stdout.startswith("y" * 100)
    assert "truncated" in out.stdout


class RaisingRunner:
    def __init__(self, exc):
        self.exc = exc

    async def run(self, command):
        raise self.exc


async def test_execute_translates_timeout_to_structured_failure():
    out = await execute("sleep 999", RaisingRunner(TimeoutError()))
    assert out.phase == "failed"
    assert out.exit_code == 124
    assert "ensure_endpoint_up" in (out.notice or "")


async def test_execute_translates_generic_exception_to_failure():
    out = await execute("boom", RaisingRunner(RuntimeError("kaboom")))
    assert out.phase == "failed"
    assert "kaboom" in (out.stderr_snippet or "")


class _NamedError(Exception):
    pass


async def test_execute_translates_result_size_by_class_name():
    err = type("MaxResultSizeExceeded", (Exception,), {})()
    out = await execute("cat huge", RaisingRunner(err))
    assert out.phase == "failed"
    assert "10 MB" in (out.notice or "")
