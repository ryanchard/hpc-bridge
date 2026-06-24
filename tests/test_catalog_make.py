# tests/test_catalog_make.py
import hpc_bridge.server as server
from hpc_bridge.catalog.bundled import BundledCatalog
from hpc_bridge.catalog.search import SearchCatalog


def test_make_catalog_defaults_to_bundled(monkeypatch):
    monkeypatch.delenv("HPC_BRIDGE_SEARCH_INDEX", raising=False)
    assert isinstance(server.make_catalog(), BundledCatalog)


def test_make_catalog_uses_search_when_index_set(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    monkeypatch.setenv("HPC_BRIDGE_SEARCH_INDEX", "idx-uuid")
    # avoid real Globus auth by substituting the client builder
    monkeypatch.setattr(server, "_make_search_client", lambda: object())
    assert isinstance(server.make_catalog(), SearchCatalog)


def test_make_catalog_falls_back_to_bundled_if_search_client_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    monkeypatch.setenv("HPC_BRIDGE_SEARCH_INDEX", "idx-uuid")

    def boom():
        raise RuntimeError("no auth")

    monkeypatch.setattr(server, "_make_search_client", boom)
    assert isinstance(server.make_catalog(), BundledCatalog)


def test_make_catalog_falls_back_if_searchcatalog_construction_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    monkeypatch.setenv("HPC_BRIDGE_SEARCH_INDEX", "idx-uuid")
    monkeypatch.setattr(server, "_make_search_client", lambda: object())
    import hpc_bridge.catalog.search as search_mod

    def boom(*a, **k):
        raise OSError("cache dir unwritable")

    monkeypatch.setattr(search_mod, "SearchCatalog", boom)
    assert isinstance(server.make_catalog(), BundledCatalog)
