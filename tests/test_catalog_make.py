# tests/test_catalog_make.py
import pytest

import hpc_bridge.server as server
from hpc_bridge.catalog.search import SearchCatalog


def test_make_catalog_defaults_to_the_public_registry(monkeypatch, tmp_path):
    # Out of the box: no env, no login — the plugin ships the public registry's id (Tier-2 A).
    from hpc_bridge.catalog.search import PUBLIC_REGISTRY_INDEX
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    monkeypatch.delenv("HPC_BRIDGE_SEARCH_INDEX", raising=False)
    monkeypatch.setattr(server, "_make_search_client", lambda: object())
    cat = server.make_catalog()
    assert isinstance(cat, SearchCatalog) and cat._index_id == PUBLIC_REGISTRY_INDEX
    assert PUBLIC_REGISTRY_INDEX == "6ff95fb8-1113-42be-a811-3d1cb5a67bd5"


def test_make_catalog_env_overrides_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path))
    monkeypatch.setenv("HPC_BRIDGE_SEARCH_INDEX", "staging-idx")
    monkeypatch.setattr(server, "_make_search_client", lambda: object())
    assert server.make_catalog()._index_id == "staging-idx"


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



def test_search_client_is_anonymous_unless_the_scope_is_already_held():
    # the registry is public: a fresh install reads it with NO login/consent; an identity that already
    # holds the Search scope (a curator) gets the authenticated client (sees restricted entries too)
    from hpc_bridge import server

    class _App:
        def __init__(self, required):
            self._required = required

        def login_required(self):
            return self._required

        def add_scope_requirements(self, *a, **k):  # SearchClient(app=...) registers its scope here
            pass

        @property
        def app_name(self):
            return "t"

    anon = server._make_search_client(_app_factory=lambda: _App(required=True))
    assert getattr(anon, "authorizer", None) is None  # anonymous: public entries only
    def boom():
        raise OSError("no storage.db at all")
    assert getattr(server._make_search_client(_app_factory=boom), "authorizer", None) is None


def test_search_client_never_lets_the_compute_client_version_check(monkeypatch):
    # the version check is an AUTHENTICATED call: on a fresh install it triggers the SDK's own
    # command-line login on the MCP transport (review merge-blocker) — must be off
    import globus_compute_sdk
    from hpc_bridge import server
    seen = {}

    class _Client:
        def __init__(self, *a, **kw):
            seen.update(kw)
            class _App:
                def login_required(self): return True
                def add_scope_requirements(self, *a, **k): pass
            self.app = _App()

    monkeypatch.setattr(globus_compute_sdk, "Client", _Client)
    server._make_search_client()
    assert seen.get("do_version_check") is False
