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


async def test_start_parses_endpoint_id():
    cli = make_cli(
        [(0, "Starting endpoint\n>>> Endpoint ID: 4b116d3c-1703-4f8f-9f6f-39921e5864df <<<\n", "")]
    )
    eid = await cli.start("dev")
    assert eid == "4b116d3c-1703-4f8f-9f6f-39921e5864df"
    assert cli.calls[0] == ("start", "dev")


async def test_configure_raises_on_nonzero():
    cli = make_cli([(1, "", "boom")])
    with pytest.raises(RuntimeError, match="configure failed"):
        await cli.configure("dev")


async def test_start_raises_when_no_id_in_output():
    cli = make_cli([(0, "no id here", "")])
    with pytest.raises(RuntimeError, match="no endpoint id"):
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
