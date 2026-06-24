# src/hpc_bridge/catalog/ingest.py
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bundled import BundledCatalog


def ingest(index_id: str, seed_path: str | Path, client) -> int:
    """Validate every seed entry against CatalogEntry and upsert them as GMetaEntries.

    Idempotent: keyed by subject (<facility_key>:<id>), so re-running overwrites in place.
    Returns the number of entries ingested. Run by a curator holding the index writer role.
    """
    catalog = BundledCatalog(Path(seed_path))  # construction re-validates every entry
    gmeta = [
        {
            "subject": entry.subject,
            "visible_to": ["public"],  # TODO(curator): per-entry/--visible-to for group-restricted machines (spec §6)
            "content": json.loads(entry.model_dump_json()),
        }
        for entry in catalog.entries()
    ]
    doc = {"ingest_type": "GMetaList", "ingest_data": {"gmeta": gmeta}}
    client.ingest(index_id, doc)
    return len(gmeta)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hpc-bridge-catalog",
        description="Validate seed YAML and ingest to a Globus Search index.",
    )
    parser.add_argument("index_id", help="target Globus Search index UUID")
    parser.add_argument("seed_path", help="seed .yaml file or directory")
    args = parser.parse_args(argv)

    from globus_compute_sdk import Client
    from globus_sdk import SearchClient

    authorizer = Client().app.get_authorizer("search.api.globus.org")
    client = SearchClient(authorizer=authorizer)
    n = ingest(index_id=args.index_id, seed_path=args.seed_path, client=client)
    print(f"ingested {n} entr{'y' if n == 1 else 'ies'} to {args.index_id}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
