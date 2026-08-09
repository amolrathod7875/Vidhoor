from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from llm_engine import LLMEngine


def test_sanitize_enhanced_prompt_strips_instructional_prefixes() -> None:
    raw_prompt = "what is BNS Section 64"
    enhanced = (
        "You are a legal analyst. Please answer in markdown. "
        "Explain BNS (Bharatiya Nyaya Sanhita) Section 64. Do not fabricate citations."
    )

    result = LLMEngine._sanitize_enhanced_prompt(enhanced, raw_prompt)

    assert result == "Explain BNS (Bharatiya Nyaya Sanhita) Section 64."


def test_sanitize_enhanced_prompt_falls_back_to_raw_prompt_when_empty() -> None:
    raw_prompt = "what is BNS Section 64"

    result = LLMEngine._sanitize_enhanced_prompt("   ", raw_prompt)

    assert result == raw_prompt