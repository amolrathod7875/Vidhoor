from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Any, Optional
import logging
import os
import re
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4
import uvicorn

from chroma_manager import ChromaManager
from database import OracleChatHistoryRepository
from llm_engine import LLMEngine
from pii_vault import PIIVault

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


def infer_act_filter(query: str) -> Optional[str]:
    """Infer likely legal source from query text for retrieval precision."""
    normalized = query.lower()

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


def _extract_requested_references(query: str) -> list[str]:
    """Extract all requested section/article references from query text."""
    references: list[str] = []

    section_refs = re.findall(
        r"\b(?:section|sec\.?|u/s)\s*([0-9]+[a-z]?)\b",
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
        values = re.findall(r"\b([0-9]+[a-z]?)\b", block, flags=re.IGNORECASE)
        references.extend([str(item).upper() for item in values])

    article_refs = re.findall(
        r"\b(?:article|art\.?)\s*([0-9]+[a-z]?)\b",
        query,
        flags=re.IGNORECASE,
    )
    references.extend([str(item).upper() for item in article_refs])

    shorthand_refs = re.findall(
        r"\b(?:bns|bnss|bsa|ipc|crpc)\s*[-/]?\s*([0-9]+[a-z]?)\b",
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

    haystack = _normalize_text_token(
        f"{citation.title} {citation.source} {citation.doc_id}"
    )

    for act_name in effective_filters:
        for alias in _act_aliases(str(act_name)):
            if alias and alias in haystack:
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
        session_id = request.session_id or f"session_{uuid4().hex}"
        citations: list[Citation] = []
        overall_confidence: Optional[float] = None

        if is_legal_query(masked_message):
            try:
                chroma_manager = get_chroma_manager()
                _bm25_refresh_counter += 1
                if _bm25_refresh_counter % 5 == 0:
                    chroma_manager.refresh_bm25_from_oracle(filter_status="active", filter_act=None)

                act_filters = infer_act_filters(masked_message)

                retrieved_context: list[str] = []
                raw_citations: list[dict[str, Any]] = []
                seen_context: set[str] = set()
                seen_citations: set[tuple[str, str]] = set()

                for act_filter in act_filters:
                    retrieval = chroma_manager.retrieve_context_with_metadata(
                        query_string=masked_message,
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

                retrieved_context = [item.snippet for item in citations if item.snippet]

                if citations:
                    overall_confidence = round(
                        sum(item.confidence for item in citations) / len(citations),
                        2,
                    )

                if not retrieved_context:
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
                    ai_response_masked = llm_engine.generate_legal_response(
                        masked_query=masked_message,
                        retrieved_context_list=retrieved_context,
                    )
            except Exception as exc:
                logger.exception("Legal retrieval failed: %s", exc)
                ai_response_masked = (
                    "I couldn't access the legal source index right now, so I can't provide a "
                    "citation-grounded legal answer at the moment. Please try again shortly."
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