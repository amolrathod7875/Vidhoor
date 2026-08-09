from __future__ import annotations

import logging
import os
from typing import Any

from langdetect import detect
from transformers import MarianMTModel, MarianTokenizer

logger = logging.getLogger(__name__)

INDIC_LANGUAGES = {"hi", "mr", "bn", "ta"}
MODEL_BY_LANGUAGE: dict[str, str] = {
    "hi": "Helsinki-NLP/opus-mt-hi-en",
    "mr": "Helsinki-NLP/opus-mt-mr-en",
    "bn": "Helsinki-NLP/opus-mt-bn-en",
    "ta": "Helsinki-NLP/opus-mt-ta-en",
}

_model_cache: dict[str, tuple[MarianTokenizer, MarianMTModel]] = {}


def _has_meaningful_devanagari(text: str, minimum_chars: int = 8) -> bool:
    devanagari_count = sum(1 for char in text if "\u0900" <= char <= "\u097F")
    return devanagari_count >= minimum_chars


def translate_via_llm(text: str, llm_engine: Any) -> str:
    """Translate text to English using the LLM with a strict translate-only prompt."""
    if not llm_engine:
        return text
    if not text or not text.strip():
        return text
    prompt = (
        "Translate the following Indian legal document text to clear, factual English. "
        "Preserve all names, numbers, dates, statute names and section numbers exactly. "
        "Return only the translation.\n\n"
        f"{text}"
    )
    try:
        return llm_engine.generate_general_response(prompt)
    except Exception as exc:
        logger.warning("LLM translation failed, returning original text: %s", exc)
        return text


def resolve_translation_language(text: str, detected_language: str | None = None) -> str:
    """Resolve language used for translation with script-aware fallback."""
    resolved = (detected_language or detect_language(text) or "unknown").strip().lower()
    if resolved in MODEL_BY_LANGUAGE:
        return resolved

    if resolved in {"en", "unknown"} and _has_meaningful_devanagari(text):
        fallback = os.environ.get("HELSINKI_DEVANAGARI_FALLBACK", "mr").strip().lower()
        if fallback in MODEL_BY_LANGUAGE:
            return fallback

    return resolved


def _split_text_for_translation(text: str, max_chars: int = 450) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""

    for part in text.split("\n"):
        candidate = f"{current}\n{part}".strip() if current else part
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)

        if len(part) <= max_chars:
            current = part
            continue

        start = 0
        while start < len(part):
            end = min(start + max_chars, len(part))
            chunks.append(part[start:end])
            start = end
        current = ""

    if current:
        chunks.append(current)

    return [item for item in chunks if item.strip()]


def _get_model(language: str) -> tuple[MarianTokenizer, MarianMTModel] | None:
    model_name = MODEL_BY_LANGUAGE.get(language)
    if not model_name:
        return None

    cached = _model_cache.get(model_name)
    if cached:
        return cached

    try:
        tokenizer = MarianTokenizer.from_pretrained(model_name)
        model = MarianMTModel.from_pretrained(model_name)
        _model_cache[model_name] = (tokenizer, model)
        return tokenizer, model
    except Exception as exc:
        logger.warning("Failed to load Helsinki translation model '%s': %s", model_name, exc)
        return None


def detect_language(text: str) -> str:
    if not text or not text.strip():
        return "unknown"

    sample = text[:4000]
    try:
        return detect(sample)
    except Exception:
        return "unknown"


def translate_to_english(text: str, language: str | None = None, llm_engine: Any = None) -> str:
    """Translate text to English using LLM for Indic scripts, falling back to Helsinki or original."""
    if not text or not text.strip():
        return text

    resolved_language = resolve_translation_language(text, detected_language=language)

    if resolved_language in {"en", "unknown"}:
        return text

    use_marian = os.environ.get("USE_MARIAN_TRANSLATION", "0").strip().lower() in {"1", "true", "yes"}

    if resolved_language in INDIC_LANGUAGES or _has_meaningful_devanagari(text):
        if llm_engine is not None and not use_marian:
            return translate_via_llm(text, llm_engine)
        if use_marian:
            model_bundle = _get_model(resolved_language)
            if model_bundle:
                tokenizer, model = model_bundle
                translated_parts: list[str] = []
                for part in _split_text_for_translation(text):
                    encoded = tokenizer([part], return_tensors="pt", truncation=True, max_length=512)
                    generated = model.generate(**encoded, max_length=512)
                    translated = tokenizer.batch_decode(generated, skip_special_tokens=True)[0]
                    translated_parts.append(translated)
                return "\n".join(translated_parts).strip()
        return text

    model_bundle = _get_model(resolved_language)
    if not model_bundle:
        return text

    tokenizer, model = model_bundle
    translated_parts: list[str] = []

    for part in _split_text_for_translation(text):
        encoded = tokenizer([part], return_tensors="pt", truncation=True, max_length=512)
        generated = model.generate(**encoded, max_length=512)
        translated = tokenizer.batch_decode(generated, skip_special_tokens=True)[0]
        translated_parts.append(translated)

    return "\n".join(translated_parts).strip()


def translate_pages_to_english(pages: list[dict[str, Any]], llm_engine: Any = None) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []

    for item in pages:
        original_text = str(item.get("text") or "")
        detected_language = detect_language(original_text)
        resolved_language = resolve_translation_language(original_text, detected_language=detected_language)
        translated_text = translate_to_english(original_text, language=resolved_language, llm_engine=llm_engine)
        output.append(
            {
                "page": int(item.get("page") or 1),
                "detected_language": resolved_language,
                "text": original_text,
                "text_en": translated_text,
            }
        )

    return output
