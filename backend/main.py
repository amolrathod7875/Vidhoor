import grpc_stubs  # noqa: F401 — sets up DLL stubs for grpc/oracledb on restricted Windows
from fastapi import FastAPI, HTTPException, Header, Depends, UploadFile, File, Form, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Any, Optional
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4
import uvicorn
import firebase_admin
from firebase_admin import auth as firebase_auth, credentials as firebase_credentials

from chroma_manager import ChromaManager
from database import OracleChatHistoryRepository
from llm_engine import LLMEngine
from agentic_rag import AgenticRagConfig, AgenticRagHelpers, AgenticRagRunner
from pii_vault import PIIVault
from services.draft_mailer import send_legal_draft_email
from services.draft_exporter import render_draft_docx_bytes, render_draft_pdf_bytes
from services.ocr_vision import VisionOCRService
from services.translate_helsinki import translate_pages_to_english
from services.indian_kanoon_live import fetch_indian_kanoon_case_links

logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Vidhoor Legal Copilot API")

_BASE_DIR = Path(__file__).resolve().parent
_LEGAL_DOCS_DIR = _BASE_DIR / "data"

if _LEGAL_DOCS_DIR.exists() and _LEGAL_DOCS_DIR.is_dir():
    app.mount("/legal", StaticFiles(directory=str(_LEGAL_DOCS_DIR)), name="legal")

# VERY IMPORTANT: Configure CORS so your React frontend can talk to this backend
DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://localhost:8080",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8080",
    "https://vidhoor-lizs.vercel.app",
]


def _build_cors_allowed_origins() -> list[str]:
    configured_origins_raw = os.getenv("CORS_ALLOW_ORIGINS", "")
    configured_origins = [
        origin.strip()
        for origin in configured_origins_raw.split(",")
        if origin.strip()
    ]
    merged_origins = DEFAULT_CORS_ORIGINS + configured_origins
    unique_origins = list(dict.fromkeys(merged_origins))
    return unique_origins


cors_allowed_origins = _build_cors_allowed_origins()
cors_allow_origin_regex = os.getenv("CORS_ALLOW_ORIGIN_REGEX", r"https://.*\.vercel\.app")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allowed_origins,
    allow_origin_regex=cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger.info("CORS allowed origins configured: %s", cors_allowed_origins)

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
    doc_type: str = ""
    case_name: str = ""
    citation_text: str = ""
    court: str = ""
    year: Optional[int] = None
    jurisdiction: str = ""
    bench: str = ""
    topic: str = ""
    precedent_rank: Optional[float] = None
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
    follow_ups: list[str] = []


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
    evidence_id: str | None = None
    encrypted_stored: bool = False


class EvidenceSummary(BaseModel):
    evidence_id: str
    file_name: str
    file_extension: str
    encryption_alg: str
    key_id: str
    session_id: str
    created_at: str


class EvidencePayloadResponse(BaseModel):
    evidence_id: str
    file_name: str
    file_extension: str
    encryption_alg: str
    key_id: str
    iv_b64: str
    encrypted_payload_b64: str
    masked_summary: str
    masked_analysis: str
    session_id: str
    created_at: str


class ConnectedDocument(BaseModel):
    file_name: str
    relative_path: str
    size_bytes: int
    updated_at: str


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
    citations: list[Citation] = []
    overall_confidence: Optional[float] = None
    follow_ups: list[str] = []


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    pinned: bool | None = None


class SessionShareCreateResponse(BaseModel):
    share_id: str
    share_url: str
    expires_at: Optional[str] = None


class SharedSessionPayload(BaseModel):
    session_id: str
    title: str
    messages: list[SessionMessage]
    created_at: str
    updated_at: str


class DraftGenerateRequest(BaseModel):
    application_type: str = "bail_application"
    case_facts: str
    session_id: Optional[str] = None
    auto_email_to_user: bool = True


class DraftGenerateResponse(BaseModel):
    draft_id: str
    title: str
    application_type: str
    draft_content: str
    disclaimer: str
    email_target: str | None = None
    email_sent: bool = False
    email_message: str = ""


class DraftEmailRequest(BaseModel):
    recipient_email: str | None = None


class DraftUpdateRequest(BaseModel):
    title: str | None = None
    draft_content: str | None = None


class DraftEmailResponse(BaseModel):
    draft_id: str
    sent: bool
    recipient_email: str
    message: str


class DraftRecord(BaseModel):
    draft_id: str
    user_id: str
    email_id: str
    session_id: str
    application_type: str
    title: str
    draft_content: str
    draft_meta: dict[str, Any] = Field(default_factory=dict)
    delivery_status: str
    last_delivery_error: str
    emailed_at: str
    created_at: str
    updated_at: str


class FeedbackRequest(BaseModel):
    message: str = Field(min_length=1, max_length=5000)
    allow_follow_up: bool = False
    page_url: str | None = None
    user_agent: str | None = None
    app_version: str | None = None
    context: str | None = None


class FeedbackResponse(BaseModel):
    accepted: bool
    feedback_id: str
    message: str


_chroma_manager: Optional[ChromaManager] = None
_llm_engine: Optional[LLMEngine] = None
_pii_vault: Optional[PIIVault] = None
_chat_repo: Optional[OracleChatHistoryRepository] = None
_ocr_service: Optional[VisionOCRService] = None
_bm25_refresh_counter: int = 0
_firebase_auth_initialized: bool = False

_SHARE_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


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

CASE_QUERY_KEYWORDS = {
    "case",
    "cases",
    "judgment",
    "judgement",
    "order",
    "orders",
    "precedent",
    "precedents",
}

RECENT_CASE_HINTS = {
    "recent",
    "latest",
    "new",
    "current",
    "this year",
    "last year",
    "today",
    "now",
    "recent cases",
    "latest cases",
    "recent judgments",
    "recent judgements",
}

BAIL_QUERY_KEYWORDS = {
    "bail",
    "bailable",
    "non-bailable",
    "non bailable",
    "custody",
    "remand",
}


DRAFT_DISCLAIMER = (
    "This is an AI-generated legal draft for assistance only and is not a substitute for "
    "advice from a licensed advocate. Verify facts, sections, court details, and jurisdiction "
    "before filing or sending."
)


APPLICATION_TYPE_LABELS: dict[str, str] = {
    "bail_application": "Bail Application",
    "legal_notice": "Legal Notice",
    "police_complaint": "Police Complaint",
    "consumer_complaint": "Consumer Complaint",
    "custom": "Legal Draft",
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


def _normalize_application_type(value: str | None) -> str:
    """Normalize incoming application type to supported values."""
    normalized = re.sub(r"[^a-z_]", "", str(value or "").strip().lower())
    return normalized if normalized in APPLICATION_TYPE_LABELS else "custom"


def _build_legal_draft_prompt(
    application_type: str,
    case_facts_masked: str,
    session_context_masked: str,
) -> str:
    """Create prompt for legal draft generation with safety guardrails."""
    app_label = APPLICATION_TYPE_LABELS.get(application_type, "Legal Draft")
    bailable_note = (
        "If this is a bail draft, first assess whether facts suggest bailable vs non-bailable context. "
        "If uncertain, explicitly say uncertainty and include placeholders for advocate verification."
        if application_type == "bail_application"
        else ""
    )

    return (
        "You are Vidhoor, assisting with legal drafting in India. "
        "Generate a practical draft document in formal legal style, using placeholders where facts are missing. "
        "Never claim filing has happened. Do not fabricate case numbers, dates, or addresses.\n\n"
        f"Draft type: {app_label}\n"
        f"Case facts from user:\n{case_facts_masked[:12000]}\n\n"
        f"Recent conversation context (optional):\n{session_context_masked[:6000]}\n\n"
        f"{bailable_note}\n\n"
        "Output markdown with this structure:\n"
        "## Draft Title\n"
        "## Parties\n"
        "## Facts\n"
        "## Legal Grounds\n"
        "## Prayer/Relief Sought\n"
        "## Verification\n"
        "## Missing Details To Fill\n"
        "- bullet list\n"
    )


def _build_draft_email_subject(application_type: str, draft_title: str) -> str:
    """Build concise email subject for draft delivery."""
    label = APPLICATION_TYPE_LABELS.get(application_type, "Legal Draft")
    title = " ".join((draft_title or "").split())[:80]
    return f"Vidhoor Draft: {label} - {title or 'Review Required'}"


def _slugify_filename(value: str, fallback: str = "draft") -> str:
    """Generate filesystem-safe filename stem."""
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:80] or fallback


def _extract_recent_session_context(user_id: str, session_id: str | None, limit: int = 8) -> str:
    """Collect recent user/assistant messages for draft grounding."""
    if not session_id:
        return ""

    try:
        rows = get_chat_repo().get_session_messages(user_id=user_id, session_id=session_id)
    except Exception:
        return ""

    if not rows:
        return ""

    selected = rows[-limit:]
    lines = [
        f"{str(item.get('role') or 'user').upper()}: {str(item.get('content') or '').strip()}"
        for item in selected
        if str(item.get("content") or "").strip()
    ]
    return "\n\n".join(lines)


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
        try:
            count = _chroma_manager.collection.count()
            if count == 0:
                logger.warning(
                    "Chroma collection '%s' is empty at startup.",
                    _chroma_manager.collection_name,
                )
            else:
                logger.info(
                    "Chroma collection '%s' has %d chunks.",
                    _chroma_manager.collection_name,
                    count,
                )
            dim_info = _chroma_manager.check_embedding_dimension()
            if dim_info.get("expected") and dim_info.get("stored") and not dim_info.get("match"):
                logger.warning(
                    "Embedding dimension mismatch in collection '%s': expected %s, stored %s",
                    _chroma_manager.collection_name,
                    dim_info.get("expected"),
                    dim_info.get("stored"),
                )
            elif dim_info.get("expected") and dim_info.get("stored"):
                logger.info(
                    "Embedding dimension OK: %d",
                    dim_info.get("expected"),
                )
        except Exception as exc:
            logger.warning("Chroma startup health check failed: %s", exc)
    return _chroma_manager


def get_llm_engine() -> LLMEngine:
    """Get or create singleton LLM engine instance."""
    global _llm_engine
    if _llm_engine is None:
        _llm_engine = LLMEngine(model="gpt-oss-120b")
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


def _build_agentic_rag_runner(
    llm_engine: LLMEngine,
    chroma_manager: ChromaManager,
) -> AgenticRagRunner:
    """Create agentic RAG runner with environment-tuned settings."""
    use_router_llm = os.getenv("AGENTIC_RAG_USE_ROUTER_LLM", "true").strip().lower() not in {"0", "false", "no"}
    config = AgenticRagConfig(
        max_expansions=int(os.getenv("AGENTIC_RAG_MAX_EXPANSIONS", "3") or "3"),
        max_citations=int(os.getenv("AGENTIC_RAG_MAX_CITATIONS", "8") or "8"),
        max_context_chunks=int(os.getenv("AGENTIC_RAG_MAX_CONTEXT_CHUNKS", "12") or "12"),
        max_context_chars=int(os.getenv("AGENTIC_RAG_MAX_CONTEXT_CHARS", "12000") or "12000"),
        max_router_chars=int(os.getenv("AGENTIC_RAG_MAX_ROUTER_CHARS", "1200") or "1200"),
        use_router_llm=use_router_llm,
    )
    helpers = AgenticRagHelpers(
        infer_act_filters=infer_act_filters,
        extract_requested_references=_extract_requested_references,
        citation_matches_allowed_acts=_citation_matches_allowed_acts,
        citation_matches_requested_references=_citation_matches_requested_references,
        format_citation_context=_format_citation_context,
        normalize_citation_links=_normalize_citation_links,
        citation_factory=lambda item: Citation(**item),
    )
    return AgenticRagRunner(
        llm_engine=llm_engine,
        chroma_manager=chroma_manager,
        helpers=helpers,
        config=config,
    )


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
    if "information technology act" in normalized or re.search(r"\bit\s*act\b", normalized):
        return "Information Technology Act, 2000"
    if "it act 2000" in normalized:
        return "Information Technology Act, 2000"

    if any(token in normalized for token in ("case law", "case", "judgment", "judgement", "precedent")):
        return "Indian Case Law"

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
    if "information technology act" in normalized or re.search(r"\bit\s*act\b", normalized):
        filters.append("Information Technology Act, 2000")
    if "it act 2000" in normalized:
        filters.append("Information Technology Act, 2000")

    if any(token in normalized for token in ("case law", "case", "judgment", "judgement", "precedent")):
        filters.append("Indian Case Law")

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


def _extract_years_from_query(query: str) -> list[int]:
    """Extract 4-digit years from query text."""
    found = re.findall(r"\b(20\d{2})\b", str(query or ""))
    years: list[int] = []
    for item in found:
        try:
            years.append(int(item))
        except ValueError:
            continue
    return years


def _is_recent_case_query(query: str) -> bool:
    """Detect if query explicitly asks for recent/current/year-specific cases."""
    normalized = str(query or "").lower().strip()
    if not normalized:
        return False

    if any(hint in normalized for hint in RECENT_CASE_HINTS):
        return True

    current_year = datetime.now().year
    if any(year in {current_year, current_year - 1} for year in _extract_years_from_query(normalized)):
        return True

    return False


def _is_case_request_query(query: str) -> bool:
    """Detect case/judgment lookup intent in legal context."""
    normalized = str(query or "").lower().strip()
    if not normalized:
        return False

    has_case_intent = any(keyword in normalized for keyword in CASE_QUERY_KEYWORDS)
    if not has_case_intent:
        return False

    has_legal_context = is_legal_query(normalized) or any(
        keyword in normalized
        for keyword in ("rape", "murder", "assault", "harassment", "dowry", "pocso", "crime", "offence")
    )
    return has_legal_context


def _should_fetch_indian_kanoon_links(query: str) -> bool:
    """Determine whether Indian Kanoon live links should be fetched for a query.

    Trigger modes via env INDIAN_KANOON_TRIGGER_MODE:
    - recent_only: only recent/current/year case queries
    - case_queries: any legal case/judgment request or legal query mentioning acts/sections/crime types
    - always_legal: any legal query
    """
    mode = os.environ.get("INDIAN_KANOON_TRIGGER_MODE", "case_queries").strip().lower()

    if mode == "recent_only":
        return _is_recent_case_query(query)
    if mode == "always_legal":
        return is_legal_query(query)
    
    # Default case_queries: case/judgment intent OR legal query with act/section mentions
    if _is_case_request_query(query) or _is_recent_case_query(query):
        return True
    
    # Also fetch for legal queries that mention specific acts, sections, or crime types
    if is_legal_query(query):
        normalized = str(query or "").lower()
        
        # Check for act mentions
        has_act_mention = any(
            act in normalized 
            for act in ("bns", "bnss", "bsa", "ipc", "crpc", "bharatiya", "constitution")
        )
        
        # Check for section/article references
        has_section_mention = bool(re.search(r"\b(section|article|sec|art)\s+\d+", normalized, flags=re.IGNORECASE))
        
        # Check for crime/offense types that typically have case law
        has_crime_type = any(
            crime in normalized
            for crime in ("rape", "sexual assault", "assault", "murder", "theft", "fraud", 
                         "harassment", "dowry", "child abuse", "pornography", "offense", "offence",
                         "domestic violence", "cruelty", "extortion", "kidnapping", "abduction")
        )
        
        if has_act_mention or has_section_mention or has_crime_type:
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


def _extract_section_refs_from_text(text: str | None) -> list[str]:
    """Extract section reference tokens mentioned in snippet text."""
    if not text:
        return []

    refs = re.findall(
        r"\b(?:section|sec\.?)(?:\s*[-:]?\s*)([0-9]+[A-Z]?(?:\([0-9A-Z]+\))?)\b",
        str(text),
        flags=re.IGNORECASE,
    )

    article_refs = re.findall(
        r"\b(?:article|art\.?)(?:\s*[-:]?\s*)([0-9]+[A-Z]?(?:\([0-9A-Z]+\))?)\b",
        str(text),
        flags=re.IGNORECASE,
    )

    heading_refs = re.findall(
        r"(?:^|\s)([0-9]{1,3}[A-Z]?)\s*[\.\:\-–—\)]\s*[A-Za-z]",
        str(text),
        flags=re.IGNORECASE,
    )

    paren_heading_refs = re.findall(
        r"(?:^|\s)([0-9]{1,3}[A-Z]?)\s*\.\s*\(",
        str(text),
        flags=re.IGNORECASE,
    )

    normalized: list[str] = []
    for value in refs + article_refs + heading_refs + paren_heading_refs:
        token = str(value).upper().strip()
        if token and token not in normalized:
            normalized.append(token)
    return normalized


def _citation_matches_requested_references(
    citation: "Citation",
    requested_references: list[str],
) -> bool:
    """Check whether a citation actually supports the requested references."""
    if not requested_references:
        return True

    section_value = str(citation.section or "").strip()
    if section_value and not any(
        _references_match(requested, section_value)
        for requested in requested_references
    ):
        return False

    snippet_refs = _extract_section_refs_from_text(citation.snippet)

    # If snippet explicitly mentions section(s), require a match to requested refs.
    if snippet_refs:
        return any(
            _references_match(requested, snippet_ref)
            for requested in requested_references
            for snippet_ref in snippet_refs
        )

    # Otherwise fall back to metadata section match.
    return any(
        _references_match(requested, section_value)
        for requested in requested_references
    )


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


def _looks_like_statute_source(citation: "Citation") -> bool:
    """Detect statute sources even when metadata doc_type is incorrect."""
    haystack = f"{citation.title} {citation.source} {citation.doc_id}"
    normalized = _normalize_text_token(haystack)
    token_haystack = set(re.findall(r"[a-z0-9]+", haystack.lower()))

    short_aliases = {"bns", "bnss", "bsa", "ipc"}
    if any(alias in token_haystack for alias in short_aliases):
        return True

    return any(
        alias in normalized
        for alias in (
            "bharatiyanyayasanhita",
            "bharatiyanagariksurakshasanhita",
            "bharatiyasakshyaadhiniyam",
            "constitutionofindia",
            "constitution",
            "informationtechnologyact",
            "itact",
            "itact2000",
        )
    )


def _act_aliases(act_name: str) -> list[str]:
    """Return canonical aliases for known act names."""
    normalized = _normalize_text_token(act_name)

    if "bharatiyanyayasanhita" in normalized:
        return ["bharatiyanyayasanhita", "bns"]
    if "bharatiyanagariksurakshasanhita" in normalized:
        return ["bharatiyanagariksurakshasanhita", "bnss"]
    if "bharatiyasakshyaadhiniyam" in normalized:
        return ["bharatiyasakshyaadhiniyam", "bsa"]
    if "informationtechnologyact" in normalized:
        return ["informationtechnologyact", "itact", "it", "itact2000", "informationtechnologyact2000"]
    if "constitutionofindia" in normalized:
        return ["constitutionofindia", "constitution"]

    return [normalized] if normalized else []


def _citation_matches_allowed_acts(citation: Citation, act_filters: list[str | None]) -> bool:
    """Check whether citation appears to belong to one of requested act filters."""
    doc_type = str(citation.doc_type or "").lower()
    if doc_type == "case_law" and not _looks_like_statute_source(citation):
        return True

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


def _derive_legal_source_base_url(request: Request | None = None) -> str:
    """Resolve public base URL for legal static files."""
    configured_base_url = os.environ.get("LEGAL_SOURCE_BASE_URL", "").strip().rstrip("/")
    if configured_base_url:
        return configured_base_url

    fallback_base_url = os.environ.get("APP_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if fallback_base_url:
        return f"{fallback_base_url}/legal"

    if request:
        return f"{request.url.scheme}://{request.url.netloc}/legal"

    return ""


def _derive_app_public_base_url(request: Request | None = None) -> str:
    """Resolve public app base URL for user-facing links."""
    configured = os.environ.get("APP_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured

    def _normalize_origin(value: str | None) -> str:
        candidate = str(value or "").strip().rstrip("/")
        if not _is_http_url(candidate):
            return ""
        parsed = urlsplit(candidate)
        if not parsed.scheme or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}"

    if request:
        frontend_origin = _normalize_origin(request.headers.get("x-frontend-origin"))
        if frontend_origin:
            return frontend_origin

        origin = _normalize_origin(request.headers.get("origin"))
        if origin:
            return origin

        referer = _normalize_origin(request.headers.get("referer"))
        if referer:
            return referer

        return f"{request.url.scheme}://{request.url.netloc}"

    return ""


def _get_share_signing_secret() -> bytes:
    """Resolve HMAC secret used for share link signing."""
    configured = os.environ.get("SHARE_LINK_SECRET", "").strip()
    if configured:
        return configured.encode("utf-8")

    fallback = os.environ.get("CEREBRAS_API_KEY", "").strip()
    if fallback:
        logger.warning("SHARE_LINK_SECRET not set; using CEREBRAS_API_KEY as fallback")
        return fallback.encode("utf-8")

    raise EnvironmentError("Missing SHARE_LINK_SECRET (or fallback CEREBRAS_API_KEY)")


def _create_share_id(user_id: str, session_id: str, ttl_seconds: int = _SHARE_TOKEN_TTL_SECONDS) -> str:
    """Create signed, url-safe share identifier for one user session."""
    now = int(time.time())
    exp = now + max(60, int(ttl_seconds))
    payload = {
        "uid": str(user_id).strip(),
        "sid": str(session_id).strip(),
        "iat": now,
        "exp": exp,
    }
    payload_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode("utf-8")).decode("ascii").rstrip("=")

    secret = _get_share_signing_secret()
    signature = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{payload_b64}.{sig_b64}"


def _decode_share_id(share_id: str) -> dict[str, Any]:
    """Validate and decode signed share identifier."""
    token = str(share_id or "").strip()
    if not token or "." not in token:
        raise HTTPException(status_code=400, detail="Invalid share id")

    payload_b64, sig_b64 = token.split(".", 1)
    if not payload_b64 or not sig_b64:
        raise HTTPException(status_code=400, detail="Invalid share id")

    secret = _get_share_signing_secret()
    expected_sig = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
    expected_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode("ascii").rstrip("=")
    if not hmac.compare_digest(expected_sig_b64, sig_b64):
        raise HTTPException(status_code=403, detail="Invalid share signature")

    padded_payload = payload_b64 + "=" * ((4 - len(payload_b64) % 4) % 4)
    try:
        payload_raw = base64.urlsafe_b64decode(padded_payload.encode("ascii"))
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Malformed share payload: {exc}")

    uid = str(payload.get("uid") or "").strip()
    sid = str(payload.get("sid") or "").strip()
    exp = int(payload.get("exp") or 0)
    if not uid or not sid or not exp:
        raise HTTPException(status_code=400, detail="Incomplete share payload")

    if exp < int(time.time()):
        raise HTTPException(status_code=410, detail="Share link expired")

    payload["uid"] = uid
    payload["sid"] = sid
    payload["exp"] = exp
    return payload


def _is_local_source_url(value: str | None) -> bool:
    """Check whether URL points to localhost/loopback host."""
    candidate = str(value or "").strip()
    if not _is_http_url(candidate):
        return False

    host = (urlsplit(candidate).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _build_source_url_from_base(source_name: str, request: Request | None = None) -> str:
    """Build source URL from configured or request-derived legal source base URL and source filename."""
    base_url = _derive_legal_source_base_url(request)
    if not base_url:
        return ""

    filename = str(source_name or "").strip()
    if not filename:
        return ""

    return f"{base_url}/{quote(filename)}"


def _normalize_source_url(source_url: str, source_name: str, request: Request | None = None) -> str:
    """Normalize source URL and rewrite localhost links to a public legal base URL."""
    cleaned_source_url = str(source_url or "").strip()
    if not _is_http_url(cleaned_source_url):
        return _build_source_url_from_base(source_name, request)

    if not _is_local_source_url(cleaned_source_url):
        return cleaned_source_url

    legal_base_url = _derive_legal_source_base_url(request)
    if not legal_base_url:
        return cleaned_source_url

    parsed = urlsplit(cleaned_source_url)
    raw_path = str(parsed.path or "").strip()
    if raw_path.startswith("/legal/"):
        relative_path = raw_path[len("/legal/"):]
    else:
        relative_path = Path(raw_path).name

    if not relative_path:
        return cleaned_source_url

    rebuilt_url = f"{legal_base_url}/{quote(relative_path, safe='/')}"
    if parsed.query:
        rebuilt_url = f"{rebuilt_url}?{parsed.query}"
    if parsed.fragment:
        rebuilt_url = f"{rebuilt_url}#{parsed.fragment}"

    return rebuilt_url


def _append_page_anchor(url: str, page: int | None) -> str:
    """Append PDF page anchor to URL when page is available."""
    if not url:
        return ""
    if page is None:
        return url
    if "#" in url:
        return url
    return f"{url}#page={int(page)}"


def _normalize_citation_links(citations: list[Citation], request: Request | None = None) -> list[Citation]:
    """Ensure citations include usable source URLs with optional page deep links."""
    normalized: list[Citation] = []
    for citation in citations:
        source_url = _normalize_source_url(
            source_url=str(citation.source_url or ""),
            source_name=citation.source,
            request=request,
        )

        normalized_url = _append_page_anchor(source_url, citation.page)
        normalized.append(citation.model_copy(update={"source_url": normalized_url}))
    return normalized


def _format_citation_context(citation: Citation) -> str:
    """Render a retrieval context block with richer metadata for prompting."""
    metadata_parts: list[str] = [
        f"Title: {citation.title}",
        f"Source: {citation.source}",
    ]

    if citation.section:
        metadata_parts.append(f"Section/Article: {citation.section}")

    if str(citation.doc_type or "").lower() == "case_law":
        if citation.case_name:
            metadata_parts.append(f"Case: {citation.case_name}")
        if citation.citation_text:
            metadata_parts.append(f"Citation: {citation.citation_text}")
        if citation.court:
            metadata_parts.append(f"Court: {citation.court}")
        if citation.year:
            metadata_parts.append(f"Year: {citation.year}")
        if citation.jurisdiction:
            metadata_parts.append(f"Jurisdiction: {citation.jurisdiction}")
        if citation.bench:
            metadata_parts.append(f"Bench: {citation.bench}")
        if citation.topic:
            metadata_parts.append(f"Topic: {citation.topic}")

    header = " | ".join(metadata_parts)
    return f"[{header}]\n{citation.snippet}"


def _sanitize_markdown_link_label(text: str) -> str:
    """Sanitize user-visible markdown link labels."""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    cleaned = cleaned.replace("[", "(").replace("]", ")")
    return cleaned


def _build_related_case_links_markdown(case_links: list[dict[str, str]]) -> str:
    """Build markdown section for live Indian Kanoon links."""
    if not case_links:
        return ""

    lines = ["### Related recent case links (Indian Kanoon)"]
    for link in case_links:
        title = _sanitize_markdown_link_label(link.get("title") or "Indian Kanoon Case")
        url = str(link.get("url") or "").strip()
        if not url:
            continue

        tail_parts: list[str] = []
        if str(link.get("court") or "").strip():
            tail_parts.append(str(link.get("court") or "").strip())
        if str(link.get("date") or "").strip():
            tail_parts.append(str(link.get("date") or "").strip())

        if tail_parts:
            lines.append(f"- [{title}]({url}) — {' | '.join(tail_parts)}")
        else:
            lines.append(f"- [{title}]({url})")

    if len(lines) <= 1:
        return ""

    lines.append("- Links are for quick reference; verify facts on the source page.")
    return "\n".join(lines)


def _fallback_follow_ups(query: str, citations: list[Citation]) -> list[str]:
    """Build deterministic follow-up suggestions when LLM follow-up generation fails."""
    seed = " ".join(str(query or "").split())
    if not seed:
        seed = "this legal issue"

    suggestions = [
        f"What are the key legal ingredients for {seed}?"[:120],
        "What evidence and documents should I gather next?",
        "What are the possible outcomes and legal risks?",
    ]

    if citations:
        title = " ".join(str(citations[0].title or "").split())
        if title:
            suggestions.insert(1, f"Can you summarize the key points from {title}?"[:120])

    normalized: list[str] = []
    seen: set[str] = set()
    for item in suggestions:
        text = " ".join(str(item or "").split())
        if not text:
            continue
        if not text.endswith("?"):
            text = f"{text}?"
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized[:5]


def _retrieve_legal_citations(masked_query: str, request: Request | None = None) -> tuple[list[Citation], Optional[float]]:
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
    citations = _normalize_citation_links(citations[:8], request)

    if not citations:
        return [], None

    overall_confidence = round(
        sum(item.confidence for item in citations) / len(citations),
        2,
    )
    return citations, overall_confidence

def _decode_jwt_payload_unverified(token: str) -> dict[str, Any]:
    """Decode JWT payload without signature verification (fallback only)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8")
        data = json.loads(decoded)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def _initialize_firebase_auth_if_configured() -> bool:
    """Initialize firebase-admin if service account configuration exists."""
    global _firebase_auth_initialized
    if _firebase_auth_initialized:
        return True

    try:
        if not firebase_admin._apps:
            cred_path = (os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH") or "").strip()
            if cred_path:
                resolved = Path(cred_path)
                if not resolved.is_absolute():
                    resolved = (_BASE_DIR / resolved).resolve()
                if not resolved.exists():
                    logger.warning("Firebase service account file not found at %s", resolved)
                    return False
                cred = firebase_credentials.Certificate(str(resolved))
                firebase_admin.initialize_app(cred)
            else:
                firebase_admin.initialize_app()

        _firebase_auth_initialized = True
        return True
    except Exception as exc:
        logger.warning("Firebase auth initialization skipped: %s", exc)
        return False


# --- Dependency to verify Firebase Auth Token ---
async def verify_token(
    authorization: str = Header(None),
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
):
    if not authorization:
        # For Guest Mode, we might not have a token
        return None
    token_parts = authorization.split(" ")
    if len(token_parts) != 2 or token_parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    token = token_parts[1]
    if not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")

    if _initialize_firebase_auth_if_configured():
        try:
            decoded_token = firebase_auth.verify_id_token(token)
            return {
                "uid": str(decoded_token.get("uid") or decoded_token.get("sub") or "").strip(),
                "email": str(decoded_token.get("email") or x_user_email or "").strip(),
            }
        except Exception as exc:
            logger.warning("Firebase token verification failed, falling back to JWT payload decode: %s", exc)

    payload = _decode_jwt_payload_unverified(token)
    uid = str(payload.get("user_id") or payload.get("uid") or payload.get("sub") or "").strip()
    email = str(payload.get("email") or x_user_email or "").strip()
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid bearer token")

    return {"uid": uid, "email": email}


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
    chroma_info = {"collection": "indian_law", "count": 0, "status": "unknown"}
    try:
        manager = get_chroma_manager()
        chroma_info["count"] = manager.collection.count()
        dim_info = manager.check_embedding_dimension()
        chroma_info["embedding_dimension"] = dim_info
        if dim_info.get("expected") and dim_info.get("stored") and not dim_info.get("match"):
            chroma_info["status"] = "degraded: embedding dimension mismatch"
        else:
            chroma_info["status"] = "healthy"
    except Exception as exc:
        chroma_info["status"] = f"error: {exc}"
    return {
        "status": "Vidhoor Backend is live and waiting for legal queries.",
        "chroma": chroma_info,
    }


@app.post("/api/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    feedback: FeedbackRequest,
    request: Request,
    user: dict | None = Depends(verify_token),
):
    """Accept feedback submissions from UI and persist them in Oracle DB."""
    feedback_id = str(uuid4())
    user_id = str((user or {}).get("uid") or "").strip() or None
    user_email = str((user or {}).get("email") or "").strip() or None
    clean_message = "\n".join(line.rstrip() for line in feedback.message.strip().splitlines())

    try:
        get_chat_repo().save_user_feedback(
            feedback_id=feedback_id,
            message=clean_message,
            allow_follow_up=bool(feedback.allow_follow_up),
            page_url=str(feedback.page_url or request.url.path),
            user_agent=str(feedback.user_agent or request.headers.get("user-agent") or ""),
            app_version=str(feedback.app_version or ""),
            context=str(feedback.context or ""),
            user_id=user_id,
            user_email=user_email,
        )
    except Exception as exc:
        logger.exception("Failed to store feedback in Oracle")
        raise HTTPException(status_code=500, detail=f"Failed to submit feedback: {exc}") from exc

    logger.info("Feedback submitted: id=%s, user_id=%s", feedback_id, user_id or "guest")
    return FeedbackResponse(
        accepted=True,
        feedback_id=feedback_id,
        message="Report sent. Thank you!",
    )

@app.post("/api/chat", response_model=ChatResponse)
async def process_chat(chat_request: ChatRequest, request: Request, user: dict = Depends(verify_token)):
    try:
        global _bm25_refresh_counter
        pii_vault = get_pii_vault()
        llm_engine = get_llm_engine()
        masked_message, pii_map = pii_vault.mask_text(chat_request.message)

        document_pairs: list[tuple[str, str]] = []
        multi_contexts = [str(item or "").strip() for item in (chat_request.document_contexts or []) if str(item or "").strip()]
        multi_names = [str(item or "").strip() for item in (chat_request.document_names or [])]
        if multi_contexts:
            for index, context in enumerate(multi_contexts, start=1):
                name = multi_names[index - 1] if index - 1 < len(multi_names) and multi_names[index - 1] else f"Document {index}"
                document_pairs.append((name, context))

        single_context = (chat_request.document_context or "").strip()
        if not document_pairs and single_context:
            document_pairs.append((chat_request.document_name or "uploaded document", single_context))

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
        session_id = chat_request.session_id or f"session_{uuid4().hex}"
        citations: list[Citation] = []
        overall_confidence: Optional[float] = None
        skip_follow_ups = False

        if is_legal_query(masked_message):
            try:
                chroma_manager = get_chroma_manager()
                _bm25_refresh_counter += 1
                if _bm25_refresh_counter % 5 == 0:
                    chroma_manager.refresh_bm25_from_oracle(filter_status="active", filter_act=None)

                use_agentic = os.getenv("ENABLE_AGENTIC_RAG", "true").strip().lower() not in {"0", "false", "no"}
                if use_agentic:
                    agentic_runner = _build_agentic_rag_runner(
                        llm_engine=llm_engine,
                        chroma_manager=chroma_manager,
                    )
                    agentic_result = agentic_runner.run(
                        masked_query=masked_message,
                        masked_document_context=masked_document_context,
                        request=request,
                    )
                    ai_response_masked = agentic_result.response
                    citations = agentic_result.citations
                    overall_confidence = agentic_result.overall_confidence
                    skip_follow_ups = agentic_result.clarifying_question is not None
                else:

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

                        if (
                            act_filter
                            and not retrieval.get("documents")
                            and not retrieval.get("citations")
                        ):
                            retrieval = chroma_manager.retrieve_context_with_metadata(
                                query_string=retrieval_query,
                                filter_status="active",
                                filter_act=None,
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
                    citations = _normalize_citation_links(citations, request)

                    # retrieved_context already populated from Chroma results above

                    requested_references = _extract_requested_references(masked_message)

                    has_direct_reference_match = True
                    if requested_references:
                        has_direct_reference_match = all(
                            any(
                                _citation_matches_requested_references(
                                    citation,
                                    [requested_reference],
                                )
                                for citation in citations
                            )
                            for requested_reference in requested_references
                        )

                    if requested_references and citations:
                        citations = [
                            citation
                            for citation in citations
                            if _citation_matches_requested_references(
                                citation,
                                requested_references,
                            )
                        ]

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
                        retrieved_context = [
                            _format_citation_context(item)
                            for item in citations
                            if item.snippet
                        ]

                        if citations:
                            overall_confidence = round(
                                sum(item.confidence for item in citations) / len(citations),
                                2,
                            )

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

        should_try_indian_kanoon = _should_fetch_indian_kanoon_links(chat_request.message)
        if should_try_indian_kanoon:
            try:
                include_related_links = os.environ.get("ENABLE_INDIAN_KANOON_LINKS", "true").strip().lower() not in {"0", "false", "no"}
                if include_related_links:
                    max_links = int(os.environ.get("INDIAN_KANOON_MAX_LINKS", "3") or "3")
                    case_links = fetch_indian_kanoon_case_links(
                        query=chat_request.message,
                        max_links=max_links,
                    )
                    links_section = _build_related_case_links_markdown(case_links)
                    if links_section:
                        final_readable_response = f"{final_readable_response}\n\n{links_section}"
            except Exception as exc:
                logger.warning("Failed to fetch Indian Kanoon related links: %s", exc)

        follow_ups: list[str] = []
        if not skip_follow_ups:
            try:
                follow_ups = llm_engine.generate_follow_up_questions(
                    user_query=chat_request.message,
                    assistant_answer=final_readable_response,
                    max_count=5,
                )
            except Exception as exc:
                logger.warning("Failed to generate follow-up suggestions: %s", exc)

            if not follow_ups:
                follow_ups = _fallback_follow_ups(chat_request.message, citations)
        
        # Step 5: Save to Oracle DB (if NOT temporary and authenticated)
        if not chat_request.is_temporary_chat and user and user.get("uid"):
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
                        user_message=chat_request.message,
                        assistant_message=final_readable_response,
                    )

                chat_repo.save_chat_turn(
                    user_id=user_id,
                    session_id=session_id,
                    user_message=chat_request.message,
                    assistant_message=final_readable_response,
                    masked_entities=pii_map,
                    assistant_citations=[item.model_dump() for item in citations],
                    assistant_follow_ups=follow_ups,
                    assistant_overall_confidence=overall_confidence,
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
            follow_ups=follow_ups,
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/drafts/generate", response_model=DraftGenerateResponse)
async def generate_legal_draft(
    request: DraftGenerateRequest,
    user: dict = Depends(verify_token),
):
    """Generate and persist legal draft, then optionally email it to the authenticated user."""
    user_id = get_required_user_id(user)
    case_facts = (request.case_facts or "").strip()
    if not case_facts:
        raise HTTPException(status_code=400, detail="case_facts is required")

    application_type = _normalize_application_type(request.application_type)
    pii_vault = get_pii_vault()
    llm_engine = get_llm_engine()

    masked_case_facts, pii_map = pii_vault.mask_text(case_facts)
    recent_context = _extract_recent_session_context(user_id=user_id, session_id=request.session_id)
    masked_context, _ = pii_vault.mask_text(recent_context) if recent_context else ("", {})

    draft_prompt = _build_legal_draft_prompt(
        application_type=application_type,
        case_facts_masked=masked_case_facts,
        session_context_masked=masked_context,
    )

    try:
        generated_masked = llm_engine.generate_general_response(draft_prompt)
    except Exception as exc:
        logger.exception("Draft generation failed: %s", exc)
        raise HTTPException(status_code=500, detail="Draft generation failed")

    draft_content = pii_vault.unmask_text(generated_masked, pii_map)
    title = f"{APPLICATION_TYPE_LABELS.get(application_type, 'Legal Draft')} - {datetime.utcnow().date().isoformat()}"
    draft_id = f"draft_{uuid4().hex}"
    email_id = str(user.get("email") or "").strip() or None

    chat_repo = get_chat_repo()
    chat_repo.save_user_draft(
        user_id=user_id,
        draft_id=draft_id,
        email_id=email_id,
        application_type=application_type,
        title=title,
        draft_content=draft_content,
        session_id=request.session_id,
        draft_meta={
            "source": "agentic_drafting",
            "auto_email_requested": bool(request.auto_email_to_user),
        },
    )

    if request.session_id:
        try:
            chat_repo.save_chat_turn(
                user_id=user_id,
                session_id=request.session_id,
                user_message=f"Generate legal draft: {APPLICATION_TYPE_LABELS.get(application_type, 'Legal Draft')}",
                assistant_message=f"### {title}\n\n{draft_content}\n\n> {DRAFT_DISCLAIMER}",
                masked_entities={},
                session_title=None,
            )
        except Exception as exc:
            logger.exception("Failed to append generated draft into chat history: %s", exc)

    email_target = email_id
    email_sent = False
    email_message = "Draft generated successfully."
    if request.auto_email_to_user and email_target:
        sent, delivery_message = send_legal_draft_email(
            recipient_email=email_target,
            subject=_build_draft_email_subject(application_type, title),
            draft_title=title,
            draft_content=draft_content,
            disclaimer=DRAFT_DISCLAIMER,
        )
        email_sent = sent
        email_message = delivery_message
        chat_repo.mark_draft_delivery(
            user_id=user_id,
            draft_id=draft_id,
            delivery_status="sent" if sent else "email_failed",
            last_delivery_error="" if sent else delivery_message,
            emailed_at=sent,
        )
    elif request.auto_email_to_user and not email_target:
        email_message = "Draft generated, but user email is unavailable."
        chat_repo.mark_draft_delivery(
            user_id=user_id,
            draft_id=draft_id,
            delivery_status="email_skipped",
            last_delivery_error="User email unavailable from auth context",
            emailed_at=False,
        )

    return DraftGenerateResponse(
        draft_id=draft_id,
        title=title,
        application_type=application_type,
        draft_content=draft_content,
        disclaimer=DRAFT_DISCLAIMER,
        email_target=email_target,
        email_sent=email_sent,
        email_message=email_message,
    )


@app.post("/api/drafts/{draft_id}/email", response_model=DraftEmailResponse)
async def email_saved_draft(
    draft_id: str,
    request: DraftEmailRequest,
    user: dict = Depends(verify_token),
):
    """Email an already-generated draft to user-selected or authenticated email."""
    user_id = get_required_user_id(user)
    chat_repo = get_chat_repo()
    draft = chat_repo.get_user_draft(user_id=user_id, draft_id=draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    recipient_email = (request.recipient_email or "").strip() or str(draft.get("email_id") or "").strip() or str(user.get("email") or "").strip()
    if not recipient_email:
        raise HTTPException(status_code=400, detail="Recipient email is required")

    sent, message = send_legal_draft_email(
        recipient_email=recipient_email,
        subject=_build_draft_email_subject(str(draft.get("application_type") or "custom"), str(draft.get("title") or "")),
        draft_title=str(draft.get("title") or "Legal Draft"),
        draft_content=str(draft.get("draft_content") or ""),
        disclaimer=DRAFT_DISCLAIMER,
    )

    chat_repo.mark_draft_delivery(
        user_id=user_id,
        draft_id=draft_id,
        delivery_status="sent" if sent else "email_failed",
        last_delivery_error="" if sent else message,
        emailed_at=sent,
    )

    return DraftEmailResponse(
        draft_id=draft_id,
        sent=sent,
        recipient_email=recipient_email,
        message=message,
    )


@app.get("/api/drafts", response_model=list[DraftRecord])
async def list_saved_drafts(
    session_id: str | None = Query(default=None),
    user: dict = Depends(verify_token),
):
    """List generated drafts for authenticated user, optionally by chat session."""
    user_id = get_required_user_id(user)

    try:
        rows = get_chat_repo().list_user_drafts(user_id=user_id, session_id=session_id)
        return [DraftRecord(**item) for item in rows]
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch draft history: {exc}")


@app.get("/api/drafts/{draft_id}", response_model=DraftRecord)
async def get_saved_draft(
    draft_id: str,
    user: dict = Depends(verify_token),
):
    """Get one generated draft owned by authenticated user."""
    user_id = get_required_user_id(user)

    try:
        draft = get_chat_repo().get_user_draft(user_id=user_id, draft_id=draft_id)
        if not draft:
            raise HTTPException(status_code=404, detail="Draft not found")
        return DraftRecord(**draft)
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch draft: {exc}")


@app.patch("/api/drafts/{draft_id}", response_model=DraftRecord)
async def update_saved_draft(
    draft_id: str,
    request: DraftUpdateRequest,
    user: dict = Depends(verify_token),
):
    """Update saved draft title/content for authenticated user."""
    user_id = get_required_user_id(user)

    next_title = request.title.strip() if request.title is not None else None
    next_content = request.draft_content if request.draft_content is not None else None

    if next_title is not None and not next_title:
        raise HTTPException(status_code=400, detail="title cannot be empty")
    if next_content is not None and not str(next_content).strip():
        raise HTTPException(status_code=400, detail="draft_content cannot be empty")

    try:
        chat_repo = get_chat_repo()
        updated = chat_repo.update_user_draft(
            user_id=user_id,
            draft_id=draft_id,
            title=next_title,
            draft_content=next_content,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Draft not found")

        latest = chat_repo.get_user_draft(user_id=user_id, draft_id=draft_id)
        if not latest:
            raise HTTPException(status_code=404, detail="Draft not found")
        return DraftRecord(**latest)
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update draft: {exc}")


@app.get("/api/drafts/{draft_id}/export")
async def export_saved_draft(
    draft_id: str,
    format: str = Query(default="pdf"),
    user: dict = Depends(verify_token),
):
    """Export saved draft as PDF or DOCX for download."""
    user_id = get_required_user_id(user)
    normalized = str(format or "pdf").strip().lower()
    if normalized not in {"pdf", "docx"}:
        raise HTTPException(status_code=400, detail="format must be 'pdf' or 'docx'")

    draft = get_chat_repo().get_user_draft(user_id=user_id, draft_id=draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    title = str(draft.get("title") or "Legal Draft")
    content = str(draft.get("draft_content") or "")

    try:
        if normalized == "pdf":
            payload = render_draft_pdf_bytes(title=title, draft_content=content, disclaimer=DRAFT_DISCLAIMER)
            media_type = "application/pdf"
            extension = "pdf"
        else:
            payload = render_draft_docx_bytes(title=title, draft_content=content, disclaimer=DRAFT_DISCLAIMER)
            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            extension = "docx"
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Draft export failed: %s", exc)
        raise HTTPException(status_code=500, detail="Draft export failed")

    filename = f"{_slugify_filename(title)}.{extension}"
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.post("/api/fir/analyze", response_model=OCRAnalyzeResponse)
async def analyze_fir_document(
    request: Request,
    file: UploadFile = File(...),
    query: str | None = Form(default=None),
    encrypted_payload_b64: str | None = Form(default=None),
    iv_b64: str | None = Form(default=None),
    encryption_alg: str | None = Form(default=None),
    key_id: str | None = Form(default=None),
    session_id: str | None = Form(default=None),
    is_temporary_chat: bool = Form(default=False),
    user: dict = Depends(verify_token),
):
    """Analyze uploaded FIR/scanned document with OCR, translation, PII masking, and legal grounding."""
    global _bm25_refresh_counter

    filename = file.filename or "uploaded_document"
    extension = Path(filename).suffix.lower()
    allowed_extensions = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Upload PDF, PNG, JPG, JPEG, WEBP, TIFF, or BMP.",
        )

    evidence_id: str | None = None
    encrypted_stored = False

    try:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        ocr_service = get_ocr_service()
        pages = ocr_service.extract_pages_from_bytes(filename=filename, file_bytes=file_bytes)
        del file_bytes
        if not pages:
            raise HTTPException(status_code=422, detail="No readable text found in uploaded document.")

        garbage_pages = [
            item for item in pages
            if VisionOCRService._looks_like_garbage(str(item.get("text") or ""))
        ]
        if garbage_pages:
            raise HTTPException(
                status_code=422,
                detail="Document scanned in an unsupported script or quality. Please upload a clearer document or a text-based PDF.",
            )

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

        citations, overall_confidence = _retrieve_legal_citations(legal_query, request)

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
            _format_citation_context(item)
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

        assistant_content = [
            "### OCR Summary",
            summary,
            "",
            "### Legal Analysis",
            final_response,
        ]

        if not is_temporary_chat and user and user.get("uid") and session_id:
            try:
                chat_repo = get_chat_repo()
                chat_repo.save_chat_turn(
                    user_id=str(user["uid"]),
                    session_id=session_id,
                    user_message=f"Uploaded document: {filename}",
                    assistant_message="\n".join(assistant_content),
                    masked_entities=pii_map,
                    assistant_citations=[item.model_dump() for item in citations],
                    assistant_overall_confidence=overall_confidence,
                )
            except Exception as exc:
                logger.exception("Failed to persist upload chat history: %s", exc)

        if (
            user
            and user.get("uid")
            and encrypted_payload_b64
            and iv_b64
            and encryption_alg
        ):
            try:
                evidence_id = f"evi_{uuid4().hex}"
                chat_repo = get_chat_repo()
                chat_repo.save_encrypted_evidence(
                    user_id=str(user["uid"]),
                    evidence_id=evidence_id,
                    file_name=filename,
                    file_extension=extension,
                    encrypted_payload_b64=encrypted_payload_b64,
                    iv_b64=iv_b64,
                    encryption_alg=encryption_alg,
                    key_id=key_id,
                    masked_summary=summary_masked,
                    masked_analysis=final_response_masked,
                    session_id=session_id,
                )
                encrypted_stored = True
            except Exception as exc:
                logger.exception("Failed to persist encrypted evidence: %s", exc)

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
            evidence_id=evidence_id,
            encrypted_stored=encrypted_stored,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("FIR OCR analysis failed: %s", exc)
        raise HTTPException(status_code=500, detail="FIR OCR analysis failed. Please try again.")


@app.get("/api/evidence", response_model=list[EvidenceSummary])
async def list_user_evidence(
    session_id: str | None = None,
    user: dict = Depends(verify_token),
):
    """List encrypted uploaded resources for authenticated user."""
    user_id = get_required_user_id(user)

    try:
        chat_repo = get_chat_repo()
        rows = chat_repo.list_user_evidence(user_id=user_id, session_id=session_id)
        return [EvidenceSummary(**item) for item in rows]
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch evidence list: {exc}")


@app.get("/api/evidence/{evidence_id}", response_model=EvidencePayloadResponse)
async def get_user_evidence(evidence_id: str, user: dict = Depends(verify_token)):
    """Get one encrypted uploaded resource payload for authenticated user."""
    user_id = get_required_user_id(user)

    try:
        chat_repo = get_chat_repo()
        payload = chat_repo.get_evidence_payload(user_id=user_id, evidence_id=evidence_id)
        if not payload:
            raise HTTPException(status_code=404, detail="Evidence not found")
        return EvidencePayloadResponse(**payload)
    except HTTPException:
        raise
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch evidence payload: {exc}")


@app.get("/api/connected-documents", response_model=list[ConnectedDocument])
async def list_connected_documents():
    """List legal source documents under backend/data, excluding the Case subtree."""
    try:
        if not _LEGAL_DOCS_DIR.exists() or not _LEGAL_DOCS_DIR.is_dir():
            return []

        documents: list[ConnectedDocument] = []
        for path in _LEGAL_DOCS_DIR.rglob("*"):
            if not path.is_file():
                continue

            relative = path.relative_to(_LEGAL_DOCS_DIR)
            if relative.parts and relative.parts[0].lower() == "case":
                continue

            stat = path.stat()
            documents.append(
                ConnectedDocument(
                    file_name=path.name,
                    relative_path=relative.as_posix(),
                    size_bytes=int(stat.st_size),
                    updated_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                )
            )

        documents.sort(key=lambda item: item.relative_path.lower())
        return documents
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list connected documents: {exc}")


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


@app.post("/api/history/sessions/{session_id}/share", response_model=SessionShareCreateResponse)
async def create_share_link(
    session_id: str,
    request: Request,
    user: dict = Depends(verify_token),
):
    """Create a signed public share link for a user-owned chat session."""
    user_id = get_required_user_id(user)

    try:
        chat_repo = get_chat_repo()
        rows = chat_repo.get_session_messages(user_id=user_id, session_id=session_id)
        if not rows:
            raise HTTPException(status_code=404, detail="Session not found")

        share_id = _create_share_id(user_id=user_id, session_id=session_id)
        base_url = _derive_app_public_base_url(request)
        if not base_url:
            raise HTTPException(status_code=500, detail="Unable to derive public base URL")

        share_url = f"{base_url}/shared/{share_id}"
        exp_ts = _decode_share_id(share_id).get("exp")
        expires_at = datetime.fromtimestamp(int(exp_ts)).isoformat() if exp_ts else None
        return SessionShareCreateResponse(
            share_id=share_id,
            share_url=share_url,
            expires_at=expires_at,
        )
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create share link: {exc}")


@app.get("/api/shared/{share_id}", response_model=SharedSessionPayload)
async def get_shared_session(share_id: str):
    """Read a shared chat session without authentication using signed link token."""
    decoded = _decode_share_id(share_id)
    user_id = str(decoded["uid"])
    session_id = str(decoded["sid"])

    try:
        chat_repo = get_chat_repo()
        sessions = chat_repo.list_sessions(user_id=user_id)
        session_meta = next((item for item in sessions if item.get("session_id") == session_id), None)
        if not session_meta:
            raise HTTPException(status_code=404, detail="Shared session not found")

        rows = chat_repo.get_session_messages(user_id=user_id, session_id=session_id)
        if not rows:
            raise HTTPException(status_code=404, detail="Shared session not found")

        return SharedSessionPayload(
            session_id=session_id,
            title=str(session_meta.get("title") or "Shared Conversation").strip() or "Shared Conversation",
            messages=[SessionMessage(**item) for item in rows],
            created_at=str(session_meta.get("created_at") or ""),
            updated_at=str(session_meta.get("updated_at") or ""),
        )
    except EnvironmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch shared session: {exc}")

if __name__ == "__main__":
    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    port = int(os.getenv("BACKEND_PORT", "8001"))
    uvicorn.run(app, host=host, port=port)