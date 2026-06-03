from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Awaitable, Callable

Runner = Callable[..., Awaitable[tuple[int, str, str]]]
_EID = re.compile(r"Endpoint ID:\s*([0-9a-fA-F-]{36})")


class EndpointCLI:
    def __init__(self, user_dir: Path | None = None, runner: Runner | None = None) -> None:
        self.user_dir = user_dir
        self._run = runner or self._default_run

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.user_dir is not None:
            env["GLOBUS_COMPUTE_USER_DIR"] = str(self.user_dir)
        return env

    def config_path(self, name: str) -> Path:
        base = self.user_dir or (Path.home() / ".globus_compute")
        return base / name / "config.yaml"

    def user_template_path(self, name: str) -> Path:
        # globus-compute-endpoint 4.x runs `start` as an EndpointManager whose
        # config.yaml must be engine-free; the compute engine lives here, in the
        # per-user-process (UEP) template.
        base = self.user_dir or (Path.home() / ".globus_compute")
        return base / name / "user_config_template.yaml.j2"

    async def _default_run(self, *args: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "globus-compute-endpoint",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env(),
        )
        out, err = await proc.communicate()
        return proc.returncode or 0, out.decode(), err.decode()

    async def configure(self, name: str, multi_user: bool = False) -> None:
        # hpc-bridge invariant: always a PERSONAL (single-user) endpoint, never a MEP.
        # globus-compute-endpoint's default auto-selects multi-user from the configuring
        # user's POSIX capabilities, which can silently create a multi-user (identity-
        # mapping) endpoint — the exact thing this project avoids. Force it off.
        rc, _out, err = await self._run(
            "configure", "--multi-user", "true" if multi_user else "false", name
        )
        if rc != 0:
            raise RuntimeError(f"configure failed: {err}")

    async def start(self, name: str) -> str:
        rc, out, err = await self._run("start", name)
        if rc != 0:
            raise RuntimeError(f"start failed: {err}")
        m = _EID.search(out)
        if not m:
            raise RuntimeError(f"no endpoint id in output: {out!r}")
        return m.group(1)

    async def stop(self, name: str) -> None:
        rc, _out, err = await self._run("stop", name)
        if rc != 0:
            raise RuntimeError(f"stop failed: {err}")
