"""Test reranker on real pilot retrieval results."""
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
os.environ["QDRANT_ENABLE_RERANKER"] = "true"
os.environ["BGE_DEVICE"] = "cpu"
os.environ["BGE_USE_FP16"] = "false"
os.environ["BGE_BATCH_SIZE"] = "1"
os.environ["BGE_MAX_LENGTH"] = "1024"
os.environ["RERANKER_DEVICE"] = "cpu"
os.environ["RERANKER_USE_FP16"] = "false"
os.environ["RERANKER_BATCH_SIZE"] = "4"
os.environ["RERANKER_MAX_LENGTH"] = "1024"

from qdrant_manager import QdrantManager
from services.legal_reranker import rerank_candidates

manager = QdrantManager(collection_name="indian_law_v2_pilot")

query = "What does the Constitution say about personal liberty?"
print(f"=== Query: {query} ===")

# Get hybrid results
start = time.perf_counter()
result = manager.retrieve_context_with_metadata(
    query_string=query,
    filter_status="active",
    filter_act=None,
)
hybrid_latency = time.perf_counter() - start
citations = result.get("citations", [])
print(f"Hybrid retrieval: {len(citations)} results, {hybrid_latency:.2f}s")

# Rerank top 20 (we only have 8, so use all)
start = time.perf_counter()
reranked = rerank_candidates(query, citations[:20], text_key="snippet")
rerank_latency = time.perf_counter() - start
print(f"Reranking: {len(reranked)} results, {rerank_latency:.2f}s")

print("\nComparison:")
print(f"{'Rank':<5} {'Type':<10} {'Score':<10} {'Section':<10} {'Snippet':<60}")
print("-" * 100)
for i, c in enumerate(citations[:5]):
    print(f"{i+1:<5} {'hybrid':<10} {c.get('confidence', 0):.4f}     {c.get('section', ''):<10} {str(c.get('snippet', ''))[:60]}")

for i, c in enumerate(reranked[:5]):
    print(f"{i+1:<5} {'reranked':<10} {c.get('reranker_score', 0):.4f}     {c.get('section', ''):<10} {str(c.get('snippet', ''))[:60]}")
