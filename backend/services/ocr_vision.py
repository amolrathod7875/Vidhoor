from __future__ import annotations

import logging
import os
import tempfile
import time
from importlib import import_module
from io import BytesIO
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_OCR_SPACE_ENDPOINT = "https://api.ocr.space/parse/image"
DEFAULT_LANGUAGE = "auto"
DEFAULT_OCR_ENGINE = 3
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
        return self._request_ocr_with_fallback(
            file_opener=lambda: file_path.open("rb"),
            filename=file_path.name,
        )

    def _request_ocr_from_bytes(self, filename: str, file_bytes: bytes) -> dict[str, Any]:
        return self._request_ocr_with_fallback(
            file_opener=lambda: BytesIO(file_bytes),
            filename=filename,
        )

    def _request_ocr_with_fallback(
        self,
        file_opener,
        filename: str,
    ) -> dict[str, Any]:
        attempts = max(1, int(self.max_retries))
        last_error: Exception | None = None

        configs = [
            (self.ocr_engine, self.language),
            (1, "eng"),
        ]

        for engine, language in configs:
            for attempt in range(1, attempts + 1):
                try:
                    with file_opener() as file_stream:
                        files = {
                            "filename": (filename, file_stream),
                        }
                        data = {
                            "language": language,
                            "isOverlayRequired": "false",
                            "OCREngine": str(engine),
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
                    if engine == 1:
                        raise RuntimeError(f"OCR.space request failed: {exc}") from exc
                    break

                if response.status_code != 200:
                    message = f"OCR.space request failed with status {response.status_code}: {response.text}"
                    if response.status_code >= 500 and attempt < attempts:
                        time.sleep(self.retry_delay_seconds)
                        continue
                    if engine == 1:
                        raise RuntimeError(message)
                    break

                try:
                    payload = response.json()
                except ValueError as exc:
                    last_error = exc
                    if attempt < attempts:
                        time.sleep(self.retry_delay_seconds)
                        continue
                    if engine == 1:
                        raise RuntimeError("OCR.space returned non-JSON response.") from exc
                    break

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
                    if engine == 1:
                        raise RuntimeError(f"OCR.space error: {message_text}")
                    break

                parsed = self._parse_pages(payload)
                if parsed:
                    logger.warning("OCR succeeded with engine=%s language=%s", engine, language)
                    return payload

                if attempt < attempts:
                    time.sleep(self.retry_delay_seconds)
                    continue
                break

            if engine == 1:
                raise RuntimeError("OCR.space returned empty ParsedResults after retries.")
            logger.warning("OCR engine=%s language=%s returned empty ParsedResults; trying fallback", engine, language)

        raise RuntimeError(f"OCR.space request failed after retries: {last_error}")

    @staticmethod
    def _looks_like_garbage(text: str, min_words: int = 5, max_noise_ratio: float = 0.55) -> bool:
        if not text or not text.strip():
            return True

        words = text.split()
        if len(words) < min_words:
            return True

        alnum_count = sum(1 for char in text if char.isalnum())
        noise_ratio = 1.0 - (alnum_count / max(len(text), 1))
        return noise_ratio > max_noise_ratio

    @staticmethod
    def _parse_pages(payload: dict[str, Any]) -> list[dict[str, Any]]:
        parsed_results = payload.get("ParsedResults") or []
        output: list[dict[str, Any]] = []

        for page_index, item in enumerate(parsed_results, start=1):
            text = str(item.get("ParsedText") or "").strip()
            exit_code = item.get("FileParseExitCode")
            error_message = item.get("ErrorMessage")

            if exit_code is not None and str(exit_code) != "0" and str(exit_code) != "":
                if not text:
                    logger.warning(
                        "OCR page %d returned non-zero exit code %s: %s",
                        page_index,
                        exit_code,
                        error_message or "no detail",
                    )

            if not text:
                continue

            raw_page = item.get("PageNumber")
            page_number = page_index
            if isinstance(raw_page, int):
                page_number = raw_page
            elif isinstance(raw_page, str) and raw_page.isdigit():
                page_number = int(raw_page)

            page_dict: dict[str, Any] = {"page": page_number, "text": text}
            if exit_code is not None:
                page_dict["parse_exit_code"] = exit_code
            if error_message:
                page_dict["parse_error_message"] = error_message

            output.append(page_dict)

        return output

    @staticmethod
    def _read_pages_via_pagewise_ocr(file_path: Path) -> list[dict[str, Any]]:
        try:
            pypdf_module = import_module("pypdf")
            PdfReader = getattr(pypdf_module, "PdfReader")
            PdfWriter = getattr(pypdf_module, "PdfWriter")
        except Exception as exc:
            raise RuntimeError("pypdf is required for page-wise OCR fallback") from exc

        reader = PdfReader(str(file_path))
        aggregated_pages: list[dict[str, Any]] = []
        service = VisionOCRService()

        for page_index, page in enumerate(reader.pages, start=1):
            writer = PdfWriter()
            writer.add_page(page)

            temp_fd, temp_path_raw = tempfile.mkstemp(suffix=f"_page_{page_index}.pdf")
            os.close(temp_fd)
            try:
                with Path(temp_path_raw).open("wb") as temp_pdf:
                    writer.write(temp_pdf)
                page_results = service.extract_pages(str(temp_path_raw))
            except Exception:
                continue
            finally:
                try:
                    Path(temp_path_raw).unlink(missing_ok=True)
                except Exception:
                    pass

            page_text = "\n".join(
                str(item.get("text") or "").strip()
                for item in page_results
                if str(item.get("text") or "").strip()
            ).strip()

            if page_text:
                aggregated_pages.append({"page": page_index, "text": page_text})

        return aggregated_pages

    def extract_pages(self, file_path: str) -> list[dict[str, Any]]:
        """Extract OCR text page-wise from an uploaded image/PDF."""
        path = Path(file_path)
        extension = path.suffix.lower()

        if extension not in {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp", ".pdf"}:
            raise ValueError("Unsupported file type. Upload PDF, PNG, JPG, JPEG, WEBP, TIFF, or BMP.")

        try:
            payload = self._request_ocr(path)
            pages = self._parse_pages(payload)
        except RuntimeError as exc:
            error_text = str(exc).lower()
            if extension == ".pdf" and "exceeds the maximum permissible file size" in error_text:
                pages = self._read_pages_via_pagewise_ocr(path)
            else:
                raise

        if not pages and extension == ".pdf":
            pages = self._read_pages_via_pagewise_ocr(path)

        return pages

    def extract_pages_from_bytes(self, filename: str, file_bytes: bytes) -> list[dict[str, Any]]:
        """Extract OCR text page-wise from uploaded image/PDF bytes."""
        extension = Path(filename).suffix.lower()

        if extension not in {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp", ".pdf"}:
            raise ValueError("Unsupported file type. Upload PDF, PNG, JPG, JPEG, WEBP, TIFF, or BMP.")

        if extension == ".pdf":
            temp_fd, temp_path = tempfile.mkstemp(suffix=".pdf")
            os.close(temp_fd)
            try:
                with Path(temp_path).open("wb") as temp_file:
                    temp_file.write(file_bytes)
                return self.extract_pages(temp_path)
            finally:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception:
                    pass

        payload = self._request_ocr_from_bytes(filename=filename, file_bytes=file_bytes)
        return self._parse_pages(payload)

