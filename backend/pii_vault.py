"""PII masking and unmasking utilities for Vidhoor backend."""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Detection:
	"""Represents a detected sensitive span in text."""

	start: int
	end: int
	entity_type: str
	score: float = 0.5


class PIIVault:
	"""Mask and unmask PII entities for safe retrieval and generation."""

	def __init__(self) -> None:
		self._analyzer = None
		self._presidio_ready = False
		self._allowed_entities = {
			"PERSON",
			"EMAIL_ADDRESS",
			"PHONE_NUMBER",
			"IN_AADHAAR",
			"IN_PAN",
		}
		self._init_presidio()

	def _init_presidio(self) -> None:
		"""Initialize Presidio analyzer and add Indian legal identifiers."""
		try:
			from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer

			analyzer = AnalyzerEngine()

			pan_recognizer = PatternRecognizer(
				supported_entity="IN_PAN",
				patterns=[
					Pattern(
						name="in_pan_pattern",
						regex=r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
						score=0.85,
					)
				],
				supported_language="en",
			)

			aadhaar_recognizer = PatternRecognizer(
				supported_entity="IN_AADHAAR",
				patterns=[
					Pattern(
						name="in_aadhaar_pattern",
						regex=r"\b\d{4}\s*\d{4}\s*\d{4}\b",
						score=0.85,
					)
				],
				supported_language="en",
			)

			analyzer.registry.add_recognizer(pan_recognizer)
			analyzer.registry.add_recognizer(aadhaar_recognizer)

			self._analyzer = analyzer
			self._presidio_ready = True
		except Exception as exc:
			logger.warning("Presidio unavailable, using regex-only masking: %s", exc)
			self._presidio_ready = False

	@staticmethod
	def _prune_overlaps(detections: list[Detection]) -> list[Detection]:
		"""Keep highest-priority non-overlapping detections."""
		if not detections:
			return []

		ordered = sorted(
			detections,
			key=lambda x: (x.start, -(x.end - x.start), -x.score),
		)

		kept: list[Detection] = []
		current_end = -1
		for item in ordered:
			if item.start >= current_end:
				kept.append(item)
				current_end = item.end
		return kept

	@staticmethod
	def _is_likely_person_phrase(value: str) -> bool:
		"""Heuristic filter to reduce non-name PERSON false positives."""
		cleaned = re.sub(r"\s+", " ", value).strip()
		if not cleaned:
			return False

		lowered = cleaned.lower()
		disallowed_phrases = {
			"police station",
			"complainant details",
			"aadhaar no",
			"mobile no",
			"vehicle theft",
			"information of offence",
			"acts & sections",
			"acts and sections",
		}
		if any(phrase in lowered for phrase in disallowed_phrases):
			return False

		if re.search(r"\d", cleaned):
			return False

		disallowed_tokens = {
			"explain",
			"article",
			"section",
			"constitution",
			"bharatiya",
			"nyaya",
			"sanhita",
			"what",
			"tell",
			"please",
			"police",
			"station",
			"complainant",
			"details",
			"aadhaar",
			"mobile",
			"vehicle",
			"theft",
			"information",
			"offence",
			"acts",
			"sections",
			"signature",
			"address",
			"type",
			"hero",
			"splendor",
			"security",
			"code",
			"authority",
			"nigadi",
			"flat",
			"civil",
			"suraksha",
			"nagarik",
		}

		if re.fullmatch(r"(?:[A-Z]\.\s*){1,3}[A-Z][a-z]+", cleaned):
			last_name = re.sub(r"^(?:[A-Z]\.\s*){1,3}", "", cleaned).lower()
			return last_name not in disallowed_tokens

		if re.fullmatch(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2}", cleaned):
			words = [word.lower() for word in cleaned.split()]
			return not any(word in disallowed_tokens for word in words)

		return False

	@staticmethod
	def _fallback_regex_detections(text: str) -> list[Detection]:
		"""Detect common PII entities with regex when Presidio is not available."""
		patterns: list[tuple[str, str]] = [
			("EMAIL_ADDRESS", r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b"),
			("PHONE_NUMBER", r"\b(?:\+91[-\s]?)?[6-9]\d{9}\b"),
			("IN_AADHAAR", r"\b\d{4}\s*\d{4}\s*\d{4}\b"),
			("IN_PAN", r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),
			(
				"PERSON",
				r"\b(?:Mr|Ms|Mrs|Dr|Adv)\.?[ \t]+[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){0,2}\b|\b[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+){1,2}\b",
			),
		]

		detections: list[Detection] = []
		for entity_type, pattern in patterns:
			for match in re.finditer(pattern, text):
				if entity_type == "PERSON" and not PIIVault._is_likely_person_phrase(match.group(0)):
					continue
				detections.append(
					Detection(
						start=match.start(),
						end=match.end(),
						entity_type=entity_type,
						score=0.7,
					)
				)

		return PIIVault._prune_overlaps(detections)

	def _presidio_detections(self, text: str) -> list[Detection]:
		"""Detect PII with Presidio recognizers."""
		if not self._presidio_ready or self._analyzer is None:
			return []

		try:
			results = self._analyzer.analyze(text=text, language="en")
			detections = [
				Detection(
					start=result.start,
					end=result.end,
					entity_type=result.entity_type,
					score=result.score,
				)
				for result in results
				if result.entity_type != "PERSON"
				or self._is_likely_person_phrase(text[result.start : result.end])
			]
			return self._prune_overlaps(detections)
		except Exception as exc:
			logger.warning("Presidio detection failed, falling back to regex: %s", exc)
			return []

	def mask_text(self, text: str) -> tuple[str, dict[str, str]]:
		"""Mask PII entities with stable placeholders.

		Returns:
			Tuple of (masked_text, placeholder_to_original_mapping).
		"""
		if not text:
			return text, {}

		presidio_detections = [
			item for item in self._presidio_detections(text) if item.entity_type in self._allowed_entities
		]
		fallback_detections = self._fallback_regex_detections(text)
		detections = self._prune_overlaps(presidio_detections + fallback_detections)

		if not detections:
			return text, {}

		counters: dict[str, int] = defaultdict(int)
		value_entity_to_placeholder: dict[tuple[str, str], str] = {}
		replacements: list[tuple[int, int, str]] = []
		placeholder_map: dict[str, str] = {}

		for item in detections:
			original_value = text[item.start : item.end]
			normalized_value = original_value.strip()
			if not normalized_value:
				continue

			key = (item.entity_type, normalized_value)
			if key not in value_entity_to_placeholder:
				counters[item.entity_type] += 1
				placeholder = f"<{item.entity_type}_{counters[item.entity_type]}>"
				value_entity_to_placeholder[key] = placeholder
				placeholder_map[placeholder] = normalized_value

			replacements.append((item.start, item.end, value_entity_to_placeholder[key]))

		masked_text = text
		for start, end, placeholder in sorted(replacements, key=lambda x: x[0], reverse=True):
			masked_text = masked_text[:start] + placeholder + masked_text[end:]

		return masked_text, placeholder_map

	@staticmethod
	def unmask_text(masked_text: str, placeholder_map: dict[str, str]) -> str:
		"""Restore placeholders back to original values in generated text."""
		if not masked_text or not placeholder_map:
			return masked_text

		restored = masked_text
		for placeholder in sorted(placeholder_map.keys(), key=len, reverse=True):
			restored = restored.replace(placeholder, placeholder_map[placeholder])
		return restored
