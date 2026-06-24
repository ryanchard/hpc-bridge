# tests/test_catalog_search.py
import json
from pathlib import Path

from hpc_bridge.catalog.search import SearchCatalog
from tests.fakes import FakeCatalog, fake_entry

VALID_UUID = "11111111-2222-3333-4444-555555555555"


def _gmeta(entry):
    # Mimic a Globus Search get_subject response: entry content under entries[].content
    return {"entries": [{"content": json.loads(entry.model_dump_json())}]}


class _FakeSearchClient:
    def __init__(self, subjects=None, fail=False):
        self._subjects = subjects or {}
        self._fail = fail
        self.calls = []

    def get_subject(self, index_id, subject):
        self.calls.append((index_id, subject))
        if self._fail:
            raise RuntimeError("search offline")
        if subject not in self._subjects:
            return {"entries": []}  # a simple miss
        return _gmeta(self._subjects[subject])


async def test_search_get_hits_live_and_writes_through_cache(tmp_path):
    e = fake_entry(id="anvil", facility_key="purdue")
    client = _FakeSearchClient(subjects={"purdue:anvil": e})
    c = SearchCatalog(index_id="idx", client=client,
                      fallback=FakeCatalog([]), cache_dir=tmp_path)
    got = await c.get("purdue:anvil")
    assert got.id == "anvil"
    assert client.calls == [("idx", "purdue:anvil")]
    assert (tmp_path / "purdue:anvil.json").exists()  # write-through


async def test_search_falls_back_to_cache_then_bundled_on_error(tmp_path):
    e = fake_entry(id="anvil", facility_key="purdue")
    # prime the cache
    (tmp_path / "purdue:anvil.json").write_text(e.model_dump_json())
    client = _FakeSearchClient(fail=True)
    c = SearchCatalog(index_id="idx", client=client,
                      fallback=FakeCatalog([]), cache_dir=tmp_path)
    got = await c.get("purdue:anvil")
    assert got.id == "anvil"  # served from cache, not the failing client


async def test_search_falls_back_to_bundled_when_no_cache(tmp_path):
    e = fake_entry(id="anvil", facility_key="purdue")
    client = _FakeSearchClient(fail=True)
    c = SearchCatalog(index_id="idx", client=client,
                      fallback=FakeCatalog([e]), cache_dir=tmp_path)
    got = await c.get("purdue:anvil")
    assert got.id == "anvil"  # served from the bundled fallback


async def test_search_miss_returns_none(tmp_path):
    client = _FakeSearchClient(subjects={})
    c = SearchCatalog(index_id="idx", client=client,
                      fallback=FakeCatalog([]), cache_dir=tmp_path)
    assert await c.get("purdue:absent") is None
