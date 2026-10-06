"""Diagnose Qdrant collection health: connection, collection, config, counts."""
import logging
import os
import sys

# Ensure backend/ is importable when running scripts/ directly.
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from qdrant_manager import QdrantManager


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    host = os.environ.get("QDRANT_HOST", "127.0.0.1")
    port = int(os.environ.get("QDRANT_PORT", "6333"))
    grpc_port = int(os.environ.get("QDRANT_GRPC_PORT", "6334"))
    collection = os.environ.get("QDRANT_COLLECTION", "indian_law_v2")
    prefer_grpc = os.environ.get("QDRANT_PREFER_GRPC", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }

    print(f"Qdrant host: {host}:{port}")
    print(f"gRPC port: {grpc_port}")
    print(f"Collection: {collection}")
    print(f"Prefer gRPC: {prefer_grpc}")
    print()

    manager = None
    try:
        manager = QdrantManager(
            host=host,
            port=port,
            grpc_port=grpc_port,
            collection_name=collection,
            prefer_grpc=prefer_grpc,
        )
    except RuntimeError as exc:
        print(f"Qdrant manager initialization failed: {exc}")
        print("This may be due to BGE-M3 model loading issues in low-RAM environments.")
        print("The existing Chroma pipeline is unaffected.")
        return
    except Exception as exc:
        print(f"Qdrant manager initialization error: {exc}")
        return

    health = manager.health_check()
    print(f"Health: {health}")

    status = manager.get_status()
    print(f"Status: {status}")


if __name__ == "__main__":
    main()
