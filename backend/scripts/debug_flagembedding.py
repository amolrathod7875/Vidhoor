"""Debug FlagEmbedding sparse encoding issue."""
import os
import sys

sys.path.insert(0, r"D:\vidhoor-legal-copilot\backend")

os.environ["BGE_DEVICE"] = "cpu"
os.environ["BGE_USE_FP16"] = "false"
os.environ["BGE_BATCH_SIZE"] = "1"
os.environ["BGE_MAX_LENGTH"] = "1024"

from FlagEmbedding.inference.embedder.encoder_only.m3 import M3Embedder

model = M3Embedder("BAAI/bge-m3", use_fp16=False, device="cpu")

texts = [
    "Article 21 protects life and personal liberty.",
    "Section 64 of BNS deals with rape.",
]

print("Test 1: dense=True, sparse=True")
try:
    result = model.encode(texts, return_dense=True, return_sparse=True, return_colbert_vecs=False)
    print("Success:", type(result), result.keys() if hasattr(result, 'keys') else 'no keys')
except Exception as exc:
    print("Error:", exc)

print("\nTest 2: dense=False, sparse=True")
try:
    result = model.encode(texts, return_dense=False, return_sparse=True, return_colbert_vecs=False)
    print("Success:", type(result), result.keys() if hasattr(result, 'keys') else 'no keys')
except Exception as exc:
    print("Error:", exc)

print("\nTest 3: single text, dense=False, sparse=True")
try:
    result = model.encode([texts[0]], return_dense=False, return_sparse=True, return_colbert_vecs=False)
    print("Success:", type(result), result.keys() if hasattr(result, 'keys') else 'no keys')
except Exception as exc:
    print("Error:", exc)

print("\nTest 4: batch_size=1, dense=False, sparse=True")
try:
    result = model.encode(texts, return_dense=False, return_sparse=True, return_colbert_vecs=False, batch_size=1)
    print("Success:", type(result), result.keys() if hasattr(result, 'keys') else 'no keys')
except Exception as exc:
    print("Error:", exc)
