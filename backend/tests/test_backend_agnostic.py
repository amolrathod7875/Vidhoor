"""Tests for backend-agnostic AgenticRagRunner (Phase 3)."""

from __future__ import annotations

import os
import sys
from typing import Any
from unittest import mock

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from agentic_rag import AgenticRagRunner, AgenticRagConfig, AgenticRagHelpers


class MockRetrievalManager:
    def __init__(self):
        self.calls = []

    def retrieve_context_with_metadata(
        self,
        query_string: str,
        filter_status: str = "active",
        filter_act: str | None = None,
    ) -> dict[str, list[Any]]:
        self.calls.append((query_string, filter_status, filter_act))
        return {
            "documents": ["Section 64 of BNS defines punishment."],
            "citations": [
                {
                    "doc_id": "bns",
                    "title": "Bharatiya Nyaya Sanhita",
                    "source": "BNS.pdf",
                    "source_url": "",
                    "section": "64",
                    "doc_type": "statute",
                    "case_name": "",
                    "citation_text": "",
                    "court": "",
                    "year": None,
                    "jurisdiction": "",
                    "bench": "",
                    "topic": "",
                    "precedent_rank": None,
                    "page": None,
                    "snippet": "Section 64 of BNS defines punishment.",
                    "confidence": 0.95,
                    "last_updated": "",
                }
            ],
        }


class Citation:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class MockLLMEngine:
    def llm(self):
        m = mock.Mock()
        m.invoke.return_value = '{"status": "sufficient", "final_answer": "Section 64 of BNS defines punishment."}'
        original_invoke = m.invoke

        def _flexible_invoke(*args, **kwargs):
            return original_invoke(*args, **kwargs)

        m.invoke = _flexible_invoke
        return m

    def _enforce_subheading_bullets(self, text):
        return text

    def _bold_legal_labels(self, text):
        return text

    def _normalize_summary_table(self, text):
        return text


def _make_runner(retrieval_manager: Any) -> AgenticRagRunner:
    helpers = AgenticRagHelpers(
        infer_act_filters=lambda q: ["Bharatiya Nyaya Sanhita"],
        extract_requested_references=lambda q: ["64"],
        extract_legal_targets=lambda q: [{"act": "", "reference_type": "section", "reference": "64"}],
        citation_matches_allowed_acts=lambda c, f: True,
        citation_matches_requested_references=lambda c, r: True,
        format_citation_context=lambda c: getattr(c, "snippet", ""),
        normalize_citation_links=lambda c, r: c,
        citation_factory=lambda item: Citation(**item),
    )
    return AgenticRagRunner(
        llm_engine=MockLLMEngine(),
        retrieval_manager=retrieval_manager,
        helpers=helpers,
        config=AgenticRagConfig(use_router_llm=False),
    )


class TestBackendAgnosticRunner:
    def test_accepts_retrieval_manager_parameter(self):
        manager = MockRetrievalManager()
        runner = _make_runner(manager)
        assert runner._retrieval_manager is manager

    def test_retrieval_manager_attribute_name(self):
        manager = MockRetrievalManager()
        runner = _make_runner(manager)
        assert hasattr(runner, "_retrieval_manager")
        assert not hasattr(runner, "_chroma_manager")
        assert runner._retrieval_manager is manager
