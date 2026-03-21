"""Chroma vector database manager for Vidhoor Legal Copilot.

This module provides ingestion and retrieval utilities for Indian law chunks
using a Chroma HTTP server.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.errors import ChromaError
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

logger = logging.getLogger(__name__)


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
			where_filter: dict[str, Any]
			if filter_act:
				where_filter = {
					"$and": [
						{"status": {"$eq": filter_status}},
						{"act": {"$eq": filter_act}},
					]
				}
			else:
				where_filter = {"status": {"$eq": filter_status}}

			result = self.collection.query(
				query_texts=[query_string],
				n_results=5,
				where=where_filter,
				include=["documents", "metadatas", "distances"],
			)

			# Chroma returns nested lists (one list per query).
			documents = result.get("documents", [[]])
			return documents[0] if documents and documents[0] else []
		except ChromaError as exc:
			logger.exception("Chroma retrieval error")
			raise RuntimeError("Failed to retrieve legal context from Chroma") from exc
		except Exception as exc:
			logger.exception("Unexpected retrieval error")
			raise RuntimeError("Unexpected failure during Chroma retrieval") from exc
