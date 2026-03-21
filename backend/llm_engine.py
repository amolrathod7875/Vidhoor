"""LLM orchestration for Vidhoor Legal Copilot.

This module handles context-grounded answer generation using ChatCerebras.
"""

from __future__ import annotations

import logging
import os
from importlib import import_module
from pathlib import Path
from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

try:
	from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency at runtime
	load_dotenv = None

logger = logging.getLogger(__name__)


def _load_chat_cerebras() -> Any:
	"""Load ChatCerebras class from supported import paths.

	Returns:
		ChatCerebras class object.

	Raises:
		ImportError: If langchain-cerebras is not installed or import path differs.
	"""
	try:
		return getattr(import_module("langchain_cerebras"), "ChatCerebras")
	except Exception:
		# Alternate path used by some package versions.
		return getattr(import_module("langchain_cerebras.chat_models"), "ChatCerebras")


class LLMEngine:
	"""Generate legal responses from retrieved context using Cerebras LLM."""

	def __init__(self, model: str = "llama3.1-70b") -> None:
		"""Initialize ChatCerebras model.

		Args:
			model: Cerebras chat model name.

		Raises:
			EnvironmentError: If CEREBRAS_API_KEY is not set.
			RuntimeError: If model initialization fails.
		"""
		self._load_environment()
		api_key = os.environ.get("CEREBRAS_API_KEY")
		if not api_key:
			raise EnvironmentError(
				"CEREBRAS_API_KEY is not set. Please add it to your environment."
			)

		self._api_key = api_key
		self._model_candidates = self._build_model_candidates(model)
		self._active_model = self._model_candidates[0]

		try:
			self.llm = self._create_llm(self._active_model)
		except ImportError as exc:
			logger.exception("langchain-cerebras package import failed")
			raise RuntimeError(
				"langchain-cerebras is not installed or importable in this environment"
			) from exc
		except Exception as exc:
			logger.exception("Failed to initialize ChatCerebras model")
			raise RuntimeError("Unable to initialize Cerebras chat model") from exc

		# Strict grounding prompt for legal safety and hallucination control.
		self.prompt = ChatPromptTemplate.from_messages(
			[
				(
					"system",
					(
						"You are Vidhoor, an expert Indian legal AI. "
						"You must ONLY answer using the provided legal context. "
						"If the context does not contain the answer, state that you do not know. "
						"Do not hallucinate."
					),
				),
				(
					"human",
					(
						"Legal Context:\n{context}\n\n"
						"Masked User Query:\n{query}\n\n"
						"Answer in clear, concise legal language grounded only in the context."
					),
				),
			]
		)

		self.chain = self.prompt | self.llm | StrOutputParser()

	@staticmethod
	def _build_model_candidates(primary_model: str) -> list[str]:
		"""Build a unique list of model aliases to try in order."""
		candidates = [
			primary_model,
			"llama-3.1-70b",
			"llama3.1-70b",
			"llama-3.1-8b",
			"llama3.1-8b",
		]

		ordered_unique: list[str] = []
		for candidate in candidates:
			if candidate and candidate not in ordered_unique:
				ordered_unique.append(candidate)
		return ordered_unique

	def _create_llm(self, model_name: str):
		"""Create a ChatCerebras instance for the given model."""
		ChatCerebras = _load_chat_cerebras()
		return ChatCerebras(model=model_name, api_key=self._api_key)

	def _switch_model(self, model_name: str) -> None:
		"""Switch active model and rebuild runnable chain."""
		self.llm = self._create_llm(model_name)
		self.chain = self.prompt | self.llm | StrOutputParser()
		self._active_model = model_name

	@staticmethod
	def _load_environment() -> None:
		"""Load environment variables from backend/.env if available."""
		if load_dotenv is None:
			return

		env_path = Path(__file__).resolve().parent / ".env"
		if env_path.exists():
			load_dotenv(dotenv_path=env_path, override=False)

	def generate_legal_response(
		self,
		masked_query: str,
		retrieved_context_list: list[str],
	) -> str:
		"""Generate a context-grounded legal response.

		Args:
			masked_query: User query with sensitive values masked.
			retrieved_context_list: Retrieved legal chunks from Chroma.

		Returns:
			Generated legal response text.

		Raises:
			ValueError: If masked_query is empty.
			RuntimeError: If LLM invocation fails.
		"""
		if not masked_query or not masked_query.strip():
			raise ValueError("masked_query cannot be empty")

		# Keep prompt deterministic when no retrieval is available.
		context_text = (
			"\n\n".join(retrieved_context_list).strip()
			if retrieved_context_list
			else "No legal context was retrieved."
		)

		last_error: Exception | None = None
		for model_name in self._model_candidates:
			if model_name != self._active_model:
				try:
					self._switch_model(model_name)
				except Exception as exc:
					last_error = exc
					continue

			try:
				return self.chain.invoke(
					{
						"query": masked_query,
						"context": context_text,
					}
				)
			except Exception as exc:
				last_error = exc
				error_text = str(exc).lower()
				if "model_not_found" in error_text or "does not exist" in error_text:
					logger.warning("Model '%s' unavailable, trying fallback", model_name)
					continue
				logger.exception("Cerebras generation failed")
				raise RuntimeError("Failed to generate legal response") from exc

		logger.exception("All configured Cerebras model aliases failed")
		raise RuntimeError("Failed to generate legal response") from last_error
