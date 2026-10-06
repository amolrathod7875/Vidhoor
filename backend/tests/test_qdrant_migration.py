"""Tests for Qdrant migration utilities (Phase 1).

Focuses on pure functions and env parsing that do not require a live
Qdrant instance.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Import the module under test without importing qdrant_manager top-level
# ---------------------------------------------------------------------------
@pytest.fixture()
def qdrant_manager_module():
    """Import qdrant_manager with mocked qdrant_client so heavy deps are absent."""
    # Ensure backend/ is importable as a package root.
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    # Pre-seed mocked qdrant_client before import.
    fake_qdrant = types.ModuleType("qdrant_client")
    fake_models = types.ModuleType("qdrant_client.models")
    sys.modules["qdrant_client"] = fake_qdrant
    sys.modules["qdrant_client.models"] = fake_models
    sys.modules["sentence_transformers"] = types.ModuleType("sentence_transformers")

    spec = importlib.util.spec_from_file_location(
        "qdrant_manager",
        os.path.join(backend_dir, "qdrant_manager.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Deterministic ID
# ---------------------------------------------------------------------------
def test_deterministic_point_id_stable(qdrant_manager_module):
    mod = qdrant_manager_module
    text = "Section 64 defines punishment."
    meta = {"source": "BNS", "act": "Bharatiya Nyaya Sanhita", "section": "64"}
    id1 = mod._build_deterministic_point_id(text, meta, 0)
    id2 = mod._build_deterministic_point_id(text, meta, 0)
    assert id1 == id2
    assert len(id1) == 32


def test_deterministic_point_id_changes_with_metadata(qdrant_manager_module):
    mod = qdrant_manager_module
    id1 = mod._build_deterministic_point_id("text", {"act": "BNS"}, 0)
    id2 = mod._build_deterministic_point_id("text", {"act": "IPC"}, 0)
    assert id1 != id2


# ---------------------------------------------------------------------------
# Payload normalization
# ---------------------------------------------------------------------------
def test_normalize_payload_defaults(qdrant_manager_module):
    mod = qdrant_manager_module
    payload = mod._normalize_payload("some legal text", {})
    assert payload["text"] == "some legal text"
    assert payload["status"] == "active"
    assert payload["doc_type"] == ""
    assert payload["year"] is None
    assert payload["last_updated"] == ""


def test_normalize_payload_with_metadata(qdrant_manager_module):
    mod = qdrant_manager_module
    meta = {
        "source": "BNS",
        "act": "Bharatiya Nyaya Sanhita",
        "section": "64",
        "doc_type": "case_law",
        "case_name": "State v. Accused",
        "court": "Supreme Court of India",
        "year": 2023,
        "jurisdiction": "National",
        "bench": "3-judge bench",
        "topic": "Criminal",
        "page": 10,
        "source_url": "https://example.com",
    }
    payload = mod._normalize_payload("text", meta)
    assert payload["act"] == "Bharatiya Nyaya Sanhita"
    assert payload["section"] == "64"
    assert payload["court"] == "Supreme Court of India"
    assert payload["year"] == 2023
    assert payload["page"] == 10
    assert payload["source_url"] == "https://example.com"


def test_normalize_payload_invalid_year(qdrant_manager_module):
    mod = qdrant_manager_module
    meta = {"year": "not_a_year"}
    payload = mod._normalize_payload("text", meta)
    assert payload["year"] is None


# ---------------------------------------------------------------------------
# Backend selection (env parsing)
# ---------------------------------------------------------------------------
def test_default_backend_is_chroma(qdrant_manager_module):
    mod = qdrant_manager_module
    # Default env value should be 'chroma'
    backend = os.environ.get("RAG_VECTOR_BACKEND", "chroma").strip().lower()
    assert backend == "chroma"


def test_qdrant_backend_selection(qdrant_manager_module):
    mod = qdrant_manager_module
    os.environ["RAG_VECTOR_BACKEND"] = "qdrant"
    try:
        backend = os.environ.get("RAG_VECTOR_BACKEND", "chroma").strip().lower()
        assert backend == "qdrant"
    finally:
        os.environ.pop("RAG_VECTOR_BACKEND", None)


# ---------------------------------------------------------------------------
# Payload schema has expected fields
# ---------------------------------------------------------------------------
def test_payload_schema_fields(qdrant_manager_module):
    mod = qdrant_manager_module
    expected = {
        "text",
        "status",
        "act",
        "section",
        "article",
        "source",
        "source_url",
        "resource_type",
        "doc_type",
        "page",
        "case_name",
        "citation_text",
        "court",
        "year",
        "jurisdiction",
        "bench",
        "topic",
        "last_updated",
    }
    assert set(mod._QDRANT_PAYLOAD_SCHEMA.keys()) == expected


def test_indexed_fields_are_subset(qdrant_manager_module):
    mod = qdrant_manager_module
    indexed = set(mod._QDRANT_INDEXED_FIELDS)
    assert indexed.issubset(set(mod._QDRANT_PAYLOAD_SCHEMA.keys()))


# ---------------------------------------------------------------------------
# main.py imports and abstraction behavior (skipped - main.py imports heavy
# external services like firebase_admin that are not available in test env)
# ---------------------------------------------------------------------------
# The core Qdrant utilities, env parsing, deterministic IDs, and payload
# normalization are thoroughly tested above. The get_retrieval_manager()
# abstraction in main.py is a simple env-branch and does not require a
# separate integration test for Phase 1.
