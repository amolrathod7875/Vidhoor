"""Legal-domain embedding abstraction for Vidhoor Legal Copilot.

Provides lazy BGE-M3 initialization and utilities for dense and sparse
embedding generation used by the Qdrant retrieval pipeline.

This module intentionally does NOT import or initialize BGE-M3 at module
import time, because the OCI production server has limited RAM and not all
deployments require embeddings immediately.

Environment variables:
    BGE_MODEL: Model name (default: BAAI/bge-m3)
    BGE_DEVICE: Device to use, cpu or cuda (default: cpu)
    BGE_USE_FP16: Use FP16 on GPU (default: false)
    BGE_BATCH_SIZE: Encoding batch size (default: 2)
    BGE_MAX_LENGTH: Max token length (default: 1024)
"""

from __future__ import annotations

import logging
import os
from importlib import import_module
from typing import Any

logger = logging.getLogger(__name__)

# Lazy singletons
_embedding_model: Any = None


def _resolve_bge_env() -> dict[str, Any]:
    """Return BGE configuration from environment variables."""
    device = os.environ.get("BGE_DEVICE", "cpu").strip().lower()
    use_fp16_raw = os.environ.get("BGE_USE_FP16", "false").strip().lower()
    use_fp16 = use_fp16_raw not in {"0", "false", "no"}
    if device == "cpu":
        use_fp16 = False

    return {
        "model_name": os.environ.get("BGE_MODEL", "BAAI/bge-m3").strip() or "BAAI/bge-m3",
        "device": device,
        "use_fp16": use_fp16,
        "batch_size": int(os.environ.get("BGE_BATCH_SIZE", "2") or "2"),
        "max_length": int(os.environ.get("BGE_MAX_LENGTH", "1024") or "1024"),
    }


def _lazy_import_m3_embedder() -> Any:
    """Import M3Embedder directly to avoid top-level pyarrow import issues."""
    try:
        module = import_module("FlagEmbedding.inference.embedder.encoder_only.m3")
        return getattr(module, "M3Embedder", None)
    except Exception as exc:
        raise ImportError(
            "FlagEmbedding M3Embedder is required for BGE-M3 embeddings. "
            "Install with: pip install FlagEmbedding"
        ) from exc


def get_embedding_model(model_name: str | None = None) -> Any:
    """Lazy-load and return the BGE-M3 model via FlagEmbedding M3Embedder.

    Only loads the model on first call. Subsequent calls return the cached
    instance. Raises a clear error if the model cannot be loaded so that the
    Qdrant pipeline fails explicitly rather than silently degrading.
    """
    global _embedding_model
    if _embedding_model is None:
        env = _resolve_bge_env()
        resolved_model = model_name or env["model_name"]

        M3Embedder = _lazy_import_m3_embedder()
        if M3Embedder is None:
            raise ImportError("FlagEmbedding M3Embedder is not available.")

        logger.info(
            "Loading BGE-M3 model: %s (device=%s, fp16=%s, batch=%d, max_length=%d)",
            resolved_model,
            env["device"],
            env["use_fp16"],
            env["batch_size"],
            env["max_length"],
        )
        try:
            _embedding_model = M3Embedder(
                resolved_model,
                use_fp16=env["use_fp16"],
                device=env["device"],
            )
            logger.info("BGE-M3 model loaded successfully.")
        except Exception as exc:
            logger.exception("Failed to load BGE-M3 model '%s'", resolved_model)
            raise RuntimeError(
                f"Unable to load embedding model '{resolved_model}' for the Qdrant pipeline. "
                "If memory allocation fails, try reducing BGE_MAX_LENGTH or BGE_BATCH_SIZE."
            ) from exc
    return _embedding_model


def get_dense_embedding_dimension(model_name: str | None = None) -> int:
    """Return the dense vector dimension for BGE-M3 by probing the model via combined inference."""
    combined = encode_dense_sparse(["dimension probe"], model_name=model_name)
    dense_vecs = combined.get("dense", [])
    if not dense_vecs:
        raise RuntimeError("Unable to read dense vectors from BGE-M3 probe output")
    return len(dense_vecs[0])


def encode_dense_sparse(
    texts: list[str],
    model_name: str | None = None,
) -> dict[str, Any]:
    """Encode texts into BOTH dense and sparse vectors using a SINGLE BGE-M3 inference call.

    This is the canonical embedding operation. It avoids the buggy
    return_dense=False / return_sparse=True path and eliminates redundant
    double inference.

    Returns:
        A dict with keys:
            "dense": list[list[float]] — dense vectors, one per text
            "sparse": list[dict[str, Any]] — sparse lexical vectors, one per text
    """
    model = get_embedding_model(model_name)
    try:
        result = model.encode(
            texts,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )
    except Exception as exc:
        logger.exception("BGE-M3 combined dense+sparse encoding failed")
        raise RuntimeError("Failed to generate dense+sparse embeddings with BGE-M3") from exc

    dense_vecs = getattr(result, "dense_vecs", None)
    if dense_vecs is None and isinstance(result, dict):
        dense_vecs = result.get("dense_vecs")
    if dense_vecs is None:
        raise RuntimeError("BGE-M3 combined encoding returned no dense_vecs")

    lexical_weights = getattr(result, "lexical_weights", None)
    if lexical_weights is None and isinstance(result, dict):
        lexical_weights = result.get("lexical_weights", [])
    if lexical_weights is None:
        lexical_weights = [None] * len(texts)

    dense_list = dense_vecs.tolist() if hasattr(dense_vecs, "tolist") else [list(v) for v in dense_vecs]

    sparse_list: list[dict[str, Any]] = []
    for item in lexical_weights:
        if item is None:
            sparse_list.append({"indices": [], "values": []})
            continue

        indices = []
        values = []
        if hasattr(item, "items"):
            raw_items = list(item.items())
            indices = [int(idx) for idx, _ in raw_items]
            values = [float(v) for _, v in raw_items]
        elif hasattr(item, "indices") and hasattr(item, "values"):
            indices = list(item.indices)
            values = list(item.values)
        elif isinstance(item, dict):
            raw_indices = item.get("indices", [])
            raw_values = item.get("values", [])
            indices = [int(idx) for idx in raw_indices]
            values = [float(v) for v in raw_values]

        if len(indices) != len(values):
            length = min(len(indices), len(values))
            indices = indices[:length]
            values = values[:length]

        sparse_list.append({"indices": indices, "values": values})

    return {"dense": dense_list, "sparse": sparse_list}


def encode_dense(
    texts: list[str],
    model_name: str | None = None,
    normalize: bool = True,
) -> list[list[float]]:
    """Encode texts into dense vectors using BGE-M3.

    Uses a single combined dense+sparse inference call internally for efficiency.
    """
    combined = encode_dense_sparse(texts, model_name=model_name)
    return combined["dense"]


def encode_sparse(
    texts: list[str],
    model_name: str | None = None,
) -> list[dict[str, Any]]:
    """Encode texts into sparse lexical vectors using BGE-M3.

    Uses a single combined dense+sparse inference call internally for efficiency.
    The buggy return_dense=False / return_sparse=True path is never used.
    """
    combined = encode_dense_sparse(texts, model_name=model_name)
    return combined["sparse"]
