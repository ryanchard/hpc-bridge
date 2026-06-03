from pathlib import Path

from hpc_bridge.facility.local import LocalFacility
from hpc_bridge.profile import Profile


class FakeCLI:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.configured = []
        self.started = []

    def config_path(self, name):
        p = self.tmp / name / "config.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def user_template_path(self, name):
        p = self.tmp / name / "user_config_template.yaml.j2"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    async def configure(self, name):
        self.configured.append(name)

    async def start(self, name):
        self.started.append(name)
        return "local-eid"

    async def stop(self, name):
        pass


def test_config_template_uses_localprovider_and_warm_blocks():
    f = LocalFacility(cli=None, endpoint_name="dev")
    interactive = f.config_template(Profile(mode="interactive"))
    assert interactive["engine"]["provider"]["type"] == "LocalProvider"
    assert interactive["engine"]["provider"]["min_blocks"] == 1
    batch = f.config_template(Profile(mode="batch"))
    assert batch["engine"]["provider"]["min_blocks"] == 0


async def test_provision_writes_config_and_starts(tmp_path):
    cli = FakeCLI(tmp_path)
    f = LocalFacility(cli=cli, endpoint_name="dev")
    handle = await f.provision(Profile(mode="batch"))
    assert handle.endpoint_id == "local-eid"
    assert cli.configured == ["dev"] and cli.started == ["dev"]
    # engine must be written to the UEP template, NOT the manager config.yaml
    written = (tmp_path / "dev" / "user_config_template.yaml.j2").read_text()
    assert "LocalProvider" in written
    assert not (tmp_path / "dev" / "config.yaml").exists()
