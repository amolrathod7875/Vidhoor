"""Debug reranker via direct call to rerank_candidates."""
import os
import sys

sys.path.insert(0, r"D:\vidhoor-legal-copilot\backend")

os.environ["RERANKER_DEVICE"] = "cpu"
os.environ["RERANKER_USE_FP16"] = "false"
os.environ["RERANKER_BATCH_SIZE"] = "4"
os.environ["RERANKER_MAX_LENGTH"] = "1024"
os.environ["QDRANT_ENABLE_RERANKER"] = "true"

from services.legal_reranker import get_reranker_model, rerank_candidates

# First load model directly to ensure singleton
model = get_reranker_model()
print("Model type:", type(model))

# Now test compute_score directly
pairs = [
    ("Explain Article 21", "Article 21 protects life and personal liberty."),
    ("Explain Article 21", "Section 64 of BNS deals with rape."),
    ("Explain Article 21", "The Constitution of India is the supreme law."),
]

scores = model.compute_score(pairs, normalize=True)
print("Direct scores:", scores)
print("Scores type:", type(scores))

# Now via rerank_candidates
candidates = [
    {"text": "Article 21 protects life and personal liberty.", "confidence": 0.8},
    {"text": "Section 64 of BNS deals with rape.", "confidence": 0.7},
    {"text": "The Constitution of India is the supreme law.", "confidence": 0.6},
]

# Monkey-patch to see what's happening
original_compute = model.compute_score
def debug_compute(*args, **kwargs):
    result = original_compute(*args, **kwargs)
    print("Inside monkeypatched compute_score:", result, type(result))
    return result

model.compute_score = debug_compute

result = rerank_candidates("Explain Article 21", candidates)
print("Result:", result)
