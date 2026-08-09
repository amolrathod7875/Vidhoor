"""Diagnose Chroma collection health: count, metadata sample, probe queries."""
import os
from chroma_manager import ChromaManager


def main() -> None:
    host = os.environ.get("CHROMA_HOST", "127.0.0.1")
    port = int(os.environ.get("CHROMA_PORT", "8000"))
    manager = ChromaManager(
        host=host,
        port=port,
        preferred_embedding_model="all-MiniLM-L6-v2",
        fallback_embedding_model="all-MiniLM-L6-v2",
    )
    col = manager.collection
    print("count:", col.count())
    sample = col.peek(limit=5)
    for md in (sample.get("metadatas") or []):
        print({k: md.get(k) for k in ("act", "status", "source", "section", "article")})
    res = col.query(
        query_texts=["BNS Section 64 rape punishment"],
        n_results=5,
        where={"status": {"$eq": "active"}},
    )
    print(
        "hits with status filter:",
        len((res.get("documents") or [[]])[0] or []),
    )
    res2 = col.query(
        query_texts=["BNS Section 64 rape punishment"],
        n_results=5,
    )
    print(
        "hits no filter:",
        len((res2.get("documents") or [[]])[0] or []),
    )


if __name__ == "__main__":
    main()
