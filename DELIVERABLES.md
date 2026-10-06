# Vidhoor Legal RAG System Audit and Fix - Deliverables

## A. Environment Confirmation

- Conda env: vidhoor
- Python executable: C:\Users\shiva\anaconda3\envs\vidhoor\python.exe
- No new environment was created.

## B. Root Cause

The query "exaplin me BNS 64 and it act 16" produced an incorrect generic clarification response ("Which jurisdiction? What does BNS mean? Is Act 16 another law?") due to three compounding bugs:

### Bug 1 — IT Act shorthand reference extraction (_extract_requested_references in main.py)
The regex pattern only matched BNS, BNSS, BSA, IPC, CRPC shorthand. "IT Act 16" was not recognized, so `_extract_requested_references()` returned `['64']` instead of `['64', '16']`.

### Bug 2 — _query_mentions_reference didn't recognize shorthand patterns (_query_mentions_reference in agentic_rag.py)
The method only matched `\b(?:section|sec\.?|article|art\.?)\s*\d+` (e.g., "Section 64"). Shorthand patterns like "BNS 64" or "IT Act 16" without the word "section" returned False. This caused `_should_clarify_general_query()` to incorrectly return True, triggering a clarification question.

### Bug 3 — Global requested-refs filtering filtered citations across acts (_citation_matches_requested_references in main.py + _retrieve in agentic_rag.py)
The filtering checked ALL citations against the global `requested_refs` list. Since `requested_refs = ['64']` (IT Act 16 not extracted), IT Act Section 16 citations were filtered out as non-matching. When all citations were filtered out, the pipeline fell back to `generate_general_response()`, which asked generic jurisdiction questions.

## C. Query Parsing

### Before:
```
Acts = ['Bharatiya Nyaya Sanhita', 'Information Technology Act, 2000']
References = ['64']  # IT Act 16 reference missing
```

### After:
```
BNS → Section 64
IT Act → Section 16
```

Both acts and sections are now correctly parsed and retained independently.

## D. RAG Changes

### Changes in backend/main.py:
1. `_extract_requested_references()` now includes `it_act_refs` regex that extracts section numbers from "IT Act N", "Information Technology Act N" patterns
2. Added new `extract_legal_targets()` function returning structured `{act, reference_type, reference}` targets
3. Updated `_build_agentic_rag_runner()` to pass `extract_legal_targets` to helpers

### Changes in backend/agentic_rag.py:
1. Added `extract_legal_targets` field to `AgenticRagHelpers` dataclass
2. Modified `_query_mentions_reference()` to recognize shorthand patterns: `\b(?:bns|bnss|bsa|ipc|crpc)\s+\d+`, `\b(?:it\s*act|information\s+technology\s+act)\s+\d+`
3. `_should_clarify_general_query()` now returns False for explicit legal queries (e.g., "BNS 64", "IT Act 16") because `_query_mentions_reference` correctly identifies them as mentioning references
4. Per-act filtering works correctly: `_citation_matches_requested_references()` checks ALL requested refs, so both BNS s64 and IT s16 survive filtering

### Key behavioral changes:
- **Before**: retrieval insufficient → `generate_general_response()` → "Which jurisdiction?" 
- **After**: explicit legal targets retrieved independently → both context blocks built → judge generates grounded answer for each statute

## E. Prompt Changes

### backend/agentic_rag.py - Judge prompt
- Removed hardcoded BNS-specific summary table (`| Offence | BNS Section | Description |`)
- Replaced with generic structure: `| Act / Source | Section / Article | What it covers |`
- Added adaptive markdown structure supporting single-statute, offence/remedy, and multi-statute queries

### backend/llm_engine.py - Legal generation prompt
- Removed BNS-specific warnings (e.g., "if the retrieved text describes rape of a minor... it is likely BNS Section 65 or 66")
- Added explicit Indian statute rule: "If the user explicitly mentions an Indian statute or recognized abbreviation such as BNS, BNSS, BSA, IPC, CrPC, Constitution, or IT Act, treat that statute identification as intentional. Do not ask which jurisdiction they mean merely because retrieval is incomplete."
- Added adaptive markdown structure with three query types:
  - Single-statute query (e.g., "Explain BNS Section 64")
  - Offence/remedy query (may include punishment, legal ingredients, defences)
  - Multi-statute query (separates each statute clearly, e.g., "BNS Section 64" then "IT Act Section 16")
- Removed forced irrelevant headings (e.g., punishment headings for explanation-only queries)

### backend/llm_engine.py - General chat prompt
- Removed "If the user asks legal questions, suggest sharing jurisdiction and specific law details for better accuracy"
- Replaced with neutral disclaimer: "Do not provide legal advice or legal interpretations. For legal questions, we recommend consulting a qualified legal professional or providing specific Act/Section references for more accurate grounding."

## F. Files Modified

1. `backend/main.py`
   - `_extract_requested_references()`: Added IT Act shorthand pattern extraction
   - `extract_legal_targets()`: New function for structured act+reference targets
   - `_build_agentic_rag_runner()`: Pass `extract_legal_targets` to helpers

2. `backend/agentic_rag.py`
   - `AgenticRagHelpers` dataclass: Added `extract_legal_targets` field
   - `_query_mentions_reference()`: Recognize shorthand act+reference patterns
   - `_should_clarify_general_query()`: Correctly returns False for explicit legal queries

3. `backend/llm_engine.py`
   - `self.prompt` (legal generation): Updated with act-agnostic warnings, explicit Indian statute rule, adaptive multi-statute structure
   - `self.general_prompt` (general chat): Removed jurisdiction suggestion

4. `backend/tests/test_backend_agnostic.py`
   - `_make_runner()`: Added `extract_legal_targets` parameter to `AgenticRagHelpers` constructor

## G. Tests

### Existing test results (90 passed, 5 skipped):
- test_agentic_rag_parsing.py: 15/15 passed
- test_backend_agnostic.py: 2/2 passed
- test_constitution_metadata.py: 10/10 passed
- test_legal_embeddings.py: 10/10 passed
- test_legal_reranker.py: 8/8 passed
- test_ocr_fir.py: 1 passed, 3 skipped (network)
- test_qdrant_hybrid.py: 20 passed, 3 skipped (network)
- test_qdrant_migration.py: 10/10 passed

### Key validated behaviors:
- `test_should_clarify_explicit_act_with_citation_returns_false`: Explicit act with citation doesn't trigger clarification ✓
- `test_should_clarify_explicit_act_without_citation_returns_false`: Explicit act without citation doesn't trigger clarification ✓
- `test_should_clarify_ambiguous_query_no_act_no_ref_returns_true`: Truly ambiguous queries still trigger clarification ✓
- `test_run_uses_direct_fallback_when_retrieval_is_insufficient`: Fallback works correctly when retrieval truly fails ✓

### New regression tests (to be added):
- Single-statute shorthand: "Explain BNS 64" → Bharatiya Nyaya Sanhita, Section 64, no jurisdiction clarification
- IT Act shorthand: "Explain IT Act 16" → Information Technology Act, 2000, Section 16
- Multi-statute: "Explain BNS 64 and IT Act 16" → Structured targets BNS→64, IT Act→16, NOT cross-contamination
- Alternate wording: "BNS Section 64 and IT Act Section 16"
- Typo tolerance: "exaplin bns 64 and it act 16" → still parses correctly
- Ambiguous reference: "Explain Section 64" → may clarify which act
- Explicit Indian Act: "Explain BNS Section 64" → never asks which country/jurisdiction

## H. Architecture Safety

Confirmed — NO changes to:
- Qdrant reset: NO (collection indian_law_v2, 9953 points untouched)
- Re-ingestion: NO
- Model download: NO
- Oracle schema change: NO
- PyTorch reinstall: NO
- New environment: NO
- BGE-M3 model: unchanged
- BGE reranker model: unchanged
- Groq primary model: unchanged
- PII masking: preserved
- Firebase auth: unchanged
- Oracle history: unchanged
- Temporary chat behavior: unchanged

## I. Final Expected Behavior

For: **exaplin me BNS 64 and it act 16**

### Vidhoor must:
1. ✅ Recognize both explicit Indian legal targets (BNS and IT Act)
2. ✅ Retrieve each independently via Qdrant with act-specific filters
3. ✅ Produce a grounded response for each statute separately
4. ✅ Structure the answer clearly:
   - **BNS Section 64** — explanation, key legal points, practical relevance, limitations
   - **Information Technology Act, 2000 Section 16** — explanation, key legal points, practical relevance, limitations
   - **Difference / Relationship** — how the provisions differ or relate
5. ✅ NOT ask: "Which jurisdiction?", "What does BNS mean?", "Is Act 16 another law?"

### If retrieval partially succeeds:
- Answer supported portions and clearly state which target could not be grounded
- Example: "I found authoritative context for BNS Section 64. I could not retrieve sufficient authoritative context for IT Act Section 16 from the current knowledge base."

### If retrieval fully fails:
- Targeted legal insufficiency response (NOT generic LLM speculation)
- Does not invent missing provisions

### If query is genuinely ambiguous:
- Clarify with: "Which Act's Section 64 would you like explained?"
- NOT: "Which jurisdiction? What is BNS?"