from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import requests

DEFAULT_OCR_SPACE_ENDPOINT = "https://api.ocr.space/parse/image"
DEFAULT_LANGUAGE = "eng"
DEFAULT_OCR_ENGINE = 1
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY_SECONDS = 2


class VisionOCRService:
    """OCR.space service for images and scanned PDFs."""

    def __init__(self) -> None:
        api_key = os.environ.get("OCR_SPACE_API_KEY", "").strip()
        self.api_key = api_key or "helloworld"
        self.endpoint = os.environ.get("OCR_SPACE_ENDPOINT", DEFAULT_OCR_SPACE_ENDPOINT).strip() or DEFAULT_OCR_SPACE_ENDPOINT
        self.language = os.environ.get("OCR_SPACE_LANGUAGE", DEFAULT_LANGUAGE).strip() or DEFAULT_LANGUAGE

        ocr_engine_raw = os.environ.get("OCR_SPACE_ENGINE", str(DEFAULT_OCR_ENGINE)).strip()
        timeout_raw = os.environ.get("OCR_SPACE_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)).strip()
        max_retries_raw = os.environ.get("OCR_SPACE_MAX_RETRIES", str(DEFAULT_MAX_RETRIES)).strip()
        retry_delay_raw = os.environ.get("OCR_SPACE_RETRY_DELAY_SECONDS", str(DEFAULT_RETRY_DELAY_SECONDS)).strip()

        self.ocr_engine = int(ocr_engine_raw) if ocr_engine_raw.isdigit() else DEFAULT_OCR_ENGINE
        self.timeout_seconds = int(timeout_raw) if timeout_raw.isdigit() else DEFAULT_TIMEOUT_SECONDS
        self.max_retries = int(max_retries_raw) if max_retries_raw.isdigit() else DEFAULT_MAX_RETRIES
        self.retry_delay_seconds = (
            float(retry_delay_raw)
            if retry_delay_raw.replace(".", "", 1).isdigit()
            else float(DEFAULT_RETRY_DELAY_SECONDS)
        )

    def _request_ocr(self, file_path: Path) -> dict[str, Any]:
        attempts = max(1, int(self.max_retries))
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                with file_path.open("rb") as file_stream:
                    files = {
                        "filename": (file_path.name, file_stream),
                    }
                    data = {
                        "language": self.language,
                        "isOverlayRequired": "false",
                        "OCREngine": str(self.ocr_engine),
                        "detectOrientation": "true",
                        "scale": "true",
                    }
                    headers = {
                        "apikey": self.api_key,
                    }

                    response = requests.post(
                        self.endpoint,
                        headers=headers,
                        data=data,
                        files=files,
                        timeout=self.timeout_seconds,
                    )
            except requests.RequestException as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(self.retry_delay_seconds)
                    continue
                raise RuntimeError(f"OCR.space request failed: {exc}") from exc

            if response.status_code != 200:
                message = f"OCR.space request failed with status {response.status_code}: {response.text}"
                if response.status_code >= 500 and attempt < attempts:
                    time.sleep(self.retry_delay_seconds)
                    continue
                raise RuntimeError(message)

            try:
                payload = response.json()
            except ValueError as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(self.retry_delay_seconds)
                    continue
                raise RuntimeError("OCR.space returned non-JSON response.") from exc

            if payload.get("IsErroredOnProcessing"):
                message = payload.get("ErrorMessage") or payload.get("ErrorDetails") or "OCR.space processing failed."
                if isinstance(message, list):
                    message = "; ".join(str(item) for item in message)
                message_text = str(message)
                is_transient = any(
                    token in message_text.lower()
                    for token in ["timeout", "try again", "tempor", "overload", "busy"]
                )
                if is_transient and attempt < attempts:
                    time.sleep(self.retry_delay_seconds)
                    continue
                raise RuntimeError(f"OCR.space error: {message_text}")

            return payload

        raise RuntimeError(f"OCR.space request failed after retries: {last_error}")

    @staticmethod
    def _parse_pages(payload: dict[str, Any]) -> list[dict[str, Any]]:
        parsed_results = payload.get("ParsedResults") or []
        output: list[dict[str, Any]] = []

        for page_index, item in enumerate(parsed_results, start=1):
            text = str(item.get("ParsedText") or "").strip()
            if not text:
                continue

            raw_page = item.get("PageNumber")
            page_number = page_index
            if isinstance(raw_page, int):
                page_number = raw_page
            elif isinstance(raw_page, str) and raw_page.isdigit():
                page_number = int(raw_page)

            output.append({"page": page_number, "text": text})

        return output

    def extract_pages(self, file_path: str) -> list[dict[str, Any]]:
        """Extract OCR text page-wise from an uploaded image/PDF."""
        path = Path(file_path)
        extension = path.suffix.lower()

        if extension not in {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp", ".pdf"}:
            raise ValueError("Unsupported file type. Upload PDF, PNG, JPG, JPEG, WEBP, TIFF, or BMP.")

        payload = self._request_ocr(path)
        return self._parse_pages(payload)

