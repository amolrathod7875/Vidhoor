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
