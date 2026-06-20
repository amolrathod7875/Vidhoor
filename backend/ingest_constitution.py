"""Utility script to ingest Constitution of India text into Chroma.

Usage examples:
    python ingest_constitution.py --input data/constitution_of_india.txt
    python ingest_constitution.py --input data/constitution_of_india.txt --status active
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import quote

from chroma_manager import ChromaManager


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
    if not match:
        return None
    return match.group(1).upper()


def build_metadata(
    chunks: list[str],
    status: str,
    source: str,
    source_url: str = "",
    resource_type: str = "",
) -> list[dict[str, str]]:
    """Build metadata list for Chroma ingestion."""
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
    chroma_host: str,
    chroma_port: int,
    source_base_url: str | None,
) -> int:
    """Ingest Constitution text file into Chroma and return ingested chunk count."""
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

    manager = ChromaManager(
        host=chroma_host,
        port=chroma_port,
        preferred_embedding_model="all-MiniLM-L6-v2",
        fallback_embedding_model="all-MiniLM-L6-v2",
    )
    return manager.ingest_law(text_chunks=chunks, metadata_list=metadata)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for ingestion script."""
    parser = argparse.ArgumentParser(
        description="Ingest Constitution of India text into Chroma collection 'indian_law'."
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
        "--host",
        default="localhost",
        help="Chroma host (default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Chroma port (default: 8000)",
    )
    parser.add_argument(
        "--source-base-url",
        default=None,
        help="Optional public base URL for source files (e.g., https://cdn.example.com/legal)",
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
        chroma_host=args.host,
        chroma_port=args.port,
        source_base_url=args.source_base_url,
    )

    print(f"Successfully ingested {ingested} chunks into 'indian_law'.")


if __name__ == "__main__":
    main()
