"""Oracle Autonomous Database utilities for Vidhoor chat history."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import oracledb

try:
	oracledb.defaults.fetch_lobs = False
except Exception:
	pass


class OracleChatHistoryRepository:
	"""Repository for persisting and reading user chat history in Oracle DB."""

	def __init__(self) -> None:
		_load_environment()
		self._base_dir = Path(__file__).resolve().parent

		self.user = os.environ.get("ORACLE_USER")
		self.password = os.environ.get("ORACLE_PASSWORD")
		self.dsn = os.environ.get("ORACLE_DSN")
		self.config_dir = self._resolve_optional_path(os.environ.get("ORACLE_CONFIG_DIR"))
		self.wallet_location = self._resolve_optional_path(os.environ.get("ORACLE_WALLET_LOCATION"))
		self.wallet_password = os.environ.get("ORACLE_WALLET_PASSWORD")

		if not self.user or not self.password or not self.dsn:
			raise EnvironmentError(
				"Missing Oracle DB configuration. Required: ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN"
			)

	def _resolve_optional_path(self, value: str | None) -> str | None:
		"""Resolve relative env paths from backend directory."""
		if not value:
			return None

		raw = Path(value.strip().strip('"').strip("'"))
		if raw.is_absolute():
			return str(raw)
		return str((self._base_dir / raw).resolve())

	def _connect(self) -> oracledb.Connection:
		"""Create an Oracle connection with optional wallet configuration."""
		connect_kwargs: dict[str, Any] = {
			"user": self.user,
			"password": self.password,
			"dsn": self.dsn,
		}

		if self.config_dir:
			connect_kwargs["config_dir"] = self.config_dir
		if self.wallet_location:
			connect_kwargs["wallet_location"] = self.wallet_location
		if self.wallet_password:
			connect_kwargs["wallet_password"] = self.wallet_password

		return oracledb.connect(**connect_kwargs)

	def initialize_schema(self) -> None:
		"""Create chat session/message tables when they do not already exist."""
		ddl_statements = [
			"""
			CREATE TABLE vidhoor_chat_sessions (
				session_id VARCHAR2(128) PRIMARY KEY,
				user_id VARCHAR2(256) NOT NULL,
				title VARCHAR2(512),
				pinned NUMBER(1) DEFAULT 0 NOT NULL,
				created_at TIMESTAMP DEFAULT SYSTIMESTAMP,
				updated_at TIMESTAMP DEFAULT SYSTIMESTAMP
			)
			""",
			"""
			CREATE TABLE vidhoor_chat_messages (
				message_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
				session_id VARCHAR2(128) NOT NULL,
				user_id VARCHAR2(256) NOT NULL,
				role VARCHAR2(32) NOT NULL,
				content CLOB NOT NULL,
				masked_entities CLOB,
				citations_json CLOB,
				follow_ups_json CLOB,
				overall_confidence NUMBER,
				created_at TIMESTAMP DEFAULT SYSTIMESTAMP,
				CONSTRAINT fk_vidhoor_chat_session
					FOREIGN KEY (session_id) REFERENCES vidhoor_chat_sessions(session_id)
			)
			""",
			"""
			CREATE INDEX idx_vidhoor_chat_sessions_user
			ON vidhoor_chat_sessions(user_id, updated_at)
			""",
			"""
			CREATE INDEX idx_vidhoor_chat_messages_session
			ON vidhoor_chat_messages(session_id, created_at)
			""",
			"""
			CREATE TABLE vidhoor_user_evidence (
				evidence_id VARCHAR2(128) PRIMARY KEY,
				user_id VARCHAR2(256) NOT NULL,
				session_id VARCHAR2(128),
				file_name VARCHAR2(512) NOT NULL,
				file_extension VARCHAR2(32),
				encryption_alg VARCHAR2(64),
				key_id VARCHAR2(256),
				iv_b64 CLOB,
				encrypted_payload_b64 CLOB,
				masked_summary CLOB,
				masked_analysis CLOB,
				created_at TIMESTAMP DEFAULT SYSTIMESTAMP,
				updated_at TIMESTAMP DEFAULT SYSTIMESTAMP
			)
			""",
			"""
			CREATE INDEX idx_vidhoor_user_evidence_user
			ON vidhoor_user_evidence(user_id, created_at)
			""",
			"""
			CREATE TABLE vidhoor_user_drafts (
				draft_id VARCHAR2(128) PRIMARY KEY,
				user_id VARCHAR2(256) NOT NULL,
				email_id VARCHAR2(320),
				session_id VARCHAR2(128),
				application_type VARCHAR2(64) NOT NULL,
				title VARCHAR2(512),
				draft_content CLOB NOT NULL,
				draft_meta_json CLOB,
				delivery_status VARCHAR2(64) DEFAULT 'generated' NOT NULL,
				last_delivery_error VARCHAR2(1000),
				emailed_at TIMESTAMP,
				created_at TIMESTAMP DEFAULT SYSTIMESTAMP,
				updated_at TIMESTAMP DEFAULT SYSTIMESTAMP
			)
			""",
			"""
			CREATE INDEX idx_vidhoor_user_drafts_user
			ON vidhoor_user_drafts(user_id, created_at)
			""",
			"""
			CREATE TABLE vidhoor_user_feedback (
				feedback_id VARCHAR2(128) PRIMARY KEY,
				user_id VARCHAR2(256),
				user_email VARCHAR2(320),
				message CLOB NOT NULL,
				allow_follow_up NUMBER(1) DEFAULT 0 NOT NULL,
				page_url VARCHAR2(1000),
				user_agent VARCHAR2(2000),
				app_version VARCHAR2(64),
				context VARCHAR2(256),
				status VARCHAR2(32) DEFAULT 'new' NOT NULL,
				created_at TIMESTAMP DEFAULT SYSTIMESTAMP,
				updated_at TIMESTAMP DEFAULT SYSTIMESTAMP
			)
			""",
			"""
			CREATE INDEX idx_vidhoor_user_feedback_created
			ON vidhoor_user_feedback(created_at)
			""",
			"""
			CREATE INDEX idx_vidhoor_user_feedback_status
			ON vidhoor_user_feedback(status, created_at)
			""",
			"""
			CREATE INDEX idx_vidhoor_user_feedback_user
			ON vidhoor_user_feedback(user_id, created_at)
			""",
		]

		with self._connect() as connection:
			with connection.cursor() as cursor:
				for statement in ddl_statements:
					try:
						cursor.execute(statement)
					except oracledb.DatabaseError as exc:
						error_obj = exc.args[0]
						# ORA-00955: name is already used by an existing object
						if getattr(error_obj, "code", None) == 955:
							continue
						raise
			connection.commit()

		# Backward compatibility for existing databases created before pinned column.
		with self._connect() as connection:
			with connection.cursor() as cursor:
				try:
					cursor.execute(
						"ALTER TABLE vidhoor_chat_sessions ADD (pinned NUMBER(1) DEFAULT 0 NOT NULL)"
					)
				except oracledb.DatabaseError as exc:
					error_obj = exc.args[0]
					# ORA-01430: column being added already exists
					if getattr(error_obj, "code", None) != 1430:
						raise

				try:
					cursor.execute(
						"ALTER TABLE vidhoor_chat_messages ADD (citations_json CLOB)"
					)
				except oracledb.DatabaseError as exc:
					error_obj = exc.args[0]
					# ORA-01430: column being added already exists
					if getattr(error_obj, "code", None) != 1430:
						raise

				try:
					cursor.execute(
						"ALTER TABLE vidhoor_chat_messages ADD (overall_confidence NUMBER)"
					)
				except oracledb.DatabaseError as exc:
					error_obj = exc.args[0]
					# ORA-01430: column being added already exists
					if getattr(error_obj, "code", None) != 1430:
						raise

				try:
					cursor.execute(
						"ALTER TABLE vidhoor_chat_messages ADD (follow_ups_json CLOB)"
					)
				except oracledb.DatabaseError as exc:
					error_obj = exc.args[0]
					# ORA-01430: column being added already exists
					if getattr(error_obj, "code", None) != 1430:
						raise
			connection.commit()

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
		"""Persist one user/assistant message turn in Oracle."""
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

		with self._connect() as connection:
			with connection.cursor() as cursor:
				cursor.execute(
					"""
					MERGE INTO vidhoor_chat_sessions target
					USING (
						SELECT :session_id AS session_id,
							   :user_id AS user_id,
							   :title AS title
						FROM dual
					) source
					ON (target.session_id = source.session_id)
					WHEN MATCHED THEN
						UPDATE SET target.updated_at = SYSTIMESTAMP
					WHEN NOT MATCHED THEN
						INSERT (session_id, user_id, title, created_at, updated_at)
						VALUES (source.session_id, source.user_id, source.title, SYSTIMESTAMP, SYSTIMESTAMP)
					""",
					{
						"session_id": session_id,
						"user_id": user_id,
						"title": resolved_title,
					},
				)

				cursor.execute(
					"""
					INSERT INTO vidhoor_chat_messages (
						session_id,
						user_id,
						role,
						content,
						masked_entities,
						citations_json,
						follow_ups_json,
						overall_confidence,
						created_at
					) VALUES (
						:session_id,
						:user_id,
						'user',
						:content,
						:masked_entities,
						NULL,
						NULL,
						NULL,
						SYSTIMESTAMP
					)
					""",
					{
						"session_id": session_id,
						"user_id": user_id,
						"content": user_message,
						"masked_entities": masked_entities_json,
					},
				)

				cursor.execute(
					"""
					INSERT INTO vidhoor_chat_messages (
						session_id,
						user_id,
						role,
						content,
						masked_entities,
						citations_json,
						follow_ups_json,
						overall_confidence,
						created_at
					) VALUES (
						:session_id,
						:user_id,
						'assistant',
						:content,
						:masked_entities,
						:citations_json,
						:follow_ups_json,
						:overall_confidence,
						SYSTIMESTAMP
					)
					""",
					{
						"session_id": session_id,
						"user_id": user_id,
						"content": assistant_message,
						"masked_entities": masked_entities_json,
						"citations_json": assistant_citations_json,
						"follow_ups_json": assistant_follow_ups_json,
						"overall_confidence": assistant_overall_confidence,
					},
				)

				cursor.execute(
					"""
					UPDATE vidhoor_chat_sessions
					SET updated_at = SYSTIMESTAMP
					WHERE session_id = :session_id
					""",
					{"session_id": session_id},
				)

			connection.commit()

	def list_sessions(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
		"""Return session summaries for a specific user."""
		with self._connect() as connection:
			with connection.cursor() as cursor:
				cursor.execute(
					"""
					SELECT session_id, title, pinned, created_at, updated_at
					FROM vidhoor_chat_sessions
					WHERE user_id = :user_id
					ORDER BY pinned DESC, updated_at DESC
					FETCH FIRST :limit ROWS ONLY
					""",
					{"user_id": user_id, "limit": limit},
				)
				rows = cursor.fetchall()

		result: list[dict[str, Any]] = []
		for session_id, title, pinned, created_at, updated_at in rows:
			result.append(
				{
					"session_id": session_id,
					"title": title,
					"pinned": bool(pinned),
					"created_at": _iso(created_at),
					"updated_at": _iso(updated_at),
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
		"""Update title and/or pin state of a user-owned session."""
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

		updates.append("updated_at = SYSTIMESTAMP")

		with self._connect() as connection:
			with connection.cursor() as cursor:
				cursor.execute(
					f"""
					UPDATE vidhoor_chat_sessions
					SET {', '.join(updates)}
					WHERE session_id = :session_id AND user_id = :user_id
					""",
					params,
				)
				updated = cursor.rowcount > 0
			connection.commit()

		return updated

	def delete_session(self, user_id: str, session_id: str) -> bool:
		"""Delete a user-owned session and all its messages."""
		with self._connect() as connection:
			with connection.cursor() as cursor:
				cursor.execute(
					"""
					DELETE FROM vidhoor_chat_messages
					WHERE session_id = :session_id AND user_id = :user_id
					""",
					{"session_id": session_id, "user_id": user_id},
				)
				cursor.execute(
					"""
					DELETE FROM vidhoor_chat_sessions
					WHERE session_id = :session_id AND user_id = :user_id
					""",
					{"session_id": session_id, "user_id": user_id},
				)
				deleted = cursor.rowcount > 0
			connection.commit()

		return deleted

	def get_session_messages(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
		"""Return ordered messages for a user-owned session."""
		with self._connect() as connection:
			with connection.cursor() as cursor:
				cursor.execute(
					"""
					SELECT 1
					FROM vidhoor_chat_sessions
					WHERE session_id = :session_id AND user_id = :user_id
					""",
					{"session_id": session_id, "user_id": user_id},
				)
				if cursor.fetchone() is None:
					return []

				cursor.execute(
					"""
					SELECT role, content, created_at, masked_entities, citations_json, follow_ups_json, overall_confidence
					FROM vidhoor_chat_messages
					WHERE session_id = :session_id AND user_id = :user_id
					ORDER BY created_at ASC, message_id ASC
					""",
					{"session_id": session_id, "user_id": user_id},
				)
				rows = cursor.fetchall()

				messages: list[dict[str, Any]] = []
				last_user_message = ""
				for role, content, created_at, masked_entities, citations_json, follow_ups_json, overall_confidence in rows:
					content_text = content.read() if hasattr(content, "read") else str(content)
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
							"created_at": _iso(created_at),
							"masked_entities": _decode_json(masked_entities),
							"citations": decoded_citations,
							"follow_ups": decoded_follow_ups,
							"overall_confidence": float(overall_confidence) if overall_confidence is not None else None,
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
		"""Persist encrypted evidence payload and anonymized OCR/legal text."""

		with self._connect() as connection:
			with connection.cursor() as cursor:
				cursor.execute(
					"""
					INSERT INTO vidhoor_user_evidence (
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
						created_at,
						updated_at
					) VALUES (
						:evidence_id,
						:user_id,
						:session_id,
						:file_name,
						:file_extension,
						:encryption_alg,
						:key_id,
						:iv_b64,
						:encrypted_payload_b64,
						:masked_summary,
						:masked_analysis,
						SYSTIMESTAMP,
						SYSTIMESTAMP
					)
					""",
					{
						"evidence_id": evidence_id,
						"user_id": user_id,
						"session_id": session_id,
						"file_name": file_name,
						"file_extension": file_extension,
						"encryption_alg": encryption_alg,
						"key_id": key_id,
						"iv_b64": iv_b64,
						"encrypted_payload_b64": encrypted_payload_b64,
						"masked_summary": masked_summary,
						"masked_analysis": masked_analysis,
					},
				)
			connection.commit()

	def list_user_evidence(
		self,
		user_id: str,
		limit: int = 100,
		session_id: str | None = None,
	) -> list[dict[str, Any]]:
		"""Return encrypted evidence metadata for a user, optionally filtered by session."""
		with self._connect() as connection:
			with connection.cursor() as cursor:
				cursor.execute(
					"""
					SELECT evidence_id, file_name, file_extension, encryption_alg, key_id, session_id, created_at
					FROM vidhoor_user_evidence
					WHERE user_id = :user_id
					  AND (:session_id IS NULL OR session_id = :session_id)
					ORDER BY created_at DESC
					FETCH FIRST :limit ROWS ONLY
					""",
					{"user_id": user_id, "limit": limit, "session_id": session_id},
				)
				rows = cursor.fetchall()

		result: list[dict[str, Any]] = []
		for evidence_id, file_name, file_extension, encryption_alg, key_id, session_id, created_at in rows:
			result.append(
				{
					"evidence_id": str(evidence_id),
					"file_name": str(file_name),
					"file_extension": str(file_extension or ""),
					"encryption_alg": str(encryption_alg or ""),
					"key_id": str(key_id or ""),
					"session_id": str(session_id or ""),
					"created_at": _iso(created_at),
				}
			)
		return result

	def get_evidence_payload(self, user_id: str, evidence_id: str) -> dict[str, Any] | None:
		"""Return one encrypted evidence payload owned by the user."""
		with self._connect() as connection:
			with connection.cursor() as cursor:
				cursor.execute(
					"""
					SELECT evidence_id, file_name, file_extension, encryption_alg, key_id,
					       iv_b64, encrypted_payload_b64, masked_summary, masked_analysis,
					       session_id, created_at
					FROM vidhoor_user_evidence
					WHERE user_id = :user_id AND evidence_id = :evidence_id
					""",
					{"user_id": user_id, "evidence_id": evidence_id},
				)
				row = cursor.fetchone()

		if not row:
			return None

		(
			evidence_id,
			file_name,
			file_extension,
			encryption_alg,
			key_id,
			iv_b64,
			encrypted_payload_b64,
			masked_summary,
			masked_analysis,
			session_id,
			created_at,
		) = row

		def _lob_to_text(value: Any) -> str:
			return value.read() if hasattr(value, "read") else str(value or "")

		return {
			"evidence_id": str(evidence_id),
			"file_name": str(file_name),
			"file_extension": str(file_extension or ""),
			"encryption_alg": str(encryption_alg or ""),
			"key_id": str(key_id or ""),
			"iv_b64": _lob_to_text(iv_b64),
			"encrypted_payload_b64": _lob_to_text(encrypted_payload_b64),
			"masked_summary": _lob_to_text(masked_summary),
			"masked_analysis": _lob_to_text(masked_analysis),
			"session_id": str(session_id or ""),
			"created_at": _iso(created_at),
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
		"""Persist one generated legal draft for a user."""
		draft_meta_json = json.dumps(draft_meta or {}, ensure_ascii=False)

		with self._connect() as connection:
			with connection.cursor() as cursor:
				cursor.execute(
					"""
					INSERT INTO vidhoor_user_drafts (
						draft_id,
						user_id,
						email_id,
						session_id,
						application_type,
						title,
						draft_content,
						draft_meta_json,
						delivery_status,
						created_at,
						updated_at
					) VALUES (
						:draft_id,
						:user_id,
						:email_id,
						:session_id,
						:application_type,
						:title,
						:draft_content,
						:draft_meta_json,
						'generated',
						SYSTIMESTAMP,
						SYSTIMESTAMP
					)
					""",
					{
						"draft_id": draft_id,
						"user_id": user_id,
						"email_id": email_id,
						"session_id": session_id,
						"application_type": application_type,
						"title": title,
						"draft_content": draft_content,
						"draft_meta_json": draft_meta_json,
					},
				)
			connection.commit()

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
		"""Persist one feedback submission record for analytics and follow-up."""
		with self._connect() as connection:
			with connection.cursor() as cursor:
				cursor.execute(
					"""
					INSERT INTO vidhoor_user_feedback (
						feedback_id,
						user_id,
						user_email,
						message,
						allow_follow_up,
						page_url,
						user_agent,
						app_version,
						context,
						status,
						created_at,
						updated_at
					) VALUES (
						:feedback_id,
						:user_id,
						:user_email,
						:message,
						:allow_follow_up,
						:page_url,
						:user_agent,
						:app_version,
						:context,
						'new',
						SYSTIMESTAMP,
						SYSTIMESTAMP
					)
					""",
					{
						"feedback_id": feedback_id,
						"user_id": user_id,
						"user_email": user_email,
						"message": message,
						"allow_follow_up": 1 if allow_follow_up else 0,
						"page_url": (page_url or "")[:1000],
						"user_agent": (user_agent or "")[:2000],
						"app_version": (app_version or "")[:64],
						"context": (context or "")[:256],
					},
				)
			connection.commit()

	def get_user_draft(self, user_id: str, draft_id: str) -> dict[str, Any] | None:
		"""Return one generated draft owned by the user."""
		with self._connect() as connection:
			with connection.cursor() as cursor:
				cursor.execute(
					"""
					SELECT draft_id, user_id, email_id, session_id, application_type,
					       title, draft_content, draft_meta_json, delivery_status,
					       last_delivery_error, emailed_at, created_at, updated_at
					FROM vidhoor_user_drafts
					WHERE user_id = :user_id AND draft_id = :draft_id
					""",
					{"user_id": user_id, "draft_id": draft_id},
				)
				row = cursor.fetchone()

		if not row:
			return None

		(
			draft_id,
			user_id,
			email_id,
			session_id,
			application_type,
			title,
			draft_content,
			draft_meta_json,
			delivery_status,
			last_delivery_error,
			emailed_at,
			created_at,
			updated_at,
		) = row

		content = draft_content.read() if hasattr(draft_content, "read") else str(draft_content or "")
		draft_meta = _decode_json(draft_meta_json)

		return {
			"draft_id": str(draft_id),
			"user_id": str(user_id),
			"email_id": str(email_id or ""),
			"session_id": str(session_id or ""),
			"application_type": str(application_type or ""),
			"title": str(title or ""),
			"draft_content": content,
			"draft_meta": draft_meta,
			"delivery_status": str(delivery_status or "generated"),
			"last_delivery_error": str(last_delivery_error or ""),
			"emailed_at": _iso(emailed_at),
			"created_at": _iso(created_at),
			"updated_at": _iso(updated_at),
		}

	def list_user_drafts(
		self,
		user_id: str,
		limit: int = 100,
		session_id: str | None = None,
	) -> list[dict[str, Any]]:
		"""Return draft history for a user, optionally scoped to a session."""
		with self._connect() as connection:
			with connection.cursor() as cursor:
				cursor.execute(
					"""
					SELECT draft_id, user_id, email_id, session_id, application_type,
					       title, draft_content, draft_meta_json, delivery_status,
					       last_delivery_error, emailed_at, created_at, updated_at
					FROM vidhoor_user_drafts
					WHERE user_id = :user_id
					  AND (:session_id IS NULL OR session_id = :session_id)
					ORDER BY created_at DESC
					FETCH FIRST :limit ROWS ONLY
					""",
					{"user_id": user_id, "session_id": session_id, "limit": limit},
				)
				rows = cursor.fetchall()

		result: list[dict[str, Any]] = []
		for (
			draft_id,
			row_user_id,
			email_id,
			session_id_value,
			application_type,
			title,
			draft_content,
			draft_meta_json,
			delivery_status,
			last_delivery_error,
			emailed_at,
			created_at,
			updated_at,
		) in rows:
			content = draft_content.read() if hasattr(draft_content, "read") else str(draft_content or "")
			result.append(
				{
					"draft_id": str(draft_id),
					"user_id": str(row_user_id),
					"email_id": str(email_id or ""),
					"session_id": str(session_id_value or ""),
					"application_type": str(application_type or ""),
					"title": str(title or ""),
					"draft_content": content,
					"draft_meta": _decode_json(draft_meta_json),
					"delivery_status": str(delivery_status or "generated"),
					"last_delivery_error": str(last_delivery_error or ""),
					"emailed_at": _iso(emailed_at),
					"created_at": _iso(created_at),
					"updated_at": _iso(updated_at),
				}
			)

		return result

	def update_user_draft(
		self,
		user_id: str,
		draft_id: str,
		title: str | None = None,
		draft_content: str | None = None,
	) -> bool:
		"""Update draft title/content for a user-owned draft."""
		updates = ["updated_at = SYSTIMESTAMP"]
		params: dict[str, Any] = {
			"user_id": user_id,
			"draft_id": draft_id,
		}

		if title is not None:
			updates.append("title = :title")
			params["title"] = str(title).strip()[:512]

		if draft_content is not None:
			updates.append("draft_content = :draft_content")
			params["draft_content"] = str(draft_content)

		if len(updates) == 1:
			return False

		with self._connect() as connection:
			with connection.cursor() as cursor:
				cursor.execute(
					f"""
					UPDATE vidhoor_user_drafts
					SET {', '.join(updates)}
					WHERE user_id = :user_id AND draft_id = :draft_id
					""",
					params,
				)
				updated = cursor.rowcount > 0
			connection.commit()

		return updated

	def mark_draft_delivery(
		self,
		user_id: str,
		draft_id: str,
		delivery_status: str,
		last_delivery_error: str | None = None,
		emailed_at: bool = False,
	) -> bool:
		"""Update delivery status for a generated draft."""
		updates = ["delivery_status = :delivery_status", "updated_at = SYSTIMESTAMP"]
		params: dict[str, Any] = {
			"user_id": user_id,
			"draft_id": draft_id,
			"delivery_status": delivery_status,
			"last_delivery_error": (last_delivery_error or "")[:900],
		}

		updates.append("last_delivery_error = :last_delivery_error")
		if emailed_at:
			updates.append("emailed_at = SYSTIMESTAMP")

		with self._connect() as connection:
			with connection.cursor() as cursor:
				cursor.execute(
					f"""
					UPDATE vidhoor_user_drafts
					SET {', '.join(updates)}
					WHERE user_id = :user_id AND draft_id = :draft_id
					""",
					params,
				)
				updated = cursor.rowcount > 0
			connection.commit()

		return updated


def _decode_json(value: Any) -> dict[str, str]:
	"""Safely decode JSON CLOB/string values."""
	if value is None:
		return {}

	text_value = value.read() if hasattr(value, "read") else str(value)
	try:
		parsed = json.loads(text_value)
		return parsed if isinstance(parsed, dict) else {}
	except Exception:
		return {}


def _decode_json_list(value: Any) -> list[dict[str, Any]]:
	"""Safely decode JSON CLOB/string values as a list of dict items."""
	if value is None:
		return []

	text_value = value.read() if hasattr(value, "read") else str(value)
	try:
		parsed = json.loads(text_value)
		if isinstance(parsed, list):
			return [item for item in parsed if isinstance(item, dict)]
		return []
	except Exception:
		return []


def _decode_json_string_list(value: Any) -> list[str]:
	"""Safely decode JSON CLOB/string values as a list of non-empty strings."""
	if value is None:
		return []

	text_value = value.read() if hasattr(value, "read") else str(value)
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
	"""Backfill citation-like resources from markdown links in legacy assistant messages."""
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
	"""Build fallback follow-up questions for legacy assistant messages."""
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


def _iso(value: Any) -> str:
	"""Convert Oracle datetime/timestamp values into ISO strings."""
	if value is None:
		return ""
	if isinstance(value, datetime):
		return value.isoformat()
	return str(value)


def _load_environment() -> None:
	"""Load environment variables from backend/.env when available."""
	try:
		from dotenv import load_dotenv
	except Exception:
		return

	env_path = Path(__file__).resolve().parent / ".env"
	if env_path.exists():
		load_dotenv(dotenv_path=env_path, override=False)


class OracleChunkRepository:
	"""Repository for persistent legal chunks used by BM25 retrieval."""

	def __init__(self) -> None:
		_load_environment()
		self._base_dir = Path(__file__).resolve().parent

		self.user = os.environ.get("ORACLE_USER")
		self.password = os.environ.get("ORACLE_PASSWORD")
		self.dsn = os.environ.get("ORACLE_DSN")
		self.config_dir = self._resolve_optional_path(os.environ.get("ORACLE_CONFIG_DIR"))
		self.wallet_location = self._resolve_optional_path(os.environ.get("ORACLE_WALLET_LOCATION"))
		self.wallet_password = os.environ.get("ORACLE_WALLET_PASSWORD")

		if not self.user or not self.password or not self.dsn:
			raise EnvironmentError(
				"Missing Oracle DB configuration. Required: ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN"
			)

	def _resolve_optional_path(self, value: str | None) -> str | None:
		"""Resolve relative env paths from backend directory."""
		if not value:
			return None

		raw = Path(value.strip().strip('"').strip("'"))
		if raw.is_absolute():
			return str(raw)
		return str((self._base_dir / raw).resolve())

	def _connect(self) -> oracledb.Connection:
		"""Create an Oracle connection with optional wallet configuration."""
		connect_kwargs: dict[str, Any] = {
			"user": self.user,
			"password": self.password,
			"dsn": self.dsn,
		}

		if self.config_dir:
			connect_kwargs["config_dir"] = self.config_dir
		if self.wallet_location:
			connect_kwargs["wallet_location"] = self.wallet_location
		if self.wallet_password:
			connect_kwargs["wallet_password"] = self.wallet_password

		return oracledb.connect(**connect_kwargs)

	def initialize_schema(self) -> None:
		"""Create persistent chunk table and supporting indexes when absent."""
		ddl_statements = [
			"""
			CREATE TABLE vidhoor_legal_chunks (
				chunk_id VARCHAR2(128) PRIMARY KEY,
				chunk_text CLOB NOT NULL,
				status VARCHAR2(64),
				act VARCHAR2(256),
				source VARCHAR2(512),
				section_ref VARCHAR2(64),
				metadata_json CLOB,
				created_at TIMESTAMP DEFAULT SYSTIMESTAMP,
				updated_at TIMESTAMP DEFAULT SYSTIMESTAMP
			)
			""",
			"""
			CREATE INDEX idx_vidhoor_legal_chunks_status
			ON vidhoor_legal_chunks(status)
			""",
			"""
			CREATE INDEX idx_vidhoor_legal_chunks_act
			ON vidhoor_legal_chunks(act)
			""",
		]

		with self._connect() as connection:
			with connection.cursor() as cursor:
				for statement in ddl_statements:
					try:
						cursor.execute(statement)
					except oracledb.DatabaseError as exc:
						error_obj = exc.args[0]
						if getattr(error_obj, "code", None) == 955:
							continue
						raise
			connection.commit()

	def upsert_chunks(self, chunks: list[dict[str, Any]]) -> int:
		"""Insert or update legal chunks for BM25 reconstruction."""
		if not chunks:
			return 0

		with self._connect() as connection:
			with connection.cursor() as cursor:
				for chunk in chunks:
					cursor.execute(
						"""
						MERGE INTO vidhoor_legal_chunks target
						USING (
							SELECT
								:chunk_id AS chunk_id,
								:chunk_text AS chunk_text,
								:status AS status,
								:act AS act,
								:source AS source,
								:section_ref AS section_ref,
								:metadata_json AS metadata_json
							FROM dual
						) source
						ON (target.chunk_id = source.chunk_id)
						WHEN MATCHED THEN
							UPDATE SET
								target.chunk_text = source.chunk_text,
								target.status = source.status,
								target.act = source.act,
								target.source = source.source,
								target.section_ref = source.section_ref,
								target.metadata_json = source.metadata_json,
								target.updated_at = SYSTIMESTAMP
						WHEN NOT MATCHED THEN
							INSERT (
								chunk_id,
								chunk_text,
								status,
								act,
								source,
								section_ref,
								metadata_json,
								created_at,
								updated_at
							)
							VALUES (
								source.chunk_id,
								source.chunk_text,
								source.status,
								source.act,
								source.source,
								source.section_ref,
								source.metadata_json,
								SYSTIMESTAMP,
								SYSTIMESTAMP
							)
						""",
						{
							"chunk_id": str(chunk.get("chunk_id") or "").strip(),
							"chunk_text": str(chunk.get("chunk_text") or ""),
							"status": str(chunk.get("status") or "active"),
							"act": str(chunk.get("act") or ""),
							"source": str(chunk.get("source") or ""),
							"section_ref": str(chunk.get("section_ref") or ""),
							"metadata_json": str(chunk.get("metadata_json") or "{}"),
						},
					)
			connection.commit()

		return len(chunks)

	def load_chunks(
		self,
		filter_status: str | None = "active",
		filter_act: str | None = None,
	) -> list[dict[str, Any]]:
		"""Load chunks from Oracle for BM25 index warmup/rebuild."""
		query = """
			SELECT chunk_id, chunk_text, status, act, source, section_ref, metadata_json, updated_at
			FROM vidhoor_legal_chunks
			WHERE (:filter_status IS NULL OR status = :filter_status)
			  AND (:filter_act IS NULL OR act = :filter_act)
			ORDER BY updated_at DESC
		"""

		chunks: list[dict[str, Any]] = []
		with self._connect() as connection:
			with connection.cursor() as cursor:
				cursor.execute(
					query,
					{
						"filter_status": filter_status,
						"filter_act": filter_act,
					},
				)
				rows = cursor.fetchall()

				for (
					chunk_id,
					chunk_text,
					status,
					act,
					source,
					section_ref,
					metadata_json,
					updated_at,
				) in rows:
					meta_raw = str(metadata_json or "{}")
					try:
						meta = json.loads(meta_raw)
					except Exception:
						meta = {}

					chunk_text_value = str(chunk_text or "")

					chunks.append(
						{
							"chunk_id": str(chunk_id),
							"chunk_text": chunk_text_value,
							"status": str(status or ""),
							"act": str(act or ""),
							"source": str(source or ""),
							"section_ref": str(section_ref or ""),
					"metadata": meta,
						"last_updated": _iso(updated_at),
					}
				)

		return chunks


_chat_repo = None


def get_chat_repo():
    """Return a chat history repository, preferring Oracle when configured, else SQLite."""
    global _chat_repo
    if _chat_repo is not None:
        return _chat_repo

    user = os.environ.get("ORACLE_USER")
    password = os.environ.get("ORACLE_PASSWORD")
    dsn = os.environ.get("ORACLE_DSN")
    if user and password and dsn:
        _chat_repo = OracleChatHistoryRepository()
    else:
        from sqlite_chat_repo import SQLiteChatHistoryRepository
        _chat_repo = SQLiteChatHistoryRepository()
    _chat_repo.initialize_schema()
    return _chat_repo
