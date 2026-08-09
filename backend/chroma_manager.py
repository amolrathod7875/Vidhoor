"""Chroma vector database manager for Vidhoor Legal Copilot.

This module provides ingestion and retrieval utilities for Indian law chunks
using a Chroma HTTP server and Oracle-backed hybrid retrieval (vector + BM25).
"""

from __future__ import annotations

import grpc_stubs  # noqa: F401 — sets up DLL stubs for grpc/oracledb on restricted Windows
import hashlib
from importlib import import_module
import json
import logging
import os
import re
from typing import Any
from uuid import uuid4

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.errors import ChromaError
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from database import OracleChunkRepository


def _load_bm25_okapi() -> Any:
	"""Lazily import BM25 implementation to avoid hard runtime dependency failures."""
	try:
		module = import_module("rank_bm25")
		return getattr(module, "BM25Okapi", None)
	except Exception:
		return None


BM25Okapi = _load_bm25_okapi()

logger = logging.getLogger(__name__)

HYBRID_VECTOR_WEIGHT = 0.5
HYBRID_BM25_WEIGHT = 0.5


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


def _distance_to_confidence(distance: Any) -> float:
	"""Convert retrieval distance to a normalized confidence score [0, 1]."""
	try:
		distance_value = float(distance)
	except (TypeError, ValueError):
		return 0.5

	confidence = 1.0 - (abs(distance_value) / 2.0)
	return max(0.0, min(1.0, confidence))


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


def _source_matches_act_filter(source: str, act_filter: str | None) -> bool:
	"""Best-effort sanity guard when historic metadata has mislabeled acts."""
	if not act_filter:
		return True

	normalized_source = (source or "").lower()
	if not normalized_source:
		return True

	if act_filter == "Bharatiya Nyaya Sanhita":
		return "bnss" not in normalized_source

	if act_filter == "Bharatiya Nagarik Suraksha Sanhita":
		if "bnss" in normalized_source:
			return True
		if "bns" in normalized_source:
			return False

	return True


def _looks_like_statute_source(value: str) -> bool:
	"""Detect statute sources from filenames/titles when metadata is stale."""
	normalized = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
	tokens = set(re.findall(r"[a-z0-9]+", str(value or "").lower()))

	short_aliases = {"bns", "bnss", "bsa", "ipc"}
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


def _tokenize_for_bm25(text: str) -> list[str]:
	"""Simple legal-text tokenizer for BM25 ranking."""
	return re.findall(r"[a-z0-9]+", (text or "").lower())


def _normalize_minmax(value: float, minimum: float, maximum: float) -> float:
	"""Normalize a value to [0, 1] using min-max scaling."""
	if maximum <= minimum:
		return 1.0 if value > 0 else 0.0
	return max(0.0, min(1.0, (value - minimum) / (maximum - minimum)))


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

	# BNS Sections 64/65 (punishment) rely on Section 63 (definition); retrieve both.
	if ("64" in ordered_sections or "65" in ordered_sections) and "63" not in ordered_sections:
		ordered_sections.append("63")

	ordered_articles: list[str] = []
	for value in article_refs:
		if value not in ordered_articles:
			ordered_articles.append(value)

	return ordered_sections, ordered_articles


class ChromaManager:
	"""Manage Chroma collection lifecycle, ingestion, and hybrid retrieval."""

	def __init__(
		self,
		host: str = "localhost",
		port: int = 8000,
		collection_name: str = "indian_law",
		preferred_embedding_model: str = "BAAI/bge-m3",
		fallback_embedding_model: str = "all-MiniLM-L6-v2",
	) -> None:
		"""Initialize Chroma HTTP client, collection, and BM25 warm cache."""
		self.host = host
		self.port = port
		self.collection_name = collection_name
		self.chunk_repository: OracleChunkRepository | None = None
		self._bm25_index: Any = None
		self._bm25_chunks: list[dict[str, Any]] = []
		self._bm25_tokens: list[list[str]] = []
		self.filter_status = os.environ.get("CHROMA_FILTER_STATUS", "active")

		try:
			self.client = chromadb.HttpClient(host=self.host, port=self.port)
		except Exception as exc:
			logger.exception("Failed to initialize Chroma HTTP client")
			raise RuntimeError(
				f"Unable to connect to Chroma at {self.host}:{self.port}"
			) from exc

		self.embedding_function = self._build_embedding_function(
			preferred_embedding_model=preferred_embedding_model,
			fallback_embedding_model=fallback_embedding_model,
		)

		try:
			self.collection: Collection = self.client.get_or_create_collection(
				name=self.collection_name,
				embedding_function=self.embedding_function,
			)
		except Exception as exc:
			logger.exception("Failed to get or create Chroma collection")
			raise RuntimeError(
				f"Unable to initialize Chroma collection '{self.collection_name}'"
			) from exc

		self._initialize_bm25_repository()

	def _build_embedding_function(
		self,
		preferred_embedding_model: str,
		fallback_embedding_model: str,
	) -> SentenceTransformerEmbeddingFunction:
		"""Create embedding function with fallback model support."""
		try:
			return SentenceTransformerEmbeddingFunction(
				model_name=preferred_embedding_model
			)
		except Exception as exc:
			logger.warning(
				"Primary embedding model '%s' failed. Falling back to '%s'. Error: %s",
				preferred_embedding_model,
				fallback_embedding_model,
				exc,
			)

		try:
			return SentenceTransformerEmbeddingFunction(
				model_name=fallback_embedding_model
			)
		except Exception as exc:
			logger.exception("Fallback embedding model initialization failed")
			raise RuntimeError(
				"Unable to initialize sentence-transformer embedding model"
			) from exc

	def check_embedding_dimension(self) -> dict[str, Any]:
		"""Return expected vs stored embedding dimension for the collection."""
		expected = None
		try:
			probe = self.embedding_function(["dimension probe"])
			if probe is not None and len(probe) > 0:
				expected = len(probe[0])
		except Exception as exc:
			logger.warning("Failed to determine embedding dimension: %s", exc)

		stored = None
		try:
			sample = self.collection.get(limit=1, include=["embeddings"])
			embeddings = sample.get("embeddings")
			if embeddings is not None and len(embeddings) > 0:
				stored = len(embeddings[0])
		except Exception as exc:
			logger.warning("Failed to inspect stored embedding dimension: %s", exc)

		match = expected is not None and expected == stored
		return {
			"expected": expected,
			"stored": stored,
			"match": match,
		}

	def _initialize_bm25_repository(self) -> None:
		"""Initialize Oracle chunk repository and warm BM25 index from persistent chunks."""
		if BM25Okapi is None:
			logger.warning(
				"rank-bm25 is not installed. Hybrid retrieval disabled; using vector-only."
			)
			return

		try:
			self.chunk_repository = OracleChunkRepository()
			self.chunk_repository.initialize_schema()
			self.refresh_bm25_from_oracle()
		except Exception as exc:
			logger.warning(
				"Oracle chunk repository unavailable; continuing with vector-only retrieval. Error: %s",
				exc,
			)
			self.chunk_repository = None

	def refresh_bm25_from_oracle(
		self,
		filter_status: str | None = "active",
		filter_act: str | None = None,
	) -> int:
		"""Rebuild in-memory BM25 index from Oracle persisted chunks."""
		if not self.chunk_repository or BM25Okapi is None:
			self._bm25_index = None
			self._bm25_chunks = []
			self._bm25_tokens = []
			return 0

		chunks = self.chunk_repository.load_chunks(
			filter_status=filter_status,
			filter_act=filter_act,
		)

		tokens = [_tokenize_for_bm25(item.get("chunk_text", "")) for item in chunks]
		valid_pairs = [
			(chunk, token_list)
			for chunk, token_list in zip(chunks, tokens)
			if token_list
		]

		if not valid_pairs:
			self._bm25_index = None
			self._bm25_chunks = []
			self._bm25_tokens = []
			return 0

		self._bm25_chunks = [chunk for chunk, _ in valid_pairs]
		self._bm25_tokens = [token_list for _, token_list in valid_pairs]
		self._bm25_index = BM25Okapi(self._bm25_tokens)
		return len(self._bm25_chunks)

	def ingest_law(
		self,
		text_chunks: list[str],
		metadata_list: list[dict[str, Any]],
	) -> int:
		"""Ingest legal chunks into Chroma and persist chunks for BM25 recovery."""
		if not text_chunks:
			raise ValueError("text_chunks cannot be empty")
		if not metadata_list:
			raise ValueError("metadata_list cannot be empty")
		if len(text_chunks) != len(metadata_list):
			raise ValueError(
				"Length mismatch: text_chunks and metadata_list must have equal size"
			)

		persistent_chunk_ids = [
			self._build_chunk_id(text_chunks[index], metadata_list[index], index)
			for index in range(len(text_chunks))
		]
		chroma_document_ids = [str(uuid4()) for _ in text_chunks]

		try:
			self.collection.add(
				ids=chroma_document_ids,
				documents=text_chunks,
				metadatas=metadata_list,
			)
		except ChromaError as exc:
			logger.exception("Chroma ingestion error")
			raise RuntimeError("Failed to ingest legal chunks into Chroma") from exc
		except Exception as exc:
			logger.exception("Unexpected ingestion error")
			raise RuntimeError("Unexpected failure during Chroma ingestion") from exc

		self._persist_chunks_for_bm25(
			chunk_ids=persistent_chunk_ids,
			text_chunks=text_chunks,
			metadata_list=metadata_list,
		)
		return len(text_chunks)

	def _build_chunk_id(
		self,
		chunk_text: str,
		metadata: dict[str, Any],
		index: int,
	) -> str:
		"""Create deterministic chunk ID for cross-index traceability."""
		payload = {
			"text": (chunk_text or "").strip(),
			"source": str(metadata.get("source") or ""),
			"act": str(metadata.get("act") or ""),
			"section": str(metadata.get("section") or metadata.get("article") or ""),
			"index": index,
		}
		raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
		digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
		return f"chunk_{digest[:40]}"

	def _persist_chunks_for_bm25(
		self,
		chunk_ids: list[str],
		text_chunks: list[str],
		metadata_list: list[dict[str, Any]],
	) -> None:
		"""Persist chunks to Oracle and refresh in-memory BM25 cache."""
		if not self.chunk_repository:
			return

		rows: list[dict[str, Any]] = []
		for chunk_id, chunk_text, metadata in zip(chunk_ids, text_chunks, metadata_list):
			inferred_section, inferred_article = _extract_references_from_text(chunk_text)
			section_ref = str(
				metadata.get("section")
				or metadata.get("article")
				or inferred_section
				or inferred_article
				or ""
			)
			rows.append(
				{
					"chunk_id": chunk_id,
					"chunk_text": chunk_text,
					"status": str(metadata.get("status") or "active"),
					"act": str(metadata.get("act") or ""),
					"source": str(metadata.get("source") or ""),
					"section_ref": section_ref,
					"metadata_json": json.dumps(metadata, ensure_ascii=False),
				}
			)

		try:
			self.chunk_repository.upsert_chunks(rows)
			self.refresh_bm25_from_oracle(filter_status="active", filter_act=None)
		except Exception as exc:
			logger.warning("Failed to persist/rebuild BM25 chunks from Oracle: %s", exc)

	def retrieve_context(
		self,
		query_string: str,
		filter_status: str = "active",
		filter_act: str | None = None,
	) -> list[str]:
		"""Retrieve top legal context chunks by hybrid semantic+lexical ranking."""
		if not query_string or not query_string.strip():
			raise ValueError("query_string cannot be empty")

		try:
			retrieval = self.retrieve_context_with_metadata(
				query_string=query_string,
				filter_status=filter_status,
				filter_act=filter_act,
			)
			return retrieval["documents"]
		except ChromaError as exc:
			logger.exception("Chroma retrieval error")
			raise RuntimeError("Failed to retrieve legal context from Chroma") from exc
		except Exception as exc:
			logger.exception("Unexpected retrieval error")
			raise RuntimeError("Unexpected failure during Chroma retrieval") from exc

	def retrieve_context_with_metadata(
		self,
		query_string: str,
		filter_status: str = "active",
		filter_act: str | None = None,
	) -> dict[str, list[Any]]:
		"""Retrieve legal chunks with citation metadata and hybrid confidence signals."""
		if not query_string or not query_string.strip():
			raise ValueError("query_string cannot be empty")

		section_refs, article_refs = _extract_query_references(query_string)

		# Force dependency retrieval so punishment sections include definition context.
		if ("64" in section_refs or "65" in section_refs) and "63" not in section_refs:
			section_refs.append("63")

		vector_candidates = self._retrieve_vector_candidates(
			query_string=query_string,
			filter_status=filter_status,
			filter_act=filter_act,
			article_refs=article_refs,
			section_refs=section_refs,
		)

		bm25_candidates = self._retrieve_bm25_candidates(
			query_string=query_string,
			filter_status=filter_status,
			filter_act=filter_act,
			article_refs=article_refs,
			section_refs=section_refs,
		)

		fused = self._fuse_candidates(
			vector_candidates=vector_candidates,
			bm25_candidates=bm25_candidates,
			query_string=query_string,
			requested_sections=section_refs,
			requested_articles=article_refs,
		)

		if not fused:
			return {
				"documents": [],
				"citations": [],
			}

		return {
			"documents": [item["snippet"] for item in fused],
			"citations": fused,
		}

	def _retrieve_vector_candidates(
		self,
		query_string: str,
		filter_status: str,
		filter_act: str | None,
		article_refs: list[str],
		section_refs: list[str],
	) -> list[dict[str, Any]]:
		"""Run vector retrieval via Chroma and return normalized candidates."""
		primary_section_ref = section_refs[0] if section_refs else None
		primary_article_ref = article_refs[0] if article_refs else None

		def combine_conditions(conditions: list[dict[str, Any]]) -> dict[str, Any]:
			if len(conditions) == 1:
				return conditions[0]
			return {"$and": conditions}

		base_conditions: list[dict[str, Any]] = [{"status": {"$eq": filter_status}}]
		if filter_act:
			base_conditions.append({"act": {"$eq": filter_act}})

		search_filters: list[dict[str, Any]] = []
		for article_ref in article_refs:
			search_filters.append(
				combine_conditions(base_conditions + [{"article": {"$eq": article_ref}}])
			)
		for section_ref in section_refs:
			search_filters.append(
				combine_conditions(base_conditions + [{"section": {"$eq": section_ref}}])
			)
		search_filters.append(combine_conditions(base_conditions))
		if not filter_act:
			search_filters.append({"status": {"$eq": filter_status}})

		query_plans: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
		for where_filter in search_filters:
			query_plans.append((where_filter, None))

		for section_ref in section_refs:
			for where_filter in search_filters:
				query_plans.append(
					(
						where_filter,
						{"$contains": f"Section {section_ref}"},
					)
				)
				query_plans.append(
					(
						where_filter,
						{"$contains": f"{section_ref}."},
					)
				)
		for article_ref in article_refs:
			for where_filter in search_filters:
				query_plans.append(
					(
						where_filter,
						{"$contains": f"Article {article_ref}"},
					)
				)

		seen_filters: set[str] = set()
		candidates: list[dict[str, Any]] = []
		seen_snippets: set[str] = set()

		for where_filter, where_document in query_plans:
			filter_key = f"{where_filter}|{where_document}"
			if filter_key in seen_filters:
				continue
			seen_filters.add(filter_key)

			query_kwargs: dict[str, Any] = {
				"query_texts": [query_string],
				"n_results": 12,
				"where": where_filter,
				"include": ["documents", "metadatas", "distances"],
			}
			if where_document is not None:
				query_kwargs["where_document"] = where_document

			try:
				result = self.collection.query(**query_kwargs)
			except Exception as exc:
				logger.error(
					"Chroma vector query failed for collection '%s' (filter=%s): %s",
					self.collection_name,
					where_filter,
					exc,
				)
				continue

			documents = result.get("documents", [[]])
			metadatas = result.get("metadatas", [[]])
			distances = result.get("distances", [[]])

			if not documents or not documents[0]:
				continue

			doc_list = documents[0]
			meta_list = metadatas[0] if metadatas else []
			distance_list = distances[0] if distances else []

			for index, doc_text in enumerate(doc_list):
				metadata = meta_list[index] if index < len(meta_list) else {}
				distance = distance_list[index] if index < len(distance_list) else None
				vector_score = _distance_to_confidence(distance)

				source_name = str(metadata.get("source") or "unknown")
				if not _source_matches_act_filter(source_name, filter_act):
					continue

				doc_text_value = str(doc_text)
				snippet = _clean_snippet(doc_text_value)
				snippet_key = snippet.lower()
				if not snippet or snippet_key in seen_snippets:
					continue
				seen_snippets.add(snippet_key)

				title = str(metadata.get("act") or "").strip() or "Legal Source"
				if title == "Legal Source" and source_name and source_name != "unknown":
					title = source_name

				last_updated = str(
					metadata.get("last_updated")
					or metadata.get("updated_at")
					or metadata.get("effective_date")
					or metadata.get("ingested_at")
					or ""
				)

				inferred_section, inferred_article = _extract_references_from_text(
					doc_text_value,
					preferred_section=primary_section_ref,
					preferred_article=primary_article_ref,
				)
				section_value = str(
					metadata.get("section")
					or metadata.get("article")
					or inferred_section
					or inferred_article
					or ""
				)
				if primary_section_ref and _contains_reference(
					doc_text_value,
					"Section",
					primary_section_ref,
				):
					section_value = primary_section_ref
				if primary_article_ref and _contains_reference(
					doc_text_value,
					"Article",
					primary_article_ref,
				):
					section_value = primary_article_ref

				if any(
					_contains_reference(doc_text_value, "Article", article_ref)
					for article_ref in article_refs
				):
					vector_score = min(1.0, vector_score + 0.1)
				if any(
					_contains_reference(doc_text_value, "Section", section_ref)
					for section_ref in section_refs
				):
					vector_score = min(1.0, vector_score + 0.1)

				candidates.append(
					{
						"doc_id": str(metadata.get("source") or metadata.get("act") or f"doc_{index + 1}"),
						"title": title,
						"source": source_name,
						"source_url": str(
							metadata.get("source_url")
							or metadata.get("doc_url")
							or metadata.get("source_uri")
							or ""
						),
						"section": section_value,
						"doc_type": str(metadata.get("doc_type") or ""),
						"case_name": str(metadata.get("case_name") or ""),
						"citation_text": str(metadata.get("citation_text") or ""),
						"court": str(metadata.get("court") or ""),
						"year": metadata.get("year"),
						"jurisdiction": str(metadata.get("jurisdiction") or ""),
						"bench": str(metadata.get("bench") or ""),
						"topic": str(metadata.get("topic") or ""),
						"page": (
							metadata.get("page")
							or metadata.get("page_number")
							or metadata.get("page_no")
						),
						"snippet": snippet,
						"last_updated": last_updated,
						"vector_score": vector_score,
						"bm25_score": 0.0,
					}
				)

		if not candidates:
			return []

		candidates.sort(key=lambda item: item.get("vector_score", 0.0), reverse=True)
		return candidates[:10]

	def _retrieve_bm25_candidates(
		self,
		query_string: str,
		filter_status: str,
		filter_act: str | None,
		article_refs: list[str],
		section_refs: list[str],
	) -> list[dict[str, Any]]:
		"""Run lexical BM25 retrieval against Oracle-persisted chunks."""
		primary_section_ref = section_refs[0] if section_refs else None
		primary_article_ref = article_refs[0] if article_refs else None

		if self._bm25_index is None or not self._bm25_chunks:
			return []

		query_tokens = _tokenize_for_bm25(query_string)
		if not query_tokens:
			return []

		raw_scores = self._bm25_index.get_scores(query_tokens)
		if raw_scores is None or len(raw_scores) == 0:
			return []

		scored_indices: list[tuple[int, float]] = []
		for index, score in enumerate(raw_scores):
			try:
				score_value = float(score)
			except (TypeError, ValueError):
				score_value = 0.0
			if score_value > 0.0:
				scored_indices.append((index, score_value))

		if not scored_indices:
			return []

		scored_indices.sort(key=lambda item: item[1], reverse=True)
		top_scored = scored_indices[:30]

		forced_indices: set[int] = set()
		if section_refs or article_refs:
			for index, chunk in enumerate(self._bm25_chunks):
				raw_text = str(chunk.get("chunk_text") or "")
				if not raw_text:
					continue
				if any(
					_contains_reference(raw_text, "Section", section_ref)
					for section_ref in section_refs
				):
					forced_indices.add(index)
					continue
				if any(
					_contains_reference(raw_text, "Article", article_ref)
					for article_ref in article_refs
				):
					forced_indices.add(index)

		score_by_index: dict[int, float] = {index: score for index, score in top_scored}
		boosted_raw_score = (top_scored[0][1] + 1.0) if top_scored else 1.0
		for forced_index in forced_indices:
			if forced_index not in score_by_index:
				score_by_index[forced_index] = boosted_raw_score

		top_scored = sorted(
			score_by_index.items(),
			key=lambda item: item[1],
			reverse=True,
		)[:40]

		raw_values = [item[1] for item in top_scored]
		minimum = min(raw_values)
		maximum = max(raw_values)

		candidates: list[dict[str, Any]] = []
		seen_snippets: set[str] = set()

		for index, score in top_scored:
			chunk = self._bm25_chunks[index]
			metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}

			status_value = str(metadata.get("status") or chunk.get("status") or "")
			if filter_status and status_value and status_value != filter_status:
				continue

			act_value = str(metadata.get("act") or chunk.get("act") or "")
			if filter_act and act_value != filter_act:
				continue

			source_name = str(metadata.get("source") or chunk.get("source") or "unknown")
			if not _source_matches_act_filter(source_name, filter_act):
				continue

			raw_text = str(chunk.get("chunk_text") or "")
			snippet = _clean_snippet(raw_text)
			snippet_key = snippet.lower()
			if not snippet or snippet_key in seen_snippets:
				continue
			seen_snippets.add(snippet_key)

			title = act_value.strip() or source_name or "Legal Source"
			inferred_section, inferred_article = _extract_references_from_text(
				raw_text,
				preferred_section=primary_section_ref,
				preferred_article=primary_article_ref,
			)
			section_value = str(
				metadata.get("section")
				or metadata.get("article")
				or chunk.get("section_ref")
				or inferred_section
				or inferred_article
				or ""
			)
			if primary_section_ref and _contains_reference(
				raw_text,
				"Section",
				primary_section_ref,
			):
				section_value = primary_section_ref
			if primary_article_ref and _contains_reference(
				raw_text,
				"Article",
				primary_article_ref,
			):
				section_value = primary_article_ref

			bm25_score = _normalize_minmax(score, minimum, maximum)
			if any(
				_contains_reference(raw_text, "Article", article_ref)
				for article_ref in article_refs
			):
				bm25_score = min(1.0, bm25_score + 0.15)
			if any(
				_contains_reference(raw_text, "Section", section_ref)
				for section_ref in section_refs
			):
				bm25_score = min(1.0, bm25_score + 0.15)

			candidates.append(
				{
					"doc_id": str(chunk.get("chunk_id") or source_name),
					"title": title,
					"source": source_name,
					"source_url": str(
						metadata.get("source_url")
						or metadata.get("doc_url")
						or metadata.get("source_uri")
						or ""
					),
					"section": section_value,
					"doc_type": str(metadata.get("doc_type") or ""),
					"case_name": str(metadata.get("case_name") or ""),
					"citation_text": str(metadata.get("citation_text") or ""),
					"court": str(metadata.get("court") or ""),
					"year": metadata.get("year"),
					"jurisdiction": str(metadata.get("jurisdiction") or ""),
					"bench": str(metadata.get("bench") or ""),
					"topic": str(metadata.get("topic") or ""),
					"page": (
						metadata.get("page")
						or metadata.get("page_number")
						or metadata.get("page_no")
					),
					"snippet": snippet,
					"last_updated": str(chunk.get("last_updated") or ""),
					"vector_score": 0.0,
					"bm25_score": bm25_score,
				}
			)

		candidates.sort(key=lambda item: item.get("bm25_score", 0.0), reverse=True)
		return candidates[:10]

	def _fuse_candidates(
		self,
		vector_candidates: list[dict[str, Any]],
		bm25_candidates: list[dict[str, Any]],
		query_string: str,
		requested_sections: list[str],
		requested_articles: list[str],
	) -> list[dict[str, Any]]:
		"""Fuse vector and BM25 candidates using fixed 0.5/0.5 weighted sum."""
		combined: dict[str, dict[str, Any]] = {}

		def key_for(item: dict[str, Any]) -> str:
			snippet = str(item.get("snippet") or "").strip().lower()
			doc_id = str(item.get("doc_id") or "")
			return f"{doc_id}::{snippet}"

		for item in vector_candidates:
			key = key_for(item)
			combined[key] = dict(item)

		for item in bm25_candidates:
			key = key_for(item)
			existing = combined.get(key)
			if existing is None:
				combined[key] = dict(item)
				continue

			existing["bm25_score"] = max(
				float(existing.get("bm25_score", 0.0)),
				float(item.get("bm25_score", 0.0)),
			)

		for item in combined.values():
			vector_score = float(item.get("vector_score", 0.0))
			bm25_score = float(item.get("bm25_score", 0.0))
			hybrid_score = (
				(HYBRID_VECTOR_WEIGHT * vector_score)
				+ (HYBRID_BM25_WEIGHT * bm25_score)
			)

			snippet = str(item.get("snippet") or "")
			section_value = str(item.get("section") or "")
			reference_bonus = 0.0
			if requested_sections and any(
				section_ref.upper() == section_value.upper()
				or _contains_reference(snippet, "Section", section_ref)
				for section_ref in requested_sections
			):
				reference_bonus += 0.2
			if requested_articles and any(
				article_ref.upper() == section_value.upper()
				or _contains_reference(snippet, "Article", article_ref)
				for article_ref in requested_articles
			):
				reference_bonus += 0.2

			hybrid_score += reference_bonus

			doc_type = str(item.get("doc_type") or "").lower()
			source_haystack = " ".join(
				[
					str(item.get("title") or ""),
					str(item.get("source") or ""),
					str(item.get("doc_id") or ""),
				]
			)
			if doc_type == "case_law" and _looks_like_statute_source(source_haystack):
				doc_type = "statute"
				item["doc_type"] = "statute"

			is_case_doc = doc_type == "case_law"
			query_lower = (query_string or "").lower()
			wants_precedent = any(
				token in query_lower
				for token in ("case", "judgment", "judgement", "precedent", "supreme court", "high court")
			)

			if is_case_doc:
				court_bonus = 0.12 * _court_precedent_weight(item.get("court"))
				recency_bonus = 0.08 * _year_recency_weight(item.get("year"))
				base_case_bonus = 0.22 if wants_precedent else 0.05
				hybrid_score += base_case_bonus + court_bonus + recency_bonus
				item["precedent_rank"] = round(court_bonus + recency_bonus + base_case_bonus, 3)
			else:
				if wants_precedent:
					hybrid_score = max(0.0, hybrid_score - 0.08)
				item["precedent_rank"] = 0.0

			item["confidence"] = max(0.0, min(1.0, hybrid_score))

		if not combined:
			return []

		ranked = sorted(
			combined.values(),
			key=lambda item: float(item.get("confidence", 0.0)),
			reverse=True,
		)

		selected: list[dict[str, Any]] = []
		for item in ranked[:8]:
			selected.append(
				{
					"doc_id": str(item.get("doc_id") or ""),
					"title": str(item.get("case_name") or item.get("title") or "Legal Source"),
					"source": str(item.get("source") or "unknown"),
					"source_url": str(item.get("source_url") or ""),
					"section": str(item.get("section") or ""),
					"doc_type": str(item.get("doc_type") or ""),
					"case_name": str(item.get("case_name") or ""),
					"citation_text": str(item.get("citation_text") or ""),
					"court": str(item.get("court") or ""),
					"year": item.get("year"),
					"jurisdiction": str(item.get("jurisdiction") or ""),
					"bench": str(item.get("bench") or ""),
					"topic": str(item.get("topic") or ""),
					"precedent_rank": float(item.get("precedent_rank", 0.0)),
					"page": item.get("page"),
					"snippet": str(item.get("snippet") or ""),
					"confidence": float(item.get("confidence", 0.0)),
					"last_updated": str(item.get("last_updated") or ""),
				}
			)

		return selected
