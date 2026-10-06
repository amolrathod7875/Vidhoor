"""Tests for Constitution Article metadata and ingest reference detection (Phase 5)."""

from __future__ import annotations

import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from ingest_legal_resources import (
    detect_reference,
)

from qdrant_manager import (
    _build_deterministic_point_id,
    _clean_snippet,
    _contains_reference,
    _extract_query_references,
    _normalize_payload,
)


class TestConstitutionArticleMetadata:
    """Article metadata must be correct for Constitution chunks."""

    def test_article_14_stored_as_article_not_section(self):
        meta = detect_reference(
            "Article 14 of the Constitution of India",
            act_name="Constitution of India",
        )
        assert meta.get("article") == "14"
        assert meta.get("section", "") == ""

    def test_article_19_stored_as_article_not_section(self):
        meta = detect_reference(
            "Article 19 guarantees freedom of speech",
            act_name="Constitution of India",
        )
        assert meta.get("article") == "19"
        assert meta.get("section", "") == ""

    def test_article_21_stored_as_article_not_section(self):
        meta = detect_reference(
            "Article 21 protects life and personal liberty",
            act_name="Constitution of India",
        )
        assert meta.get("article") == "21"
        assert meta.get("section", "") == ""

    def test_article_32_stored_as_article_not_section(self):
        meta = detect_reference(
            "Article 32 provides the right to constitutional remedies",
            act_name="Constitution of India",
        )
        assert meta.get("article") == "32"
        assert meta.get("section", "") == ""

    def test_article_51a_stored_as_article_not_section(self):
        meta = detect_reference(
            "Article 51A enumerates fundamental duties",
            act_name="Constitution of India",
        )
        assert meta.get("article") == "51A"
        assert meta.get("section", "") == ""

    def test_constitution_article_carry_forward(self):
        last_article = "21"
        chunk = "No person shall be deprived of his life or personal liberty"
        meta = detect_reference(
            chunk,
            default_article=last_article,
            act_name="Constitution of India",
        )
        assert meta.get("article") == "21"
        assert meta.get("section", "") == ""

    def test_constitution_section_field_empty(self):
        meta = detect_reference(
            "Article 21 protects life and personal liberty",
            act_name="Constitution of India",
        )
        assert "section" not in meta or meta.get("section", "") == ""

    def test_statute_section_not_confused_with_article(self):
        meta = detect_reference(
            "Section 64 of BNS defines punishment for rape",
            act_name="Bharatiya Nyaya Sanhita",
        )
        assert meta.get("section") == "64"
        assert meta.get("article", "") == ""


class TestPayloadNormalizationConstitution:
    """Qdrant payload normalization for Constitution metadata."""

    def test_article_field_populated_not_section(self):
        payload = _normalize_payload(
            "Article 21 protects life and personal liberty",
            {
                "source": "CONSTITUTION.pdf",
                "act": "Constitution of India",
                "article": "21",
                "status": "active",
            },
        )
        assert payload["article"] == "21"
        assert payload["section"] == ""

    def test_both_article_and_section_preserved(self):
        payload = _normalize_payload(
            "Some legal text",
            {
                "source": "CONSTITUTION.pdf",
                "act": "Constitution of India",
                "article": "21",
                "section": "",
                "status": "active",
            },
        )
        assert payload["article"] == "21"
        assert payload["section"] == ""

    def test_section_not_copied_to_article_for_statute(self):
        payload = _normalize_payload(
            "Section 64 of BNS defines punishment",
            {
                "source": "BNS.pdf",
                "act": "Bharatiya Nyaya Sanhita",
                "section": "64",
                "status": "active",
            },
        )
        assert payload["section"] == "64"
        assert payload["article"] == ""


class TestReferenceExtraction:
    """Query reference extraction distinguishes section vs article."""

    def test_article_extraction_from_query(self):
        sections, articles = _extract_query_references("What is Article 21?")
        assert articles == ["21"]
        assert sections == []

    def test_section_extraction_from_query(self):
        sections, articles = _extract_query_references("Explain Section 64 of BNS")
        assert "64" in sections
        assert articles == []

    def test_combined_references(self):
        sections, articles = _extract_query_references("Article 14 and Section 64")
        assert "14" in articles
        assert "64" in sections

    def test_contains_reference_article(self):
        assert _contains_reference("Article 21 protects life", "Article", "21")
        assert not _contains_reference("Article 22 protects rights", "Article", "21")

    def test_contains_reference_section(self):
        assert _contains_reference("Section 64 deals with punishment", "Section", "64")
        assert not _contains_reference("Section 65 deals with punishment", "Section", "64")
