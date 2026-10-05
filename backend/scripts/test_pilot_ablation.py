"""Pilot retrieval test with dense/sparse/hybrid ablation."""
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
from qdrant_client.models import Filter, FieldCondition, MatchValue

manager = QdrantManager(collection_name="indian_law_v2_pilot")

query = "What does the Constitution say about personal liberty?"
print(f"=== Query: {query} ===")

# Test hybrid (default)
start = time.perf_counter()
result = manager.retrieve_context_with_metadata(
    query_string=query,
    filter_status="active",
    filter_act=None,
)
latency = time.perf_counter() - start
print(f"Hybrid latency: {latency:.2f}s")
print(f"Citations: {len(result.get('citations', []))}")
for i, c in enumerate(result.get('citations', [])[:3]):
    print(f"  {i+1}. Title: {c.get('title', '')}")
    print(f"     Section: {c.get('section', '')} | Article: {c.get('article', '')}")
    print(f"     Source: {c.get('source', '')} | Doc type: {c.get('doc_type', '')}")
    print(f"     Score: {c.get('confidence', 0):.4f}")

# Now test dense only and sparse only by directly calling _run_hybrid_query
print("\n=== Dense/Sparse Ablation ===")
query_filter = manager._build_qdrant_filter(filter_status="active")
dense_query, sparse_query = manager._encode_query(query)

qdrant_models = __import__('qdrant_client.models', fromlist=['models'])

# Dense only
start = time.perf_counter()
dense_result = manager.client.query_points(
    collection_name='indian_law_v2_pilot',
    query=dense_query,
    using='dense',
    limit=8,
    query_filter=query_filter,
    with_payload=True,
    with_vectors=False,
)
dense_points = getattr(dense_result, 'points', []) or []
dense_latency = time.perf_counter() - start
print(f"Dense only: {len(dense_points)} results, {dense_latency:.2f}s")
for i, point in enumerate(dense_points[:3]):
    payload = point.payload or {}
    print(f"  {i+1}. Score: {point.score:.4f} | {str(payload.get('text', ''))[:80]}")

# Sparse only
start = time.perf_counter()
sparse_result = manager.client.query_points(
    collection_name='indian_law_v2_pilot',
    query=qdrant_models.SparseVector(**sparse_query) if sparse_query.get('indices') else qdrant_models.SparseVector(indices=[], values=[]),
    using='sparse',
    limit=8,
    query_filter=query_filter,
    with_payload=True,
    with_vectors=False,
)
sparse_points = getattr(sparse_result, 'points', []) or []
sparse_latency = time.perf_counter() - start
print(f"Sparse only: {len(sparse_points)} results, {sparse_latency:.2f}s")
for i, point in enumerate(sparse_points[:3]):
    payload = point.payload or {}
    print(f"  {i+1}. Score: {point.score:.4f} | {str(payload.get('text', ''))[:80]}")
