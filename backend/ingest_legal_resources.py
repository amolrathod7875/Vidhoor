"""Batch ingestion utility for Indian legal resources into Chroma.

Supports PDF/TXT/MD ingestion and is designed for loading Constitution plus
3-5 additional legal materials.

Examples:
    python ingest_legal_resources.py --inputs data/constitution.pdf data/bns.pdf
    python ingest_legal_resources.py --input-dir data/legal_docs --status active
    python ingest_legal_resources.py --inputs data/constitution.pdf --act "Constitution of India"
"""

from __future__ import annotations

import argparse
import re
from importlib import import_module
from pathlib import Path

from chroma_manager import ChromaManager

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md"}


def read_pdf_file(file_path: Path) -> str:
    """Extract text from a PDF file using pypdf."""
    try:
        PdfReader = getattr(import_module("pypdf"), "PdfReader")
    except Exception as exc:
        raise RuntimeError(
            "pypdf is required for PDF ingestion. Install with: pip install pypdf"
        ) from exc

    reader = PdfReader(str(file_path))
    page_texts: list[str] = []

    for page in reader.pages:
        extracted = page.extract_text() or ""
        if extracted.strip():
            page_texts.append(extracted)

    return "\n".join(page_texts).strip()


def read_text_file(file_path: Path) -> str:
    """Read UTF text from TXT/MD files."""
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return file_path.read_text(encoding="utf-8-sig")


def read_resource(file_path: Path) -> str:
    """Read a supported resource file and return raw text."""
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    extension = file_path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{extension}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    if extension == ".pdf":
        return read_pdf_file(file_path)

    return read_text_file(file_path)


def split_into_chunks(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    """Split text into overlapping character chunks for embedding."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if overlap < 0:
        raise ValueError("overlap must be >= 0")
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []

    chunks: list[str] = []
    step = chunk_size - overlap
    start = 0

    while start < len(normalized):
        end = min(start + chunk_size, len(normalized))
        chunks.append(normalized[start:end])
        if end == len(normalized):
            break
        start += step

    return chunks


def infer_act_name(file_path: Path, explicit_act: str | None) -> str:
    """Infer legal source name from filename unless act is explicitly provided."""
    if explicit_act:
        return explicit_act

    name = file_path.stem.lower()
    if "constitution" in name:
        return "Constitution of India"
    if "bnss" in name:
        return "Bharatiya Nagarik Suraksha Sanhita"
    if "bns" in name:
        return "Bharatiya Nyaya Sanhita"
    if "bsa" in name:
        return "Bharatiya Sakshya Adhiniyam"

    return file_path.stem.replace("_", " ").replace("-", " ").strip().title()


def detect_reference(chunk: str) -> dict[str, str]:
    """Extract best-effort article/section metadata from chunk text."""
    metadata: dict[str, str] = {}

    article_match = re.search(r"\bArticle\s+([0-9]+[A-Z]?)\b", chunk, flags=re.IGNORECASE)
    if article_match:
        metadata["article"] = article_match.group(1).upper()

    section_match = re.search(r"\bSection\s+([0-9]+[A-Z]?)\b", chunk, flags=re.IGNORECASE)
    if section_match:
        metadata["section"] = section_match.group(1).upper()

    return metadata


def build_metadata(
    chunks: list[str],
    file_path: Path,
    act_name: str,
    status: str,
) -> list[dict[str, str]]:
    """Build metadata aligned with chunks for Chroma ingestion."""
    all_metadata: list[dict[str, str]] = []

    for chunk in chunks:
        item: dict[str, str] = {
            "act": act_name,
            "status": status,
            "source": file_path.name,
            "resource_type": file_path.suffix.lower().lstrip("."),
        }
        item.update(detect_reference(chunk))
        all_metadata.append(item)

    return all_metadata


def collect_input_files(inputs: list[str], input_dir: str | None) -> list[Path]:
    """Collect and validate files from --inputs and/or --input-dir."""
    files: list[Path] = []

    for raw in inputs:
        files.append(Path(raw))

    if input_dir:
        directory = Path(input_dir)
        if not directory.exists() or not directory.is_dir():
            raise NotADirectoryError(f"Invalid input directory: {directory}")
        for extension in SUPPORTED_EXTENSIONS:
            files.extend(directory.glob(f"*{extension}"))

    unique_files: list[Path] = []
    seen: set[str] = set()

    for file_path in files:
        key = str(file_path.resolve()) if file_path.exists() else str(file_path)
        if key not in seen:
            seen.add(key)
            unique_files.append(file_path)

    if not unique_files:
        raise ValueError("No input files provided. Use --inputs and/or --input-dir")

    return unique_files


def ingest_file(
    manager: ChromaManager,
    file_path: Path,
    status: str,
    chunk_size: int,
    overlap: int,
    act_name: str | None,
) -> int:
    """Ingest a single file and return chunk count."""
    raw_text = read_resource(file_path)
    chunks = split_into_chunks(text=raw_text, chunk_size=chunk_size, overlap=overlap)

    if not chunks:
        raise ValueError(f"No chunks generated for file: {file_path}")

    metadata = build_metadata(
        chunks=chunks,
        file_path=file_path,
        act_name=infer_act_name(file_path=file_path, explicit_act=act_name),
        status=status,
    )

    return manager.ingest_law(text_chunks=chunks, metadata_list=metadata)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Ingest PDF/TXT/MD legal resources into Chroma collection 'indian_law'."
    )
    parser.add_argument(
        "--inputs",
        nargs="*",
        default=[],
        help="Space-separated file paths (pdf/txt/md)",
    )
    parser.add_argument(
        "--input-dir",
        default=None,
        help="Directory containing legal resources to ingest",
    )
    parser.add_argument(
        "--status",
        default="active",
        help="Metadata status (default: active)",
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
        "--act",
        default=None,
        help="Optional override for 'act' metadata field for all files",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint for batch legal resource ingestion."""
    args = parse_args()
    files = collect_input_files(inputs=args.inputs, input_dir=args.input_dir)

    manager = ChromaManager(
        host=args.host,
        port=args.port,
        preferred_embedding_model="all-MiniLM-L6-v2",
        fallback_embedding_model="all-MiniLM-L6-v2",
    )

    total_chunks = 0
    results: list[tuple[str, int]] = []

    for file_path in files:
        ingested_count = ingest_file(
            manager=manager,
            file_path=file_path,
            status=args.status,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
            act_name=args.act,
        )
        total_chunks += ingested_count
        results.append((str(file_path), ingested_count))

    print("Ingestion summary:")
    for path, count in results:
        print(f"- {path}: {count} chunks")
    print(f"Total chunks ingested: {total_chunks}")


if __name__ == "__main__":
    main()
