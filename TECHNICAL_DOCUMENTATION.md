# Vidhoor Legal Copilot — Technical Documentation

> **Status:** Draft for submission. Edit freely before finalizing.
> **Repo:** `vidhoor-legal-copilot`
> **Last reviewed:** 2026-08-22

---

## 1. Overview

Vidhoor is an Indian legal copilot that answers legal questions in plain language, grounded in
authoritative Indian legal sources (Bharatiya Nyaya Sanhita / BNS, Bharatiya Nagarik Suraksha
Sanhita / BNSS, Bharatiya Sakshya Adhiniyam / BSA, Constitution of India, Information Technology
Act 2000, and Indian case law). It combines a **grounded RAG pipeline**, **PII masking**, and
**client-side AES encryption** so that users get trustworthy, citation-backed answers without
exposing their identity or documents.

Primary capabilities:
- Legal Q&A with citations and confidence scores.
- Agentic RAG (router → retrieval → judge/answer) to reduce hallucination.
- OCR + translation of uploaded FIRs / scanned legal documents.
- AI-generated legal drafts (bail, notice, complaint) with email/DOCX/PDF export.
- Persistent, encrypted evidence storage.
- Chat history, pinned sessions, follow-ups, and shared conversation links.

---

## 2. System Architecture

```
┌─────────────────────────┐         HTTPS/CORS         ┌──────────────────────────────┐
│  React + Vite Frontend   │  ───────────────────────▶  │   FastAPI Backend (main.py)  │
│  (TS, Tailwind, shadcn)  │ ◀───────────────────────  │                              │
│  Firebase Auth (client)  │       JSON responses       │  ┌────────────────────────┐  │
└─────────────────────────┘                            │  │ AgenticRagRunner       │  │
                                                       │  │  router → judge        │  │
                                                       │  └───────────┬──────────┘  │
                                                       │              │             │
                                                       │   ┌──────────▼─────────┐   │
                                                        │   │ LLMEngine           │   │
                                                        │   │ (Groq gpt-oss)      │   │
                                                       │   └──────────┬─────────┘   │
                                                       │              │             │
                                                       │   ┌──────────▼─────────┐   │
                                                       │   │ ChromaManager       │   │
                                                       │   │ vector + BM25(Oracle)│  │
                                                       │   └──────────┬─────────┘   │
                                                       │              │             │
                                                       │   ┌──────────▼─────────┐   │
                                                       │   │ PIIVault (Presidio) │   │
                                                       │   └──────────┬─────────┘   │
                                                       │              │             │
                                                       │   ┌──────────▼─────────┐   │
                                                       │   │ Oracle Autonomous DB │  │
                                                       │   │ (SQLite fallback)     │  │
                                                       │   └──────────────────────┘  │
                                                       └──────────────────────────────┘
```

**Reference diagram:** `architecture diagram.png` (repo root).

Key flow for a chat message (`/api/chat`):
1. Frontend sends message (+ optional document context) with a Firebase bearer token.
2. `verify_token` authenticates (or allows guest).
3. `PIIVault.mask_text` masks PII before any LLM call.
4. `is_legal_query` routes to legal RAG or general response.
5. Agentic RAG: **Router LLM** picks act filters + query expansions → **ChromaManager**
   (hybrid vector + BM25) retrieves chunks → **Judge LLM** generates a grounded answer or
   returns `insufficient`.
6. Answer is unmasked, optional Indian Kanoon links appended, follow-ups generated.
7. If not temporary and authenticated, the turn is saved to Oracle (with `masked_entities`).

---

## 3. Technology Stack

| Layer | Technology |
| --- | --- |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, shadcn/ui, Firebase Auth |
| Backend | Python FastAPI, Uvicorn, Pydantic v2 |
| LLM | Groq `gpt-oss-120b` via `langchain-groq` (`ChatGroq`), OpenAI-compatible client |
| Embeddings | `all-MiniLM-L6-v2` (primary preferred `BAAI/bge-m3`) via `SentenceTransformerEmbeddingFunction` |
| Vector DB | ChromaDB (HTTP server, collection `indian_law`) |
| Hybrid retrieval | BM25 (`rank_bm25`) warmed from Oracle-persisted chunks |
| PII | Microsoft Presidio (`presidio_analyzer`) + custom regex recognizers for Aadhaar/PAN |
| Client encryption | Web Crypto API — AES-GCM-256 |
| Persistence | Oracle Autonomous DB (`oracledb`, wallet-secured); SQLite fallback for local dev |
| OCR / Translation | Vision OCR service (`services/ocr_vision.py`) + Helsinki-NLP translation (`services/translate_helsinki.py`) |
| Live case links | Indian Kanoon fetch (`services/indian_kanoon_live.py`) |
| Draft export | DOCX/PDF rendering (`services/draft_exporter.py`), SMTP mailer (`services/draft_mailer.py`) |
| Containerization | Docker Compose for Chroma (`docker-compose.chroma.yml`) |

---

## 4. Backend Components

### 4.1 LLM Engine — `backend/llm_engine.py`
- Wraps `ChatGroq` with an environment-driven API base and OpenAI-compatible env mapping.
- Multiple LangChain chains:
  - `prompt` / `chain` — strict **grounding** prompt for legal answers (answer only from context,
    copy redaction placeholders verbatim, do not rename BNS→IPC, do not invent sections).
  - `general_chain` — non-legal / fallback answers.
  - `title_chain`, `follow_up_chain`, `enhance_chain` — session titles, follow-ups, prompt enhancement.
- Model fallback list: configured model → `openai/gpt-oss-120b`.
- Post-processing: enforces `###` subheading bullets, bolds legal labels, normalizes the summary
  table to a fixed markdown grid.

### 4.2 Agentic RAG — `backend/agentic_rag.py`
Two-stage pipeline (`AgenticRagRunner`):
1. **Router** (`_router_prompt`): fast LLM returns JSON with `act_filters`
   (BNS/BNSS/BSA/Constitution/IT Act/Indian Case Law), `expansions`, `needs_case_law`,
   `needs_recent_case_law`. Config: `max_expansions=3`, `max_citations=8`,
   `max_context_chunks=12`, `max_context_chars=12000`.
2. **Judge/Answer** (`_judge_prompt`): checks retrieved context; returns `insufficient` if
   grounding is weak instead of hallucinating. Enabled via `ENABLE_AGENTIC_RAG` (default `true`).
   Falls back to direct Chroma retrieval (`main.py:_retrieve_legal_citations`) when disabled.

### 4.3 Retrieval — `backend/chroma_manager.py`
- Hybrid **vector (Chroma) + lexical (BM25)** fusion with fixed weights
  `HYBRID_VECTOR_WEIGHT = 0.5`, `HYBRID_BM25_WEIGHT = 0.5`.
- Act-filtered retrieval (`infer_act_filters`) prevents mixing statutes; section/article filters
  force dependency retrieval (e.g. BNS 64/65 pulls definition 63).
- Confidence scoring: distance→confidence, plus bonuses for reference match, court precedent
  hierarchy (`_court_precedent_weight`), and year recency (`_year_recency_weight`).
- Chunks persisted to Oracle (`vidhoor_legal_chunks`) for BM25 rebuild; refreshed every 5 chats.

### 4.4 PII Vault — `backend/pii_vault.py`
- Detects & masks: `PERSON`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `IN_AADHAAR`, `IN_PAN`.
- Uses Presidio `AnalyzerEngine` with custom `PatternRecognizer`s for Aadhaar (`\d{4} \d{4} \d{4}`)
  and PAN (`[A-Z]{5}[0-9]{4}[A-Z]`); regex fallback if Presidio unavailable.
- Produces stable placeholders `<TYPE_n>`; `unmask_text` restores them after generation.
- Heuristics (`_is_likely_person_phrase`) reduce false-positive PERSON masking.

### 4.5 OCR / FIR Analysis — `/api/fir/analyze`
- Accepts PDF/PNG/JPG/JPEG/WEBP/TIFF/BMP.
- Pipeline: OCR (`VisionOCRService`) → Helsinki translation to English → PII mask →
  summary + legal analysis (with offence-first guidance) → citation retrieval → optional
  encrypted evidence persistence.
- Garbage-script / empty-document guards return 422 with clear messages.

### 4.6 Draft Generation — `/api/drafts/*`
- Types: `bail_application`, `legal_notice`, `police_complaint`, `consumer_complaint`, `custom`.
- PII-masked facts → `generate_general_response` with a structured drafting prompt → unmask →
  save to Oracle → optional SMTP email → export as PDF/DOCX.
- Always includes `DRAFT_DISCLAIMER` (not a substitute for a licensed advocate).

### 4.7 Evidence & Encryption — `/api/evidence/*`
- Uploaded files are encrypted **client-side** (see §6). Backend stores only ciphertext
  (`encrypted_payload_b64`), `iv_b64`, `encryption_alg`, `key_id`, plus `masked_summary` /
  `masked_analysis`.

### 4.8 Authentication & Sharing
- Firebase Auth bearer token verified in `verify_token`; guest mode allowed for chat/enhance.
- Signed, HMAC-SHA256 share links (`_create_share_id` / `_decode_share_id`) for conversations,
  TTL 30 days.

---

## 5. Frontend — `frontend/`
- **Stack:** React + TypeScript + Vite + Tailwind + shadcn/ui.
- **Auth:** `src/hooks/useAuth.tsx`, `src/lib/firebaseConfig.ts` (email/Google; guest fallback).
- **Chat:** `src/pages/Index.tsx`, `src/components/ChatArea.tsx`, `src/components/ChatInput.tsx`.
- **Evidence crypto:** `src/lib/evidenceCrypto.ts` — AES-GCM-256 encrypt/decrypt in browser
  (see §6).
- **State/UI:** sidebar sessions, drafts, evidence list, toasts.

---

## 6. Privacy & Security

### 6.1 PII Masking (backend)
- Every user message and uploaded document text is masked via `PIIVault.mask_text` **before**
  it reaches the LLM or is stored.
- The LLM system prompt explicitly instructs it to copy placeholders like `<PERSON_1>` verbatim
  and never reveal the underlying value.
- Chat history stores `masked_entities` (a map of placeholder→original) so the unmasked value is
  reconstructed only in the user's own response, never persisted in plaintext.

### 6.2 AES Encryption (client-side) — `frontend/src/lib/evidenceCrypto.ts`
- Algorithm: **AES-GCM** (256-bit), via `window.crypto.subtle`.
- Key: 32 random bytes from `crypto.getRandomValues`; kept **only in browser `localStorage`**
  under `vidhoor_evidence_key_b64`. Never sent to the server.
- `key_id`: SHA-256 fingerprint of the key (first 8 bytes, hex), stored as `browser-local-<hex>`.
- IV: fresh random **12-byte** value per file (`crypto.getRandomValues`).
- Upload payload: `encryptedPayloadB64`, `ivB64`, `encryptionAlg = "AES-GCM-256"`, `keyId`.
- Decryption happens only in the browser when the owner re-opens the file.

**Net effect:** The backend and database only ever see ciphertext + IV + masked text. A full DB
leak yields unreadable blobs.

### 6.3 Database Schema (Oracle — `backend/database.py`)

```sql
-- Chat sessions
CREATE TABLE vidhoor_chat_sessions (
    session_id  VARCHAR2(128) PRIMARY KEY,
    user_id     VARCHAR2(256) NOT NULL,
    title       VARCHAR2(512),
    pinned      NUMBER(1) DEFAULT 0 NOT NULL,
    created_at  TIMESTAMP DEFAULT SYSTIMESTAMP,
    updated_at  TIMESTAMP DEFAULT SYSTIMESTAMP
);

-- Chat messages (content stored; masked_entities kept alongside)
CREATE TABLE vidhoor_chat_messages (
    message_id        NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id        VARCHAR2(128) NOT NULL,
    user_id           VARCHAR2(256) NOT NULL,
    role              VARCHAR2(32) NOT NULL,
    content           CLOB NOT NULL,
    masked_entities   CLOB,
    citations_json    CLOB,
    follow_ups_json   CLOB,
    overall_confidence NUMBER,
    created_at        TIMESTAMP DEFAULT SYSTIMESTAMP,
    CONSTRAINT fk_vidhoor_chat_session
        FOREIGN KEY (session_id) REFERENCES vidhoor_chat_sessions(session_id)
);

-- Encrypted evidence (NO plaintext; ciphertext + IV + masked text only)
CREATE TABLE vidhoor_user_evidence (
    evidence_id         VARCHAR2(128) PRIMARY KEY,
    user_id             VARCHAR2(256) NOT NULL,
    session_id          VARCHAR2(128),
    file_name           VARCHAR2(512) NOT NULL,
    file_extension      VARCHAR2(32),
    encryption_alg      VARCHAR2(64),      -- e.g. AES-GCM-256
    key_id              VARCHAR2(256),     -- browser-local-<sha256 fingerprint>
    iv_b64              CLOB,               -- per-file 12-byte IV
    encrypted_payload_b64 CLOB,            -- ciphertext only
    masked_summary      CLOB,              -- anonymized OCR summary
    masked_analysis     CLOB,              -- anonymized analysis
    created_at          TIMESTAMP DEFAULT SYSTIMESTAMP,
    updated_at          TIMESTAMP DEFAULT SYSTIMESTAMP
);

-- Generated drafts
CREATE TABLE vidhoor_user_drafts (
    draft_id         VARCHAR2(128) PRIMARY KEY,
    user_id          VARCHAR2(256) NOT NULL,
    email_id         VARCHAR2(320),
    session_id       VARCHAR2(128),
    application_type VARCHAR2(64) NOT NULL,
    title            VARCHAR2(512),
    draft_content    CLOB NOT NULL,
    draft_meta_json  CLOB,
    delivery_status  VARCHAR2(64) DEFAULT 'generated' NOT NULL,
    last_delivery_error VARCHAR2(1000),
    emailed_at       TIMESTAMP,
    created_at       TIMESTAMP DEFAULT SYSTIMESTAMP,
    updated_at       TIMESTAMP DEFAULT SYSTIMESTAMP
);

-- Feedback
CREATE TABLE vidhoor_user_feedback (
    feedback_id  VARCHAR2(128) PRIMARY KEY,
    user_id      VARCHAR2(256),
    user_email   VARCHAR2(320),
    message      CLOB NOT NULL,
    allow_follow_up NUMBER(1) DEFAULT 0 NOT NULL,
    page_url     VARCHAR2(1000),
    user_agent   VARCHAR2(2000),
    app_version  VARCHAR2(64),
    context      VARCHAR2(256),
    status       VARCHAR2(32) DEFAULT 'new' NOT NULL,
    created_at   TIMESTAMP DEFAULT SYSTIMESTAMP,
    updated_at   TIMESTAMP DEFAULT SYSTIMESTAMP
);

-- Persistent legal chunks for BM25 hybrid retrieval
CREATE TABLE vidhoor_legal_chunks (
    chunk_id     VARCHAR2(128) PRIMARY KEY,
    chunk_text   CLOB NOT NULL,
    status       VARCHAR2(64),
    act          VARCHAR2(256),
    source       VARCHAR2(512),
    section_ref  VARCHAR2(64),
    metadata_json CLOB,
    created_at   TIMESTAMP DEFAULT SYSTIMESTAMP,
    updated_at   TIMESTAMP DEFAULT SYSTIMESTAMP
);
```

> Local/dev fallback: identical schema in SQLite (`backend/sqlite_chat_repo.py`).

---

## 7. API Endpoints (summary)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Health check (+ Chroma status) |
| POST | `/api/chat` | Legal/general Q&A with citations, confidence, follow-ups |
| POST | `/api/prompt/enhance` | Rewrite raw prompt into optimized legal query (PII-masked) |
| POST | `/api/fir/analyze` | OCR + translate + summarize + legal analysis of uploaded doc |
| POST | `/api/drafts/generate` | Generate legal draft (bail/notice/complaint/...) |
| POST | `/api/drafts/{id}/email` | Email a saved draft |
| GET | `/api/drafts` | List user drafts |
| GET | `/api/drafts/{id}` | Get a draft |
| PATCH | `/api/drafts/{id}` | Update draft title/content |
| GET | `/api/drafts/{id}/export` | Export draft as PDF/DOCX |
| GET | `/api/evidence` | List encrypted evidence |
| GET | `/api/evidence/{id}` | Get encrypted evidence payload (owner only) |
| POST | `/api/feedback` | Submit feedback |
| GET/POST | `/api/sessions/*`, `/api/share/*` | Session management + signed share links |

---

## 8. Setup & Deployment

See `README.md` and `start.txt`. Essentials:

**Backend**
```bash
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1         # Windows PowerShell
pip install -r requirements.txt
docker compose -f docker-compose.chroma.yml up -d
python ingest_legal_resources.py --input-dir data --resource-category auto --status active --ocr-fallback --chunk-size 700
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

**Frontend**
```bash
cd frontend
npm install
npm run dev
```

**Key environment variables**
- `GROQ_API_KEY` (LLM)
- `CHROMA_HOST`, `CHROMA_PORT` (vector store)
- `ORACLE_USER/PASSWORD/DSN` + wallet (`ORACLE_CONFIG_DIR`, `ORACLE_WALLET_LOCATION`,
  `ORACLE_WALLET_PASSWORD`) for persistence; omit for SQLite fallback
- `ENABLE_AGENTIC_RAG`, `ENABLE_INDIAN_KANOON_LINKS`, `INDIAN_KANOON_TRIGGER_MODE`,
  `INDIAN_KANOON_MAX_LINKS`
- `OCR_SPACE_API_KEY`, `SMTP_*` (optional OCR/mail)
- `FIREBASE_SERVICE_ACCOUNT_PATH` (auth; guest mode if absent)

---

## 9. Evaluation & Quality

- `backend/tests/run_eval.py` scores answers against the running API (`--base-url`,
  `--timeout`).
- Unit tests: `pytest` (agentic-RAG parsing, prompt-enhance, OCR/FIR PII + AES storage).
- Anti-hallucination controls: grounded system prompt, judge `insufficient` path, section
  cross-verification (e.g. BNS 64/65 vs 63), strict citation matching.

---

## 10. Limitations & Future Work (suggested for submission)

- Embedding dimension must match between ingestion and query (health check warns on mismatch).
- Presidio requires model download; regex fallback covers core Indian identifiers if unavailable.
- Evidence key lives in browser `localStorage` — clearing it makes encrypted evidence
  unrecoverable (consider optional key backup/escrow).
- Extend ingestion to more regional-language bare acts and live case-law refresh.
- Add role-based access for legal-aid org deployments.
