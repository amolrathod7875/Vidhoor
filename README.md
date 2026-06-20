# Vidhoor Legal Copilot

Vidhoor is an Indian legal copilot application with:
- a FastAPI backend for legal Q&A, retrieval, OCR/FIR analysis, drafting, and history
- a React + Vite frontend for chat, evidence workflows, and draft operations
- ChromaDB for retrieval indexing and Oracle for persistent user/session data

## Repository Structure

- `backend/` — API server, retrieval, ingestion, OCR, drafting, and integrations
- `frontend/` — web application (React + TypeScript + Tailwind + shadcn/ui)
- `start.txt` — quick local startup commands
- `.vscode/` — workspace/editor settings
- `.git/` — git metadata

## Quick Start

### 1) Backend

```bash
cd backend
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

docker compose -f docker-compose.chroma.yml up -d
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
```

### 2) Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend runs on `http://127.0.0.1:5173` by default and calls backend at `http://127.0.0.1:8001` unless `VITE_API_BASE_URL` is set.

## Environment Variables (High-Level)

### Backend core
- `CEREBRAS_API_KEY`
- `CHROMA_HOST` (default `127.0.0.1`)
- `CHROMA_PORT` (default `8000`)
- `APP_PUBLIC_BASE_URL`
- `LEGAL_SOURCE_BASE_URL`

### Oracle persistence
- `ORACLE_USER`, `ORACLE_PASSWORD`, `ORACLE_DSN`
- `ORACLE_CONFIG_DIR`, `ORACLE_WALLET_LOCATION`, `ORACLE_WALLET_PASSWORD`

### Live Indian Kanoon links
- `ENABLE_INDIAN_KANOON_LINKS` (`true`/`false`)
- `INDIAN_KANOON_MAX_LINKS` (1–5)
- `INDIAN_KANOON_TRIGGER_MODE` (`recent_only`, `case_queries`, `always_legal`)

### OCR and Draft Mail (optional)
- `OCR_SPACE_API_KEY`, `OCR_SPACE_ENDPOINT`, `OCR_SPACE_LANGUAGE`
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_USE_TLS`

## Documentation by Subproject

- Backend details: see `backend/README.md`
- Frontend details: see `frontend/README.md`

## Architecture

![Architecture Diagram](architecture%20diagram.png)

## Notes

- Live Indian Kanoon links are appended at response time and are **not** stored in ChromaDB.
- Respect source site terms/robots and rate limits when using scraping functionality.
