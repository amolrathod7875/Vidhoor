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
from datetime import datetime
import re
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import quote

from chroma_manager import ChromaManager

SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".md"}

CASE_DOC_TYPE = "case_law"
STATUTE_DOC_TYPE = "statute"


def infer_resource_category(file_path: Path, explicit_category: str | None = None) -> str:
    """Infer whether a resource is statute text or case law."""
    if explicit_category and explicit_category != "auto":
        return explicit_category

    lowered_parts = [part.lower() for part in file_path.parts]
    if any(part in {"case", "cases", "judgments", "judgements"} for part in lowered_parts):
        return "case"

    lowered_name = file_path.stem.lower()
    if any(keyword in lowered_name for keyword in ("v", "vs", "judgment", "judgement", "appeal")):
        return "case"

    return "statute"


def _extract_case_citation(text: str) -> str:
    """Extract common Indian case citation tokens from text."""
    if not text:
        return ""

    patterns = [
        r"\b\(?\d{4}\)?\s*\d+\s*SCC\s*\d+\b",
        r"\bAIR\s*\d{4}\s*[A-Z]{2,}\s*\d+\b",
        r"\b\d{4}\s*CriLJ\s*\d+\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return ""


def _extract_case_year(text: str) -> int | None:
    """Extract likely year from case text."""
    if not text:
        return None

    current_year = datetime.now().year
    for raw in re.findall(r"\b(19\d{2}|20\d{2})\b", text):
        year = int(raw)
        if 1900 <= year <= current_year:
            return year
    return None


def _detect_court(text: str) -> str:
    """Detect court from case text."""
    normalized = (text or "").lower()
    if "supreme court" in normalized:
        return "Supreme Court of India"
    if "high court" in normalized:
        return "High Court"
    if "district court" in normalized:
        return "District Court"
    if "sessions court" in normalized or "session court" in normalized:
        return "Sessions Court"
    return ""


def extract_case_metadata(file_path: Path, full_text: str) -> dict[str, Any]:
    """Build case-law specific metadata fields from filename and body text."""
    text_head = (full_text or "")[:12000]
    citation_value = _extract_case_citation(text_head)
    year_value = _extract_case_year(citation_value or text_head)
    court_value = _detect_court(text_head)

    normalized_name = file_path.stem.replace("_", " ").replace("-", " ").strip()
    case_name = re.sub(r"\s+", " ", normalized_name).title()
    topic_value = case_name

    jurisdiction_value = "India"
    if "High Court" in court_value:
        jurisdiction_value = "State"

    bench_value = ""
    bench_match = re.search(
        r"\b([A-Z][a-z]+\s+Bench|Constitution\s+Bench|Division\s+Bench|Single\s+Judge\s+Bench)\b",
        text_head,
        flags=re.IGNORECASE,
    )
    if bench_match:
        bench_value = bench_match.group(1).strip()

    return {
        "doc_type": CASE_DOC_TYPE,
        "case_name": case_name,
        "citation_text": citation_value,
        "year": year_value,
        "court": court_value,
        "jurisdiction": jurisdiction_value,
        "bench": bench_value,
        "topic": topic_value,
    }


def read_pdf_pages(file_path: Path) -> list[tuple[int, str]]:
    """Extract text from a PDF file as (page_number, page_text) tuples."""
    try:
        PdfReader = getattr(import_module("pypdf"), "PdfReader")
    except Exception as exc:
        raise RuntimeError(
            "pypdf is required for PDF ingestion. Install with: pip install pypdf"
        ) from exc

    reader = PdfReader(str(file_path))
    page_texts: list[tuple[int, str]] = []

    for page_index, page in enumerate(reader.pages, start=1):
        extracted = page.extract_text() or ""
        if extracted.strip():
            page_texts.append((page_index, extracted))

    return page_texts


def read_pdf_pages_with_ocr_fallback(
    file_path: Path,
    use_ocr_fallback: bool,
) -> tuple[list[tuple[int, str]], bool]:
    """Extract PDF text with optional OCR fallback for scanned/image-only files."""
    pages = read_pdf_pages(file_path)
    if pages or not use_ocr_fallback:
        return pages, False

    try:
        VisionOCRService = getattr(import_module("services.ocr_vision"), "VisionOCRService")
    except Exception as exc:
        raise RuntimeError(
            "OCR fallback requested but OCR service is unavailable."
        ) from exc

    try:
        ocr_service = VisionOCRService()
        ocr_pages = ocr_service.extract_pages(str(file_path))
    except Exception as exc:
        raise RuntimeError(
            f"OCR fallback failed for '{file_path.name}': {exc}"
        ) from exc

    extracted_pages: list[tuple[int, str]] = []
    for item in ocr_pages:
        page_number = int(item.get("page") or 0)
        page_text = str(item.get("text") or "").strip()
        if page_number <= 0 or not page_text:
            continue
        extracted_pages.append((page_number, page_text))

    if not extracted_pages:
        raise RuntimeError(
            f"OCR fallback returned no text for '{file_path.name}'."
        )

    return extracted_pages, True


def read_text_file(file_path: Path) -> str:
    """Read UTF text from TXT/MD files."""
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return file_path.read_text(encoding="utf-8-sig")


def build_source_url(file_path: Path, source_base_url: str | None) -> str:
    """Build source URL for a file using optional cloud base URL."""
    if not source_base_url:
        return ""

    normalized_base = source_base_url.strip().rstrip("/")
    if not normalized_base:
        return ""

    return f"{normalized_base}/{quote(file_path.name)}"


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
        pages = read_pdf_pages(file_path)
        return "\n".join(text for _, text in pages).strip()

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

    if infer_resource_category(file_path=file_path) == "case":
        return "Indian Case Law"

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


def detect_reference(
    chunk: str,
    default_section: str | None = None,
    default_article: str | None = None,
) -> dict[str, str]:
    """Extract article/section metadata from chunk with fallback carry-forward."""
    metadata: dict[str, str] = {}

    article_matches = re.findall(
        r"\b(?:Article|Art\.?)\s*[-:]?\s*([0-9]+[A-Z]?(?:\([0-9A-Z]+\))?)\b",
        chunk,
        flags=re.IGNORECASE,
    )
    if article_matches:
        metadata["article"] = str(article_matches[-1]).upper()
    elif default_article:
        metadata["article"] = default_article

    section_matches = re.findall(
        r"\b(?:Section|Sec\.?)\s*[-:]?\s*([0-9]+[A-Z]?(?:\([0-9A-Z]+\))?)\b",
        chunk,
        flags=re.IGNORECASE,
    )
    section_value = str(section_matches[-1]).upper() if section_matches else None
    if not section_value:
        heading_matches = re.findall(
            r"(?:^|\s)([0-9]{1,3}[A-Z]?)\s*[\.:]\s*(?:[A-Z][a-z]|[A-Z]{2,})",
            chunk,
            flags=re.IGNORECASE,
        )
        if heading_matches:
            section_value = str(heading_matches[-1]).upper()

    if section_value:
        metadata["section"] = section_value
    elif default_section:
        metadata["section"] = default_section

    return metadata


def build_metadata(
    chunks: list[str],
    file_path: Path,
    act_name: str,
    status: str,
    page_numbers: list[int] | None = None,
    source_url: str = "",
    resource_category: str = "statute",
    case_metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build metadata aligned with chunks for Chroma ingestion."""
    all_metadata: list[dict[str, Any]] = []
    last_section: str | None = None
    last_article: str | None = None

    for index, chunk in enumerate(chunks):
        item: dict[str, Any] = {
            "act": act_name,
            "status": status,
            "source": file_path.name,
            "resource_type": file_path.suffix.lower().lstrip("."),
            "doc_type": CASE_DOC_TYPE if resource_category == "case" else STATUTE_DOC_TYPE,
        }
        if source_url:
            item["source_url"] = source_url
        if page_numbers and index < len(page_numbers):
            item["page"] = int(page_numbers[index])

        if resource_category == "case" and case_metadata:
            item.update(case_metadata)

        reference = detect_reference(
            chunk,
            default_section=last_section,
            default_article=last_article,
        )
        item.update(reference)

        if reference.get("section"):
            last_section = reference["section"]
        if reference.get("article"):
            last_article = reference["article"]

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
            files.extend(directory.rglob(f"*{extension}"))

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
    source_base_url: str | None,
    resource_category: str,
    use_ocr_fallback: bool,
) -> int:
    """Ingest a single file and return chunk count."""
    source_url = build_source_url(file_path=file_path, source_base_url=source_base_url)
    inferred_act_name = infer_act_name(file_path=file_path, explicit_act=act_name)

    extension = file_path.suffix.lower()
    chunks: list[str] = []
    page_numbers: list[int] = []
    raw_full_text = ""

    if extension == ".pdf":
        pages, used_ocr = read_pdf_pages_with_ocr_fallback(
            file_path=file_path,
            use_ocr_fallback=use_ocr_fallback,
        )
        raw_full_text = "\n".join(page_text for _, page_text in pages)
        for page_number, page_text in pages:
            page_chunks = split_into_chunks(
                text=page_text,
                chunk_size=chunk_size,
                overlap=overlap,
            )
            chunks.extend(page_chunks)
            page_numbers.extend([page_number] * len(page_chunks))

        if used_ocr:
            print(f"[OCR fallback] {file_path}")
    else:
        raw_text = read_resource(file_path)
        raw_full_text = raw_text
        chunks = split_into_chunks(text=raw_text, chunk_size=chunk_size, overlap=overlap)

    if not chunks:
        raise ValueError(f"No chunks generated for file: {file_path}")

    case_metadata = (
        extract_case_metadata(file_path=file_path, full_text=raw_full_text)
        if resource_category == "case"
        else None
    )

    metadata = build_metadata(
        chunks=chunks,
        file_path=file_path,
        act_name=inferred_act_name,
        status=status,
        page_numbers=page_numbers if page_numbers else None,
        source_url=source_url,
        resource_category=resource_category,
        case_metadata=case_metadata,
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
    parser.add_argument(
        "--source-base-url",
        default=None,
        help="Optional public base URL for source files (e.g., https://cdn.example.com/legal)",
    )
    parser.add_argument(
        "--resource-category",
        choices=["auto", "statute", "case"],
        default="auto",
        help="Ingestion profile for metadata enrichment (default: auto)",
    )
    parser.add_argument(
        "--ocr-fallback",
        action="store_true",
        help="Use OCR fallback for scanned/image-only PDFs when text extraction is empty",
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
    failures: list[tuple[str, str]] = []

    for file_path in files:
        resource_category = infer_resource_category(
            file_path=file_path,
            explicit_category=args.resource_category,
        )
        try:
            ingested_count = ingest_file(
                manager=manager,
                file_path=file_path,
                status=args.status,
                chunk_size=args.chunk_size,
                overlap=args.overlap,
                act_name=args.act,
                source_base_url=args.source_base_url,
                resource_category=resource_category,
                use_ocr_fallback=bool(args.ocr_fallback),
            )
            total_chunks += ingested_count
            results.append((str(file_path), ingested_count))
        except Exception as exc:
            failures.append((str(file_path), str(exc)))

    print("Ingestion summary:")
    for path, count in results:
        print(f"- {path}: {count} chunks")
    if failures:
        print("Skipped files:")
        for path, reason in failures:
            print(f"- {path}: {reason}")
    print(f"Total chunks ingested: {total_chunks}")


if __name__ == "__main__":
    main()
