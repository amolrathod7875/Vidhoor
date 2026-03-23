"""Chroma vector database manager for Vidhoor Legal Copilot.

This module provides ingestion and retrieval utilities for Indian law chunks
using a Chroma HTTP server.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import uuid4

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.errors import ChromaError
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

logger = logging.getLogger(__name__)


def _distance_to_confidence(distance: Any) -> float:
	"""Convert retrieval distance to a normalized confidence score [0, 1]."""
	try:
		distance_value = float(distance)
	except (TypeError, ValueError):
		return 0.5

	# Chroma distance is typically smaller-is-better. This keeps behavior stable
	# across cosine/L2-like ranges and clamps to 0..1.
	confidence = 1.0 - (abs(distance_value) / 2.0)
	return max(0.0, min(1.0, confidence))


def _clean_snippet(text: str) -> str:
	"""Normalize noisy OCR/gazette text while preserving full excerpt content."""
	normalized = re.sub(r"[_]{3,}|[-]{3,}", " ", text or "")
	normalized = re.sub(r"\s+", " ", normalized).strip()
	return normalized


def _contains_reference(text: str, key: str, value: str) -> bool:
	"""Check whether text contains exact Article/Section reference."""
	if not text or not value:
		return False
	pattern = rf"\b{re.escape(key)}\s+{re.escape(value)}\b"
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
		# If filename has bns but not bnss, treat it as BNS and reject.
		if "bns" in normalized_source:
			return False

	return True


class ChromaManager:
	"""Manage Chroma collection lifecycle, ingestion, and context retrieval."""

	def __init__(
		self,
		host: str = "localhost",
		port: int = 8000,
		collection_name: str = "indian_law",
		preferred_embedding_model: str = "BAAI/bge-m3",
		fallback_embedding_model: str = "all-MiniLM-L6-v2",
	) -> None:
		"""Initialize Chroma HTTP client and collection.

		Args:
			host: Chroma host (default points to local placeholder).
			port: Chroma HTTP port.
			collection_name: Name of the Chroma collection.
			preferred_embedding_model: Primary sentence-transformer model.
			fallback_embedding_model: Fallback model if primary init fails.

		Raises:
			RuntimeError: If the HTTP client, embedding function, or collection
				setup fails.
		"""
		self.host = host
		self.port = port
		self.collection_name = collection_name

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

	def ingest_law(
		self,
		text_chunks: list[str],
		metadata_list: list[dict[str, Any]],
	) -> int:
		"""Ingest legal text chunks into Chroma collection.

		Args:
			text_chunks: List of legal text chunks to store.
			metadata_list: Metadata dictionaries aligned by index with chunks,
				for example: {"act": "BNS", "section": "480", "status": "active"}.

		Returns:
			Number of ingested chunks.

		Raises:
			ValueError: If inputs are invalid or mismatched.
			RuntimeError: If ingestion into Chroma fails.
		"""
		if not text_chunks:
			raise ValueError("text_chunks cannot be empty")
		if not metadata_list:
			raise ValueError("metadata_list cannot be empty")
		if len(text_chunks) != len(metadata_list):
			raise ValueError(
				"Length mismatch: text_chunks and metadata_list must have equal size"
			)

		try:
			document_ids = [str(uuid4()) for _ in text_chunks]
			self.collection.add(
				ids=document_ids,
				documents=text_chunks,
				metadatas=metadata_list,
			)
			return len(text_chunks)
		except ChromaError as exc:
			logger.exception("Chroma ingestion error")
			raise RuntimeError("Failed to ingest legal chunks into Chroma") from exc
		except Exception as exc:
			logger.exception("Unexpected ingestion error")
			raise RuntimeError("Unexpected failure during Chroma ingestion") from exc

	def retrieve_context(
		self,
		query_string: str,
		filter_status: str = "active",
		filter_act: str | None = None,
	) -> list[str]:
		"""Retrieve top legal context chunks by semantic similarity.

		Uses Chroma metadata filtering with `where={"status": filter_status}`.

		Args:
			query_string: User query after masking PII.
			filter_status: Metadata status filter value (default "active").
			filter_act: Optional metadata act filter.

		Returns:
			Top 5 matching legal chunks as plain text.

		Raises:
			ValueError: If query_string is empty.
			RuntimeError: If retrieval fails.
		"""
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
		"""Retrieve legal chunks with citation metadata and confidence signals."""
		if not query_string or not query_string.strip():
			raise ValueError("query_string cannot be empty")

		article_match = re.search(
			r"\bArticle\s+([0-9]+[A-Z]?)\b", query_string, flags=re.IGNORECASE
		)
		section_match = re.search(
			r"\bSection\s+([0-9]+[A-Z]?)\b", query_string, flags=re.IGNORECASE
		)

		article_ref = article_match.group(1).upper() if article_match else None
		section_ref = section_match.group(1).upper() if section_match else None

		def combine_conditions(conditions: list[dict[str, Any]]) -> dict[str, Any]:
			if len(conditions) == 1:
				return conditions[0]
			return {"$and": conditions}

		base_conditions: list[dict[str, Any]] = [{"status": {"$eq": filter_status}}]
		if filter_act:
			base_conditions.append({"act": {"$eq": filter_act}})

		search_filters: list[dict[str, Any]] = []

		if article_ref:
			search_filters.append(
				combine_conditions(base_conditions + [{"article": {"$eq": article_ref}}])
			)
		if section_ref:
			search_filters.append(
				combine_conditions(base_conditions + [{"section": {"$eq": section_ref}}])
			)

		search_filters.append(combine_conditions(base_conditions))
		search_filters.append({"status": {"$eq": filter_status}})

		seen_filters: set[str] = set()
		for where_filter in search_filters:
			filter_key = str(where_filter)
			if filter_key in seen_filters:
				continue
			seen_filters.add(filter_key)

			result = self.collection.query(
				query_texts=[query_string],
				n_results=8,
				where=where_filter,
				include=["documents", "metadatas", "distances"],
			)

			documents = result.get("documents", [[]])
			metadatas = result.get("metadatas", [[]])
			distances = result.get("distances", [[]])

			if not documents or not documents[0]:
				continue

			doc_list = documents[0]
			meta_list = metadatas[0] if metadatas else []
			distance_list = distances[0] if distances else []

			citations: list[dict[str, Any]] = []
			seen_snippets: set[str] = set()
			minimum_confidence = 0.5 if (article_ref or section_ref) else 0.55
			fallback_candidates: list[dict[str, Any]] = []

			for index, doc_text in enumerate(doc_list):
				metadata = meta_list[index] if index < len(meta_list) else {}
				distance = distance_list[index] if index < len(distance_list) else None
				confidence = _distance_to_confidence(distance)
				if confidence < minimum_confidence:
					continue

				source_name = str(metadata.get("source") or "unknown")
				if not _source_matches_act_filter(source_name, filter_act):
					continue

				doc_text_value = str(doc_text)
				snippet = _clean_snippet(doc_text_value)
				snippet_key = snippet.lower()
				if snippet_key in seen_snippets:
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

				citation = {
					"doc_id": str(metadata.get("source") or metadata.get("act") or f"doc_{index + 1}"),
					"title": title,
					"source": source_name,
					"source_url": str(metadata.get("source_url") or ""),
					"section": str(metadata.get("section") or metadata.get("article") or ""),
					"page": metadata.get("page"),
					"snippet": snippet,
					"confidence": confidence,
					"last_updated": last_updated,
				}
				fallback_candidates.append(citation)
				citations.append(citation)

			if not citations:
				fallback_candidates.sort(key=lambda item: item["confidence"], reverse=True)
				if fallback_candidates:
					citations = fallback_candidates[:2]
				else:
					continue

			citations.sort(key=lambda item: item["confidence"], reverse=True)
			selected = citations[:4]

			return {
				"documents": [item["snippet"] for item in selected],
				"citations": selected,
			}

		return {
			"documents": [],
			"citations": [],
		}
