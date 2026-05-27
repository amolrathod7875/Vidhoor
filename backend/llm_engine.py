"""LLM orchestration for Vidhoor Legal Copilot.

This module handles context-grounded answer generation using ChatCerebras.
"""

from __future__ import annotations

import inspect
import logging
import os
import re
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

	def __init__(self, model: str = "llama3.1-8b") -> None:
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
		self._api_base = (
			os.environ.get("CEREBRAS_API_BASE")
			or os.environ.get("CEREBRAS_API_URL")
			or "https://api.cerebras.ai/v1"
		).strip()
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
						"If the context does not contain the answer, clearly say the context is insufficient. "
						"If the provided context contains a punishment but not the definition of the crime, explicitly state that the definition (for example, Section 63 for rape) is missing from the retrieved context and do not invent it. "
						"If a specific section/article is requested, do not infer from nearby text unless that exact section/article appears in context. "
						"Do not hallucinate. "
						"Use Act name and section identifiers exactly as shown in context metadata; do not rename BNS/BNSS/BSA to IPC/CrPC/Evidence Act unless metadata explicitly says so."
					),
				),
				(
					"human",
					(
						"Legal Context:\n{context}\n\n"
						"Masked User Query:\n{query}\n\n"
						"You are Vidhoor, an expert Indian legal AI. Your goal is to provide a factual, grounded response based ONLY on the provided legal context.\n\n"
						"CRITICAL INSTRUCTION FOR ACCURACY:\n"
						"1. Cross-verify Section Numbers: Sometimes metadata tags (like 'Section 64') are incorrectly applied to text describing different sections (like Section 65 or 66).\n"
						"2. Content Priority: If the retrieved text describes rape of a minor (under 16 or 12 years) or mentions the death penalty, it is likely BNS Section 65 or 66. Do NOT label this as 'Section 64' just because the metadata suggests it.\n"
						"3. If you detect a conflict between the text content and the section number in the metadata, prioritize the text's legal description and use the correct BNS section number in your output.\n\n"
						"If the user asks about a specific section (for example, Section 64) and the retrieved context does not contain the text for that specific section, state that the context is insufficient. Do NOT substitute it with information from a different section (like Section 67) unless it specifically cross-references the original query.\n\n"
						"Answer in professional legal language. Return markdown using this structure exactly and do not add a summary section:\n"
						"## What you can do\n"
						"- Action or remedy in plain language\n"
						"  - **Legal basis:** <Act Name> Section <Number>\n"
						"  - **Why this helps:** short bullet\n\n"
						"## Laws supporting the above actions\n"
						"### <Act Name> Section <Number>: Detailed Legal Explanation\n"
						"- **What the law states:** bullet points only\n"
						"- **Essential legal ingredients:** bullet points only\n"
						"- **Punishment or legal consequences:** bullet points only (Ensure these match the specific section number)\n"
						"- **Exceptions, provisos, and defences:** bullet points only\n"
						"- **Practical application:** bullet points only\n"
						"- **Limits and uncertainty:** bullet points only\n\n"
						"## Summary table of applicable laws\n"
						"| Offence | BNS Section | Description |\n"
						"| --- | --- | --- |\n"
						"| <Short offence label> | Section <Number> | <Single-sentence practical use for this query> |\n"
						""
					),
				),
			]
		)

		self.chain = self.prompt | self.llm | StrOutputParser()

		self.general_prompt = ChatPromptTemplate.from_messages(
			[
				(
					"system",
					(
						"You are Vidhoor, a helpful AI assistant. "
						"Answer clearly and concisely. "
						"If the user asks legal questions, suggest sharing jurisdiction and specific law details for better accuracy."
					),
				),
				(
					"human",
					"User Query:\n{query}\n\nProvide a direct and useful response.",
				),
			]
		)

		self.general_chain = self.general_prompt | self.llm | StrOutputParser()

		self.title_prompt = ChatPromptTemplate.from_messages(
			[
				(
					"system",
					(
						"You create short conversation titles. "
						"Return only a concise title between 3 and 7 words. "
						"Do not use quotes, emojis, or trailing punctuation."
					),
				),
				(
					"human",
					(
						"User message:\n{user_message}\n\n"
						"Assistant response:\n{assistant_message}\n\n"
						"Create a title summarizing the conversation topic."
					),
				),
			]
		)

		self.title_chain = self.title_prompt | self.llm | StrOutputParser()

		self.follow_up_prompt = ChatPromptTemplate.from_messages(
			[
				(
					"system",
					(
						"You generate suggested follow-up questions for a chat assistant. "
						"Return only short questions relevant to the user's most recent query and the assistant answer. "
						"Do not include numbering, bullets, explanations, markdown, or duplicate questions."
					),
				),
				(
					"human",
					(
						"User query:\n{query}\n\n"
						"Assistant answer:\n{answer}\n\n"
						"Generate {max_count} follow-up questions. "
						"Each question must be one line, under 14 words, and actionable."
					),
				),
			]
		)

		self.follow_up_chain = self.follow_up_prompt | self.llm | StrOutputParser()

	@staticmethod
	def _build_model_candidates(primary_model: str) -> list[str]:
		"""Build a unique list of model aliases to try in order."""
		candidates = [
			primary_model,
			"llama3.1-8b",
			"gpt-oss-120b",
		]

		ordered_unique: list[str] = []
		for candidate in candidates:
			if candidate and candidate not in ordered_unique:
				ordered_unique.append(candidate)
		return ordered_unique

	def _create_llm(self, model_name: str):
		"""Create a ChatCerebras instance for the given model."""
		ChatCerebras = _load_chat_cerebras()
		kwargs: dict[str, Any] = {
			"model": model_name,
			"api_key": self._api_key,
		}

		if self._api_base:
			try:
				params = inspect.signature(ChatCerebras).parameters
				if "base_url" in params:
					kwargs["base_url"] = self._api_base
				elif "api_base" in params:
					kwargs["api_base"] = self._api_base
			except (ValueError, TypeError):
				pass

		return ChatCerebras(**kwargs)

	def _switch_model(self, model_name: str) -> None:
		"""Switch active model and rebuild runnable chain."""
		self.llm = self._create_llm(model_name)
		self.chain = self.prompt | self.llm | StrOutputParser()
		self.general_chain = self.general_prompt | self.llm | StrOutputParser()
		self.title_chain = self.title_prompt | self.llm | StrOutputParser()
		self.follow_up_chain = self.follow_up_prompt | self.llm | StrOutputParser()
		self._active_model = model_name

	@staticmethod
	def _load_environment() -> None:
		"""Load environment variables from backend/.env if available."""
		if load_dotenv is None:
			return

		env_path = Path(__file__).resolve().parent / ".env"
		if env_path.exists():
			load_dotenv(dotenv_path=env_path, override=False)

	@staticmethod
	def _enforce_subheading_bullets(markdown_text: str) -> str:
		"""Ensure markdown content under ### subheadings is formatted as bullet points."""
		if not markdown_text or not markdown_text.strip():
			return markdown_text

		lines = markdown_text.splitlines()
		normalized_lines: list[str] = []
		inside_level3 = False

		for line in lines:
			stripped = line.strip()

			if re.match(r"^(\d+[\).]|[ivxlcdm]+\))\s+", stripped, flags=re.IGNORECASE):
				stripped = f"### {stripped}"
				line = stripped

			if stripped.startswith("### "):
				inside_level3 = True
				normalized_lines.append(line)
				continue

			if stripped.startswith("## ") or stripped.startswith("# "):
				inside_level3 = False
				normalized_lines.append(line)
				continue

			if not inside_level3:
				normalized_lines.append(line)
				continue

			if not stripped:
				normalized_lines.append(line)
				continue

			if stripped.startswith("- "):
				normalized_lines.append(line)
				continue

			if stripped.startswith("* "):
				normalized_lines.append(f"- {stripped[2:].strip()}")
				continue

			if stripped.startswith((">", "```", "|")):
				normalized_lines.append(line)
				continue

			if re.match(r"^\d+\.\s", stripped):
				normalized_lines.append(line)
				continue

			normalized_lines.append(f"- {stripped}")

		return "\n".join(normalized_lines)

	@staticmethod
	def _bold_legal_labels(markdown_text: str) -> str:
		"""Ensure key legal field labels are bolded for markdown readability."""
		if not markdown_text or not markdown_text.strip():
			return markdown_text

		labels = [
			"Legal basis:",
			"Why this helps:",
			"What the law states:",
			"Essential legal ingredients:",
			"Punishment or legal consequences:",
			"Exceptions, provisos, and defences:",
			"Practical application:",
		]

		updated_text = markdown_text
		for label in labels:
			updated_text = re.sub(
				rf"(?im)^(\s*-\s*)?(?!\*\*)({re.escape(label)})(\s*)",
				lambda match: f"{match.group(1) or ''}**{match.group(2)}**{match.group(3)}",
				updated_text,
			)

		return updated_text

	@staticmethod
	def _normalize_summary_table(markdown_text: str) -> str:
		"""Normalize final summary table to a consistent markdown grid format."""
		if not markdown_text or not markdown_text.strip():
			return markdown_text

		lines = markdown_text.splitlines()
		heading_index: int | None = None
		for i, line in enumerate(lines):
			if line.strip().lower() == "## summary table of applicable laws":
				heading_index = i
				break

		if heading_index is None:
			return markdown_text

		trailing_lines = lines[heading_index + 1 :]
		table_rows: list[list[str]] = []

		for line in trailing_lines:
			stripped = line.strip()
			if not stripped.startswith("|"):
				continue
			cells = [cell.strip() for cell in stripped.strip("|").split("|")]
			if len(cells) < 3:
				continue
			if all(re.fullmatch(r"-+", cell.replace(" ", "")) for cell in cells[:3]):
				continue
			normalized = [re.sub(r"\s+", " ", cell).strip() for cell in cells[:3]]
			table_rows.append(normalized)

		if table_rows:
			# Remove likely header row if model already emitted one.
			first = [value.lower() for value in table_rows[0]]
			if first[0] in {"offence", "legal act"} and first[1] in {"bns section", "section"}:
				table_rows = table_rows[1:]

		rebuilt_table = [
			"## Summary table of applicable laws",
			"| Offence | BNS Section | Description |",
			"| --- | --- | --- |",
		]

		for row in table_rows:
			rebuilt_table.append(f"| {row[0]} | {row[1]} | {row[2]} |")

		if not table_rows:
			rebuilt_table.append("| Not available in retrieved context | Not available | Context did not include enough detail to populate the table. |")

		prefix = lines[:heading_index]
		return "\n".join(prefix + [""] + rebuilt_table).strip()

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
				raw_response = self.chain.invoke(
					{
						"query": masked_query,
						"context": context_text,
					}
				)
				normalized_response = self._enforce_subheading_bullets(str(raw_response))
				normalized_response = self._bold_legal_labels(normalized_response)
				return self._normalize_summary_table(normalized_response)
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

	def generate_general_response(self, masked_query: str) -> str:
		"""Generate a general-purpose response without retrieval context."""
		if not masked_query or not masked_query.strip():
			raise ValueError("masked_query cannot be empty")

		last_error: Exception | None = None
		for model_name in self._model_candidates:
			if model_name != self._active_model:
				try:
					self._switch_model(model_name)
				except Exception as exc:
					last_error = exc
					continue

			try:
				return self.general_chain.invoke({"query": masked_query})
			except Exception as exc:
				last_error = exc
				error_text = str(exc).lower()
				if "model_not_found" in error_text or "does not exist" in error_text:
					logger.warning("Model '%s' unavailable, trying fallback", model_name)
					continue
				logger.exception("Cerebras generation failed for general response")
				raise RuntimeError("Failed to generate general response") from exc

		logger.exception("All configured Cerebras model aliases failed")
		raise RuntimeError("Failed to generate general response") from last_error

	def generate_session_title(self, user_message: str, assistant_message: str) -> str:
		"""Generate a concise session title from the first user/assistant turn."""
		seed_title = " ".join((user_message or "").split())[:80].strip()
		if not seed_title:
			seed_title = "New Chat"

		last_error: Exception | None = None
		for model_name in self._model_candidates:
			if model_name != self._active_model:
				try:
					self._switch_model(model_name)
				except Exception as exc:
					last_error = exc
					continue

			try:
				raw = self.title_chain.invoke(
					{
						"user_message": user_message,
						"assistant_message": assistant_message,
					}
				)
				title = " ".join(str(raw).split()).strip(" \t\n\r\"'`-:,.!?")
				if not title:
					return seed_title
				return title[:120]
			except Exception as exc:
				last_error = exc
				error_text = str(exc).lower()
				if "model_not_found" in error_text or "does not exist" in error_text:
					logger.warning("Model '%s' unavailable, trying fallback", model_name)
					continue
				logger.warning("Failed to generate session title, falling back", exc_info=True)
				break

		if last_error:
			logger.info("Using fallback title because model invocation failed: %s", last_error)
		return seed_title

	def generate_follow_up_questions(
		self,
		user_query: str,
		assistant_answer: str,
		max_count: int = 5,
	) -> list[str]:
		"""Generate contextual follow-up questions for the latest chat turn."""
		query = str(user_query or "").strip()
		answer = str(assistant_answer or "").strip()
		if not query or not answer:
			return []

		target_count = max(1, min(int(max_count or 5), 5))

		last_error: Exception | None = None
		for model_name in self._model_candidates:
			if model_name != self._active_model:
				try:
					self._switch_model(model_name)
				except Exception as exc:
					last_error = exc
					continue

			try:
				raw = self.follow_up_chain.invoke(
					{
						"query": query,
						"answer": answer[:5000],
						"max_count": target_count,
					}
				)
				return self._normalize_follow_ups(str(raw), target_count)
			except Exception as exc:
				last_error = exc
				error_text = str(exc).lower()
				if "model_not_found" in error_text or "does not exist" in error_text:
					logger.warning("Model '%s' unavailable, trying fallback", model_name)
					continue
				logger.warning("Failed to generate follow-up questions", exc_info=True)
				break

		if last_error:
			logger.info("Using fallback follow-up parsing after model failure: %s", last_error)
		return []

	@staticmethod
	def _normalize_follow_ups(raw_text: str, max_count: int) -> list[str]:
		"""Normalize follow-up output into a clean unique list."""
		lines = [line.strip() for line in str(raw_text or "").splitlines()]
		results: list[str] = []
		seen: set[str] = set()

		for line in lines:
			if not line:
				continue

			cleaned = re.sub(r"^[-*\d\.)\s]+", "", line).strip()
			if not cleaned:
				continue
			if not cleaned.endswith("?"):
				cleaned = f"{cleaned}?"
			cleaned = " ".join(cleaned.split())[:120]

			key = cleaned.lower()
			if key in seen:
				continue
			seen.add(key)
			results.append(cleaned)

			if len(results) >= max_count:
				break

		return results
