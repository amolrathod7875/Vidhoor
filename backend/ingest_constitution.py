"""Utility script to ingest Constitution of India text into Qdrant.

Usage examples:
    python ingest_constitution.py --input data/constitution_of_india.txt
    python ingest_constitution.py --input data/constitution_of_india.txt --status active
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import quote

from qdrant_manager import QdrantManager


def read_text_file(file_path: Path) -> str:
    """Read UTF-8 (or UTF-8-sig) text from a file path."""
    if not file_path.exists():
        raise FileNotFoundError(f"Input file not found: {file_path}")

    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return file_path.read_text(encoding="utf-8-sig")


def split_into_chunks(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    """Split long text into overlapping chunks."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if overlap < 0:
        raise ValueError("overlap must be >= 0")
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    clean_text = re.sub(r"\s+", " ", text).strip()
    if not clean_text:
        return []

    chunks: list[str] = []
    start = 0
    step = chunk_size - overlap

    while start < len(clean_text):
        end = min(start + chunk_size, len(clean_text))
        chunks.append(clean_text[start:end])
        if end == len(clean_text):
            break
        start += step

    return chunks


def detect_article(chunk: str) -> str | None:
    """Best-effort extraction of Constitution article reference from chunk."""
    match = re.search(
        r"\b(?:Article|Art\.?)\s*[-:]?\s*([0-9]+[A-Z]?(?:\([0-9A-Z]+\))?)\b",
        chunk,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).upper()

    bare_match = re.search(
        r"\b([0-9]+[A-Z]?)\s*\.\s*[A-Z]",
        chunk,
    )
    if bare_match:
        return bare_match.group(1).upper()

    return None


def build_metadata(
    chunks: list[str],
    status: str,
    source: str,
    source_url: str = "",
    resource_type: str = "",
) -> list[dict[str, str]]:
    """Build metadata list for ingestion."""
    metadata: list[dict[str, str]] = []
    last_article: str | None = None
    for chunk in chunks:
        article = detect_article(chunk) or last_article
        item = {
            "act": "Constitution of India",
            "status": status,
            "source": source,
            "doc_type": "statute",
            "resource_type": resource_type,
        }
        if source_url:
            item["source_url"] = source_url
        if article:
            item["article"] = article
            last_article = article
        metadata.append(item)
    return metadata


def build_source_url(file_path: Path, source_base_url: str | None) -> str:
    """Build source URL using optional cloud base URL."""
    if not source_base_url:
        return ""

    normalized_base = source_base_url.strip().rstrip("/")
    if not normalized_base:
        return ""

    return f"{normalized_base}/{quote(file_path.name)}"


def ingest_constitution(
    input_path: Path,
    status: str,
    chunk_size: int,
    overlap: int,
    source_base_url: str | None,
    qdrant_host: str = "127.0.0.1",
    qdrant_port: int = 6333,
    qdrant_grpc_port: int = 6334,
    qdrant_collection: str = "indian_law_v2",
) -> int:
    """Ingest Constitution text file into Qdrant and return ingested chunk count."""
    text = read_text_file(input_path)
    chunks = split_into_chunks(text=text, chunk_size=chunk_size, overlap=overlap)

    if not chunks:
        raise ValueError("No chunks created. Check the input text file content.")

    metadata = build_metadata(
        chunks=chunks,
        status=status,
        source=str(input_path.name),
        source_url=build_source_url(input_path, source_base_url),
        resource_type=input_path.suffix.lower().lstrip("."),
    )

    manager = QdrantManager(
        host=qdrant_host,
        port=qdrant_port,
        grpc_port=qdrant_grpc_port,
        collection_name=qdrant_collection,
        prefer_grpc=True,
    )
    manager.ensure_collection()
    result = manager.upsert_legal_chunks(text_chunks=chunks, metadata_list=metadata)
    return result.get("chunks_processed", 0)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for ingestion script."""
    parser = argparse.ArgumentParser(
        description="Ingest Constitution of India text into vector store."
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to plain text file of Constitution of India",
    )
    parser.add_argument(
        "--status",
        default="active",
        help="Metadata status field (default: active)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1200,
        help="Chunk size in characters (default: 1200)",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=200,
        help="Chunk overlap in characters (default: 200)",
    )
    parser.add_argument(
        "--qdrant-host",
        default="127.0.0.1",
        help="Qdrant host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--qdrant-port",
        type=int,
        default=6333,
        help="Qdrant port (default: 6333)",
    )
    parser.add_argument(
        "--qdrant-grpc-port",
        type=int,
        default=6334,
        help="Qdrant gRPC port (default: 6334)",
    )
    parser.add_argument(
        "--qdrant-collection",
        default="indian_law_v2",
        help="Qdrant collection name (default: indian_law_v2)",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint for Constitution ingestion."""
    args = parse_args()

    ingested = ingest_constitution(
        input_path=Path(args.input),
        status=args.status,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        source_base_url=args.source_base_url,
        qdrant_host=args.qdrant_host,
        qdrant_port=args.qdrant_port,
        qdrant_grpc_port=args.qdrant_grpc_port,
        qdrant_collection=args.qdrant_collection,
    )

    print(f"Successfully ingested {ingested} chunks into '{args.qdrant_collection}'.")


if __name__ == "__main__":
    main()
