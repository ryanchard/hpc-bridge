import pytest

from hpc_bridge.endpoint import EndpointCLI


def make_cli(responses):
    calls = []

    async def runner(*args):
        calls.append(args)
        return responses.pop(0)

    cli = EndpointCLI(user_dir=None, runner=runner)
    cli.calls = calls
    return cli


async def test_start_detaches_and_reads_uuid_from_endpoint_json(tmp_path):
    # 4.x `start` runs in the foreground without --detach (would hang our await),
    # and writes the registered UUID to endpoint.json, not stdout.
    (tmp_path / "dev").mkdir(parents=True)
    (tmp_path / "dev" / "endpoint.json").write_text('{"endpoint_id": "abc-123"}')
    calls = []

    async def runner(*args):
        calls.append(args)
        return (0, "", "")

    cli = EndpointCLI(user_dir=tmp_path, runner=runner)
    eid = await cli.start("dev")
    assert eid == "abc-123"
    assert calls[0] == ("start", "dev", "--detach")


async def test_configure_raises_on_nonzero():
    cli = make_cli([(1, "", "boom")])
    with pytest.raises(RuntimeError, match="configure failed"):
        await cli.configure("dev")


async def test_start_raises_on_nonzero():
    cli = make_cli([(1, "", "boom")])
    with pytest.raises(RuntimeError, match="start failed"):
        await cli.start("dev")


def test_user_template_path_points_to_jinja_template(tmp_path):
    # globus-compute-endpoint 4.x: the engine lives in the per-user-process template,
    # not config.yaml (which is the engine-free manager config).
    cli = EndpointCLI(user_dir=tmp_path)
    assert cli.user_template_path("dev") == tmp_path / "dev" / "user_config_template.yaml.j2"


async def test_configure_forces_single_user_by_default():
    # hpc-bridge invariant: always a PERSONAL (single-user) endpoint, never a MEP.
    # globus-compute-endpoint's default auto-selects multi-user from POSIX caps,
    # so we must pass --multi-user false explicitly.
    cli = make_cli([(0, "", "")])
    await cli.configure("dev")
    assert cli.calls[0] == ("configure", "--multi-user", "false", "dev")
