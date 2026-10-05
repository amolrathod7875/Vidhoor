"""Test Qdrant payload index creation."""
import os
import sys

sys.path.insert(0, r"D:\vidhoor-legal-copilot\backend")

os.environ["QDRANT_HOST"] = "127.0.0.1"
os.environ["QDRANT_PORT"] = "6333"
os.environ["QDRANT_GRPC_PORT"] = "6334"
os.environ["QDRANT_COLLECTION"] = "test_pilot"
os.environ["QDRANT_PREFER_GRPC"] = "true"

from qdrant_manager import QdrantManager

manager = QdrantManager(collection_name="test_pilot")

# Create collection
if manager.collection_exists():
    print("Collection exists, deleting...")
    manager.client.delete_collection("test_pilot")

manager.ensure_collection()
print("Collection created")

# Check indexes
try:
    collection_info = manager.client.get_collection("test_pilot")
    print("Collection info retrieved")
except Exception as exc:
    print("Failed to get collection info:", exc)

# List payload indexes
try:
    indexes = manager.client.list_payload_indexes("test_pilot")
    print("Payload indexes:", indexes)
except Exception as exc:
    print("Failed to list payload indexes:", exc)

# Check if specific index exists
for field in ["status", "act", "section", "doc_type", "court", "year", "jurisdiction"]:
    try:
        idx = manager.client.get_payload_index("test_pilot", field)
        print(f"Index for {field}:", idx)
    except Exception as exc:
        print(f"Index for {field}: ERROR - {exc}")
