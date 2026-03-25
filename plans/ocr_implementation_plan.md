# OCR Feature Implementation Plan - Vidhoor Legal Copilot

## Overview

Add OCR (Optical Character Recognition) capability to allow users to upload images/documents (like FIRs) which are then processed to extract text, apply PII masking, and query the database.

### Key Requirements
- **OCR Engine**: Google Cloud Vision API (best Hindi/Marathi accuracy)
- **Data Privacy**: Text returned to local instance for PII Vault masking
- **Frontend UX**: Loading state during OCR processing (1-3 seconds)
- **Cost**: $1.50 per 1000 images (Free tier: 1000/month)

---

## Architecture

```mermaid
graph TB
    subgraph Frontend
        A[User uploads image] --> B[ChatInput with file upload]
        B --> C[Loading state: "Processing document..."]
        C --> D[Send to backend /ocr endpoint]
    end

    subgraph Backend
        D --> E[FastAPI OCR Endpoint]
        E --> F[Google Cloud Vision API]
        F --> G[Extract text from image]
        G --> H[PII Vault masking]
        H --> I[Query Chroma DB]
        I --> J[LLM Engine]
        J --> K[Return response]
    end

    subgraph External
        F -.-> GCV[Google Cloud Vision API]
    end

    subgraph Database
        I --> L[(Chroma Vector DB)]
        J --> M[(Oracle DB - Chat History)]
    end

    style F fill:#f9f,font-weight:bold
    style H fill:#bbf,font-weight:bold
```

### Data Flow
1. User uploads FIR image → Frontend shows loading state
2. Image sent to `/ocr` endpoint as multipart/form-data
3. Google Cloud Vision API extracts text (Hindi + Marathi + English)
4. Text returned to backend → PII Vault masks sensitive entities
5. Masked text queries Chroma DB for relevant legal context
6. LLM generates response with citations
7. Response returned to frontend

**Note**: Image is sent to Google Cloud for processing, but extracted text returns to local instance for PII masking before storage/query.

---

## Implementation Steps

### Phase 1: Backend - Google Cloud Vision Integration

#### 1.1 Add Dependencies
**File**: `backend/requirements.txt`
```diff
+ google-cloud-vision>=3.7.0
+ google-auth>=2.28.0
+ pillow>=10.0.0
```

#### 1.2 Google Cloud Setup
1. Create Google Cloud Project
2. Enable Cloud Vision API
3. Create Service Account with Vision User role
4. Download JSON key file
5. Set `GOOGLE_APPLICATION_CREDENTIALS` env variable

#### 1.3 Create OCR Module
**New File**: `backend/ocr_engine.py`

```
ocr_engine.py
├── OCREngine class
│   ├── __init__() - Initialize Vision client
│   ├── extract_text(image_path) -> str
│   ├── extract_text_from_bytes(image_bytes) -> str
│   ├── extract_text_from_gcs(gcs_uri) -> str
│   └── detect_document_ocr(image_source) -> dict
```

Features:
- Uses `image_annotator` for document text detection
- Supports Hindi, Marathi, English language hints
- Returns full text + word-level confidence scores
- Handles multi-page documents

#### 1.4 Add OCR Endpoint
**File**: `backend/main.py`

```
- POST /ocr endpoint
- Accept: multipart/form-data (image file)
- Request model: OCRRequest(image: UploadFile)
- Response model: OCRResponse(extracted_text: str, masked_text: str, entities_detected: dict, confidence: float)

- POST /ocr-query endpoint (combined OCR + query)
- Input: image file + optional query
- Output: ChatResponse (response, citations, masked_entities)
```

#### 1.5 Integrate PII Vault
- Modify OCR response to include PII-masked version
- Reuse existing PIIVault from pii_vault.py

---

### Phase 2: Frontend - File Upload

#### 2.1 Add File Upload Button
**File**: `frontend/src/components/ChatInput.tsx`
- Add paperclip/attachment icon button
- Accept: image/png, image/jpeg, image/jpg, application/pdf
- Max file size: 10MB

#### 2.2 Create Loading State
**File**: `frontend/src/components/ui/ocr_loading.tsx` (new)

```
- Animated progress indicator
- Text: "Extracting text from document..."
- Text: "Processing with neural network..."
- Estimated time: 1-3 seconds (faster than EasyOCR)
```

#### 2.3 Handle OCR Response
- Display extracted text in chat as user message
- Show processing indicator while waiting

---

### Phase 3: Integration

#### 3.1 End-to-End Flow
1. User uploads image → Frontend shows "Processing..."
2. Backend receives image → Google Cloud Vision extracts text
3. PII masking applied → Query Chroma DB
4. LLM generates response → Return to frontend

#### 3.2 Error Handling
- Handle Google Cloud API errors gracefully
- Fallback message if OCR fails
- Rate limiting consideration (1000 free/month)

---

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `backend/requirements.txt` | Modify | Add google-cloud-vision, google-auth |
| `backend/.env` | Modify | Add Google credentials path |
| `backend/ocr_engine.py` | Create | Google Cloud Vision wrapper |
| `backend/main.py` | Modify | Add /ocr and /ocr-query endpoints |
| `frontend/src/components/ChatInput.tsx` | Modify | Add file upload button |
| `frontend/src/components/ui/ocr_loading.tsx` | Create | Loading component |

---

## Google Cloud Setup Instructions

### 1. Create Project
```bash
gcloud projects create vidhoor-ocr --name="Vidhoor Legal Copilot"
```

### 2. Enable Vision API
```bash
gcloud services enable vision.googleapis.com
```

### 3. Create Service Account
```bash
gcloud iam service-accounts create vidhoor-ocr \
    --display-name="Vidhoor OCR Service"

gcloud iam service-accounts keys create key.json \
    --iam-account=vidhoor-ocr@vidhoor-ocr.iam.gserviceaccount.com
```

### 4. Assign Role
```bash
gcloud projects add-iam-policy-binding vidhoor-ocr \
    --member="serviceAccount:vidhoor-ocr@vidhoor-ocr.iam.gserviceaccount.com" \
    --role="roles/vision.apiUser"
```

### 5. Configure Environment
```bash
# In backend/.env
GOOGLE_APPLICATION_CREDENTIALS="./vidhoor-ocr-key.json"
```

---

## Cost Considerations

| Tier | Images/Month | Cost |
|------|--------------|------|
| Free | 1,000 | $0 |
| Paid | 1,001+ | $1.50/1000 |

**For Legal Use Case:**
- 1 FIR image = ~1-5 pages
- Assume 100 queries/day × 30 days = 3,000 images/month
- Cost: ~$3.00/month (after free tier)

---

## PII Vault Integration

The extracted OCR text will feed directly into PII Vault for masking:

```python
from pii_vault import PIIVault

pii_vault = PIIVault()
ocr_text = "Name: राम शर्मा, Aadhaar: 1234 5678 9012"

# Mask sensitive entities
masked_text, entity_map = pii_vault.mask(ocr_text)
# Result: "Name: [PERSON_1], Aadhaar: [IN_AADHAAR_1]"
```

### Supported Entity Types
- PERSON names (Devanagari + Roman)
- IN_AADHAAR (12-digit numbers)
- IN_PAN (PAN card format)
- EMAIL_ADDRESS
- PHONE_NUMBER
- Location/Address patterns

---

## Acceptance Criteria

1. ✅ User can upload PNG/JPG/PDF images in chat input
2. ✅ Loading state shown during OCR processing (1-3 sec)
3. ✅ Hindi + Marathi + English text extracted accurately
4. ✅ PII entities masked before storage/query
5. ✅ Extracted text flows into Chroma DB query
6. ✅ Works within monthly free tier (1000 images)
7. ✅ Cost-effective for production use

---

## Next Steps (After Approval)

1. **Phase 1**: Backend Google Cloud Vision implementation (~3 tasks)
2. **Phase 2**: Frontend file upload UI (~2 tasks)
3. **Phase 3**: Integration testing (~1 task)
4. **Phase 4**: Google Cloud setup (~1 task)

---

*Plan updated for Google Cloud Vision OCR - Vidhoor Legal Copilot*
