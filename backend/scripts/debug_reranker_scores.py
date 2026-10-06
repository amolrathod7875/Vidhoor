"""Debug reranker scores."""
import os
import sys

sys.path.insert(0, r"D:\vidhoor-legal-copilot\backend")

os.environ["RERANKER_DEVICE"] = "cpu"
os.environ["RERANKER_USE_FP16"] = "false"
os.environ["RERANKER_BATCH_SIZE"] = "4"
os.environ["RERANKER_MAX_LENGTH"] = "1024"

from FlagEmbedding import FlagReranker

model = FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=False, device="cpu")

pairs = [
    ("Explain Article 21", "Article 21 protects life and personal liberty."),
    ("Explain Article 21", "Section 64 of BNS deals with rape."),
    ("Explain Article 21", "The Constitution of India is the supreme law."),
]

print("With normalize=True:")
scores = model.compute_score(pairs, normalize=True)
print("Scores:", scores)

print("\nWith normalize=False:")
scores = model.compute_score(pairs, normalize=False)
print("Scores:", scores)
