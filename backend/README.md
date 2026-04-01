# Backend - Vidhoor Legal Copilot

FastAPI backend for legal chat, retrieval, drafting, OCR/FIR analysis, evidence handling, and chat history persistence.

## Tech Stack

- FastAPI + Uvicorn
- ChromaDB (vector + hybrid retrieval helpers)
- Oracle DB (`oracledb`) for sessions/messages/evidence/drafts
- Cerebras LLM via `langchain-cerebras`
- Presidio for PII masking
- BeautifulSoup + requests for live Indian Kanoon link extraction

## Folder Structure

- `main.py` — API entrypoint and core request orchestration
- `chroma_manager.py` — retrieval/indexing and metadata-aware citation flow
- `database.py` — Oracle repositories + schema initialization
- `llm_engine.py` — legal/general generation pipelines and prompting
- `pii_vault.py` — masking/unmasking sensitive user data
- `ingest_legal_resources.py` — ingestion utility for statutes/cases from `data/`
- `ingest_constitution.py` — constitution-focused ingestion helper
- `docker-compose.chroma.yml` — local Chroma service
- `requirements.txt` — Python dependencies
- `services/`
  - `indian_kanoon_live.py` — live case-link extraction + relevance scoring
  - `ocr_vision.py` — OCR workflow utilities
  - `translate_helsinki.py` — translation pipeline helpers
  - `draft_exporter.py` — draft export helpers (PDF/DOCX)
  - `draft_mailer.py` — SMTP delivery helpers
- `data/` — legal source files used for ingestion
  - `Case/` — case-law source documents
  - statute PDFs (BNS, BNSS, BSA, Constitution, etc.)
- `chroma/` — local Chroma persistence volume
- `wallet/` — Oracle wallet/config files
- `__pycache__/` — Python bytecode cache

## Setup

```bash
cd backend
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run Services

### Start ChromaDB

```bash
docker compose -f docker-compose.chroma.yml up -d
```

### (Optional) Ingest legal resources

```bash
python ingest_legal_resources.py --input-dir data --resource-category auto --status active --ocr-fallback
```

### Run API

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

## Key API Endpoints

- `GET /` — health check
- `POST /api/chat` — primary chat endpoint
- `POST /api/drafts/generate` — generate legal draft
- `POST /api/drafts/{draft_id}/email` — email a draft
- `GET /api/drafts` — list drafts
- `GET /api/drafts/{draft_id}` — get draft by id
- `GET /api/drafts/{draft_id}/export` — export PDF/DOCX
- `POST /api/fir/analyze` — OCR + legal analysis flow
- `GET /api/evidence` — list evidence
- `GET /api/evidence/{evidence_id}` — fetch encrypted evidence payload
- `GET /api/history/sessions` — list chat sessions
- `GET /api/history/sessions/{session_id}` — list session messages
- `DELETE /api/history/sessions/{session_id}` — delete session

## Required Environment Variables

### Core

- `CEREBRAS_API_KEY`
- `CHROMA_HOST` (default `127.0.0.1`)
- `CHROMA_PORT` (default `8000`)

### Oracle

- `ORACLE_USER`, `ORACLE_PASSWORD`, `ORACLE_DSN`
- `ORACLE_CONFIG_DIR`, `ORACLE_WALLET_LOCATION`, `ORACLE_WALLET_PASSWORD`

## Optional Environment Variables

### Source links + app URL

- `APP_PUBLIC_BASE_URL`
- `LEGAL_SOURCE_BASE_URL`

### CORS

- `CORS_ALLOW_ORIGINS` (comma-separated origins, optional)
- `CORS_ALLOW_ORIGIN_REGEX` (optional, default allows `https://*.vercel.app`)

### Live Indian Kanoon links

- `ENABLE_INDIAN_KANOON_LINKS` (default `true`)
- `INDIAN_KANOON_MAX_LINKS` (default `3`, recommended <= `5`)
- `INDIAN_KANOON_TRIGGER_MODE`
  - `recent_only`: only recent/current/year-specific case requests
  - `case_queries`: any legal case/judgment-style request (default)
  - `always_legal`: all legal queries

### OCR

- `OCR_SPACE_API_KEY`
- `OCR_SPACE_ENDPOINT`, `OCR_SPACE_LANGUAGE`, `OCR_SPACE_ENGINE`
- `OCR_SPACE_TIMEOUT_SECONDS`, `OCR_SPACE_MAX_RETRIES`, `OCR_SPACE_RETRY_DELAY_SECONDS`
- `HELSINKI_DEVANAGARI_FALLBACK`

### Draft email

- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`, `SMTP_USE_TLS`

## Development Notes

- Responses are citation-grounded where available; legal prompts are strict about context use.
- Indian Kanoon results are fetched live and appended as links; they are not ingested into Chroma.
- Keep `wallet/` and secret files out of source control where possible.

## Backend Deployment (Oracle Cloud + Docker)

Backend-only deployment assets are available under `deploy/`:

- `deploy/build_and_push_ocir.sh` — build image and push to OCIR
- `deploy/deploy_backend_oci.sh` — pull image and start stack on OCI host
- `deploy/docker-compose.oci.yml` — backend + chroma + nginx runtime stack
- `deploy/nginx.conf` — reverse proxy config
- `deploy/DEPLOYMENT.md` — step-by-step runbook

Quick path:

```bash
cd backend
# 1) push image to OCIR
bash deploy/build_and_push_ocir.sh

# 2) on OCI host, after configuring BACKEND_IMAGE and .env
bash deploy/deploy_backend_oci.sh
```
