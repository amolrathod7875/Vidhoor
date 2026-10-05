"""Tests for legal reranker service (Phase 3)."""

from __future__ import annotations

import os
import sys
from typing import Any
from unittest import mock

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services.legal_reranker import (
    _resolve_reranker_env,
    get_reranker_model,
    is_reranker_enabled,
    rerank_candidates,
)


# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------

class TestRerankerEnv:
    def test_default_enabled(self):
        os.environ.pop("QDRANT_ENABLE_RERANKER", None)
        assert is_reranker_enabled() is True

    def test_disabled_via_env(self):
        os.environ["QDRANT_ENABLE_RERANKER"] = "false"
        try:
            assert is_reranker_enabled() is False
        finally:
            os.environ.pop("QDRANT_ENABLE_RERANKER", None)

    def test_default_model_name(self):
        os.environ.pop("RERANKER_MODEL", None)
        env = _resolve_reranker_env()
        assert env["model_name"] == "BAAI/bge-reranker-v2-m3"

    def test_custom_model_name(self):
        os.environ["RERANKER_MODEL"] = "custom-reranker"
        try:
            env = _resolve_reranker_env()
            assert env["model_name"] == "custom-reranker"
        finally:
            os.environ.pop("RERANKER_MODEL", None)

    def test_default_device_cpu(self):
        os.environ.pop("RERANKER_DEVICE", None)
        env = _resolve_reranker_env()
        assert env["device"] == "cpu"
        assert env["use_fp16"] is False

    def test_cuda_device_disables_fp16_on_cpu(self):
        os.environ["RERANKER_DEVICE"] = "cpu"
        os.environ["RERANKER_USE_FP16"] = "true"
        try:
            env = _resolve_reranker_env()
            assert env["device"] == "cpu"
            assert env["use_fp16"] is False
        finally:
            os.environ.pop("RERANKER_DEVICE", None)
            os.environ.pop("RERANKER_USE_FP16", None)

    def test_default_limits(self):
        os.environ.pop("RERANKER_CANDIDATE_LIMIT", None)
        os.environ.pop("RERANKER_FINAL_LIMIT", None)
        env = _resolve_reranker_env()
        assert env["candidate_limit"] == 20
        assert env["final_limit"] == 8


# ---------------------------------------------------------------------------
# rerank_candidates behavior
# ---------------------------------------------------------------------------

class TestRerankCandidates:
    def test_empty_candidates_returns_empty(self):
        os.environ["QDRANT_ENABLE_RERANKER"] = "true"
        try:
            result = rerank_candidates("test query", [])
            assert result == []
        finally:
            os.environ.pop("QDRANT_ENABLE_RERANKER", None)

    def test_disabled_returns_original_limited(self):
        os.environ["QDRANT_ENABLE_RERANKER"] = "false"
        try:
            candidates = [
                {"snippet": "doc A", "confidence": 0.9},
                {"snippet": "doc B", "confidence": 0.8},
            ]
            result = rerank_candidates("test query", candidates, final_limit=1)
            assert len(result) == 1
            assert result[0]["snippet"] == "doc A"
        finally:
            os.environ.pop("QDRANT_ENABLE_RERANKER", None)

    def test_single_candidate_passthrough(self):
        os.environ["QDRANT_ENABLE_RERANKER"] = "true"
        try:
            candidates = [{"snippet": "only doc", "confidence": 0.5}]
            result = rerank_candidates("test query", candidates)
            assert len(result) == 1
            assert result[0]["retrieval_score"] == 0.5
            assert result[0]["reranker_score"] == 0.5
        finally:
            os.environ.pop("QDRANT_ENABLE_RERANKER", None)

    @mock.patch("services.legal_reranker.get_reranker_model")
    def test_reranker_sorts_by_score(self, mock_get_model):
        mock_model = mock.Mock()
        mock_model.compute_score.return_value = [0.3, 0.9]
        mock_get_model.return_value = mock_model

        os.environ["QDRANT_ENABLE_RERANKER"] = "true"
        try:
            candidates = [
                {"snippet": "doc A", "confidence": 0.5},
                {"snippet": "doc B", "confidence": 0.6},
            ]
            result = rerank_candidates("test query", candidates)
            assert len(result) == 2
            assert result[0]["snippet"] == "doc B"
            assert result[0]["reranker_score"] == 0.9
            assert result[1]["snippet"] == "doc A"
            assert result[1]["reranker_score"] == 0.3
        finally:
            os.environ.pop("QDRANT_ENABLE_RERANKER", None)
            import services.legal_reranker as reranker_mod
            reranker_mod._reranker_model = None

    @mock.patch("services.legal_reranker.get_reranker_model", side_effect=RuntimeError("OOM"))
    def test_reranker_failure_falls_back(self, mock_get_model):
        os.environ["QDRANT_ENABLE_RERANKER"] = "true"
        try:
            candidates = [
                {"snippet": "doc A", "confidence": 0.9},
                {"snippet": "doc B", "confidence": 0.8},
            ]
            result = rerank_candidates("test query", candidates, final_limit=1)
            assert len(result) == 1
            assert result[0]["snippet"] == "doc A"
            assert result[0]["reranker_score"] == result[0]["confidence"]
        finally:
            os.environ.pop("QDRANT_ENABLE_RERANKER", None)
            import services.legal_reranker as reranker_mod
            reranker_mod._reranker_model = None
