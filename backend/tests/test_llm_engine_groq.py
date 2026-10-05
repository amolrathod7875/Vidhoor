"""Tests for LLMEngine Groq integration."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from llm_engine import LLMEngine


def test_missing_groq_api_key_raises() -> None:
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch.object(LLMEngine, "_load_environment", return_value=None):
            with pytest.raises(EnvironmentError, match="GROQ_API_KEY is not set"):
                LLMEngine()


def test_initializes_with_primary_model() -> None:
    with mock.patch.dict(os.environ, {"GROQ_API_KEY": "test-key", "GROQ_MODEL": "openai/gpt-oss-120b"}):
        with mock.patch("llm_engine._load_chat_groq") as load_mock:
            mock_cls = mock.Mock()
            mock_cls.return_value = mock.Mock()
            load_mock.return_value = mock_cls
            engine = LLMEngine(model="openai/gpt-oss-120b")
            mock_cls.assert_called_once()
            kwargs = mock_cls.call_args.kwargs
            assert kwargs["api_key"] == "test-key"
            assert kwargs["model"] == "openai/gpt-oss-120b"
            assert engine._active_model == "openai/gpt-oss-120b"


def test_build_model_candidates_uses_fallback_env() -> None:
    with mock.patch.dict(os.environ, {"GROQ_FALLBACK_MODEL": "openai/gpt-oss-20b"}):
        candidates = LLMEngine._build_model_candidates("openai/gpt-oss-120b")
        assert candidates == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]


def test_temperature_parsed_from_env() -> None:
    with mock.patch.dict(os.environ, {"GROQ_API_KEY": "test-key", "GROQ_TEMPERATURE": "0.1"}):
        with mock.patch("llm_engine._load_chat_groq") as load_mock:
            mock_cls = mock.Mock()
            mock_cls.return_value = mock.Mock()
            load_mock.return_value = mock_cls
            LLMEngine()
            kwargs = mock_cls.call_args.kwargs
            assert kwargs["temperature"] == 0.1


def test_request_timeout_parsed_from_env() -> None:
    with mock.patch.dict(os.environ, {"GROQ_API_KEY": "test-key", "GROQ_REQUEST_TIMEOUT": "30"}):
        with mock.patch("llm_engine._load_chat_groq") as load_mock:
            mock_cls = mock.Mock()
            mock_cls.return_value = mock.Mock()
            load_mock.return_value = mock_cls
            LLMEngine()
            kwargs = mock_cls.call_args.kwargs
            assert kwargs["timeout"] == 30


def test_provider_error_triggers_fallback() -> None:
    with mock.patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
        with mock.patch("llm_engine._load_chat_groq") as load_mock:
            mock_cls = mock.Mock()
            mock_llm_fail = mock.Mock()
            mock_llm_fail.side_effect = Exception("model_not_found: foo")
            mock_llm_ok = mock.Mock()
            mock_llm_ok.return_value = "Fallback response"
            mock_cls.side_effect = [mock_llm_fail, mock_llm_ok]
            load_mock.return_value = mock_cls
            engine = LLMEngine()
            result = engine.generate_general_response("hello")
            assert result == "Fallback response"


def test_rate_limit_triggers_fallback() -> None:
    mock_groq = mock.Mock()
    mock_groq.RateLimitError = type("RateLimitError", (Exception,), {})

    with mock.patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
        with mock.patch("llm_engine.groq", mock_groq):
            with mock.patch("llm_engine._load_chat_groq") as load_mock:
                mock_cls = mock.Mock()
                mock_llm_fail = mock.Mock()
                rate_limit_exc = mock_groq.RateLimitError("rate limit")
                mock_llm_fail.side_effect = rate_limit_exc
                mock_llm_ok = mock.Mock()
                mock_llm_ok.return_value = "Fallback response"
                mock_cls.side_effect = [mock_llm_fail, mock_llm_ok]
                load_mock.return_value = mock_cls
                engine = LLMEngine()
                result = engine.generate_general_response("hello")
                assert result == "Fallback response"


def test_generate_legal_response_normalizes_output() -> None:
    with mock.patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
        with mock.patch("llm_engine._load_chat_groq") as load_mock:
            mock_llm = mock.Mock()
            mock_llm.return_value = "Raw response"
            mock_cls = mock.Mock(return_value=mock_llm)
            load_mock.return_value = mock_cls
            engine = LLMEngine()
            result = engine.generate_legal_response("query", ["context"])
            assert isinstance(result, str)


def test_generate_session_title_returns_seed_on_failure() -> None:
    with mock.patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
        with mock.patch("llm_engine._load_chat_groq") as load_mock:
            mock_llm = mock.Mock()
            mock_llm.invoke.side_effect = Exception("fail")
            mock_cls = mock.Mock(return_value=mock_llm)
            load_mock.return_value = mock_cls
            engine = LLMEngine()
            title = engine.generate_session_title("user msg", "assistant msg")
            assert title == "user msg"


def test_generate_follow_up_questions_returns_list_on_failure() -> None:
    with mock.patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
        with mock.patch("llm_engine._load_chat_groq") as load_mock:
            mock_llm = mock.Mock()
            mock_llm.invoke.side_effect = Exception("fail")
            mock_cls = mock.Mock(return_value=mock_llm)
            load_mock.return_value = mock_cls
            engine = LLMEngine()
            follow_ups = engine.generate_follow_up_questions("q", "a")
            assert follow_ups == []
