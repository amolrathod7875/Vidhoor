# Vidhoor Legal Copilot - Backend Technology FAQ

This document provides detailed answers to technical questions about the backend, tech stack, and technologies used in Vidhoor Legal Copilot.

## 1. What is the primary web framework used in the Vidhoor Legal Copilot backend, and what version is specified in requirements.txt?

**Answer:** The primary web framework is FastAPI, version 0.109.2 as specified in `backend/requirements.txt`.

## 2. How does the application handle Cross-Origin Resource Sharing (CORS) to enable communication with the frontend?

**Answer:** The application configures CORS middleware in `backend/main.py` (lines 33-46) to allow specific origins including localhost ports 3000, 5173, and 8080, with credentials enabled and all methods/headers allowed.

## 3. Which LLM provider and model is primarily used for generating legal responses, and how is fallback implemented?

**Answer:** The system uses Cerebras LLM with the primary model "llama3.1-8b". Fallback implementation is in `backend/llm_engine.py` where `_build_model_candidates` creates a list including "llama3.1-8b" and "gpt-oss-120b", and the `_switch_model` method tries alternatives if the primary fails.

## 4. What vector database is used for storing and retrieving legal embeddings, and how is it accessed?

**Answer:** ChromaDB is used as the vector database. It's accessed via HTTP client in `backend/chroma_manager.py` (lines 286-292) connecting to a Chroma server at configurable host/port (default localhost:8000).

## 5. How does the system implement hybrid retrieval combining vector search with BM25 ranking?

**Answer:** Hybrid retrieval combines vector search (ChromaDB) with BM25 ranking from Oracle persistence. Weights are defined as HYBRID_VECTOR_WEIGHT = 0.5 and HYBRID_BM25_WEIGHT = 0.5 in `backend/chroma_manager.py` (lines 38-39). The BM25 index is warmed from Oracle chunks and used alongside vector search.

## 6. What Oracle database features are utilized for persisting chat history and evidence?

**Answer:** The system uses Oracle Autonomous Database with:
- Tables for chat sessions (`vidhoor_chat_sessions`) and messages (`vidhoor_chat_messages`)
- Tables for evidence storage (`vidhoor_user_evidence`)
- CLOB fields for storing large text content
- MERGE statements for upsert operations
- Connection pooling with Oracle Wallet support
- Schema initialization with backward compatibility

## 7. How are PDF documents processed for text extraction in the OCR pipeline?

**Answer:** PDF processing uses PyPDF (specified in requirements.txt) and is handled by `VisionOCRService` in `backend/services/ocr_vision.py`. The service extracts text from PDF pages and processes them through OCR and translation pipelines.

## 8. What PII (Personally Identifiable Information) protection mechanisms are implemented in the system?

**Answer:** PII protection is implemented via:
- `PIIVault` class in `backend/pii_vault.py` using Microsoft Presidio
- Entity detection and anonymization before LLM processing
- Masking of sensitive information in chat messages and evidence
- Separate storage of PII mappings for potential unmasking by frontend

## 9. How does the application handle multilingual support, particularly for translating non-English legal documents?

**Answer:** Multilingual support uses:
- `langdetect` for language detection
- Helsinki NLP translation model via `services/translate_helsinki.py`
- Translation of OCR-extracted text to English for processing
- Language detection in OCR results (`OCRPageResult.detected_language`)

## 10. What embedding models are used for converting legal text to vectors, and what fallback strategy exists?

**Answer:** Primary embedding model is BAAI/bge-m3 with fallback to all-MiniLM-L6-v2. The fallback strategy is implemented in `backend/chroma_manager.py` `_build_embedding_function` method (lines 312-338) which tries the primary model first, then falls back on failure.

## 11. How are legal citations structured and validated in the system?

**Answer:** Legal citations are structured using the `Citation` Pydantic model (lines 60-69 in `backend/main.py`) with fields for doc_id, title, source, source_url, section, page, snippet, confidence, and last_updated. Validation occurs through reference matching functions like `_references_match` and `_reference_match_for_fir`.

## 12. What security measures are in place for handling encrypted evidence files?

**Answer:** Evidence security includes:
- Encryption using AES-GCM (via `evidenceCrypto.ts` frontend, but backend handles storage)
- Storage of encrypted payloads, IVs, and metadata in Oracle DB
- Key management through key_id references
- Separation of encrypted content from masked summaries/analyses
- Access control via user_id and session_id foreign keys

## 13. How does the system determine if a user query is related to legal matters?

**Answer:** Legal query detection uses:
- Keyword matching against `LEGAL_QUERY_KEYWORDS` set (lines 149-177 in `backend/main.py`)
- Regex pattern matching for article/section references (line 365-366)
- Implemented in `is_legal_query()` function (lines 355-368)

## 14. What is the purpose of the BM25Okapi implementation and how is it integrated with ChromaDB?

**Answer:** BM25Okapi provides lexical ranking to complement semantic vector search. Integration occurs through:
- Persistence of chunks to Oracle via `OracleChunkRepository`
- In-memory BM25 index built from Oracle chunks (`refresh_bm25_from_oracle`)
- Hybrid scoring combining vector distance and BM25 scores in `retrieve_context`

## 15. How are legal acts (like BNS, BNSS, BSA) identified and filtered during retrieval?

**Answer:** Legal act identification uses:
- `infer_act_filter()` and `infer_act_filters()` functions (lines 297-352)
- Pattern matching for act names and abbreviations (BNS, BNSS, BSA)
- Special handling for bail-related queries to default to BNSS
- Filtering in `_source_matches_act_filter()` to prevent cross-contamination

## 16. What role does the PII Vault play in the evidence handling workflow?

**Answer:** The PII Vault:
- Detects and masks PII in evidence before storage
- Maintains mapping between original and masked values
- Provides unmasking capabilities for authorized frontend display
- Is invoked during OCR processing and evidence saving workflows

## 17. How does the system manage session persistence and chat history in Oracle Database?

**Answer:** Session management uses:
- `vidhoor_chat_sessions` table for session metadata
- `vidhoor_chat_messages` table for message history with CLOB content
- Oracle MERGE operations for session creation/update
- Timestamps for tracking creation and updates
- Foreign key constraints maintaining session-message relationships
- Pinned session feature for user preferences

## 18. What is the function of the `infer_act_filter` and `infer_act_filters` functions?

**Answer:** These functions analyze user queries to determine relevant legal acts for retrieval filtering:
- `infer_act_filter()` returns single most likely act
- `infer_act_filters()` returns list of possible acts
- Both use keyword and pattern matching to identify BNS, BNSS, BSA, Constitution
- Special handling for bail queries to prioritize BNSS

## 19. How does the system handle OCR processing for legal documents, including language detection?

**Answer:** OCR processing flow:
1. PDF/image input processed by `VisionOCRService`
2. Text extraction per page with language detection (`langdetect`)
3. Non-English text translated to English via Helsinki model
4. Masking of PII in extracted text
5. Storage of both original and translated/masked results
6. Language tracking in `OCRPageResult.detected_language`

## 20. What is the purpose of the `offence_guidance_rules` and how are they applied?

**Answer:** Offence guidance rules provide practical legal guidance when strict citation grounding isn't possible:
- Defined in `OFFENCE_GUIDANCE_RULES` (lines 189-210)
- Contain patterns, applicable acts, section hints, and guidance bullet points
- Applied via `_detect_offence_rule()` and `_build_offence_guidance_markdown()`
- Used in OCR analysis when citation confidence is low

## 21. How are legal chunks ingested into the system, and what metadata is associated with them?

**Answer:** Ingestion process:
1. Legal texts chunked via `ingest_legal_resources.py` or `ingest_constitution.py`
2. Metadata includes: source, act, section/article, status
3. Stored in ChromaDB with vector embeddings
4. Persisted to Oracle for BM25 recovery via `_persist_chunks_for_bm25`
5. Chunk IDs generated deterministically from content hash

## 22. What is the role of the `OracleChunkRepository` in the Chroma manager?

**Answer:** The `OracleChunkRepository`:
- Persists legal chunks to Oracle Database for BM25 recovery
- Provides `load_chunks()` and `upsert_chunks()` methods
- Initializes schema for chunk storage table
- Enables rebuilding BM25 index from persistent storage
- Acts as backup when ChromaDB needs reconstruction

## 23. How does the system ensure that LLM responses are strictly grounded in retrieved legal context?

**Answer:** Response grounding is ensured by:
- Strict system prompt in `LLMEngine.prompt` (lines 78-116)
- Instruction to ONLY answer using provided legal context
- Requirement to say context is insufficient if answer not present
- Prohibition against hallucination or inferring beyond exact matches
- Structured response format with specific sections and bullet points
- Post-processing to enforce bullet points under subheadings

## 24. What environment variables are required for configuring the Oracle Autonomous Database connection?

**Answer:** Required Oracle environment variables:
- `ORACLE_USER` - database username
- `ORACLE_PASSWORD` - database password
- `ORACLE_DSN` - database connection string
- Optional: `ORACLE_CONFIG_DIR`, `ORACLE_WALLET_LOCATION`, `ORACLE_WALLET_PASSWORD`
- Used in `OracleChatHistoryRepository.__init__()` (lines 22-36)

## 25. How does the system handle model switching and fallback when the primary Cerebras model is unavailable?

**Answer:** Model fallback mechanism:
- `_build_model_candidates()` creates ordered list: [primary_model, "llama3.1-8b", "gpt-oss-120b"]
- `_switch_model()` attempts to initialize alternative models
- Generation methods (`generate_legal_response`, etc.) iterate through candidates
- On model_not_found errors, logs warning and tries next candidate
- Falls back to seed title generation if all models fail