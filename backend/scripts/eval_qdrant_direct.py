"""Direct Qdrant retrieval evaluation using eval_cases.json."""
import json
import os
import sys
import time

sys.path.insert(0, r"D:\vidhoor-legal-copilot\backend")

os.environ["QDRANT_HOST"] = "127.0.0.1"
os.environ["QDRANT_PORT"] = "6333"
os.environ["QDRANT_GRPC_PORT"] = "6334"
os.environ["QDRANT_COLLECTION"] = "indian_law_v2_pilot"
os.environ["QDRANT_PREFER_GRPC"] = "true"
os.environ["QDRANT_ENABLE_HYBRID"] = "true"
os.environ["QDRANT_ENABLE_RERANKER"] = "false"
os.environ["BGE_DEVICE"] = "cpu"
os.environ["BGE_USE_FP16"] = "false"
os.environ["BGE_BATCH_SIZE"] = "1"
os.environ["BGE_MAX_LENGTH"] = "1024"

from qdrant_manager import QdrantManager

CASES_PATH = r"D:\vidhoor-legal-copilot\backend\tests\eval_cases.json"

def normalize_ref(value: str) -> str:
    cleaned = value.lower()
    for token in ("section", "sec", "article", "art"):
        cleaned = cleaned.replace(token, "")
    cleaned = "".join(ch for ch in cleaned if ch.isalnum())
    return cleaned.upper()

def normalize_act(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())

def act_aliases(value: str) -> list[str]:
    normalized = normalize_act(value)
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

def match_expected_act(citations: list[dict], expected_act: str) -> bool | None:
    if not expected_act:
        return None
    expected_aliases = act_aliases(expected_act)
    if not expected_aliases:
        return None
    for citation in citations:
        haystack = " ".join([
            str(citation.get("title") or ""),
            str(citation.get("source") or ""),
            str(citation.get("doc_id") or ""),
        ])
        normalized_haystack = normalize_act(haystack)
        if any(alias in normalized_haystack for alias in expected_aliases):
            return True
    return False

def match_expected_sections(citations: list[dict], expected_sections: list[str]) -> dict:
    expected_norm = [normalize_ref(item) for item in expected_sections if item]
    if not expected_norm:
        return {"precision": None, "recall": None, "matched": []}
    
    matched_sections = set()
    hit_count = 0
    for citation in citations:
        section = normalize_ref(str(citation.get("section") or ""))
        snippet = str(citation.get("snippet") or "")
        snippet_refs = set()
        for token in snippet.split():
            norm = normalize_ref(token)
            if norm and any(ch.isdigit() for ch in norm):
                snippet_refs.add(norm)
        citation_refs = {section} | snippet_refs
        if any(ref in expected_norm for ref in citation_refs if ref):
            hit_count += 1
            matched_sections.update(ref for ref in citation_refs if ref in expected_norm)
    
    precision = hit_count / len(citations) if citations else 0.0
    recall = len(matched_sections) / len(expected_norm)
    return {"precision": precision, "recall": recall, "matched": sorted(matched_sections)}

def main():
    manager = QdrantManager(collection_name="indian_law_v2_pilot")
    
    with open(CASES_PATH, "r", encoding="utf-8") as f:
        cases = json.load(f)
    
    results = []
    for case in cases:
        query = case["query"]
        expected_sections = case.get("expected_sections") or []
        expected_act = case.get("expected_act") or ""
        expect_clarify = bool(case.get("expect_clarify"))
        
        start = time.perf_counter()
        try:
            result = manager.retrieve_context_with_metadata(
                query_string=query,
                filter_status="active",
                filter_act=None,
            )
            latency = time.perf_counter() - start
            citations = result.get("citations", [])
            response_text = result.get("documents", [])
            response_text = " ".join(response_text) if response_text else ""
            
            section_match = match_expected_sections(citations, expected_sections)
            act_match = match_expected_act(citations, expected_act)
            clarify_detected = len(citations) == 0
            
            results.append({
                "id": case["id"],
                "query": query,
                "citations_count": len(citations),
                "precision": section_match["precision"],
                "recall": section_match["recall"],
                "matched_sections": section_match["matched"],
                "act_match": act_match,
                "clarify_detected": clarify_detected,
                "clarify_expected": expect_clarify,
                "clarify_correct": clarify_detected == expect_clarify,
                "latency": latency,
            })
        except Exception as exc:
            results.append({
                "id": case["id"],
                "query": query,
                "error": str(exc),
            })
    
    precision_vals = [r["precision"] for r in results if r.get("precision") is not None]
    recall_vals = [r["recall"] for r in results if r.get("recall") is not None]
    act_vals = [r["act_match"] for r in results if r.get("act_match") is not None]
    clarify_vals = [r["clarify_correct"] for r in results if "clarify_correct" in r]
    
    summary = {
        "precision_avg": sum(precision_vals) / len(precision_vals) if precision_vals else None,
        "recall_avg": sum(recall_vals) / len(recall_vals) if recall_vals else None,
        "act_match_rate": sum(1 for v in act_vals if v) / len(act_vals) if act_vals else None,
        "clarify_accuracy": sum(1 for v in clarify_vals if v) / len(clarify_vals) if clarify_vals else None,
        "total_cases": len(results),
    }
    
    print("=== Retrieval Evaluation (Pilot Collection) ===")
    print(json.dumps(summary, indent=2))
    print("\nDetailed results:")
    for r in results:
        cid = r.get('id', 'ERR')
        cc = r.get('citations_count', 'ERR')
        am = r.get('act_match')
        prec = r.get('precision')
        rec = r.get('recall')
        print(f"  {cid}: citations={cc}, act_match={am}, precision={prec}, recall={rec}")
    
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
