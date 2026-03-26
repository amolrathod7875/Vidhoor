from fastapi import FastAPI, HTTPException, Header, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Any, Optional
import logging
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote
from uuid import uuid4
import uvicorn

from chroma_manager import ChromaManager
from database import OracleChatHistoryRepository
from llm_engine import LLMEngine
from pii_vault import PIIVault
from services.ocr_vision import VisionOCRService
from services.translate_helsinki import translate_pages_to_english

logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Vidhoor Legal Copilot API")

_BASE_DIR = Path(__file__).resolve().parent
_LEGAL_DOCS_DIR = _BASE_DIR / "data"

if _LEGAL_DOCS_DIR.exists() and _LEGAL_DOCS_DIR.is_dir():
    app.mount("/legal", StaticFiles(directory=str(_LEGAL_DOCS_DIR)), name="legal")

# VERY IMPORTANT: Configure CORS so your React frontend can talk to this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic Models for strict data validation ---

class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    is_temporary_chat: bool = False
    document_context: Optional[str] = None
    document_name: Optional[str] = None
    document_contexts: list[str] | None = None
    document_names: list[str] | None = None


class Citation(BaseModel):
    doc_id: str
    title: str
    source: str
    source_url: str = ""
    section: str = ""
    page: Optional[int] = None
    snippet: str
    confidence: float
    last_updated: str = ""


class ChatResponse(BaseModel):
    response: str
    session_id: str
    masked_entities: dict # We will send back the PII map just in case the frontend needs it
    citations: list[Citation] = []
    overall_confidence: Optional[float] = None


class OCRPageResult(BaseModel):
    page: int
    detected_language: str
    text_en: str


class OCRAnalyzeResponse(BaseModel):
    response: str
    summary: str
    extracted_pages: list[OCRPageResult]
    citations: list[Citation] = []
    overall_confidence: Optional[float] = None
    masked_entities: dict


class SessionSummary(BaseModel):
    session_id: str
    title: str
    pinned: bool = False
    created_at: str
    updated_at: str


class SessionMessage(BaseModel):
    role: str
    content: str
    created_at: str
    masked_entities: dict[str, str]


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    pinned: bool | None = None


_chroma_manager: Optional[ChromaManager] = None
_llm_engine: Optional[LLMEngine] = None
_pii_vault: Optional[PIIVault] = None
_chat_repo: Optional[OracleChatHistoryRepository] = None
_ocr_service: Optional[VisionOCRService] = None
_bm25_refresh_counter: int = 0


LEGAL_QUERY_KEYWORDS = {
    "advocate",
    "appeal",
    "article",
    "bail",
    "bns",
    "bnss",
    "bsa",
    "constitution",
    "contract",
    "court",
    "crime",
    "criminal",
    "evidence",
    "fir",
    "high court",
    "ipc",
    "judge",
    "judgment",
    "jurisdiction",
    "law",
    "legal",
    "litigation",
    "offence",
    "petition",
    "punishment",
    "section",
    "supreme court",
}

BAIL_QUERY_KEYWORDS = {
    "bail",
    "bailable",
    "non-bailable",
    "non bailable",
    "custody",
    "remand",
}


OFFENCE_GUIDANCE_RULES: list[dict[str, Any]] = [
    {
        "name": "Vehicle Theft",
        "patterns": [
            r"\bvehicle theft\b",
            r"\btheft\b",
            r"\bstolen\b",
            r"\bmotorcycle\b",
            r"\bcar\b",
            r"\bचोरी\b",
        ],
        "act": "Bharatiya Nyaya Sanhita (BNS)",
        "sections_hint": ["303", "303(2)"],
        "guidance": [
            "Preserve ownership proof such as RC, insurance documents, and purchase records.",
            "Share engine/chassis number, vehicle registration, and last-seen location/time with investigating officer.",
            "Request CCTV collection from parking, nearby shops, toll points, and traffic junctions quickly.",
            "Keep FIR copy and written acknowledgement of submitted evidence for follow-up.",
            "Inform insurer immediately and keep claim timeline aligned with FIR details.",
        ],
    },
]


def _detect_offence_rule(text: str) -> dict[str, Any] | None:
    """Detect likely offence type from OCR/translated text."""
    if not text:
        return None

    lowered = text.lower()
    for rule in OFFENCE_GUIDANCE_RULES:
        patterns = rule.get("patterns") or []
        if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns):
            return rule

    return None


def _build_offence_guidance_markdown(text: str, references: list[str]) -> str:
    """Build offence-first legal guidance when strict citation grounding is unavailable."""
    rule = _detect_offence_rule(text)
    if not rule:
        return ""

    section_candidates = references or list(rule.get("sections_hint") or [])
    sections_text = ", ".join(section_candidates[:8]) if section_candidates else "Not clearly visible in OCR"
    bullet_points = "\n".join(f"- {item}" for item in (rule.get("guidance") or []))

    return (
        "### Offence-Focused Legal Guidance\n"
        f"- Likely offence type: {rule.get('name', 'Not clearly identified')}\n"
        f"- Applicable Act: {rule.get('act', 'Not clearly identified')}\n"
        f"- Relevant sections (detected/inferred): {sections_text}\n"
        "- What authorities typically examine: ownership proof, possession trail, intent indicators, and recovery evidence.\n"
        "- Immediate legal next steps:\n"
        f"{bullet_points}\n"
        "- Note: This is practical legal guidance; final section applicability must be confirmed from the latest official bare act and case facts."
    )


def get_chroma_manager() -> ChromaManager:
    """Get or create singleton Chroma manager instance."""
    global _chroma_manager
    if _chroma_manager is None:
        chroma_host = os.environ.get("CHROMA_HOST", "127.0.0.1")
        chroma_port = int(os.environ.get("CHROMA_PORT", "8000"))
        _chroma_manager = ChromaManager(
            host=chroma_host,
            port=chroma_port,
            preferred_embedding_model="all-MiniLM-L6-v2",
            fallback_embedding_model="all-MiniLM-L6-v2",
        )
    return _chroma_manager


def get_llm_engine() -> LLMEngine:
    """Get or create singleton LLM engine instance."""
    global _llm_engine
    if _llm_engine is None:
        _llm_engine = LLMEngine(model="llama3.1-8b")
    return _llm_engine


def get_pii_vault() -> PIIVault:
    """Get or create singleton PII vault instance."""
    global _pii_vault
    if _pii_vault is None:
        _pii_vault = PIIVault()
    return _pii_vault


def get_chat_repo() -> OracleChatHistoryRepository:
    """Get or create singleton Oracle chat-history repository."""
    global _chat_repo
    if _chat_repo is None:
        _chat_repo = OracleChatHistoryRepository()
        _chat_repo.initialize_schema()
    return _chat_repo


def get_ocr_service() -> VisionOCRService:
    """Get or create singleton OCR service instance."""
    global _ocr_service
    if _ocr_service is None:
        _ocr_service = VisionOCRService()
    return _ocr_service


def infer_act_filter(query: str) -> Optional[str]:
    """Infer likely legal source from query text for retrieval precision."""
    normalized = query.lower()

    if "bharatiya nyaya sanhita" in normalized:
        return "Bharatiya Nyaya Sanhita"
    if "bharatiya nagarik suraksha sanhita" in normalized:
        return "Bharatiya Nagarik Suraksha Sanhita"
    if "bharatiya sakshya adhiniyam" in normalized:
        return "Bharatiya Sakshya Adhiniyam"

    if "constitution" in normalized or "article" in normalized:
        return "Constitution of India"
    if re.search(r"\bbnss\b", normalized):
        return "Bharatiya Nagarik Suraksha Sanhita"
    if re.search(r"\bbns\b", normalized):
        return "Bharatiya Nyaya Sanhita"
    if re.search(r"\bbsa\b", normalized):
        return "Bharatiya Sakshya Adhiniyam"

    return None


def infer_act_filters(query: str) -> list[str | None]:
    """Infer one or more likely legal sources for retrieval coverage."""
    normalized = query.lower()
    filters: list[str | None] = []

    if "bharatiya nyaya sanhita" in normalized:
        filters.append("Bharatiya Nyaya Sanhita")
    if "bharatiya nagarik suraksha sanhita" in normalized:
        filters.append("Bharatiya Nagarik Suraksha Sanhita")
    if "bharatiya sakshya adhiniyam" in normalized:
        filters.append("Bharatiya Sakshya Adhiniyam")

    if "constitution" in normalized or "article" in normalized:
        filters.append("Constitution of India")
    if re.search(r"\bbnss\b", normalized):
        filters.append("Bharatiya Nagarik Suraksha Sanhita")
    if re.search(r"\bbns\b", normalized):
        filters.append("Bharatiya Nyaya Sanhita")
    if re.search(r"\bbsa\b", normalized):
        filters.append("Bharatiya Sakshya Adhiniyam")

    if any(keyword in normalized for keyword in BAIL_QUERY_KEYWORDS):
        if "Bharatiya Nagarik Suraksha Sanhita" not in filters:
            filters.append("Bharatiya Nagarik Suraksha Sanhita")

    if not filters:
        return [None]

    ordered_unique: list[str | None] = []
    for item in filters:
        if item not in ordered_unique:
            ordered_unique.append(item)
    return ordered_unique


def is_legal_query(query: str) -> bool:
    """Heuristically classify whether a query is legal in nature."""
    if not query or not query.strip():
        return False

    normalized = query.lower()

    if any(keyword in normalized for keyword in LEGAL_QUERY_KEYWORDS):
        return True

    if re.search(r"\b(article|section)\s+\d+[a-z]?\b", normalized):
        return True

    return False


def _normalize_reference(value: str | None) -> str:
    """Normalize legal reference tokens (section/article) for tolerant matching."""
    if not value:
        return ""

    normalized = re.sub(
        r"\b(section|sec\.?|article|art\.?)\b",
        "",
        value,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\s+", "", normalized).upper()
    return normalized


def _references_match(requested: str | None, candidate: str | None) -> bool:
    """Match legal references while tolerating subsection-style variants."""
    requested_norm = _normalize_reference(requested)
    candidate_norm = _normalize_reference(candidate)

    if not requested_norm or not candidate_norm:
        return False

    if requested_norm == candidate_norm:
        return True

    requested_base = re.match(r"^([0-9]+[A-Z]?)", requested_norm)
    candidate_base = re.match(r"^([0-9]+[A-Z]?)", candidate_norm)

    if requested_base and candidate_base:
        return requested_base.group(1) == candidate_base.group(1)

    return False


def _reference_has_subsection(value: str | None) -> bool:
    """Check whether reference explicitly includes subsection notation like 303(2)."""
    if not value:
        return False
    return bool(re.search(r"\([0-9A-Z]+\)", str(value), flags=re.IGNORECASE))


def _reference_match_for_fir(requested: str | None, candidate: str | None) -> bool:
    """Match FIR references with stricter behavior when subsection is explicitly requested."""
    if _reference_has_subsection(requested):
        return _normalize_reference(requested) == _normalize_reference(candidate)
    return _references_match(requested, candidate)


def _extract_requested_references(query: str) -> list[str]:
    """Extract all requested section/article references from query text."""
    references: list[str] = []
    ref_pattern = r"([0-9]+[a-z]?(?:\([0-9a-z]+\))?)"

    section_refs = re.findall(
        rf"\b(?:section|sec\.?|u/s)\s*{ref_pattern}(?=\D|$)",
        query,
        flags=re.IGNORECASE,
    )
    references.extend([str(item).upper() for item in section_refs])

    plural_section_blocks = re.findall(
        r"\bsections\s+([^.;\n]+)",
        query,
        flags=re.IGNORECASE,
    )
    for block in plural_section_blocks:
        values = re.findall(rf"{ref_pattern}(?=\D|$)", block, flags=re.IGNORECASE)
        references.extend([str(item).upper() for item in values])

    article_refs = re.findall(
        rf"\b(?:article|art\.?)\s*{ref_pattern}(?=\D|$)",
        query,
        flags=re.IGNORECASE,
    )
    references.extend([str(item).upper() for item in article_refs])

    shorthand_refs = re.findall(
        rf"\b(?:bns|bnss|bsa|ipc|crpc)\s*[-/]?\s*{ref_pattern}(?=\D|$)",
        query,
        flags=re.IGNORECASE,
    )
    references.extend([str(item).upper() for item in shorthand_refs])

    ordered_unique: list[str] = []
    for ref in references:
        if ref not in ordered_unique:
            ordered_unique.append(ref)
    return ordered_unique


def _normalize_text_token(value: str | None) -> str:
    """Normalize text for robust keyword containment checks."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _act_aliases(act_name: str) -> list[str]:
    """Return canonical aliases for known act names."""
    normalized = _normalize_text_token(act_name)

    if "bharatiyanyayasanhita" in normalized:
        return ["bharatiyanyayasanhita", "bns"]
    if "bharatiyanagariksurakshasanhita" in normalized:
        return ["bharatiyanagariksurakshasanhita", "bnss"]
    if "bharatiyasakshyaadhiniyam" in normalized:
        return ["bharatiyasakshyaadhiniyam", "bsa"]
    if "constitutionofindia" in normalized:
        return ["constitutionofindia", "constitution"]

    return [normalized] if normalized else []


def _citation_matches_allowed_acts(citation: Citation, act_filters: list[str | None]) -> bool:
    """Check whether citation appears to belong to one of requested act filters."""
    effective_filters = [item for item in act_filters if item]
    if not effective_filters:
        return True

    raw_haystack = f"{citation.title} {citation.source} {citation.doc_id}"
    haystack = _normalize_text_token(raw_haystack)
    token_haystack = set(re.findall(r"[a-z0-9]+", raw_haystack.lower()))

    for act_name in effective_filters:
        for alias in _act_aliases(str(act_name)):
            if not alias:
                continue

            if alias in {"bns", "bnss", "bsa", "ipc", "crpc"}:
                if alias in token_haystack:
                    return True
                continue

            if alias in haystack:
                return True
    return False


def _is_http_url(value: str | None) -> bool:
    """Check whether a value is an HTTP(S) URL."""
    text = str(value or "").strip().lower()
    return text.startswith("http://") or text.startswith("https://")


def _build_source_url_from_base(source_name: str) -> str:
    """Build source URL from configured legal source base URL and source filename."""
    configured_base_url = os.environ.get("LEGAL_SOURCE_BASE_URL", "").strip().rstrip("/")
    fallback_base_url = os.environ.get("APP_PUBLIC_BASE_URL", "http://127.0.0.1:8001").strip().rstrip("/")
    if fallback_base_url:
        fallback_base_url = f"{fallback_base_url}/legal"

    base_url = configured_base_url or fallback_base_url
    if not base_url:
        return ""

    filename = str(source_name or "").strip()
    if not filename:
        return ""

    return f"{base_url}/{quote(filename)}"


def _append_page_anchor(url: str, page: int | None) -> str:
    """Append PDF page anchor to URL when page is available."""
    if not url:
        return ""
    if page is None:
        return url
    if "#" in url:
        return url
    return f"{url}#page={int(page)}"


def _normalize_citation_links(citations: list[Citation]) -> list[Citation]:
    """Ensure citations include usable source URLs with optional page deep links."""
    normalized: list[Citation] = []
    for citation in citations:
        source_url = str(citation.source_url or "").strip()
        if not _is_http_url(source_url):
            source_url = _build_source_url_from_base(citation.source)

        normalized_url = _append_page_anchor(source_url, citation.page)
        normalized.append(citation.model_copy(update={"source_url": normalized_url}))
    return normalized


def _retrieve_legal_citations(masked_query: str) -> tuple[list[Citation], Optional[float]]:
    """Retrieve citation objects and overall confidence for a query."""
    chroma_manager = get_chroma_manager()
    act_filters = infer_act_filters(masked_query)

    raw_citations: list[dict[str, Any]] = []
    seen_citations: set[tuple[str, str]] = set()

    for act_filter in act_filters:
        retrieval = chroma_manager.retrieve_context_with_metadata(
            query_string=masked_query,
            filter_status="active",
            filter_act=act_filter,
        )

        for item in retrieval.get("citations", []):
            citation_doc_id = str(item.get("doc_id") or "")
            citation_snippet = str(item.get("snippet") or "")
            citation_key = (citation_doc_id, citation_snippet.strip().lower())
            if citation_key in seen_citations:
                continue
            seen_citations.add(citation_key)
            raw_citations.append(item)

    citations = [Citation(**item) for item in raw_citations]
    citations = [
        citation
        for citation in citations
        if _citation_matches_allowed_acts(citation, act_filters)
    ]
    citations.sort(key=lambda item: item.confidence, reverse=True)
    citations = _normalize_citation_links(citations[:8])

    if not citations:
        return [], None

    overall_confidence = round(
        sum(item.confidence for item in citations) / len(citations),
        2,
    )
    return citations, overall_confidence

# --- Dependency to verify Firebase Auth Token (Mocked for now) ---
async def verify_token(authorization: str = Header(None)):
    if not authorization:
        # For Guest Mode, we might not have a token
        return None 
    # Later, we will add firebase_admin.auth.verify_id_token() here
    token_parts = authorization.split(" ")
    if len(token_parts) != 2 or token_parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = token_parts[1]
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    return {"uid": "mock_user_123"}


def get_required_user_id(user: dict[str, Any] | None) -> str:
    """Require authenticated user context for history APIs."""
    if not user or not user.get("uid"):
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(user["uid"])


def _generate_document_grounded_response(
    llm_engine: LLMEngine,
    masked_query: str,
    masked_document_context: str,
    document_name: str | None,
) -> str:
    """Generate response grounded in uploaded document context."""
    context_snippet = (masked_document_context or "")[:12000]
    document_label = document_name or "uploaded document"
    prompt = (
        "You are answering a question about an uploaded legal document. "
        "Use only the provided document context when describing facts from the document. "
        "If the answer is not present in document context, explicitly say what is missing. "
        "When legal interpretation is requested, keep the answer practical and cautious.\n\n"
        f"Document name: {document_label}\n\n"
        f"Document context:\n{context_snippet}\n\n"
        f"User question:\n{masked_query}\n\n"
        "Answer in markdown with short headings and bullet points when useful."
    )
    return llm_engine.generate_general_response(prompt)

# --- Core API Endpoints ---

@app.get("/")
async def health_check():
    return {"status": "Vidhoor Backend is live and waiting for legal queries."}

@app.post("/api/chat", response_model=ChatResponse)
async def process_chat(request: ChatRequest, user: dict = Depends(verify_token)):
    try:
        global _bm25_refresh_counter
        pii_vault = get_pii_vault()
        llm_engine = get_llm_engine()
        masked_message, pii_map = pii_vault.mask_text(request.message)

        document_pairs: list[tuple[str, str]] = []
        multi_contexts = [str(item or "").strip() for item in (request.document_contexts or []) if str(item or "").strip()]
        multi_names = [str(item or "").strip() for item in (request.document_names or [])]
        if multi_contexts:
            for index, context in enumerate(multi_contexts, start=1):
                name = multi_names[index - 1] if index - 1 < len(multi_names) and multi_names[index - 1] else f"Document {index}"
                document_pairs.append((name, context))

        single_context = (request.document_context or "").strip()
        if not document_pairs and single_context:
            document_pairs.append((request.document_name or "uploaded document", single_context))

        document_context = ""
        if document_pairs:
            merged_blocks = [
                f"[{name}]\n{context[:4000]}"
                for name, context in document_pairs[:5]
            ]
            document_context = "\n\n---\n\n".join(merged_blocks)[:16000]

        masked_document_context = ""
        if document_context:
            masked_document_context, _ = pii_vault.mask_text(document_context)

        document_label = ", ".join(name for name, _ in document_pairs[:5]) if document_pairs else None
        session_id = request.session_id or f"session_{uuid4().hex}"
        citations: list[Citation] = []
        overall_confidence: Optional[float] = None

        if is_legal_query(masked_message):
            try:
                chroma_manager = get_chroma_manager()
                _bm25_refresh_counter += 1
                if _bm25_refresh_counter % 5 == 0:
                    chroma_manager.refresh_bm25_from_oracle(filter_status="active", filter_act=None)

                retrieval_query = masked_message
                if masked_document_context:
                    retrieval_query = (
                        f"{masked_message}\n\n"
                        "Document context (for grounding):\n"
                        f"{masked_document_context[:3000]}"
                    )

                act_filters = infer_act_filters(retrieval_query)

                retrieved_context: list[str] = []
                raw_citations: list[dict[str, Any]] = []
                seen_context: set[str] = set()
                seen_citations: set[tuple[str, str]] = set()

                for act_filter in act_filters:
                    retrieval = chroma_manager.retrieve_context_with_metadata(
                        query_string=retrieval_query,
                        filter_status="active",
                        filter_act=act_filter,
                    )

                    for context_chunk in retrieval.get("documents", []):
                        chunk_text = str(context_chunk or "")
                        chunk_key = chunk_text.strip().lower()
                        if not chunk_text or chunk_key in seen_context:
                            continue
                        seen_context.add(chunk_key)
                        retrieved_context.append(chunk_text)

                    for item in retrieval.get("citations", []):
                        citation_doc_id = str(item.get("doc_id") or "")
                        citation_snippet = str(item.get("snippet") or "")
                        citation_key = (citation_doc_id, citation_snippet.strip().lower())
                        if citation_key in seen_citations:
                            continue
                        seen_citations.add(citation_key)
                        raw_citations.append(item)

                citations = [Citation(**item) for item in raw_citations]
                citations = [
                    citation
                    for citation in citations
                    if _citation_matches_allowed_acts(citation, act_filters)
                ]
                citations.sort(key=lambda item: item.confidence, reverse=True)
                citations = citations[:8]
                citations = _normalize_citation_links(citations)

                requested_references = _extract_requested_references(masked_message)

                has_direct_reference_match = True
                if requested_references:
                    has_direct_reference_match = all(
                        any(
                            _references_match(requested_reference, citation.section)
                            for citation in citations
                        )
                        for requested_reference in requested_references
                    )

                if requested_references and citations:
                    citations = [
                        citation
                        for citation in citations
                        if any(
                            _references_match(requested_reference, citation.section)
                            for requested_reference in requested_references
                        )
                    ]

                retrieved_context = [
                    (
                        f"[Act: {item.title} | Source: {item.source} | Section: {item.section}]\n"
                        f"{item.snippet}"
                    )
                    for item in citations
                    if item.snippet
                ]

                if citations:
                    overall_confidence = round(
                        sum(item.confidence for item in citations) / len(citations),
                        2,
                    )

                if not retrieved_context:
                    if masked_document_context:
                        citations = []
                        overall_confidence = None
                        ai_response_masked = _generate_document_grounded_response(
                            llm_engine=llm_engine,
                            masked_query=masked_message,
                            masked_document_context=masked_document_context,
                            document_name=document_label,
                        )
                    else:
                        citations = []
                        overall_confidence = None
                        ai_response_masked = (
                            "I couldn't find sufficiently reliable legal sources for this query. "
                            "Please include the exact Act and section/article reference, then try again."
                        )
                elif requested_references and not has_direct_reference_match:
                    requested_text = ", ".join(requested_references)
                    ai_response_masked = (
                        f"I could not find exact matches for the requested reference(s): {requested_text}. "
                        "I cannot provide a citation-grounded answer without exact source matches. "
                        "Please verify the Act and section/article numbering, or refine the query."
                    )
                else:
                    legal_masked_query = masked_message
                    if masked_document_context:
                        legal_masked_query = (
                            f"{masked_message}\n\n"
                            "Document context from uploaded file:\n"
                            f"{masked_document_context[:5000]}"
                        )
                    ai_response_masked = llm_engine.generate_legal_response(
                        masked_query=legal_masked_query,
                        retrieved_context_list=retrieved_context,
                    )
            except Exception as exc:
                logger.exception("Legal retrieval failed: %s", exc)
                ai_response_masked = (
                    "I couldn't access the legal source index right now, so I can't provide a "
                    "citation-grounded legal answer at the moment. Please try again shortly."
                )
        else:
            if masked_document_context:
                ai_response_masked = _generate_document_grounded_response(
                    llm_engine=llm_engine,
                    masked_query=masked_message,
                    masked_document_context=masked_document_context,
                    document_name=document_label,
                )
            else:
                ai_response_masked = llm_engine.generate_general_response(
                    masked_query=masked_message,
                )
        
        final_readable_response = pii_vault.unmask_text(ai_response_masked, pii_map)
        
        # Step 5: Save to Oracle DB (if NOT temporary and authenticated)
        if not request.is_temporary_chat and user and user.get("uid"):
            try:
                chat_repo = get_chat_repo()
                user_id = str(user["uid"])
                existing_messages = chat_repo.get_session_messages(
                    user_id=user_id,
                    session_id=session_id,
                )
                is_first_turn = len(existing_messages) == 0
                session_title: str | None = None
                if is_first_turn:
                    session_title = llm_engine.generate_session_title(
                        user_message=request.message,
                        assistant_message=final_readable_response,
                    )

                chat_repo.save_chat_turn(
                    user_id=user_id,
                    session_id=session_id,
                    user_message=request.message,
                    assistant_message=final_readable_response,
                    masked_entities=pii_map,
                    session_title=session_title,
                )
            except Exception as exc:
                logger.exception("Failed to save chat history to Oracle: %s", exc)
            
        return ChatResponse(
            response=final_readable_response,
            session_id=session_id,
            masked_entities=pii_map,
            citations=citations,
            overall_confidence=overall_confidence,
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/fir/analyze", response_model=OCRAnalyzeResponse)
async def analyze_fir_document(
    file: UploadFile = File(...),
    query: str | None = Form(default=None),
    user: dict = Depends(verify_token),
):
    """Analyze uploaded FIR/scanned document with OCR, translation, PII masking, and legal grounding."""
    global _bm25_refresh_counter

    del user  # endpoint allows guest mode as with chat

    filename = file.filename or "uploaded_document"
    extension = Path(filename).suffix.lower()
    allowed_extensions = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Upload PDF, PNG, JPG, JPEG, WEBP, TIFF, or BMP.",
        )

    temp_path = ""
    try:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        with NamedTemporaryFile(delete=False, suffix=extension) as tmp_file:
            tmp_file.write(file_bytes)
            temp_path = tmp_file.name

        ocr_service = get_ocr_service()
        pages = ocr_service.extract_pages(temp_path)
        if not pages:
            raise HTTPException(status_code=422, detail="No readable text found in uploaded document.")

        translated_pages = translate_pages_to_english(pages)
        compiled_text = "\n\n".join(
            f"[Page {item['page']}]\n{item['text_en']}"
            for item in translated_pages
            if str(item.get("text_en") or "").strip()
        )
        if not compiled_text.strip():
            raise HTTPException(status_code=422, detail="Unable to translate OCR text to usable content.")

        pii_vault = get_pii_vault()
        llm_engine = get_llm_engine()
        masked_text, pii_map = pii_vault.mask_text(compiled_text)

        summary_prompt = (
            "Summarize this FIR/scanned legal document in English with clear bullet points. "
            "Use ONLY facts explicitly visible in OCR text. If any field is unclear/noisy, say 'Not clearly visible in OCR'. "
            "Do not infer dates, years, FIR numbers, names, or legal sections from patterns.\n\n"
            f"Document text:\n{masked_text[:20000]}"
        )
        summary_masked = llm_engine.generate_general_response(summary_prompt)

        effective_query = (query or "").strip() or summary_masked
        requested_references = _extract_requested_references(f"{masked_text[:6000]}\n{effective_query[:1500]}")
        reference_hint = ", ".join(requested_references[:10]) if requested_references else "Not clearly visible in OCR"
        offence_guidance_markdown = _build_offence_guidance_markdown(compiled_text, requested_references)
        legal_query = (
            "Identify applicable Indian legal provisions, probable Acts, and likely Sections "
            "for this FIR/scanned document. Use OCR text as primary evidence and do not invent missing facts. "
            "If OCR text is unclear, explicitly mention uncertainty."
            f"\n\nObserved references in OCR text: {reference_hint}"
            f"\n\nOCR Extracted Text:\n{masked_text[:12000]}"
            f"\n\nSummary Draft:\n{summary_masked[:3000]}"
            f"\n\nUser focus:\n{effective_query[:1500]}"
        )

        _bm25_refresh_counter += 1
        if _bm25_refresh_counter % 5 == 0:
            get_chroma_manager().refresh_bm25_from_oracle(filter_status="active", filter_act=None)

        citations, overall_confidence = _retrieve_legal_citations(legal_query)

        if citations and requested_references:
            matched_citations = [
                item
                for item in citations
                if any(_reference_match_for_fir(reference, item.section) for reference in requested_references)
            ]
            if matched_citations:
                citations = matched_citations
                overall_confidence = round(
                    sum(item.confidence for item in citations) / len(citations),
                    2,
                )
            else:
                citations = []
                overall_confidence = None

        retrieved_context = [
            (
                f"[Act: {item.title} | Source: {item.source} | Section: {item.section}]\n"
                f"{item.snippet}"
            )
            for item in citations
            if item.snippet
        ]

        if not retrieved_context:
            ocr_reference_hint = ", ".join(requested_references[:8]) if requested_references else "none clearly detected"
            final_response_masked = (
                "I extracted and summarized the uploaded document, but I could not find sufficiently "
                "reliable legal-source matches yet. "
                f"OCR-detected legal references: {ocr_reference_hint}. "
                "Please refine the query with known Act/Section references or verify the exact section text."
            )
            if offence_guidance_markdown:
                final_response_masked = f"{final_response_masked}\n\n{offence_guidance_markdown}"
        else:
            final_response_masked = llm_engine.generate_legal_response(
                masked_query=legal_query,
                retrieved_context_list=retrieved_context,
            )
            if offence_guidance_markdown:
                final_response_masked = (
                    f"{final_response_masked}\n\n"
                    "### Practical Next Steps\n"
                    f"{offence_guidance_markdown}"
                )

        summary = pii_vault.unmask_text(summary_masked, pii_map)
        final_response = pii_vault.unmask_text(final_response_masked, pii_map)

        extracted_pages = [
            OCRPageResult(
                page=int(item.get("page") or 1),
                detected_language=str(item.get("detected_language") or "unknown"),
                text_en=str(item.get("text_en") or ""),
            )
            for item in translated_pages
        ]

        return OCRAnalyzeResponse(
            response=final_response,
            summary=summary,
            extracted_pages=extracted_pages,
            citations=citations,
            overall_confidence=overall_confidence,
            masked_entities=pii_map,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("FIR OCR analysis failed: %s", exc)
        raise HTTPException(status_code=500, detail="FIR OCR analysis failed. Please try again.")
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass


@app.get("/api/history/sessions", response_model=list[SessionSummary])
async def get_history_sessions(user: dict = Depends(verify_token)):
    """List chat sessions for authenticated user."""
    user_id = get_required_user_id(user)

    try:
        chat_repo = get_chat_repo()
        rows = chat_repo.list_sessions(user_id=user_id)
        return [SessionSummary(**item) for item in rows]
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch sessions: {exc}")


@app.get("/api/history/sessions/{session_id}", response_model=list[SessionMessage])
async def get_history_messages(session_id: str, user: dict = Depends(verify_token)):
    """Get messages for one chat session for authenticated user."""
    user_id = get_required_user_id(user)

    try:
        chat_repo = get_chat_repo()
        rows = chat_repo.get_session_messages(user_id=user_id, session_id=session_id)
        if not rows:
            raise HTTPException(status_code=404, detail="Session not found")
        return [SessionMessage(**item) for item in rows]
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch session messages: {exc}")


@app.patch("/api/history/sessions/{session_id}")
async def update_history_session(
    session_id: str,
    request: UpdateSessionRequest,
    user: dict = Depends(verify_token),
):
    """Update user-owned session metadata like title/pinned."""
    user_id = get_required_user_id(user)

    try:
        chat_repo = get_chat_repo()
        updated = chat_repo.update_session(
            user_id=user_id,
            session_id=session_id,
            title=request.title,
            pinned=request.pinned,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"status": "ok"}
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update session: {exc}")


@app.delete("/api/history/sessions/{session_id}")
async def delete_history_session(session_id: str, user: dict = Depends(verify_token)):
    """Delete one user-owned chat session and all related messages."""
    user_id = get_required_user_id(user)

    try:
        chat_repo = get_chat_repo()
        deleted = chat_repo.delete_session(user_id=user_id, session_id=session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"status": "ok"}
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete session: {exc}")

if __name__ == "__main__":
    # Run the server on port 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)