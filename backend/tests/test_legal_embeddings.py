"""Tests for legal embeddings service (Phase 5 dense+sparse combined inference)."""

from __future__ import annotations

import os
import sys
from typing import Any
from unittest import mock

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from services.legal_embeddings import (
    _resolve_bge_env,
    encode_dense,
    encode_dense_sparse,
    encode_sparse,
    get_dense_embedding_dimension,
    get_embedding_model,
)


class TestBgeEnv:
    def test_default_device_cpu(self):
        os.environ.pop("BGE_DEVICE", None)
        env = _resolve_bge_env()
        assert env["device"] == "cpu"
        assert env["use_fp16"] is False

    def test_cpu_disables_fp16(self):
        os.environ["BGE_DEVICE"] = "cpu"
        os.environ["BGE_USE_FP16"] = "true"
        try:
            env = _resolve_bge_env()
            assert env["device"] == "cpu"
            assert env["use_fp16"] is False
        finally:
            os.environ.pop("BGE_DEVICE", None)
            os.environ.pop("BGE_USE_FP16", None)

    def test_default_model_name(self):
        os.environ.pop("BGE_MODEL", None)
        env = _resolve_bge_env()
        assert env["model_name"] == "BAAI/bge-m3"

    def test_custom_batch_size(self):
        os.environ["BGE_BATCH_SIZE"] = "8"
        try:
            env = _resolve_bge_env()
            assert env["batch_size"] == 8
        finally:
            os.environ.pop("BGE_BATCH_SIZE", None)


class TestEncodeDenseSparse:
    @mock.patch("services.legal_embeddings.get_embedding_model")
    def test_single_inference_call(self, mock_get_model):
        mock_model = mock.Mock()
        mock_result = mock.Mock()
        mock_result.dense_vecs = [[0.1] * 1024, [0.2] * 1024]
        mock_result.lexical_weights = [
            {1: 0.5, 5: 0.3},
            {2: 0.7, 8: 0.1},
        ]
        mock_model.encode.return_value = mock_result
        mock_get_model.return_value = mock_model

        result = encode_dense_sparse(["text one", "text two"])

        # Verify only ONE inference call was made
        mock_model.encode.assert_called_once_with(
            ["text one", "text two"],
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )

        assert len(result["dense"]) == 2
        assert len(result["dense"][0]) == 1024
        assert len(result["sparse"]) == 2
        assert result["sparse"][0]["indices"] == [1, 5]
        assert result["sparse"][0]["values"] == [0.5, 0.3]

    @mock.patch("services.legal_embeddings.get_embedding_model")
    def test_returns_both_dense_and_sparse(self, mock_get_model):
        mock_model = mock.Mock()
        mock_result = mock.Mock()
        mock_result.dense_vecs = [[0.1] * 1024]
        mock_result.lexical_weights = [{3: 0.9}]
        mock_model.encode.return_value = mock_result
        mock_get_model.return_value = mock_model

        result = encode_dense_sparse(["single text"])
        assert "dense" in result
        assert "sparse" in result
        assert len(result["dense"]) == 1
        assert len(result["sparse"]) == 1

    @mock.patch("services.legal_embeddings.get_embedding_model")
    def test_dense_dimension_1024(self, mock_get_model):
        mock_model = mock.Mock()
        mock_result = mock.Mock()
        mock_result.dense_vecs = [[0.1] * 1024, [0.2] * 1024]
        mock_result.lexical_weights = [{}, {}]
        mock_model.encode.return_value = mock_result
        mock_get_model.return_value = mock_model

        result = encode_dense_sparse(["a", "b"])
        assert all(len(v) == 1024 for v in result["dense"])

    @mock.patch("services.legal_embeddings.get_embedding_model")
    def test_sparse_indices_and_values_exist(self, mock_get_model):
        mock_model = mock.Mock()
        mock_result = mock.Mock()
        mock_result.dense_vecs = [[0.1] * 1024]
        mock_result.lexical_weights = [{10: 0.5, 20: 0.3}]
        mock_model.encode.return_value = mock_result
        mock_get_model.return_value = mock_model

        result = encode_dense_sparse(["text"])
        sparse = result["sparse"][0]
        assert "indices" in sparse
        assert "values" in sparse
        assert len(sparse["indices"]) == len(sparse["values"])
        assert sparse["indices"] == [10, 20]


class TestEncodeDenseAndSparseDelegation:
    @mock.patch("services.legal_embeddings.encode_dense_sparse")
    def test_encode_dense_delegates(self, mock_combined):
        mock_combined.return_value = {"dense": [[0.1] * 1024], "sparse": [{}]}
        result = encode_dense(["text"])
        mock_combined.assert_called_once_with(["text"], model_name=None)
        assert result == [[0.1] * 1024]

    @mock.patch("services.legal_embeddings.encode_dense_sparse")
    def test_encode_sparse_delegates(self, mock_combined):
        mock_combined.return_value = {"dense": [[]], "sparse": [{"indices": [1], "values": [0.5]}]}
        result = encode_sparse(["text"])
        mock_combined.assert_called_once_with(["text"], model_name=None)
        assert result == [{"indices": [1], "values": [0.5]}]


class TestGetDenseEmbeddingDimension:
    @mock.patch("services.legal_embeddings.encode_dense_sparse")
    def test_returns_dimension(self, mock_combined):
        mock_combined.return_value = {"dense": [[0.1] * 1024], "sparse": [{}]}
        dim = get_dense_embedding_dimension()
        assert dim == 1024
