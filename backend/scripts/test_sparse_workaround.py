"""Test sparse encoding workaround."""
import os
import sys

sys.path.insert(0, r"D:\vidhoor-legal-copilot\backend")

os.environ["BGE_DEVICE"] = "cpu"
os.environ["BGE_USE_FP16"] = "false"
os.environ["BGE_BATCH_SIZE"] = "1"
os.environ["BGE_MAX_LENGTH"] = "1024"

from services.legal_embeddings import encode_sparse

texts = [
    "Article 21 protects life and personal liberty.",
    "Section 64 of BNS deals with rape.",
]

try:
    result = encode_sparse(texts)
    print("Success! Sparse vectors:")
    for i, sparse in enumerate(result):
        print(f"  Text {i+1}: {len(sparse['indices'])} indices, {len(sparse['values'])} values")
        print(f"    Sample indices: {sparse['indices'][:5]}")
        print(f"    Sample values: {[round(v, 4) for v in sparse['values'][:5]]}")
except Exception as exc:
    print(f"Failed: {exc}")
