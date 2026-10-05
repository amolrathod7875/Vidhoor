"""Real Groq integration test (gated by RUN_GROQ_INTEGRATION=1)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from llm_engine import LLMEngine


@pytest.mark.skipif(
    os.environ.get("RUN_GROQ_INTEGRATION") != "1",
    reason="Real Groq integration test disabled",
)
def test_groq_real_integration() -> None:
    engine = LLMEngine(model="openai/gpt-oss-120b")
    response = engine.generate_general_response(
        "Say 'integration test passed' and nothing else."
    )
    assert "integration test passed" in response.lower()
