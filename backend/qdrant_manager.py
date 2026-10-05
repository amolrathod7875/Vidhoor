"""Qdrant vector database manager for Vidhoor Legal Copilot.

This module provides the Qdrant backend infrastructure for the hybrid
dense/sparse retrieval pipeline using BGE-M3 embeddings.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import time
from importlib import import_module
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# grpc stub guard
# ---------------------------------------------------------------------------
# grpc_stubs.py (imported by main.py / chroma_manager.py on Windows) may
# replace the real grpc module with a lightweight fake. qdrant_client
# requires the real grpc, so we save and restore it around qdrant imports.
_real_grpc = sys.modules.get("grpc")

# ---------------------------------------------------------------------------
# Optional dependency guards
# ---------------------------------------------------------------------------
_qdrant_client = None
_sentence_transformers = None
_qdrant_models = None


def _restore_real_grpc() -> None:
    """Restore the real grpc module if it was stubbed by grpc_stubs.py."""
    fake_grpc = sys.modules.get("grpc")
    if fake_grpc is not None and getattr(fake_grpc, "__file__", None) is None:
        sys.modules.pop("grpc", None)
        try:
            real_grpc = import_module("grpc")
            sys.modules["grpc"] = real_grpc
        except ImportError:
            if fake_grpc is not None:
                sys.modules["grpc"] = fake_grpc


def _lazy_import_qdrant_client() -> Any:
    global _qdrant_client
    if _qdrant_client is None:
        try:
            _restore_real_grpc()
            _qdrant_client = import_module("qdrant_client")
        except ImportError as exc:
            raise ImportError(
                "qdrant-client is required for the Qdrant vector backend. "
                "Install it with: pip install qdrant-client"
            ) from exc
    return _qdrant_client


def _lazy_import_qdrant_models() -> Any:
    global _qdrant_models
    if _qdrant_models is None:
        try:
            _restore_real_grpc()
            _qdrant_models = import_module("qdrant_client.models")
        except ImportError as exc:
            raise ImportError(
                "qdrant-client models are required for Qdrant collection setup."
            ) from exc
    return _qdrant_models


# ---------------------------------------------------------------------------
# Payload schema definition
# ---------------------------------------------------------------------------
_QDRANT_PAYLOAD_SCHEMA = {
    "text": {"type": "text"},
    "status": {"type": "keyword"},
    "act": {"type": "keyword"},
    "section": {"type": "keyword"},
    "article": {"type": "keyword"},
    "source": {"type": "keyword"},
    "source_url": {"type": "keyword"},
    "resource_type": {"type": "keyword"},
    "doc_type": {"type": "keyword"},
    "page": {"type": "integer"},
    "case_name": {"type": "keyword"},
    "citation_text": {"type": "keyword"},
    "court": {"type": "keyword"},
    "year": {"type": "integer"},
    "jurisdiction": {"type": "keyword"},
    "bench": {"type": "keyword"},
    "topic": {"type": "keyword"},
    "last_updated": {"type": "keyword"},
}

_QDRANT_INDEXED_FIELDS = [
    "status",
    "act",
    "section",
    "article",
    "doc_type",
    "court",
    "year",
    "jurisdiction",
]

# ---------------------------------------------------------------------------
# Retrieval configuration from environment
# ---------------------------------------------------------------------------

def _get_retrieval_env() -> dict[str, Any]:
    """Resolve Qdrant retrieval tuning from environment variables."""
    return {
        "dense_prefetch_limit": int(os.environ.get("QDRANT_DENSE_PREFETCH_LIMIT", "30") or "30"),
        "sparse_prefetch_limit": int(os.environ.get("QDRANT_SPARSE_PREFETCH_LIMIT", "30") or "30"),
        "hybrid_limit": int(os.environ.get("QDRANT_HYBRID_LIMIT", "20") or "20"),
        "final_limit": int(os.environ.get("QDRANT_FINAL_LIMIT", "8") or "8"),
    }


# ---------------------------------------------------------------------------
# Deterministic ID generation
# ---------------------------------------------------------------------------
def _build_deterministic_point_id(
    chunk_text: str,
    metadata: dict[str, Any],
    index: int,
) -> str:
    """Create a stable, deterministic point ID from chunk content and metadata.

    Produces a 32-character hex string derived from SHA-256, suitable as a
    Qdrant point ID and deterministic across re-ingest/upsert operations.
    """
    payload = {
        "text": (chunk_text or "").strip(),
        "source": str(metadata.get("source") or ""),
        "act": str(metadata.get("act") or ""),
        "section": str(metadata.get("section") or metadata.get("article") or ""),
        "index": index,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return digest[:32]


# ---------------------------------------------------------------------------
# Payload normalization
# ---------------------------------------------------------------------------
def _normalize_payload(
    chunk_text: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Normalize chunk metadata into a Qdrant payload with safe defaults."""
    if not isinstance(metadata, dict):
        metadata = {}

    source_name = str(metadata.get("source") or "").strip()
    act_name = str(metadata.get("act") or "").strip()
    section_value = str(metadata.get("section") or "").strip()
    article_value = str(metadata.get("article") or "").strip()
    doc_type_value = str(metadata.get("doc_type") or "").strip().lower()
    if doc_type_value == "case_law" and _looks_like_statute_source(source_name, act_name):
        doc_type_value = "statute"

    source_url = str(
        metadata.get("source_url")
        or metadata.get("doc_url")
        or metadata.get("source_uri")
        or ""
    ).strip()

    page_value = metadata.get("page") or metadata.get("page_number") or metadata.get("page_no")
    year_value = metadata.get("year")
    if year_value is not None:
        try:
            year_value = int(year_value)
        except (TypeError, ValueError):
            year_value = None

    last_updated = str(
        metadata.get("last_updated")
        or metadata.get("updated_at")
        or metadata.get("effective_date")
        or metadata.get("ingested_at")
        or ""
    ).strip()

    payload: dict[str, Any] = {
        "text": str(chunk_text or "").strip(),
        "status": str(metadata.get("status") or "active"),
        "act": act_name,
        "section": section_value,
        "article": article_value,
        "source": source_name,
        "source_url": source_url,
        "resource_type": str(metadata.get("resource_type") or ""),
        "doc_type": doc_type_value,
        "page": page_value,
        "case_name": str(metadata.get("case_name") or ""),
        "citation_text": str(metadata.get("citation_text") or ""),
        "court": str(metadata.get("court") or ""),
        "year": year_value,
        "jurisdiction": str(metadata.get("jurisdiction") or ""),
        "bench": str(metadata.get("bench") or ""),
        "topic": str(metadata.get("topic") or ""),
        "last_updated": last_updated,
    }
    return payload


def _looks_like_statute_source(source_name: str, act_name: str) -> bool:
    """Detect statute sources when metadata doc_type may be stale."""
    haystack = f"{source_name} {act_name}"
    normalized = re.sub(r"[^a-z0-9]", "", haystack.lower())
    tokens = set(re.findall(r"[a-z0-9]+", haystack.lower()))

    short_aliases = {"bns", "bnss", "bsa", "ipc", "constitutionofindia", "constitution"}
    if any(alias in tokens for alias in short_aliases):
        return True

    return any(
        alias in normalized
        for alias in (
            "bharatiyanyayasanhita",
            "bharatiyanagariksurakshasanhita",
            "bharatiyasakshyaadhiniyam",
            "constitutionofindia",
            "constitution",
            "informationtechnologyact",
            "itact",
            "itact2000",
        )
    )


# ---------------------------------------------------------------------------
# Qdrant Manager
# ---------------------------------------------------------------------------
class QdrantManager:
    """Manage Qdrant collection lifecycle, BGE-M3 embeddings, and hybrid retrieval."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6333,
        grpc_port: int = 6334,
        collection_name: str = "indian_law_v2",
        prefer_grpc: bool = True,
        embedding_model_name: str = "BAAI/bge-m3",
    ) -> None:
        """Initialize Qdrant client.

        Does NOT download BGE-M3 at import time, and does NOT auto-create
        the collection on initialization. Collection creation is deferred
        until ingestion time so that diagnostics and connection checks
        work on low-RAM environments where BGE-M3 cannot be loaded.
        """
        self.host = host
        self.port = port
        self.grpc_port = grpc_port
        self.collection_name = collection_name
        self.prefer_grpc = prefer_grpc
        self.embedding_model_name = embedding_model_name

        self._expected_dense_dim: Optional[int] = None

        self.client = self._create_client()
        logger.info(
            "Qdrant client initialized for %s:%s (gRPC=%s, collection=%s). "
            "Collection auto-creation deferred to ingestion time.",
            host,
            port,
            grpc_port,
            collection_name,
        )

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def _create_client(self) -> Any:
        qdrant_client = _lazy_import_qdrant_client()
        try:
            if self.prefer_grpc:
                return qdrant_client.QdrantClient(
                    host=self.host,
                    port=self.port,
                    grpc_port=self.grpc_port,
                    prefer_grpc=True,
                )
            return qdrant_client.QdrantClient(
                host=self.host,
                port=self.port,
            )
        except Exception as exc:
            logger.error("Failed to connect to Qdrant at %s:%s: %s", self.host, self.port, exc)
            raise RuntimeError(
                f"Unable to connect to Qdrant at {self.host}:{self.port}"
            ) from exc

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    def health_check(self) -> dict[str, Any]:
        """Return Qdrant server health info."""
        try:
            info = self.client.info()
            return {
                "status": "ok",
                "title": str(getattr(info, "title", "")),
                "version": str(getattr(info, "version", "")),
            }
        except Exception as exc:
            logger.warning("Qdrant health check failed: %s", exc)
            return {
                "status": "error",
                "error": str(exc),
            }

    def collection_exists(self) -> bool:
        """Return True if the configured collection exists."""
        try:
            collections = self.client.get_collections()
            names = [c.name for c in (collections.collections or [])]
            return self.collection_name in names
        except Exception as exc:
            logger.warning("Failed to list Qdrant collections: %s", exc)
            return False

    def collection_count(self) -> int:
        """Return point count for the configured collection, or 0 on error."""
        try:
            info = self.client.get_collection(self.collection_name)
            return int(getattr(info, "points_count", 0) or 0)
        except Exception as exc:
            logger.warning("Failed to get Qdrant collection count: %s", exc)
            return 0

    # ------------------------------------------------------------------
    # Collection setup
    # ------------------------------------------------------------------
    def ensure_collection(self) -> None:
        """Create or verify the Qdrant collection with dense + sparse vectors."""
        if self.collection_exists():
            logger.info("Qdrant collection '%s' already exists.", self.collection_name)
            self._validate_or_update_collection()
            return

        logger.info("Creating Qdrant collection '%s'.", self.collection_name)
        try:
            dense_dim = self._resolve_dense_dimension()
            qdrant_models = _lazy_import_qdrant_models()
            vectors_config: dict[str, Any] = {
                "dense": qdrant_models.VectorParams(
                    size=dense_dim,
                    distance=qdrant_models.Distance.COSINE,
                ),
            }
            sparse_vectors_config: dict[str, Any] = {
                "sparse": qdrant_models.SparseVectorParams(),
            }

            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=vectors_config,
                sparse_vectors_config=sparse_vectors_config,
            )
            self._create_payload_indexes()
            logger.info(
                "Qdrant collection '%s' created with dense_dim=%d.",
                self.collection_name,
                dense_dim,
            )
        except Exception as exc:
            logger.exception("Failed to create Qdrant collection '%s'", self.collection_name)
            raise RuntimeError(
                f"Unable to create Qdrant collection '{self.collection_name}'"
            ) from exc

    def _resolve_dense_dimension(self) -> int:
        """Return BGE-M3 dense dimension from the actual model via combined inference."""
        from services.legal_embeddings import encode_dense_sparse

        combined = encode_dense_sparse(["dimension probe"])
        dense_vecs = combined.get("dense", [])
        if not dense_vecs:
            raise RuntimeError("BGE-M3 probe returned no dense vectors")
        dim = len(dense_vecs[0]) if dense_vecs else 0
        self._expected_dense_dim = dim
        logger.info("BGE-M3 dense embedding dimension detected: %d", dim)
        return dim

    def _validate_or_update_collection(self) -> None:
        """Validate existing collection schema matches expected dense dimension."""
        try:
            info = self.client.get_collection(self.collection_name)
            dense_dim = self._resolve_dense_dimension()
            config = getattr(info, "config", None)
            if config is not None:
                params = getattr(config, "params", None)
                if params is not None:
                    vectors = getattr(params, "vectors", {})
                    existing_dense = getattr(vectors, "get", lambda k, d=None: getattr(vectors, k, d))("dense", {})
                    existing_size = getattr(existing_dense, "get", lambda k, d=None: getattr(existing_dense, k, d))("size")
                    if existing_size and int(existing_size) != dense_dim:
                        logger.warning(
                            "Qdrant collection dense dimension mismatch: existing=%s, expected=%s.",
                            existing_size,
                            dense_dim,
                        )
                    else:
                        logger.info(
                            "Qdrant collection dense dimension validated: %d.",
                            dense_dim,
                        )
                else:
                    logger.info("Qdrant collection config validated (no vector params to check).")
            else:
                logger.info("Qdrant collection config validated (no config object).")
        except Exception as exc:
            logger.warning("Qdrant collection validation skipped: %s", exc)

    def _create_payload_indexes(self) -> None:
        """Create payload indexes for common filter fields."""
        qdrant_models = _lazy_import_qdrant_models()

        for field_name in _QDRANT_INDEXED_FIELDS:
            try:
                field_schema = _QDRANT_PAYLOAD_SCHEMA.get(field_name, {"type": "keyword"})
                if field_schema["type"] == "keyword":
                    index = qdrant_models.PayloadSchemaType.KEYWORD
                else:
                    index = qdrant_models.PayloadSchemaType.INTEGER
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field_name,
                    field_schema=index,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to create Qdrant payload index for '%s': %s",
                    field_name,
                    exc,
                )

    # ------------------------------------------------------------------
    # Embedding model (lazy)
    # ------------------------------------------------------------------
    def get_embedding_model(self) -> Any:
        """Lazy-load and return the BGE-M3 model."""
        from services.legal_embeddings import get_embedding_model as _get_embedding_model
        return _get_embedding_model(self.embedding_model_name)

    # ------------------------------------------------------------------
    # Ingestion helpers
    # ------------------------------------------------------------------
    def upsert_points(
        self,
        dense_vectors: list[list[float]],
        sparse_vectors: Optional[list[dict[str, Any]]],
        payloads: list[dict[str, Any]],
        point_ids: Optional[list[str]] = None,
    ) -> int:
        """Upsert points into the Qdrant collection.

        For Phase 2, this is wired into the main ingestion pipeline.
        """
        if not dense_vectors or not payloads:
            return 0
        if len(dense_vectors) != len(payloads):
            raise ValueError("dense_vectors and payloads must have the same length")
        if sparse_vectors and len(sparse_vectors) != len(payloads):
            raise ValueError("sparse_vectors and payloads must have the same length")

        qdrant_client = _lazy_import_qdrant_client()
        qdrant_models = _lazy_import_qdrant_models()

        points: list[Any] = []
        for idx, (dense_vec, payload) in enumerate(zip(dense_vectors, payloads)):
            point_id = point_ids[idx] if point_ids and idx < len(point_ids) else _build_deterministic_point_id(
                payload.get("text", ""),
                {
                    "source": payload.get("source", ""),
                    "act": payload.get("act", ""),
                    "section": payload.get("section", ""),
                },
                idx,
            )
            vectors: dict[str, Any] = {"dense": dense_vec}
            if sparse_vectors and idx < len(sparse_vectors):
                sparse_vec = sparse_vectors[idx]
                if sparse_vec and (sparse_vec.get("indices") or sparse_vec.get("values")):
                    vectors["sparse"] = qdrant_models.SparseVector(**sparse_vec)
            points.append(
                qdrant_models.PointStruct(
                    id=point_id,
                    vector=vectors,
                    payload=payload,
                )
            )

        try:
            self.client.upsert(
                collection_name=self.collection_name,
                points=points,
            )
            return len(points)
        except Exception as exc:
            logger.exception("Qdrant upsert failed")
            raise RuntimeError("Failed to upsert points into Qdrant") from exc

    def upsert_legal_chunks(
        self,
        text_chunks: list[str],
        metadata_list: list[dict[str, Any]],
        batch_size: int | None = None,
    ) -> dict[str, Any]:
        """Encode and upsert legal chunks into Qdrant with dense+sparse vectors.

        Uses a single BGE-M3 inference call per batch (dense+sparse combined)
        to eliminate redundant inference and avoid the FlagEmbedding
        return_dense=False / return_sparse=True bug.

        Returns a summary dict with counts and timing.
        """
        from services.legal_embeddings import _resolve_bge_env, encode_dense_sparse

        env = _resolve_bge_env()
        resolved_batch_size = batch_size or env["batch_size"]
        total_chunks = len(text_chunks)
        if total_chunks == 0:
            return {"chunks_processed": 0, "batches": 0, "elapsed_seconds": 0.0}

        start_time = time.time()
        total_upserted = 0
        batch_num = 0

        for batch_start in range(0, total_chunks, resolved_batch_size):
            batch_end = min(batch_start + resolved_batch_size, total_chunks)
            batch_texts = text_chunks[batch_start:batch_end]
            batch_metadata = metadata_list[batch_start:batch_end]
            batch_num += 1

            logger.info(
                "Qdrant ingestion batch %d: chunks %d-%d of %d",
                batch_num,
                batch_start + 1,
                batch_end,
                total_chunks,
            )

            combined = encode_dense_sparse(batch_texts)
            dense_vectors = combined["dense"]
            sparse_vectors = combined["sparse"]
            payloads = [_normalize_payload(text, meta) for text, meta in zip(batch_texts, batch_metadata)]

            point_ids = [
                _build_deterministic_point_id(text, meta, batch_start + idx)
                for idx, (text, meta) in enumerate(zip(batch_texts, batch_metadata))
            ]

            upserted = self.upsert_points(
                dense_vectors=dense_vectors,
                sparse_vectors=sparse_vectors,
                payloads=payloads,
                point_ids=point_ids,
            )
            total_upserted += upserted

        elapsed = time.time() - start_time
        logger.info(
            "Qdrant ingestion complete: %d chunks in %d batches (%.2fs)",
            total_upserted,
            batch_num,
            elapsed,
        )
        return {
            "chunks_processed": total_upserted,
            "batches": batch_num,
            "elapsed_seconds": round(elapsed, 3),
        }

    # ------------------------------------------------------------------
    # Hybrid retrieval
    # ------------------------------------------------------------------
    def _build_qdrant_filter(
        self,
        filter_status: str = "active",
        filter_act: str | None = None,
        filter_section: str | None = None,
        filter_article: str | None = None,
        filter_doc_type: str | None = None,
        filter_court: str | None = None,
        filter_year: int | None = None,
        filter_jurisdiction: str | None = None,
    ) -> Any:
        """Build a Qdrant filter from structured legal metadata."""
        qdrant_models = _lazy_import_qdrant_models()
        conditions: list[Any] = [
            qdrant_models.FieldCondition(
                key="status",
                match=qdrant_models.MatchValue(value=filter_status),
            )
        ]

        if filter_act:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="act",
                    match=qdrant_models.MatchValue(value=filter_act),
                )
            )
        if filter_section:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="section",
                    match=qdrant_models.MatchValue(value=filter_section),
                )
            )
        if filter_article:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="article",
                    match=qdrant_models.MatchValue(value=filter_article),
                )
            )
        if filter_doc_type:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="doc_type",
                    match=qdrant_models.MatchValue(value=filter_doc_type),
                )
            )
        if filter_court:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="court",
                    match=qdrant_models.MatchValue(value=filter_court),
                )
            )
        if filter_year is not None:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="year",
                    match=qdrant_models.MatchValue(value=filter_year),
                )
            )
        if filter_jurisdiction:
            conditions.append(
                qdrant_models.FieldCondition(
                    key="jurisdiction",
                    match=qdrant_models.MatchValue(value=filter_jurisdiction),
                )
            )

        if len(conditions) == 1:
            return qdrant_models.Filter(must=conditions)
        return qdrant_models.Filter(must=conditions)

    def _encode_query(self, query_text: str) -> tuple[list[float], dict[str, Any]]:
        """Encode query into dense and sparse vectors using a SINGLE BGE-M3 inference call."""
        from services.legal_embeddings import encode_dense_sparse

        combined = encode_dense_sparse([query_text])
        dense = combined["dense"][0] if combined.get("dense") else []
        sparse = combined["sparse"][0] if combined.get("sparse") else {"indices": [], "values": []}
        return dense, sparse

    def _run_hybrid_query(
        self,
        query_text: str,
        query_filter: Any,
        dense_prefetch_limit: int,
        sparse_prefetch_limit: int,
        hybrid_limit: int,
        final_limit: int,
    ) -> list[dict[str, Any]]:
        """Execute native Qdrant hybrid query with dense+sparse prefetch + RRF."""
        qdrant_client = _lazy_import_qdrant_client()
        qdrant_models = _lazy_import_qdrant_models()

        dense_query, sparse_query = self._encode_query(query_text)

        dense_prefetch = qdrant_models.Prefetch(
            query=dense_query,
            using="dense",
            limit=dense_prefetch_limit,
            filter=query_filter,
        )
        sparse_prefetch = qdrant_models.Prefetch(
            query=qdrant_models.SparseVector(**sparse_query) if sparse_query.get("indices") else qdrant_models.SparseVector(indices=[], values=[]),
            using="sparse",
            limit=sparse_prefetch_limit,
            filter=query_filter,
        )

        search_result = self.client.query_points(
            collection_name=self.collection_name,
            prefetch=[dense_prefetch, sparse_prefetch],
            query=qdrant_models.FusionQuery(fusion=qdrant_models.Fusion.RRF),
            limit=hybrid_limit,
            with_payload=True,
            with_vectors=False,
        )

        points = getattr(search_result, "points", []) or []
        return [
            {
                "id": str(point.id),
                "score": float(point.score or 0.0),
                "payload": dict(point.payload or {}),
            }
            for point in points[:final_limit]
        ]

    def retrieve_context_with_metadata(
        self,
        query_string: str,
        filter_status: str = "active",
        filter_act: str | None = None,
        filter_section: str | None = None,
        filter_article: str | None = None,
    ) -> dict[str, list[Any]]:
        """Retrieve legal chunks with citation metadata using hybrid dense+sparse retrieval.

        External contract matches Chroma's retrieve_context_with_metadata.
        """
        if not query_string or not query_string.strip():
            raise ValueError("query_string cannot be empty")

        section_refs, article_refs = _extract_query_references(query_string)
        if not filter_section and section_refs:
            filter_section = section_refs[0]
        if not filter_article and article_refs:
            filter_article = article_refs[0]

        env = _get_retrieval_env()
        query_filter = self._build_qdrant_filter(
            filter_status=filter_status,
            filter_act=filter_act,
            filter_section=filter_section,
            filter_article=filter_article,
        )

        try:
            results = self._run_hybrid_query(
                query_text=query_string,
                query_filter=query_filter,
                dense_prefetch_limit=env["dense_prefetch_limit"],
                sparse_prefetch_limit=env["sparse_prefetch_limit"],
                hybrid_limit=env["hybrid_limit"],
                final_limit=env["final_limit"],
            )
        except Exception as exc:
            logger.error("Qdrant hybrid query failed: %s", exc)
            return {"documents": [], "citations": []}

        if not results:
            return {"documents": [], "citations": []}

        candidates: list[dict[str, Any]] = []
        seen_snippets: set[str] = set()
        for item in results:
            payload = item.get("payload", {})
            snippet = _clean_snippet(str(payload.get("text", "")))
            snippet_key = snippet.lower()
            if not snippet or snippet_key in seen_snippets:
                continue
            seen_snippets.add(snippet_key)

            source_name = str(payload.get("source") or "unknown")
            act_name = str(payload.get("act") or "").strip()
            title = act_name.strip() or source_name or "Legal Source"
            if title == "Legal Source" and source_name and source_name != "unknown":
                title = source_name

            primary_section_ref = section_refs[0] if section_refs else None
            primary_article_ref = article_refs[0] if article_refs else None
            section_value = str(
                payload.get("section")
                or payload.get("article")
                or ""
            ).strip()

            hybrid_score = float(item.get("score", 0.0))
            if any(_contains_reference(snippet, "Section", ref) for ref in section_refs):
                hybrid_score = min(1.0, hybrid_score + 0.1)
            if any(_contains_reference(snippet, "Article", ref) for ref in article_refs):
                hybrid_score = min(1.0, hybrid_score + 0.1)

            doc_type = str(payload.get("doc_type") or "").lower()
            is_case_doc = doc_type == "case_law"
            query_lower = (query_string or "").lower()
            wants_precedent = any(
                token in query_lower
                for token in ("case", "judgment", "judgement", "precedent", "supreme court", "high court")
            )

            precedent_rank = 0.0
            if is_case_doc:
                court_bonus = 0.12 * _court_precedent_weight(payload.get("court"))
                recency_bonus = 0.08 * _year_recency_weight(payload.get("year"))
                base_case_bonus = 0.22 if wants_precedent else 0.05
                hybrid_score += base_case_bonus + court_bonus + recency_bonus
                precedent_rank = round(court_bonus + recency_bonus + base_case_bonus, 3)
            elif wants_precedent:
                hybrid_score = max(0.0, hybrid_score - 0.08)

            candidates.append(
                {
                    "doc_id": str(payload.get("source") or act_name or f"doc_{len(candidates)+1}"),
                    "title": title,
                    "source": source_name,
                    "source_url": str(payload.get("source_url") or ""),
                    "section": section_value,
                    "doc_type": str(payload.get("doc_type") or ""),
                    "case_name": str(payload.get("case_name") or ""),
                    "citation_text": str(payload.get("citation_text") or ""),
                    "court": str(payload.get("court") or ""),
                    "year": payload.get("year"),
                    "jurisdiction": str(payload.get("jurisdiction") or ""),
                    "bench": str(payload.get("bench") or ""),
                    "topic": str(payload.get("topic") or ""),
                    "precedent_rank": precedent_rank,
                    "page": payload.get("page"),
                    "snippet": snippet,
                    "last_updated": str(payload.get("last_updated") or ""),
                    "confidence": max(0.0, min(1.0, hybrid_score)),
                }
            )

        candidates.sort(key=lambda item: item.get("confidence", 0.0), reverse=True)
        selected = candidates[: env["final_limit"]]

        return {
            "documents": [item["snippet"] for item in selected],
            "citations": selected,
        }

    # ------------------------------------------------------------------
    # Diagnostic / status helpers
    # ------------------------------------------------------------------
    def get_status(self) -> dict[str, Any]:
        """Return a comprehensive status dict for diagnostics."""
        exists = self.collection_exists()
        return {
            "collection": self.collection_name,
            "exists": exists,
            "count": self.collection_count() if exists else 0,
            "host": self.host,
            "port": self.port,
            "grpc_port": self.grpc_port,
            "prefer_grpc": self.prefer_grpc,
            "embedding_model": self.embedding_model_name,
        }


# ---------------------------------------------------------------------------
# Shared legal-domain helpers (reused from Chroma)
# ---------------------------------------------------------------------------
def _clean_snippet(text: str) -> str:
    """Normalize noisy OCR/gazette text while preserving full excerpt content."""
    normalized = re.sub(r"[_]{3,}|[-]{3,}", " ", text or "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _contains_reference(text: str, key: str, value: str) -> bool:
    """Check whether text contains section/article reference in common legal formats."""
    if not text or not value:
        return False

    normalized_value = str(value).strip().upper()
    if not normalized_value:
        return False

    if str(key).lower() == "section":
        section_patterns = [
            rf"\b(?:section|sec\.?)\s*[-:]?\s*{re.escape(normalized_value)}\b",
            rf"(?:^|\s|\()({re.escape(normalized_value)})\s*[\.\:\-–—\)]\s*[A-Za-z]",
            rf"\b{re.escape(normalized_value)}\s*\([0-9A-Z]+\)",
        ]
        return any(
            bool(re.search(pattern, text, flags=re.IGNORECASE))
            for pattern in section_patterns
        )

    if str(key).lower() == "article":
        article_patterns = [
            rf"\b(?:article|art\.?)\s*[-:]?\s*{re.escape(normalized_value)}\b",
            rf"(?:^|\s|\()({re.escape(normalized_value)})\s*[\.\:\-–—\)]\s*[A-Za-z]",
        ]
        return any(
            bool(re.search(pattern, text, flags=re.IGNORECASE))
            for pattern in article_patterns
        )

    pattern = rf"\b{re.escape(key)}\s+{re.escape(normalized_value)}\b"
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def _extract_references_from_text(
    text: str,
    preferred_section: str | None = None,
    preferred_article: str | None = None,
) -> tuple[str | None, str | None]:
    """Best-effort extraction of section/article references from free text."""
    if not text:
        return None, None

    if preferred_section and _contains_reference(text, "Section", preferred_section):
        return str(preferred_section).upper(), None
    if preferred_article and _contains_reference(text, "Article", preferred_article):
        return None, str(preferred_article).upper()

    section_matches = re.findall(
        r"\b(?:section|sec\.?)\s*[-:]?\s*([0-9]+[A-Z]?(?:\([0-9A-Z]+\))?)\b",
        text,
        flags=re.IGNORECASE,
    )
    article_matches = re.findall(
        r"\b(?:article|art\.?)\s*[-:]?\s*([0-9]+[A-Z]?(?:\([0-9A-Z]+\))?)\b",
        text,
        flags=re.IGNORECASE,
    )

    section_candidates = [value.upper() for value in section_matches]
    article_candidates = [value.upper() for value in article_matches]

    section_value = None
    article_value = None

    if preferred_section:
        normalized_preferred_section = preferred_section.upper()
        for candidate in section_candidates:
            if candidate == normalized_preferred_section:
                section_value = candidate
                break
    if section_value is None and section_candidates:
        section_value = section_candidates[-1]

    if preferred_article:
        normalized_preferred_article = preferred_article.upper()
        for candidate in article_candidates:
            if candidate == normalized_preferred_article:
                article_value = candidate
                break
    if article_value is None and article_candidates:
        article_value = article_candidates[-1]

    if not section_value:
        heading_matches = re.findall(
            r"(?:^|\s)([0-9]{1,3}[A-Z]?)\s*[\.\:\-–—\)]\s*[A-Za-z]",
            text,
            flags=re.IGNORECASE,
        )
        heading_candidates = [value.upper() for value in heading_matches]
        if preferred_section:
            normalized_preferred_section = preferred_section.upper()
            for candidate in heading_candidates:
                if candidate == normalized_preferred_section:
                    section_value = candidate
                    break
        if section_value is None and heading_candidates:
            section_value = heading_candidates[-1]

    if not section_value:
        paren_heading_matches = re.findall(
            r"(?:^|\s)([0-9]{1,3}[A-Z]?)\s*\.\s*\(",
            text,
            flags=re.IGNORECASE,
        )
        paren_candidates = [value.upper() for value in paren_heading_matches]
        if preferred_section:
            normalized_preferred_section = preferred_section.upper()
            for candidate in paren_candidates:
                if candidate == normalized_preferred_section:
                    section_value = candidate
                    break
        if section_value is None and paren_candidates:
            section_value = paren_candidates[-1]

    return section_value, article_value


def _extract_query_references(query: str) -> tuple[list[str], list[str]]:
    """Extract all requested section/article references from user query."""
    if not query:
        return [], []

    section_refs: list[str] = []
    article_refs: list[str] = []

    section_refs.extend(
        [
            value.upper()
            for value in re.findall(
                r"\b(?:section|sec\.?|u/s)\s*[-:]?\s*([0-9]+[A-Z]?(?:\([0-9A-Z]+\))?)\b",
                query,
                flags=re.IGNORECASE,
            )
        ]
    )

    plural_section_blocks = re.findall(
        r"\bsections\s+([^.;\n]+)",
        query,
        flags=re.IGNORECASE,
    )
    for block in plural_section_blocks:
        section_refs.extend(
            [
                value.upper()
                for value in re.findall(
                    r"\b([0-9]+[A-Z]?(?:\([0-9A-Z]+\))?)\b",
                    block,
                    flags=re.IGNORECASE,
                )
            ]
        )

    article_refs.extend(
        [
            value.upper()
            for value in re.findall(
                r"\b(?:article|art\.?)\s*[-:]?\s*([0-9]+[A-Z]?(?:\([0-9A-Z]+\))?)\b",
                query,
                flags=re.IGNORECASE,
            )
        ]
    )

    section_refs.extend(
        [
            value.upper()
            for value in re.findall(
                r"\b(?:bns|bnss|bsa|ipc|crpc)\s*[-/]?\s*([0-9]+[A-Z]?(?:\([0-9A-Z]+\))?)\b",
                query,
                flags=re.IGNORECASE,
            )
        ]
    )

    ordered_sections: list[str] = []
    for value in section_refs:
        if value not in ordered_sections:
            ordered_sections.append(value)

    ordered_articles: list[str] = []
    for value in article_refs:
        if value not in ordered_articles:
            ordered_articles.append(value)

    return ordered_sections, ordered_articles


def _court_precedent_weight(court_name: str) -> float:
    """Return relative precedent strength by court hierarchy."""
    normalized = str(court_name or "").lower()
    if not normalized:
        return 0.0
    if "supreme court" in normalized:
        return 1.0
    if "high court" in normalized:
        return 0.75
    if "sessions" in normalized:
        return 0.5
    if "district" in normalized:
        return 0.35
    return 0.25


def _year_recency_weight(year_value: Any) -> float:
    """Map year to a recency weight in [0, 1]."""
    try:
        year = int(year_value)
    except (TypeError, ValueError):
        return 0.0

    if year >= 2022:
        return 1.0
    if year >= 2018:
        return 0.8
    if year >= 2010:
        return 0.6
    if year >= 2000:
        return 0.45
    if year >= 1990:
        return 0.3
    return 0.2
