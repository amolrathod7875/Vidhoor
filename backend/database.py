"""Oracle Autonomous Database utilities for Vidhoor chat history."""

from __future__ import annotations

import json
import os
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
			connection.commit()

	def save_chat_turn(
		self,
		user_id: str,
		session_id: str,
		user_message: str,
		assistant_message: str,
		masked_entities: dict[str, str],
		session_title: str | None = None,
	) -> None:
		"""Persist one user/assistant message turn in Oracle."""
		resolved_title = (session_title or "").strip() or "New Chat"
		if len(resolved_title) > 120:
			resolved_title = resolved_title[:120]
		masked_entities_json = json.dumps(masked_entities, ensure_ascii=False)

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
						created_at
					) VALUES (
						:session_id,
						:user_id,
						'user',
						:content,
						:masked_entities,
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
						created_at
					) VALUES (
						:session_id,
						:user_id,
						'assistant',
						:content,
						:masked_entities,
						SYSTIMESTAMP
					)
					""",
					{
						"session_id": session_id,
						"user_id": user_id,
						"content": assistant_message,
						"masked_entities": masked_entities_json,
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
					SELECT role, content, created_at, masked_entities
					FROM vidhoor_chat_messages
					WHERE session_id = :session_id AND user_id = :user_id
					ORDER BY created_at ASC, message_id ASC
					""",
					{"session_id": session_id, "user_id": user_id},
				)
				rows = cursor.fetchall()

				messages: list[dict[str, Any]] = []
				for role, content, created_at, masked_entities in rows:
					messages.append(
						{
							"role": role,
							"content": content.read() if hasattr(content, "read") else str(content),
							"created_at": _iso(created_at),
							"masked_entities": _decode_json(masked_entities),
						}
					)
				return messages


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
