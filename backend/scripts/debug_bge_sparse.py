"""Debug BGE-M3 sparse output format."""
import os
import sys

sys.path.insert(0, r"D:\vidhoor-legal-copilot\backend")
os.environ["BGE_DEVICE"] = "cpu"
os.environ["BGE_USE_FP16"] = "false"
os.environ["BGE_BATCH_SIZE"] = "1"
os.environ["BGE_MAX_LENGTH"] = "1024"

from services.legal_embeddings import get_embedding_model

model = get_embedding_model()

text = "Article 21 protects life and personal liberty."
result = model.encode(
    [text],
    return_dense=True,
    return_sparse=True,
    return_colbert_vecs=False,
)

print("Result type:", type(result))
print("Has dense_vecs:", hasattr(result, "dense_vecs"))
print("Has lexical_weights:", hasattr(result, "lexical_weights"))
print("Has sparse_vecs:", hasattr(result, "sparse_vecs"))

if hasattr(result, "dense_vecs"):
    dv = result.dense_vecs
    print("dense_vecs type:", type(dv))
    print("dense_vecs shape:", getattr(dv, "shape", "no shape"))
    print("dense_vecs len:", len(dv) if hasattr(dv, "__len__") else "N/A")

if hasattr(result, "lexical_weights"):
    lw = result.lexical_weights
    print("lexical_weights type:", type(lw))
    print("lexical_weights len:", len(lw) if hasattr(lw, "__len__") else "N/A")
    if lw and len(lw) > 0:
        item = lw[0]
        print("First item type:", type(item))
        print("First item:", item)
        if hasattr(item, "indices"):
            print("indices type:", type(item.indices))
            print("indices len:", len(item.indices))
        if hasattr(item, "values"):
            print("values type:", type(item.values))
            print("values len:", len(item.values))

if isinstance(result, dict):
    for k, v in result.items():
        vlen = len(v) if hasattr(v, "__len__") else "N/A"
        print(f"Dict key {k}: type={type(v)}, len={vlen}")
        if k in ("lexical_weights", "sparse_vecs") and v:
            print(f"  First item: {v[0]}")
