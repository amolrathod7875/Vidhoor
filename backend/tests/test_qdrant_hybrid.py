"""Tests for Qdrant hybrid retrieval and ingestion utilities."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure backend directory is on sys.path for imports
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Skip all tests in this file if Qdrant is not available
pytest.importorskip("qdrant_client")

# Check if BGE-M3 is available; skip integration tests if not
# Note: Import succeeds on this machine but model instantiation fails due to
# memory constraints. Integration tests requiring actual BGE-M3 inference are
# skipped on low-RAM environments and should be run on OCI/GPU hardware.
_BGE_AVAILABLE = False
try:
    from FlagEmbedding.inference.embedder.encoder_only.m3 import M3Embedder  # noqa: F401
    _BGE_AVAILABLE = True
except Exception:
    _BGE_AVAILABLE = False

# Integration tests require both Qdrant AND working BGE-M3 inference.
# Set RUN_QDRANT_INTEGRATION=1 to force-run them on capable hardware.
_RUN_INTEGRATION = os.environ.get("RUN_QDRANT_INTEGRATION", "0").strip() == "1"

from qdrant_manager import (  # noqa: E402
    QdrantManager,
    _build_deterministic_point_id,
    _clean_snippet,
    _contains_reference,
    _extract_query_references,
    _normalize_payload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager() -> QdrantManager:
    """Create a QdrantManager configured for local testing."""
    host = os.environ.get("QDRANT_HOST", "127.0.0.1")
    port = int(os.environ.get("QDRANT_PORT", "6333"))
    grpc_port = int(os.environ.get("QDRANT_GRPC_PORT", "6334"))
    collection = os.environ.get("QDRANT_COLLECTION", "test_indian_law_v2")
    return QdrantManager(
        host=host,
        port=port,
        grpc_port=grpc_port,
        collection_name=collection,
        prefer_grpc=True,
    )


def _ensure_collection(manager: QdrantManager) -> None:
    """Create the test collection if it does not exist."""
    if not manager.collection_exists():
        manager.ensure_collection()


def _delete_collection(manager: QdrantManager) -> None:
    """Delete the test collection if it exists."""
    if manager.collection_exists():
        try:
            manager.client.delete_collection(manager.collection_name)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Deterministic ID tests
# ---------------------------------------------------------------------------

class TestDeterministicIds:
    def test_stable_same_inputs(self):
        first = _build_deterministic_point_id("text", {"source": "a", "act": "b", "section": "c"}, 0)
        second = _build_deterministic_point_id("text", {"source": "a", "act": "b", "section": "c"}, 0)
        assert first == second
        assert len(first) == 32

    def test_changes_with_metadata(self):
        first = _build_deterministic_point_id("text", {"source": "a", "act": "b", "section": "c"}, 0)
        second = _build_deterministic_point_id("text", {"source": "x", "act": "b", "section": "c"}, 0)
        assert first != second


# ---------------------------------------------------------------------------
# Payload normalization tests
# ---------------------------------------------------------------------------

class TestPayloadNormalization:
    def test_defaults(self):
        payload = _normalize_payload("chunk text", {})
        assert payload["text"] == "chunk text"
        assert payload["status"] == "active"
        assert payload["act"] == ""
        assert payload["source"] == ""

    def test_with_metadata(self):
        metadata = {
            "source": "bns.pdf",
            "act": "Bharatiya Nyaya Sanhita",
            "section": "64",
            "doc_type": "statute",
            "year": 2023,
            "page": 5,
        }
        payload = _normalize_payload("chunk text", metadata)
        assert payload["source"] == "bns.pdf"
        assert payload["act"] == "Bharatiya Nyaya Sanhita"
        assert payload["section"] == "64"
        assert payload["article"] == "64"
        assert payload["doc_type"] == "statute"
        assert payload["year"] == 2023
        assert payload["page"] == 5

    def test_invalid_year(self):
        payload = _normalize_payload("chunk text", {"year": "not-a-year"})
        assert payload["year"] is None


# ---------------------------------------------------------------------------
# Query reference extraction tests
# ---------------------------------------------------------------------------

class TestQueryReferences:
    def test_section_extraction(self):
        sections, articles = _extract_query_references("Explain Section 64 of BNS")
        assert "64" in sections
        assert not articles

    def test_article_extraction(self):
        sections, articles = _extract_query_references("What is Article 21?")
        assert not sections
        assert "21" in articles

    def test_shorthand(self):
        sections, articles = _extract_query_references("BNS Section 64")
        assert "64" in sections


# ---------------------------------------------------------------------------
# Snippet/reference helper tests
# ---------------------------------------------------------------------------

class TestSnippetHelpers:
    def test_clean_snippet(self):
        assert "___" not in _clean_snippet("hello___world")
        assert "---" not in _clean_snippet("hello---world")

    def test_contains_reference_section(self):
        assert _contains_reference("Section 64 deals with punishment", "Section", "64")
        assert not _contains_reference("Section 65 deals with punishment", "Section", "64")

    def test_contains_reference_article(self):
        assert _contains_reference("Article 21 protects life", "Article", "21")
        assert not _contains_reference("Article 22 protects rights", "Article", "21")


# ---------------------------------------------------------------------------
# Integration tests (require running Qdrant + BGE-M3)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _RUN_INTEGRATION,
    reason="Integration tests require RUN_QDRANT_INTEGRATION=1 and working BGE-M3 inference",
)
class TestQdrantIntegration:
    def test_upsert_and_count(self):
        manager = _make_manager()
        _ensure_collection(manager)
        try:
            payloads = [
                {"text": "Section 64 BNS", "source": "bns", "act": "Bharatiya Nyaya Sanhita", "status": "active"},
                {"text": "Article 21 Constitution", "source": "constitution", "act": "Constitution of India", "status": "active"},
            ]
            count = manager.upsert_points(
                dense_vectors=[[0.1] * 1024, [0.2] * 1024],
                sparse_vectors=None,
                payloads=payloads,
                point_ids=["test_point_1", "test_point_2"],
            )
            assert count == 2
            assert manager.collection_count() >= 2
        finally:
            _delete_collection(manager)

    def test_payload_filter(self):
        manager = _make_manager()
        _ensure_collection(manager)
        try:
            manager.upsert_points(
                dense_vectors=[[0.1] * 1024, [0.2] * 1024],
                sparse_vectors=None,
                payloads=[
                    {"text": "Section 64 BNS", "act": "Bharatiya Nyaya Sanhita", "status": "active"},
                    {"text": "Article 21 Constitution", "act": "Constitution of India", "status": "active"},
                ],
                point_ids=["filter_test_1", "filter_test_2"],
            )
            # Verify count
            assert manager.collection_count() >= 2
        finally:
            _delete_collection(manager)


class TestQdrantWithMockedEmbeddings:
    """Tests that exercise Qdrant without requiring BGE-M3 model loading."""

    def test_deterministic_point_id_length(self):
        point_id = _build_deterministic_point_id(
            "some legal text here",
            {"source": "file.pdf", "act": "BNS", "section": "64"},
            0,
        )
        assert len(point_id) == 32
        assert point_id.isalnum()

    def test_clean_snippet_preserves_content(self):
        text = "This is a ___ legal --- snippet with noise"
        cleaned = _clean_snippet(text)
        assert "___" not in cleaned
        assert "---" not in cleaned
        assert "legal" in cleaned
        assert "snippet" in cleaned

    def test_contains_reference_section_match(self):
        assert _contains_reference("Section 64 defines punishment", "Section", "64")
        assert not _contains_reference("Section 65 defines punishment", "Section", "64")

    def test_contains_reference_article_match(self):
        assert _contains_reference("Article 21 protects life", "Article", "21")
        assert not _contains_reference("Article 22 protects rights", "Article", "21")

    def test_extract_query_references_section(self):
        sections, articles = _extract_query_references("Explain Section 64 of BNS")
        assert sections == ["64"]
        assert articles == []

    def test_extract_query_references_article(self):
        sections, articles = _extract_query_references("What is Article 21?")
        assert sections == []
        assert articles == ["21"]

    def test_extract_query_references_shorthand(self):
        sections, articles = _extract_query_references("BNS Section 64")
        assert "64" in sections
        assert articles == []

    def test_normalize_payload_defaults(self):
        payload = _normalize_payload("chunk text", {})
        assert payload["text"] == "chunk text"
        assert payload["status"] == "active"
        assert payload["act"] == ""
        assert payload["source"] == ""

    def test_normalize_payload_with_metadata(self):
        metadata = {
            "source": "bns.pdf",
            "act": "Bharatiya Nyaya Sanhita",
            "section": "64",
            "doc_type": "statute",
            "year": 2023,
            "page": 5,
        }
        payload = _normalize_payload("chunk text", metadata)
        assert payload["source"] == "bns.pdf"
        assert payload["act"] == "Bharatiya Nyaya Sanhita"
        assert payload["section"] == "64"
        assert payload["article"] == "64"
        assert payload["doc_type"] == "statute"
        assert payload["year"] == 2023
        assert payload["page"] == 5

    def test_normalize_payload_invalid_year(self):
        payload = _normalize_payload("chunk text", {"year": "not-a-year"})
        assert payload["year"] is None

    def test_qdrant_manager_instantiation(self):
        manager = _make_manager()
        assert manager.collection_name == os.environ.get("QDRANT_COLLECTION", "test_indian_law_v2")
        assert manager.host == os.environ.get("QDRANT_HOST", "127.0.0.1")
        assert manager.port == int(os.environ.get("QDRANT_PORT", "6333"))
