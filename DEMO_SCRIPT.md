# Vidhoor Legal Copilot — Demo Video Script

**Target length:** 8–10 minutes
**Tone:** Confident, founder-style pitch-demo. Mix of talking-head + product screen recordings + architecture/diagram overlays.
**Files to reference on screen:** `README.md`, `architecture diagram.png`, `backend/main.py`, `backend/agentic_rag.py`, `backend/llm_engine.py`, `backend/pii_vault.py`, `frontend/src/lib/evidenceCrypto.ts`, Oracle schema in `backend/database.py`.

---

## a. Problem Statement  (~1 min)

**On screen:** Slow zoom over an FIR / a stack of legal bare acts. Title card: "Legal help in India is slow, costly, and opaque."

**Narration:**
- "In India, a citizen facing a legal issue — a theft, a bad FIR, a bail question — has three bad options: pay a lawyer for every small question, trust unverified advice from the internet, or simply give up."
- "The laws are also changing. The old IPC/CrPC/Evidence Act are now BNS, BNSS, BSA — and almost no one has the updated bare acts at their fingertips."
- "On top of that, when people do share documents with an AI, they leak their Aadhaar, phone number, and name with zero protection."
- "We built **Vidhoor** to fix all three at once: trustworthy Indian legal answers, grounded in real law, with privacy built in from day one."

**Screen cue:** Cut to product logo + one-liner: "Vidhoor — your Indian legal copilot. Grounded. Private."

---

## b. Proposed Solution  (~1 min)

**On screen:** Product landing / chat UI. Show a sample query and answer.

**Narration:**
- "Vidhoor is an AI legal copilot for India. You ask a question in plain language — English or Hinglish — and it answers strictly from authoritative legal sources: the BNS, BNSS, BSA, Constitution, IT Act, and Indian case law."
- "Three pillars:
  1. **Grounded answers** — every response is retrieved from a curated legal knowledge base and shows its citations.
  2. **Privacy by design** — PII is masked before it ever leaves the browser, and uploaded evidence is encrypted with AES.
  3. **End-to-end workflow** — not just chat: OCR on documents/FIRs, draft generation (bail, notice, complaint), and history you can revisit."
- "Think of it as a paralegal that never sleeps, cites its sources, and never sees your identity."

**Screen cue:** Quick montage — chat answer with citations → OCR document → generated draft.

---

## c. Tech Stack — RAG, Architecture, LLM  (~2.5 min) [CORE SECTION]

### Architecture (show `architecture diagram.png`, then live `main.py`)

**Narration:**
- "Here's the architecture. The front end is **React + TypeScript + Vite + Tailwind**, talking to a **FastAPI** backend over CORS."
- "The brain is a **two-stage Agentic RAG pipeline** — let me show the code. (`backend/agentic_rag.py`)"

**Show `agentic_rag.py` RouterDecision / JudgeDecision:**
- "Step 1 — a fast **Router LLM** classifies the query: which Act applies (BNS/BNSS/BSA/Constitution/IT Act/Case Law), and what expansions to try. (`AgenticRagConfig`: max 3 expansions, 8 citations, 12k context chars.)"
- "Step 2 — we **retrieve** from **ChromaDB** using `all-MiniLM-L6-v2` embeddings, with act filters so we don't mix up statutes."
- "Step 3 — a **Judge/Answer LLM** checks the retrieved context. If it's insufficient, it says so instead of hallucinating. That's our anti-hallucination guard."

### RAG deep-dive (show `chroma_manager` + retrieval in `main.py:_retrieve_legal_citations`)

**Narration:**
- "Retrieval isn't naive. We infer the act from the query (`infer_act_filters`), filter Chroma by that act, de-duplicate, sort by confidence, and only then build the prompt."
- "Crucially, the LLM is told: answer **only** from context, copy redaction placeholders exactly, never rename BNS to IPC, and never invent a section that isn't there. (`llm_engine.py` system prompt.)"

### LLM (show `llm_engine.py`)

**Narration:**
- "The LLM is **Groq `gpt-oss-120b`**, accessed via the `langchain-groq` ChatGroq client. We use a strict grounding prompt, multiple specialized chains — legal answer, follow-ups, title, prompt-enhance — and a model fallback list so it degrades gracefully."
- "Embeddings: **`all-MiniLM-L6-v2`** through Chroma. Persistent chunks also mirror into **Oracle** (`vidhoor_legal_chunks`) for BM25 hybrid retrieval."

### Persistence & Auth

**Narration:**
- "Chat history, drafts, feedback, and encrypted evidence persist in **Oracle Autonomous DB** (wallet-secured), with a **SQLite fallback** for local dev. Auth is **Firebase** (email/Google), with a guest mode."

**Screen cue:** Keep a static architecture callout box: `React → FastAPI → AgenticRAG(Groq) → ChromaDB + Oracle`.

---

## d. Tutorial / Live Walkthrough  (~2 min)

**On screen:** Record the actual app. Use a clean test query.

**Narration (walk while clicking):**
1. "Open the app, sign in (or use guest)."
2. "Ask: *'My bike was stolen, what should I do under BNS?'*"
   - Show the structured answer: "What you can do", "Laws supporting", "Summary table of applicable laws" with **BNS Section 303** cited.
3. "Notice the **citations** with source links — every claim is traceable."
4. "Upload a document / FIR → it runs **OCR + translation** (`services/ocr_vision.py`, Helsinki translator) and gives masked analysis + evidence storage."
5. "Open **Draft generation** → pick 'Bail Application', paste facts → it returns a formal draft and can email it to you (`services/draft_mailer.py`)."
6. "Sidebar shows **pinned sessions, drafts, and encrypted evidence** you can reopen anytime."

**Screen cue:** Highlight the "masked_entities" badge and the 🔐 "Encrypted evidence saved" toast — sets up section (e).

---

## e. PII Masking & AES  (~2 min)  [YOU WILL SHOW RESPONSE + DB SCHEMA]

### PII Masking (show `backend/pii_vault.py`)

**Narration:**
- "Privacy is non-negotiable. Before any text hits the LLM or the database, we run **PII masking** using **Microsoft Presidio** with custom Indian recognizers for **Aadhaar** and **PAN**, plus regex fallbacks."
- "We mask five entity types: `PERSON`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `IN_AADHAAR`, `IN_PAN`. Each becomes a stable token like `<PERSON_1>`, `<IN_AADHAAR_1>`. The LLM is explicitly told to copy these tokens verbatim and never reveal the original."

**SHOW — the masked response (live app or a screenshot):**
- Display a chat answer where the user's name/phone/Aadhaar appear as `<PERSON_1>`, `<PHONE_NUMBER_1>`, `<IN_AADHAAR_1>`. Point at the `masked_entities` map returned in `ChatResponse`.

### AES Encryption (show `frontend/src/lib/evidenceCrypto.ts`)

**Narration:**
- "For uploaded evidence, encryption happens **client-side, in the browser**, before upload — so our servers never see the plaintext file."
- "We use the **Web Crypto API**: **AES-GCM-256**, a fresh random **12-byte IV** per file, key generated with `crypto.getRandomValues`. The key lives only in the user's browser `localStorage`; its `key_id` is a SHA-256 fingerprint (`browser-local-…`)."
- "Only the **ciphertext** (`encrypted_payload_b64`), the `iv_b64`, `encryption_alg`, and `key_id` are sent to the backend and stored."

**SHOW — the database schema (Oracle / `database.py`):**

```sql
CREATE TABLE vidhoor_user_evidence (
    evidence_id        VARCHAR2(128) PRIMARY KEY,
    user_id            VARCHAR2(256) NOT NULL,
    file_name          VARCHAR2(512) NOT NULL,
    file_extension     VARCHAR2(32),
    encryption_alg     VARCHAR2(64),     -- e.g. AES-GCM-256
    key_id             VARCHAR2(256),    -- browser-local-<sha256 fingerprint>
    iv_b64             CLOB,             -- per-file 12-byte IV
    encrypted_payload_b64 CLOB,          -- ciphertext only
    masked_summary     CLOB,             -- anonymized OCR text
    masked_analysis    CLOB,             -- anonymized analysis
    created_at         TIMESTAMP
);
```
- "Notice: no plaintext. The DB holds only ciphertext + IV, plus **masked** summary/analysis. Even a full DB leak yields unreadable blobs."

**Screen cue:** Side-by-side: "Plaintext in browser → AES-GCM-256 → ciphertext in DB → decrypt only in browser."

---

## f. Business Impact  (~1 min)

**On screen:** Simple stat cards / bullets.

**Narration:**
- "For users: instant, affordable, 24×7 legal guidance with citations — reducing dependence on costly per-query lawyer consultations."
- "For the legal ecosystem: it scales paralegal triage, helps NGOs and legal-aid clinics serve more people, and reduces mis-information by grounding every answer in the actual bare act."
- "For us as a product: defensible differentiation — **privacy-first architecture** (PII masking + client-side AES) is a moat, not a feature. It's deployable on Oracle Cloud, integrable into legal-aid portals, and extensible to more Indian languages and statutes."
- "Monetization path: freemium chat, paid draft packs, and B2B/legal-aid licensing."

---

## g. Society Impact  (~1 min)

**On screen:** B-roll of citizens, a legal-aid clinic; closing title "Justice, accessible."

**Narration:**
- "India has over 5 crore pending cases and far too few lawyers per capita. Most people don't know their rights until it's too late."
- "Vidhoor democratizes access to legal knowledge — in plain language, in regional contexts, with the law cited — so a gig worker in a tier-2 city gets the same grounded guidance as someone in a metro."
- "And because privacy is built in, even vulnerable users can safely get help without exposing their Aadhaar or identity."
- "Our mission: **make trustworthy legal help a basic digital right, not a privilege.**"

**Close:** Logo + "Vidhoor — Grounded. Private. For every Indian." + GitHub/repo callout.

---

## Appendix — Quick reference for the presenter

- **Stack:** React+TS+Vite+Tailwind+shadcn/ui · FastAPI · ChromaDB (`all-MiniLM-L6-v2`) · Oracle Autonomous DB (SQLite fallback) · Firebase Auth · Groq `gpt-oss-120b` · Presidio + regex PII · Web Crypto AES-GCM-256.
- **RAG flow:** Router LLM → act-filtered Chroma retrieval → Judge/Answer LLM (grounding + anti-hallucination).
- **Key files:** `backend/main.py`, `backend/agentic_rag.py`, `backend/llm_engine.py`, `backend/pii_vault.py`, `backend/database.py`, `frontend/src/lib/evidenceCrypto.ts`.
- **Run locally (for live demo):** see `start.txt` — `docker compose -f docker-compose.chroma.yml up -d`, ingest, then `uvicorn main:app`, and `npm run dev` for frontend.
- **Evidence tables to show:** `vidhoor_user_evidence` (above) and `vidhoor_chat_messages` (stores `masked_entities` CLOB next to `content`).
