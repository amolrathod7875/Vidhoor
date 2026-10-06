"""Reset the Vidhoor Qdrant collection to force a clean rebuild.

Requires explicit --yes flag to confirm deletion. Never deletes arbitrary
Qdrant collections.
"""
import argparse
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

    parser = argparse.ArgumentParser(description="Reset the Vidhoor Qdrant collection.")
    parser.add_argument(
        "--yes",
        action="store_true",
        required=True,
        help="Confirm deletion of the Qdrant collection.",
    )
    args = parser.parse_args()

    host = os.environ.get("QDRANT_HOST", "127.0.0.1")
    port = int(os.environ.get("QDRANT_PORT", "6333"))
    grpc_port = int(os.environ.get("QDRANT_GRPC_PORT", "6334"))
    collection = os.environ.get("QDRANT_COLLECTION", "indian_law_v2")
    prefer_grpc = os.environ.get("QDRANT_PREFER_GRPC", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }

    manager = QdrantManager(
        host=host,
        port=port,
        grpc_port=grpc_port,
        collection_name=collection,
        prefer_grpc=prefer_grpc,
    )

    print(f"Deleting collection: {collection}")
    try:
        manager.client.delete_collection(collection)
        print("Collection deleted. It will be recreated on next access or ingest.")
    except Exception as exc:
        print(f"Failed to delete collection: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
