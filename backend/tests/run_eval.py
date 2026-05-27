"""Run evaluation queries against the backend API and compute accuracy metrics."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = Path(__file__).resolve().parent / "test_logs"
CASES_PATH = Path(__file__).resolve().parent / "eval_cases.json"


def _normalize_ref(value: str) -> str:
    cleaned = value.lower()
    for token in ("section", "sec", "article", "art"):
        cleaned = cleaned.replace(token, "")
    cleaned = "".join(ch for ch in cleaned if ch.isalnum())
    return cleaned.upper()


def _extract_refs_from_snippet(snippet: str) -> set[str]:
    if not snippet:
        return set()
    refs = set()
    for token in snippet.split():
        norm = _normalize_ref(token)
        if norm and any(ch.isdigit() for ch in norm):
            refs.add(norm)
    return refs


def _normalize_act(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _act_aliases(value: str) -> list[str]:
    normalized = _normalize_act(value)
    if not normalized:
        return []

    mapping = {
        "bharatiyanyayasanhita": ["bharatiyanyayasanhita", "bns"],
        "bharatiyanagariksurakshasanhita": ["bharatiyanagariksurakshasanhita", "bnss"],
        "bharatiyasakshyaadhiniyam": ["bharatiyasakshyaadhiniyam", "bsa"],
        "constitutionofindia": ["constitutionofindia", "constitution"],
        "informationtechnologyact2000": ["informationtechnologyact2000", "informationtechnologyact", "itact", "itact2000"],
        "ipc": ["ipc", "indianpenalcode"],
    }

    for key, aliases in mapping.items():
        if normalized == key:
            return aliases

    return [normalized]


def _is_clarify(response_text: str, citations: list[dict[str, Any]]) -> bool:
    text = (response_text or "").strip().lower()
    if citations:
        return False
    if text.endswith("?"):
        return True
    return text.startswith("please") or "clarify" in text or "specify" in text


def _match_expected_sections(citations: list[dict[str, Any]], expected_sections: list[str]) -> dict[str, Any]:
    expected_norm = [_normalize_ref(item) for item in expected_sections if item]
    if not expected_norm:
        return {"precision": None, "recall": None, "matched": []}

    matched_sections = set()
    hit_count = 0
    for citation in citations:
        section = _normalize_ref(str(citation.get("section") or ""))
        snippet = str(citation.get("snippet") or "")
        snippet_refs = _extract_refs_from_snippet(snippet)
        citation_refs = {section} | snippet_refs
        if any(ref in expected_norm for ref in citation_refs if ref):
            hit_count += 1
            matched_sections.update(ref for ref in citation_refs if ref in expected_norm)

    precision = hit_count / len(citations) if citations else 0.0
    recall = len(matched_sections) / len(expected_norm)
    return {"precision": precision, "recall": recall, "matched": sorted(matched_sections)}


def _match_expected_act(citations: list[dict[str, Any]], expected_act: str) -> bool | None:
    if not expected_act:
        return None
    expected_aliases = _act_aliases(expected_act)
    if not expected_aliases:
        return None

    for citation in citations:
        haystack = " ".join(
            [
                str(citation.get("title") or ""),
                str(citation.get("source") or ""),
                str(citation.get("doc_id") or ""),
            ]
        )
        normalized_haystack = _normalize_act(haystack)
        if any(alias in normalized_haystack for alias in expected_aliases):
            return True
    return False


def _load_cases() -> list[dict[str, Any]]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _write_log(payload: dict[str, Any]) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    target = LOG_DIR / f"eval_results_{stamp}.json"
    target.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    return target


def run_eval(base_url: str, token: str | None, timeout: float) -> int:
    cases = _load_cases()
    results: list[dict[str, Any]] = []

    for case in cases:
        payload = {
            "message": case["query"],
            "session_id": f"eval_{case['id']}",
            "is_temporary_chat": True,
        }
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        response = requests.post(
            f"{base_url}/api/chat",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()

        citations = body.get("citations") or []
        response_text = body.get("response") or ""
        expected_sections = case.get("expected_sections") or []
        expected_act = case.get("expected_act") or ""

        section_match = _match_expected_sections(citations, expected_sections)
        act_match = _match_expected_act(citations, expected_act)
        clarify = _is_clarify(response_text, citations)
        expect_clarify = bool(case.get("expect_clarify"))

        results.append(
            {
                "id": case["id"],
                "query": case["query"],
                "response": response_text,
                "citations": citations,
                "precision": section_match["precision"],
                "recall": section_match["recall"],
                "matched_sections": section_match["matched"],
                "act_match": act_match,
                "clarify_detected": clarify,
                "clarify_expected": expect_clarify,
                "clarify_correct": clarify == expect_clarify,
            }
        )

    precision_vals = [r["precision"] for r in results if r["precision"] is not None]
    recall_vals = [r["recall"] for r in results if r["recall"] is not None]
    act_vals = [r["act_match"] for r in results if r["act_match"] is not None]
    clarify_vals = [r["clarify_correct"] for r in results]

    summary = {
        "precision_avg": sum(precision_vals) / len(precision_vals) if precision_vals else None,
        "recall_avg": sum(recall_vals) / len(recall_vals) if recall_vals else None,
        "act_match_rate": sum(1 for v in act_vals if v) / len(act_vals) if act_vals else None,
        "clarify_accuracy": sum(1 for v in clarify_vals if v) / len(clarify_vals) if clarify_vals else None,
        "total_cases": len(results),
    }

    report = {"summary": summary, "results": results}
    log_path = _write_log(report)

    print(json.dumps(summary, indent=2))
    print(f"\nDetailed results saved to: {log_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--token", default="")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()

    token = args.token.strip() or None
    return run_eval(args.base_url.rstrip("/"), token, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
