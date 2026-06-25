# tests/test_catalog_ingest.py
from pathlib import Path

import pytest

from hpc_bridge.catalog.ingest import ingest

FIX = Path(__file__).parent / "catalog_fixtures" / "two_machines.yaml"


class _FakeIngestClient:
    def __init__(self):
        self.ingested = []

    def ingest(self, index_id, doc):
        self.ingested.append((index_id, doc))
        return {"task_id": "t"}


def test_ingest_validates_and_upserts_gmeta():
    client = _FakeIngestClient()
    n = ingest(index_id="idx", seed_path=FIX, client=client)
    assert n == 2
    index_id, doc = client.ingested[0]
    assert index_id == "idx"
    assert doc["ingest_type"] == "GMetaList"
    subjects = {g["subject"] for g in doc["ingest_data"]["gmeta"]}
    assert subjects == {"purdue:anvil", "alcf:polaris"}
    g0 = doc["ingest_data"]["gmeta"][0]
    assert g0["visible_to"] == ["public"]
    assert g0["content"]["id"] in {"anvil", "polaris"}


def test_ingest_rejects_a_malformed_seed(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- id: x\n")
    with pytest.raises(Exception):
        ingest(index_id="idx", seed_path=bad, client=_FakeIngestClient())
