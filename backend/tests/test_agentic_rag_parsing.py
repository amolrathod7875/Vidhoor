"""Parsing-focused tests for agentic RAG helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agentic_rag import AgenticRagConfig, AgenticRagRunner, RouterDecision

LOG_DIR = Path(__file__).resolve().parent / "test_logs"


def _log_case(name: str, payload: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamped = datetime.utcnow().isoformat()
    payload_with_time = {"timestamp": stamped, **payload}
    (LOG_DIR / f"{name}.json").write_text(
        json.dumps(payload_with_time, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _make_runner() -> AgenticRagRunner:
    runner = AgenticRagRunner.__new__(AgenticRagRunner)
    runner._config = AgenticRagConfig()
    return runner


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BNS", "Bharatiya Nyaya Sanhita"),
        ("bnss", "Bharatiya Nagarik Suraksha Sanhita"),
        ("Constitution", "Constitution of India"),
    ],
)
def test_canonical_act_mapping(raw: str, expected: str) -> None:
    runner = _make_runner()
    result = runner._canonical_act_name(raw)
    _log_case(
        f"canonical_act_{raw.lower()}",
        {"input": raw, "expected": expected, "output": result},
    )
    assert result == expected


def test_extract_inline_field_final_answer() -> None:
    runner = _make_runner()
    raw = (
        '{"status":"sufficient","final_answer":"## What you can do\\n- '
        "File a complaint under BNS Section 64"  # Example legal instruction
        '"}'
    )
    extracted = runner._extract_inline_field(raw, "final_answer")
    _log_case(
        "inline_final_answer",
        {"input": raw, "output": extracted},
    )
    assert extracted is not None
    assert "BNS Section 64" in extracted


def test_parse_judge_output_non_json_fallback() -> None:
    runner = _make_runner()
    raw = (
        "status: sufficient; final_answer: ## What you can do\n"
        "- Report the offence under BNS Section 64"
    )
    decision = runner._parse_judge_output(raw)
    _log_case(
        "judge_non_json",
        {"input": raw, "status": decision.status, "final_answer": decision.final_answer},
    )
    assert decision.status == "sufficient"
    assert decision.final_answer and "BNS Section 64" in decision.final_answer


def test_parse_router_output_invalid_json_fallback() -> None:
    runner = _make_runner()
    raw = "{not json"
    fallback = RouterDecision(
        act_filters=["Bharatiya Nyaya Sanhita"],
        expansions=["section 64"],
        needs_case_law=False,
        needs_recent_case_law=False,
    )
    decision, used = runner._parse_router_output(raw, fallback)
    _log_case(
        "router_invalid_json",
        {
            "input": raw,
            "fallback": fallback.act_filters,
            "output": decision.act_filters,
            "router_used": used,
        },
    )
    assert decision.act_filters == fallback.act_filters
    assert used is False


def test_safe_parse_json_embedded_block() -> None:
    runner = _make_runner()
    raw = "Noise... {\"status\":\"insufficient\",\"clarifying_question\":\"Which Act?\"} ..."
    parsed = runner._safe_parse_json(raw)
    _log_case("safe_parse_json", {"input": raw, "output": parsed})
    assert parsed is not None
    assert parsed.get("clarifying_question") == "Which Act?"


def test_extract_non_json_fields_clarifying_question() -> None:
    runner = _make_runner()
    raw = (
        '"status":"insufficient","clarifying_question":"Please specify the Act and section"'
    )
    decision = runner._extract_non_json_fields(raw)
    _log_case(
        "extract_non_json_clarify",
        {"input": raw, "status": decision.status, "clarify": decision.clarifying_question},
    )
    assert decision.status == "insufficient"
    assert decision.clarifying_question


def _make_citation(
    title: str = "",
    source: str = "",
    doc_id: str = "",
    doc_type: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        title=title,
        source=source,
        doc_id=doc_id,
        doc_type=doc_type,
        confidence=0.9,
        snippet="",
    )


def test_should_clarify_explicit_act_with_citation_returns_false() -> None:
    runner = _make_runner()
    query = "help me to understand BNS Section 34"
    citations = [_make_citation(title="BNS Section 34", doc_type="statute")]
    assert runner._should_clarify_general_query(query, citations, []) is False


def test_should_clarify_explicit_act_without_citation_returns_true() -> None:
    runner = _make_runner()
    query = "help me to understand BNS Section 34"
    assert runner._should_clarify_general_query(query, [], []) is True


def test_should_clarify_ambiguous_query_no_act_no_ref_returns_true() -> None:
    runner = _make_runner()
    query = "tell me about law"
    assert runner._should_clarify_general_query(query, [], []) is True


def test_should_clarify_enhanced_style_query_with_bns_citation_returns_false() -> None:
    runner = _make_runner()
    query = (
        "You are a legal analyst. BNS (Bharatiya Nyaya Sanhita) Section 34 deals with "
        "private defence. Identify and cite leading Indian case law. "
        "Interpretation & Case Law: provide meaning, explanation, punishment, ingredients."
    )
    citations = [_make_citation(title="BNS Section 34", doc_type="statute")]
    assert runner._should_clarify_general_query(query, citations, []) is False


def test_should_clarify_enhanced_style_query_without_citation_returns_true() -> None:
    runner = _make_runner()
    query = (
        "You are a legal analyst. BNS (Bharatiya Nyaya Sanhita) Section 34 deals with "
        "private defence. Identify and cite leading Indian case law."
    )
    assert runner._should_clarify_general_query(query, [], []) is True
