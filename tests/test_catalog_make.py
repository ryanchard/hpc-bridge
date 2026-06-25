# tests/test_catalog_make.py
import pytest

import hpc_bridge.server as server
from hpc_bridge.catalog.search import SearchCatalog


def test_make_catalog_requires_index(monkeypatch):
    # The catalog IS the Globus Search index — no index is a hard failure (no bundled fallback).
    monkeypatch.delenv("HPC_BRIDGE_SEARCH_INDEX", raising=False)
    with pytest.raises(RuntimeError, match="HPC_BRIDGE_SEARCH_INDEX is required"):
        server.make_catalog()


def test_make_catalog_uses_search_when_index_set(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    monkeypatch.setenv("HPC_BRIDGE_SEARCH_INDEX", "idx-uuid")
    monkeypatch.setattr(server, "_make_search_client", lambda: object())  # avoid real Globus auth
    assert isinstance(server.make_catalog(), SearchCatalog)


def test_make_catalog_propagates_search_client_failure(monkeypatch, tmp_path):
    # No bundled fallback: if the search client can't be built (e.g. the scope isn't granted),
    # that's a hard failure, not a silent fall back to hardcoded data.
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    monkeypatch.setenv("HPC_BRIDGE_SEARCH_INDEX", "idx-uuid")

    def boom():
        raise RuntimeError("scope not granted")

    monkeypatch.setattr(server, "_make_search_client", boom)
    with pytest.raises(RuntimeError, match="scope not granted"):
        server.make_catalog()
