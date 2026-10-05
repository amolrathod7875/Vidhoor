"""Qdrant retrieval diagnostic script.

Usage:
    python scripts/test_qdrant_retrieval.py "Explain Section 64 of BNS"
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.insert(0, str(BACKEND_DIR))

from qdrant_manager import QdrantManager  # noqa: E402


def _build_manager() -> QdrantManager | None:
    try:
        return QdrantManager(
            host=os.environ.get("QDRANT_HOST", "127.0.0.1"),
            port=int(os.environ.get("QDRANT_PORT", "6333")),
            grpc_port=int(os.environ.get("QDRANT_GRPC_PORT", "6334")),
            collection_name=os.environ.get("QDRANT_COLLECTION", "indian_law_v2"),
            prefer_grpc=os.environ.get("QDRANT_PREFER_GRPC", "true").strip().lower() not in {"0", "false", "no"},
        )
    except Exception as exc:
        print(f"Failed to initialize Qdrant manager: {exc}")
        return None


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else "Explain Section 64 of BNS"
    manager = _build_manager()
    if manager is None:
        return 1

    print(f"Query: {query}")
    print(f"Collection: {manager.collection_name}")
    print(f"Host: {manager.host}:{manager.port}")
    print()

    start = time.time()
    try:
        result = manager.retrieve_context_with_metadata(
            query_string=query,
            filter_status="active",
        )
    except Exception as exc:
        print(f"Retrieval failed: {exc}")
        return 1
    elapsed = time.time() - start

    citations = result.get("citations", [])
    print(f"Retrieval latency: {elapsed*1000:.1f} ms")
    print(f"Results: {len(citations)}")
    print()

    for idx, item in enumerate(citations, start=1):
        print(f"[{idx}] {item.get('title') or item.get('source') or 'Legal Source'}")
        print(f"     Act: {item.get('doc_type', '')}")
        print(f"     Section/Article: {item.get('section', '')}")
        print(f"     Court: {item.get('court', '')}")
        print(f"     Year: {item.get('year')}")
        print(f"     Score: {item.get('confidence', 0.0):.3f}")
        print(f"     Source: {item.get('source', '')}")
        snippet = str(item.get("snippet", ""))
        if len(snippet) > 180:
            snippet = snippet[:180] + "..."
        print(f"     Snippet: {snippet}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
