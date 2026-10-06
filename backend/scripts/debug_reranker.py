"""Debug reranker."""
import os
import time
import sys
import psutil

sys.path.insert(0, r"D:\vidhoor-legal-copilot\backend")

os.environ["RERANKER_DEVICE"] = "cpu"
os.environ["RERANKER_USE_FP16"] = "false"
os.environ["RERANKER_BATCH_SIZE"] = "4"
os.environ["RERANKER_MAX_LENGTH"] = "1024"
os.environ["QDRANT_ENABLE_RERANKER"] = "true"

from services.legal_reranker import get_reranker_model, is_reranker_enabled, rerank_candidates

print("Reranker enabled:", is_reranker_enabled())

vm = psutil.virtual_memory()
print("RAM before reranker load:", round(vm.available / (1024**3), 1), "GB available")

start = time.perf_counter()
model = get_reranker_model()
load_time = time.perf_counter() - start
print("Reranker load time:", round(load_time, 2), "s")

vm = psutil.virtual_memory()
print("RAM after reranker load:", round(vm.available / (1024**3), 1), "GB available")

candidates = [
    {"text": "Article 21 protects life and personal liberty.", "confidence": 0.8},
    {"text": "Section 64 of BNS deals with rape.", "confidence": 0.7},
    {"text": "The Constitution of India is the supreme law.", "confidence": 0.6},
]
start = time.perf_counter()
result = rerank_candidates("Explain Article 21", candidates)
latency = time.perf_counter() - start
print("Reranker latency:", round(latency, 2), "s")
print("Reranked results:")
for i, c in enumerate(result):
    score = c.get("reranker_score", 0)
    text = c["text"][:60]
    print(f"  {i+1}. score={score:.4f} | {text}")
