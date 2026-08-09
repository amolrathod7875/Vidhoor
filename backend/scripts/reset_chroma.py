"""Reset the Chroma indian_law collection to force a clean rebuild."""
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
    print(f"Deleting collection: {manager.collection_name}")
    manager.client.delete_collection(manager.collection_name)
    print("Collection deleted. It will be recreated on next access or ingest.")


if __name__ == "__main__":
    main()
