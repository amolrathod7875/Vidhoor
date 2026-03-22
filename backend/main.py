from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Optional
import logging
import os
import re
from uuid import uuid4
import uvicorn

from chroma_manager import ChromaManager
from database import OracleChatHistoryRepository
from llm_engine import LLMEngine
from pii_vault import PIIVault

logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Vidhoor Legal Copilot API")

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

class ChatResponse(BaseModel):
    response: str
    session_id: str
    masked_entities: dict # We will send back the PII map just in case the frontend needs it


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
        _llm_engine = LLMEngine(model="llama3.1-70b")
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
    if "bns" in normalized:
        return "Bharatiya Nyaya Sanhita"
    if "bnss" in normalized:
        return "Bharatiya Nagarik Suraksha Sanhita"
    if "bsa" in normalized:
        return "Bharatiya Sakshya Adhiniyam"

    return None


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
        pii_vault = get_pii_vault()
        llm_engine = get_llm_engine()
        masked_message, pii_map = pii_vault.mask_text(request.message)
        session_id = request.session_id or f"session_{uuid4().hex}"

        if is_legal_query(masked_message):
            chroma_manager = get_chroma_manager()
            act_filter = infer_act_filter(masked_message)
            retrieved_context = chroma_manager.retrieve_context(
                query_string=masked_message,
                filter_status="active",
                filter_act=act_filter,
            )

            ai_response_masked = llm_engine.generate_legal_response(
                masked_query=masked_message,
                retrieved_context_list=retrieved_context,
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
            masked_entities=pii_map
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