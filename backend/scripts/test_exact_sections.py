"""Exact section test for pilot collection."""
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

manager = QdrantManager(collection_name="indian_law_v2_pilot")

queries = [
    ("What is Section 9 of the Constitution?", "9"),
    ("What is Article 32 of the Constitution?", "32"),
    ("What is Section 51A of the Constitution?", "51A"),
]

for query, expected_section in queries:
    print(f"\n=== Query: {query} ===")
    result = manager.retrieve_context_with_metadata(
        query_string=query,
        filter_status="active",
        filter_act=None,
    )
    citations = result.get("citations", [])
    print(f"Citations: {len(citations)}")
    
    found_correct = False
    for i, c in enumerate(citations[:5]):
        section = c.get('section', '')
        article = c.get('article', '')
        title = c.get('title', '')
        snippet = str(c.get('snippet', ''))[:80]
        score = c.get('confidence', 0)
        print(f"  {i+1}. Section: {section} | Article: {article} | Score: {score:.4f}")
        print(f"     Title: {title}")
        print(f"     Snippet: {snippet}")
        if section == expected_section or article == expected_section:
            found_correct = True
    
    if found_correct:
        print(f"  PASS: Expected section {expected_section} found in top results")
    else:
        print(f"  FAIL: Expected section {expected_section} not found in top results")
