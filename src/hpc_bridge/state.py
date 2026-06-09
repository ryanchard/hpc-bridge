# src/hpc_bridge/state.py
"""Durable local record of which login node each endpoint landed on.

HPC SSH aliases round-robin across login nodes, but a started endpoint's manager
daemon lives on ONE node. We record the resolved FQDN at provision time so later
sessions SSH straight to that node instead of the alias (which would orphan the
endpoint). The store is keyed by (alias, endpoint name) and lives in
~/.hpc-bridge/endpoints.json, 0600 — it references a credentialed host.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class EndpointRecord:
    endpoint_id: str
    login_host: str  # resolved FQDN (hostname -f) the manager daemon runs on
    alias: str  # the round-robin SSH alias originally connected to
    user: str
    key_path: str
    name: str
    provisioned_at: str  # ISO-8601 UTC


def _key(alias: str, name: str) -> str:
    return f"{alias}::{name}"


class LoginNodeStore:
    def __init__(self, path: Path | str | None = None) -> None:
        default = Path.home() / ".hpc-bridge" / "endpoints.json"
        self.path = Path(path) if path else default

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # write 0600 from creation: open with restrictive mode, then dump
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.chmod(self.path, 0o600)

    def put(self, rec: EndpointRecord) -> None:
        data = self._load()
        data[_key(rec.alias, rec.name)] = asdict(rec)
        self._save(data)

    def get(self, *, alias: str, name: str) -> EndpointRecord | None:
        row = self._load().get(_key(alias, name))
        return EndpointRecord(**row) if row else None

    def remove(self, *, alias: str, name: str) -> None:
        data = self._load()
        if data.pop(_key(alias, name), None) is not None:
            self._save(data)

    def all(self) -> list[EndpointRecord]:
        return [EndpointRecord(**row) for row in self._load().values()]
