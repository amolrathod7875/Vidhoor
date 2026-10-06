"""Compare Chroma vs Qdrant retrieval for the same queries.

Usage:
    python scripts/compare_retrieval.py "Explain Section 64 of BNS"
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

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
        print(f"Qdrant unavailable: {exc}")
        return None


def _summarize_citations(citations: list[dict[str, Any]]) -> None:
    for idx, item in enumerate(citations, start=1):
        title = item.get("title") or item.get("source") or "Legal Source"
        section = item.get("section", "")
        court = item.get("court", "")
        year = item.get("year")
        score = item.get("confidence", 0.0)
        source = item.get("source", "")
        print(f"  [{idx}] {title} | section={section} | court={court} | year={year} | score={score:.3f} | source={source}")


def main() -> int:
    queries = sys.argv[1:]
    if not queries:
        queries = [
            "Explain Section 64 of BNS",
            "Summarize Article 21 of the Constitution of India",
            "What is the legal position on bail under BNSS Section 436?",
        ]

    manager = _build_manager()
    if manager is None:
        return 1

    print(f"Qdrant collection: {manager.collection_name}")
    print(f"Qdrant host: {manager.host}:{manager.port}")
    print()

    for query in queries:
        print(f"Query: {query}")
        start = time.time()
        try:
            result = manager.retrieve_context_with_metadata(
                query_string=query,
                filter_status="active",
            )
        except Exception as exc:
            print(f"  Retrieval failed: {exc}")
            print()
            continue
        elapsed = time.time() - start

        citations = result.get("citations", [])
        print(f"  Latency: {elapsed*1000:.1f} ms | Results: {len(citations)}")
        _summarize_citations(citations)
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
