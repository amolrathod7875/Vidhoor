"""OCR smoke test for FIR.pdf (Marathi FIR).

Run with: pytest tests/test_ocr_fir.py -v
Skip automatically when OCR_SPACE_API_KEY is unset or network is unavailable.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIR_PDF = PROJECT_ROOT / "FIR.pdf"


def _skip_conditions() -> None:
    if not os.environ.get("OCR_SPACE_API_KEY", "").strip():
        pytest.skip("OCR_SPACE_API_KEY is not set", allow_module_level=True)
    if not FIR_PDF.exists():
        pytest.skip("FIR.pdf not found in project root", allow_module_level=True)


@pytest.mark.network
def test_fir_ocr_extracts_marathi_text():
    _skip_conditions()

    from services.ocr_vision import VisionOCRService

    service = VisionOCRService()
    pages = service.extract_pages(str(FIR_PDF))

    assert pages, "OCR returned no pages for FIR.pdf"
    combined = "\n".join(item.get("text", "") for item in pages)

    assert "प्रथम माहिती अहवाल" in combined, "Marathi FIR header missing"
    assert "MH 14 AB 9988" in combined, "Vehicle registration missing"
    assert "303(2)" in combined, "BNS section reference missing"
    assert "पिंपरी-चिंचवड" in combined, "Police station name missing"


@pytest.mark.network
def test_fir_ocr_page_quality_not_garbage():
    _skip_conditions()

    from services.ocr_vision import VisionOCRService

    service = VisionOCRService()
    pages = service.extract_pages(str(FIR_PDF))

    assert pages, "OCR returned no pages for FIR.pdf"
    for item in pages:
        text = str(item.get("text") or "")
        assert not VisionOCRService._looks_like_garbage(text), (
            f"Page {item.get('page')} looks like OCR garbage"
        )


@pytest.mark.network
def test_fir_translation_contains_expected_entities():
    _skip_conditions()

    from services.ocr_vision import VisionOCRService
    from services.translate_helsinki import translate_pages_to_english
    from llm_engine import LLMEngine

    service = VisionOCRService()
    pages = service.extract_pages(str(FIR_PDF))
    assert pages, "OCR returned no pages for FIR.pdf"

    translated_pages = translate_pages_to_english(pages, llm_engine=LLMEngine())
    assert translated_pages, "Translation returned no pages"
    combined = "\n".join(item.get("text_en", "") for item in translated_pages)

    assert "Ramesh" in combined or "Vinayak" in combined or "Patil" in combined, " complainant name missing"
    assert "Hero Splendor" in combined, "Vehicle name missing"
    assert "MH 14 AB 9988" in combined, "Vehicle registration missing"
    assert "303(2)" in combined, "BNS section reference missing"
    assert "Bharatiya Nyaya Sanhita" in combined or "BNS" in combined, "Act name missing"
    assert "Party of India" not in combined, "Marian garbage text present"
    assert "Ric writing" not in combined, "Marian garbage text present"


def test_ocr_exit_code_warning_suppressed_when_text_present():
    from services.ocr_vision import VisionOCRService

    payload = {
        "ParsedResults": [
            {
                "PageNumber": 1,
                "ParsedText": "प्रथम माहिती अहवाल",
                "FileParseExitCode": 1,
                "ErrorMessage": None,
            }
        ]
    }
    pages = VisionOCRService._parse_pages(payload)
    assert len(pages) == 1
    assert pages[0]["text"] == "प्रथम माहिती अहवाल"


def test_ocr_exit_code_warning_emitted_when_text_empty():
    from services.ocr_vision import VisionOCRService

    payload = {
        "ParsedResults": [
            {
                "PageNumber": 1,
                "ParsedText": "",
                "FileParseExitCode": 1,
                "ErrorMessage": "Parse failed",
            }
        ]
    }
    pages = VisionOCRService._parse_pages(payload)
    assert len(pages) == 0


def test_sqlite_chat_repo_smoke():
    from sqlite_chat_repo import SQLiteChatHistoryRepository
    import tempfile

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    repo = None
    try:
        repo = SQLiteChatHistoryRepository(db_path=db_path)
        repo.save_chat_turn(
            user_id="user1",
            session_id="sess1",
            user_message="What is theft?",
            assistant_message="Theft is defined under BNS...",
            masked_entities={},
            session_title="Theft query",
        )
        sessions = repo.list_sessions("user1")
        assert len(sessions) == 1
        assert sessions[0]["title"] == "Theft query"

        messages = repo.get_session_messages("user1", "sess1")
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

        repo.save_encrypted_evidence(
            user_id="user1",
            evidence_id="evi1",
            file_name="fir.pdf",
            file_extension=".pdf",
            encrypted_payload_b64="abc",
            iv_b64="def",
            encryption_alg="aes",
            key_id="key1",
            masked_summary="summary",
            masked_analysis="analysis",
            session_id="sess1",
        )
        evidence = repo.list_user_evidence("user1")
        assert len(evidence) == 1
        assert evidence[0]["evidence_id"] == "evi1"

        payload = repo.get_evidence_payload("user1", "evi1")
        assert payload is not None
        assert payload["file_name"] == "fir.pdf"

        deleted = repo.delete_session("user1", "sess1")
        assert deleted is True
        assert repo.get_session_messages("user1", "sess1") == []
    finally:
        if repo is not None:
            try:
                repo._conn.close()
            except Exception:
                pass
        Path(db_path).unlink(missing_ok=True)
