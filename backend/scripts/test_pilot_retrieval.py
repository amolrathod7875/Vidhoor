"""Pilot retrieval test for Qdrant."""
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
    "Explain Article 21 of the Constitution of India.",
    "What does the Constitution say about personal liberty?",
    "Find relevant murder case law.",
    "What is Section 64 of BNS?",
]

for query in queries:
    print(f"\n=== Query: {query} ===")
    start = time.perf_counter()
    result = manager.retrieve_context_with_metadata(
        query_string=query,
        filter_status="active",
        filter_act=None,
    )
    latency = time.perf_counter() - start
    print(f"Latency: {latency:.2f}s")
    citations = result.get("citations", [])
    print(f"Citations: {len(citations)}")
    for i, c in enumerate(citations[:3]):
        print(f"  {i+1}. Act: {c.get('act', '')} | Section: {c.get('section', '')} | Article: {c.get('article', '')}")
        print(f"     Source: {c.get('source', '')} | Doc type: {c.get('doc_type', '')}")
        print(f"     Snippet: {str(c.get('snippet', c.get('text', '')))[:100]}")
        print(f"     Score: {c.get('confidence', c.get('reranker_score', 0)):.4f}")
