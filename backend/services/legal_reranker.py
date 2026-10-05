"""Legal-domain reranker abstraction for Vidhoor Legal Copilot.

Provides lazy BGE-reranker-v2-m3 initialization and reranking utilities
used by the Qdrant retrieval pipeline.

This module intentionally does NOT import or initialize the reranker model
at module import time, because the OCI production server has limited RAM
and not all deployments require reranking immediately.

Environment variables:
    QDRANT_ENABLE_RERANKER: Enable/disable reranker (default: true)
    RERANKER_MODEL: Model name (default: BAAI/bge-reranker-v2-m3)
    RERANKER_DEVICE: Device to use, cpu or cuda (default: cpu)
    RERANKER_USE_FP16: Use FP16 on GPU (default: false)
    RERANKER_CANDIDATE_LIMIT: Max candidates to rerank (default: 20)
    RERANKER_FINAL_LIMIT: Max results after reranking (default: 8)
    RERANKER_BATCH_SIZE: Encoding batch size (default: 4)
    RERANKER_MAX_LENGTH: Max token length (default: 1024)
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_reranker_model: Any = None


def _resolve_reranker_env() -> dict[str, Any]:
    """Return reranker configuration from environment variables."""
    device = os.environ.get("RERANKER_DEVICE", "cpu").strip().lower()
    use_fp16_raw = os.environ.get("RERANKER_USE_FP16", "false").strip().lower()
    use_fp16 = use_fp16_raw not in {"0", "false", "no"}
    if device == "cpu":
        use_fp16 = False

    return {
        "model_name": os.environ.get("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3").strip()
        or "BAAI/bge-reranker-v2-m3",
        "device": device,
        "use_fp16": use_fp16,
        "enabled": os.environ.get("QDRANT_ENABLE_RERANKER", "true").strip().lower()
        not in {"0", "false", "no"},
        "candidate_limit": int(os.environ.get("RERANKER_CANDIDATE_LIMIT", "20") or "20"),
        "final_limit": int(os.environ.get("RERANKER_FINAL_LIMIT", "8") or "8"),
        "batch_size": int(os.environ.get("RERANKER_BATCH_SIZE", "4") or "4"),
        "max_length": int(os.environ.get("RERANKER_MAX_LENGTH", "1024") or "1024"),
    }


def _lazy_import_flag_reranker() -> Any:
    """Import FlagReranker directly to avoid heavy top-level dependency chains."""
    try:
        from FlagEmbedding import FlagReranker
        return FlagReranker
    except Exception as exc:
        raise ImportError(
            "FlagEmbedding FlagReranker is required for BGE reranking. "
            "Install with: pip install FlagEmbedding"
        ) from exc


def get_reranker_model(model_name: str | None = None) -> Any:
    """Lazy-load and return the BGE reranker model.

    Only loads the model on first call. Subsequent calls return the cached
    instance. Raises a clear error if the model cannot be loaded so that the
    Qdrant pipeline fails explicitly rather than silently degrading.
    """
    global _reranker_model
    if _reranker_model is None:
        env = _resolve_reranker_env()
        resolved_model = model_name or env["model_name"]

        FlagReranker = _lazy_import_flag_reranker()

        logger.info(
            "Loading BGE reranker model: %s (device=%s, fp16=%s, batch=%d, max_length=%d)",
            resolved_model,
            env["device"],
            env["use_fp16"],
            env["batch_size"],
            env["max_length"],
        )
        try:
            _reranker_model = FlagReranker(
                resolved_model,
                use_fp16=env["use_fp16"],
                device=env["device"],
            )
            logger.info("BGE reranker model loaded successfully.")
        except Exception as exc:
            logger.exception("Failed to load BGE reranker model '%s'", resolved_model)
            raise RuntimeError(
                f"Unable to load reranker model '{resolved_model}'. "
                "If memory allocation fails, try reducing RERANKER_BATCH_SIZE "
                "or RERANKER_MAX_LENGTH."
            ) from exc
    return _reranker_model


def is_reranker_enabled() -> bool:
    """Return True if reranker is enabled via environment configuration."""
    env = _resolve_reranker_env()
    return env["enabled"]


def rerank_candidates(
    query: str,
    candidates: list[dict[str, Any]],
    text_key: str = "snippet",
    score_key: str = "reranker_score",
    retrieval_score_key: str = "retrieval_score",
    candidate_limit: int | None = None,
    final_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Rerank retrieval candidates using BGE reranker.

    Args:
        query: The original query text.
        candidates: List of candidate dicts with at least `text_key` content.
        text_key: Key in candidate dict containing document text.
        score_key: Key to store reranker score in candidate dict.
        retrieval_score_key: Key to preserve original retrieval score.
        candidate_limit: Max candidates to rerank (default from env).
        final_limit: Max results to return (default from env).

    Returns:
        Reranked and truncated list of candidates.
    """
    if not candidates:
        return []

    env = _resolve_reranker_env()
    if not env["enabled"]:
        return candidates[: final_limit or env["final_limit"]]

    effective_candidate_limit = candidate_limit or env["candidate_limit"]
    effective_final_limit = final_limit or env["final_limit"]

    input_candidates = candidates[:effective_candidate_limit]
    if len(input_candidates) < 2:
        for item in input_candidates:
            item[retrieval_score_key] = item.get("confidence", 0.0)
            item[score_key] = item.get("confidence", 0.0)
        return input_candidates[:effective_final_limit]

    try:
        model = get_reranker_model()
    except Exception as exc:
        logger.warning("Reranker unavailable, returning original ranking: %s", exc)
        for item in input_candidates:
            item[retrieval_score_key] = item.get("confidence", 0.0)
            item[score_key] = item.get("confidence", 0.0)
        return input_candidates[:effective_final_limit]

    try:
        pairs = [
            (query, str(item.get(text_key, "") or ""))
            for item in input_candidates
        ]
        env = _resolve_reranker_env()
        scores = model.compute_score(
            pairs,
            normalize=True,
            batch_size=env["batch_size"],
        )
        if not isinstance(scores, list):
            try:
                scores = list(scores)
            except TypeError:
                scores = [float(scores)]

        for item, score in zip(input_candidates, scores):
            item[retrieval_score_key] = item.get("confidence", 0.0)
            item[score_key] = float(score)
            item["confidence"] = max(0.0, min(1.0, float(score)))
    except Exception as exc:
        logger.warning("Reranking failed, returning original ranking: %s", exc)
        for item in input_candidates:
            item[retrieval_score_key] = item.get("confidence", 0.0)
            item[score_key] = item.get("confidence", 0.0)
        return input_candidates[:effective_final_limit]

    input_candidates.sort(key=lambda item: item.get(score_key, 0.0), reverse=True)
    return input_candidates[:effective_final_limit]
