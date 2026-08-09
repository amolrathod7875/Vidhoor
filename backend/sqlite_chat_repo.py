"""SQLite-backed chat history repository for local (non-Oracle) environments."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent / "data" / "vidhoor_local.db"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _decode_json(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    text_value = str(value)
    try:
        parsed = json.loads(text_value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _decode_json_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    text_value = str(value)
    try:
        parsed = json.loads(text_value)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        return []
    except Exception:
        return []


def _decode_json_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    text_value = str(value)
    try:
        parsed = json.loads(text_value)
        if not isinstance(parsed, list):
            return []
        results: list[str] = []
        for item in parsed:
            candidate = str(item or "").strip()
            if candidate:
                results.append(candidate)
        return results[:5]
    except Exception:
        return []


def _extract_markdown_link_citations(content: str) -> list[dict[str, Any]]:
    text = str(content or "")
    if not text:
        return []
    link_pattern = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", flags=re.IGNORECASE)
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for match in link_pattern.finditer(text):
        title = str(match.group(1) or "").strip()
        url = str(match.group(2) or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        source_label = "Indian Kanoon" if "indiankanoon" in url.lower() else "External Source"
        results.append(
            {
                "doc_id": url,
                "title": title or source_label,
                "source": source_label,
                "source_url": url,
                "section": "",
                "snippet": title or url,
                "confidence": 0.35,
                "last_updated": "",
            }
        )
    return results


def _derive_follow_ups_from_history(
    user_message: str,
    assistant_content: str,
    citations: list[dict[str, Any]],
) -> list[str]:
    seed = " ".join(str(user_message or "").split())
    if not seed:
        seed = "this legal issue"
    results: list[str] = [
        f"What are the key legal ingredients for {seed}?"[:120],
        "What documents or evidence should I collect next?",
        "Can you explain the main risks and possible outcomes?",
    ]
    if citations:
        first_title = str(citations[0].get("title") or "this source").strip()
        if first_title:
            results.insert(1, f"Can you summarize the ruling in {first_title}?"[:120])
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in results:
        normalized = " ".join(item.split())
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)
    return cleaned[:5]


class SQLiteChatHistoryRepository:
    """SQLite-backed repository mirroring OracleChatHistoryRepository for local use."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else DB_PATH
        _ensure_parent(self._db_path)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.initialize_schema()

    def initialize_schema(self) -> None:
        ddl_statements = [
            """CREATE TABLE IF NOT EXISTS vidhoor_chat_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT,
                pinned INTEGER DEFAULT 0 NOT NULL,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )""",
            """CREATE TABLE IF NOT EXISTS vidhoor_chat_messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                masked_entities TEXT,
                citations_json TEXT,
                follow_ups_json TEXT,
                overall_confidence REAL,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                FOREIGN KEY (session_id) REFERENCES vidhoor_chat_sessions(session_id)
            )""",
            """CREATE INDEX IF NOT EXISTS idx_vidhoor_chat_sessions_user
                ON vidhoor_chat_sessions(user_id, updated_at)""",
            """CREATE INDEX IF NOT EXISTS idx_vidhoor_chat_messages_session
                ON vidhoor_chat_messages(session_id, created_at)""",
            """CREATE TABLE IF NOT EXISTS vidhoor_user_evidence (
                evidence_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_id TEXT,
                file_name TEXT NOT NULL,
                file_extension TEXT,
                encryption_alg TEXT,
                key_id TEXT,
                iv_b64 TEXT,
                encrypted_payload_b64 TEXT,
                masked_summary TEXT,
                masked_analysis TEXT,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )""",
            """CREATE INDEX IF NOT EXISTS idx_vidhoor_user_evidence_user
                ON vidhoor_user_evidence(user_id, created_at)""",
            """CREATE TABLE IF NOT EXISTS vidhoor_user_drafts (
                draft_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                email_id TEXT,
                session_id TEXT,
                application_type TEXT NOT NULL,
                title TEXT,
                draft_content TEXT NOT NULL,
                draft_meta_json TEXT,
                delivery_status TEXT DEFAULT 'generated' NOT NULL,
                last_delivery_error TEXT,
                emailed_at TEXT,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )""",
            """CREATE INDEX IF NOT EXISTS idx_vidhoor_user_drafts_user
                ON vidhoor_user_drafts(user_id, created_at)""",
            """CREATE TABLE IF NOT EXISTS vidhoor_user_feedback (
                feedback_id TEXT PRIMARY KEY,
                user_id TEXT,
                user_email TEXT,
                message TEXT NOT NULL,
                allow_follow_up INTEGER DEFAULT 0 NOT NULL,
                page_url TEXT,
                user_agent TEXT,
                app_version TEXT,
                context TEXT,
                status TEXT DEFAULT 'new' NOT NULL,
                created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )""",
            """CREATE INDEX IF NOT EXISTS idx_vidhoor_user_feedback_created
                ON vidhoor_user_feedback(created_at)""",
            """CREATE INDEX IF NOT EXISTS idx_vidhoor_user_feedback_status
                ON vidhoor_user_feedback(status, created_at)""",
            """CREATE INDEX IF NOT EXISTS idx_vidhoor_user_feedback_user
                ON vidhoor_user_feedback(user_id, created_at)""",
        ]
        cursor = self._conn.cursor()
        for statement in ddl_statements:
            try:
                cursor.execute(statement)
            except sqlite3.OperationalError:
                pass
        self._conn.commit()

    def save_chat_turn(
        self,
        user_id: str,
        session_id: str,
        user_message: str,
        assistant_message: str,
        masked_entities: dict[str, str],
        assistant_citations: list[dict[str, Any]] | None = None,
        assistant_follow_ups: list[str] | None = None,
        assistant_overall_confidence: float | None = None,
        session_title: str | None = None,
    ) -> None:
        resolved_title = (session_title or "").strip() or "New Chat"
        if len(resolved_title) > 120:
            resolved_title = resolved_title[:120]
        masked_entities_json = json.dumps(masked_entities, ensure_ascii=False)
        assistant_citations_json = None
        if assistant_citations:
            assistant_citations_json = json.dumps(assistant_citations, ensure_ascii=False)
        assistant_follow_ups_json = None
        if assistant_follow_ups:
            assistant_follow_ups_json = json.dumps(assistant_follow_ups, ensure_ascii=False)

        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO vidhoor_chat_sessions (session_id, user_id, title, updated_at)
            VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            ON CONFLICT(session_id) DO UPDATE SET
                title = excluded.title,
                updated_at = excluded.updated_at
            """,
            (session_id, user_id, resolved_title),
        )
        cursor.execute(
            """
            INSERT INTO vidhoor_chat_messages (
                session_id, user_id, role, content, masked_entities,
                citations_json, follow_ups_json, overall_confidence, created_at
            ) VALUES (?, ?, 'user', ?, ?, NULL, NULL, NULL, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (session_id, user_id, user_message, masked_entities_json),
        )
        cursor.execute(
            """
            INSERT INTO vidhoor_chat_messages (
                session_id, user_id, role, content, masked_entities,
                citations_json, follow_ups_json, overall_confidence, created_at
            ) VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (
                session_id,
                user_id,
                assistant_message,
                masked_entities_json,
                assistant_citations_json,
                assistant_follow_ups_json,
                assistant_overall_confidence,
            ),
        )
        cursor.execute(
            "UPDATE vidhoor_chat_sessions SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE session_id = ?",
            (session_id,),
        )
        self._conn.commit()

    def list_sessions(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT session_id, title, pinned, created_at, updated_at
            FROM vidhoor_chat_sessions
            WHERE user_id = ?
            ORDER BY pinned DESC, updated_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        result: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            result.append(
                {
                    "session_id": row["session_id"],
                    "title": row["title"],
                    "pinned": bool(row["pinned"]),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return result

    def update_session(
        self,
        user_id: str,
        session_id: str,
        title: str | None = None,
        pinned: bool | None = None,
    ) -> bool:
        updates: list[str] = []
        params: dict[str, Any] = {"user_id": user_id, "session_id": session_id}
        if title is not None:
            updates.append("title = :title")
            params["title"] = title
        if pinned is not None:
            updates.append("pinned = :pinned")
            params["pinned"] = 1 if pinned else 0
        if not updates:
            return False
        updates.append("updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
        cursor = self._conn.cursor()
        cursor.execute(
            f"UPDATE vidhoor_chat_sessions SET {', '.join(updates)} WHERE session_id = :session_id AND user_id = :user_id",
            params,
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def delete_session(self, user_id: str, session_id: str) -> bool:
        cursor = self._conn.cursor()
        cursor.execute(
            "DELETE FROM vidhoor_chat_messages WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        )
        cursor.execute(
            "DELETE FROM vidhoor_chat_sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def get_session_messages(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT 1 FROM vidhoor_chat_sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        )
        if cursor.fetchone() is None:
            return []
        cursor.execute(
            """
            SELECT role, content, created_at, masked_entities, citations_json, follow_ups_json, overall_confidence
            FROM vidhoor_chat_messages
            WHERE session_id = ? AND user_id = ?
            ORDER BY created_at ASC, message_id ASC
            """,
            (session_id, user_id),
        )
        messages: list[dict[str, Any]] = []
        last_user_message = ""
        for row in cursor.fetchall():
            role = row["role"]
            content_text = str(row["content"] or "")
            citations_json = row["citations_json"]
            follow_ups_json = row["follow_ups_json"]
            decoded_citations = _decode_json_list(citations_json)
            if role == "assistant" and not decoded_citations:
                decoded_citations = _extract_markdown_link_citations(content_text)
            decoded_follow_ups = _decode_json_string_list(follow_ups_json)
            if role == "assistant" and not decoded_follow_ups:
                decoded_follow_ups = _derive_follow_ups_from_history(
                    user_message=last_user_message,
                    assistant_content=content_text,
                    citations=decoded_citations,
                )
            if role == "user":
                last_user_message = content_text
            messages.append(
                {
                    "role": role,
                    "content": content_text,
                    "created_at": row["created_at"],
                    "masked_entities": _decode_json(row["masked_entities"]),
                    "citations": decoded_citations,
                    "follow_ups": decoded_follow_ups,
                    "overall_confidence": float(row["overall_confidence"]) if row["overall_confidence"] is not None else None,
                }
            )
        return messages

    def save_encrypted_evidence(
        self,
        user_id: str,
        evidence_id: str,
        file_name: str,
        file_extension: str,
        encrypted_payload_b64: str,
        iv_b64: str,
        encryption_alg: str,
        key_id: str | None,
        masked_summary: str,
        masked_analysis: str,
        session_id: str | None = None,
    ) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO vidhoor_user_evidence (
                evidence_id, user_id, session_id, file_name, file_extension,
                encryption_alg, key_id, iv_b64, encrypted_payload_b64,
                masked_summary, masked_analysis, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (
                evidence_id,
                user_id,
                session_id,
                file_name,
                file_extension,
                encryption_alg,
                key_id,
                iv_b64,
                encrypted_payload_b64,
                masked_summary,
                masked_analysis,
            ),
        )
        self._conn.commit()

    def list_user_evidence(
        self,
        user_id: str,
        limit: int = 100,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT evidence_id, file_name, file_extension, encryption_alg, key_id, session_id, created_at
            FROM vidhoor_user_evidence
            WHERE user_id = ? AND (? IS NULL OR session_id = ?)
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, session_id, session_id, limit),
        )
        result: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            result.append(
                {
                    "evidence_id": str(row["evidence_id"]),
                    "file_name": str(row["file_name"]),
                    "file_extension": str(row["file_extension"] or ""),
                    "encryption_alg": str(row["encryption_alg"] or ""),
                    "key_id": str(row["key_id"] or ""),
                    "session_id": str(row["session_id"] or ""),
                    "created_at": row["created_at"],
                }
            )
        return result

    def get_evidence_payload(self, user_id: str, evidence_id: str) -> dict[str, Any] | None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT evidence_id, file_name, file_extension, encryption_alg, key_id,
                   iv_b64, encrypted_payload_b64, masked_summary, masked_analysis,
                   session_id, created_at
            FROM vidhoor_user_evidence
            WHERE user_id = ? AND evidence_id = ?
            """,
            (user_id, evidence_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "evidence_id": str(row["evidence_id"]),
            "file_name": str(row["file_name"]),
            "file_extension": str(row["file_extension"] or ""),
            "encryption_alg": str(row["encryption_alg"] or ""),
            "key_id": str(row["key_id"] or ""),
            "iv_b64": str(row["iv_b64"] or ""),
            "encrypted_payload_b64": str(row["encrypted_payload_b64"] or ""),
            "masked_summary": str(row["masked_summary"] or ""),
            "masked_analysis": str(row["masked_analysis"] or ""),
            "session_id": str(row["session_id"] or ""),
            "created_at": row["created_at"],
        }

    def save_user_draft(
        self,
        user_id: str,
        draft_id: str,
        email_id: str | None,
        application_type: str,
        title: str,
        draft_content: str,
        session_id: str | None = None,
        draft_meta: dict[str, Any] | None = None,
    ) -> None:
        draft_meta_json = json.dumps(draft_meta or {}, ensure_ascii=False)
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO vidhoor_user_drafts (
                draft_id, user_id, email_id, session_id, application_type,
                title, draft_content, draft_meta_json, delivery_status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'generated', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (
                draft_id,
                user_id,
                email_id,
                session_id,
                application_type,
                title,
                draft_content,
                draft_meta_json,
            ),
        )
        self._conn.commit()

    def list_user_drafts(
        self,
        user_id: str,
        limit: int = 100,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT draft_id, user_id, email_id, session_id, application_type,
                   title, draft_content, draft_meta_json, delivery_status,
                   last_delivery_error, emailed_at, created_at, updated_at
            FROM vidhoor_user_drafts
            WHERE user_id = ? AND (? IS NULL OR session_id = ?)
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, session_id, session_id, limit),
        )
        result: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            result.append(
                {
                    "draft_id": str(row["draft_id"]),
                    "user_id": str(row["user_id"]),
                    "email_id": str(row["email_id"] or ""),
                    "session_id": str(row["session_id"] or ""),
                    "application_type": str(row["application_type"] or ""),
                    "title": str(row["title"] or ""),
                    "draft_content": str(row["draft_content"] or ""),
                    "draft_meta": _decode_json(row["draft_meta_json"]),
                    "delivery_status": str(row["delivery_status"] or "generated"),
                    "last_delivery_error": str(row["last_delivery_error"] or ""),
                    "emailed_at": row["emailed_at"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        return result

    def get_user_draft(self, user_id: str, draft_id: str) -> dict[str, Any] | None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT draft_id, user_id, email_id, session_id, application_type,
                   title, draft_content, draft_meta_json, delivery_status,
                   last_delivery_error, emailed_at, created_at, updated_at
            FROM vidhoor_user_drafts
            WHERE user_id = ? AND draft_id = ?
            """,
            (user_id, draft_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "draft_id": str(row["draft_id"]),
            "user_id": str(row["user_id"]),
            "email_id": str(row["email_id"] or ""),
            "session_id": str(row["session_id"] or ""),
            "application_type": str(row["application_type"] or ""),
            "title": str(row["title"] or ""),
            "draft_content": str(row["draft_content"] or ""),
            "draft_meta": _decode_json(row["draft_meta_json"]),
            "delivery_status": str(row["delivery_status"] or "generated"),
            "last_delivery_error": str(row["last_delivery_error"] or ""),
            "emailed_at": row["emailed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def update_user_draft(
        self,
        user_id: str,
        draft_id: str,
        title: str | None = None,
        draft_content: str | None = None,
    ) -> bool:
        updates = ["updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"]
        params: dict[str, Any] = {"user_id": user_id, "draft_id": draft_id}
        if title is not None:
            updates.append("title = :title")
            params["title"] = str(title).strip()[:512]
        if draft_content is not None:
            updates.append("draft_content = :draft_content")
            params["draft_content"] = str(draft_content)
        if len(updates) == 1:
            return False
        cursor = self._conn.cursor()
        cursor.execute(
            f"UPDATE vidhoor_user_drafts SET {', '.join(updates)} WHERE user_id = :user_id AND draft_id = :draft_id",
            params,
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def mark_draft_delivery(
        self,
        user_id: str,
        draft_id: str,
        delivery_status: str,
        last_delivery_error: str | None = None,
        emailed_at: bool = False,
    ) -> bool:
        updates = [
            "delivery_status = :delivery_status",
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')",
            "last_delivery_error = :last_delivery_error",
        ]
        if emailed_at:
            updates.append("emailed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
        params: dict[str, Any] = {
            "user_id": user_id,
            "draft_id": draft_id,
            "delivery_status": delivery_status,
            "last_delivery_error": (last_delivery_error or "")[:900],
        }
        cursor = self._conn.cursor()
        cursor.execute(
            f"UPDATE vidhoor_user_drafts SET {', '.join(updates)} WHERE user_id = :user_id AND draft_id = :draft_id",
            params,
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def save_user_feedback(
        self,
        feedback_id: str,
        message: str,
        allow_follow_up: bool,
        page_url: str | None,
        user_agent: str | None,
        app_version: str | None,
        context: str | None,
        user_id: str | None = None,
        user_email: str | None = None,
    ) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            INSERT INTO vidhoor_user_feedback (
                feedback_id, user_id, user_email, message, allow_follow_up,
                page_url, user_agent, app_version, context, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            """,
            (
                feedback_id,
                user_id,
                user_email,
                message,
                1 if allow_follow_up else 0,
                (page_url or "")[:1000],
                (user_agent or "")[:2000],
                (app_version or "")[:64],
                (context or "")[:256],
            ),
        )
        self._conn.commit()
