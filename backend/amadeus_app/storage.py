from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4


DEFAULT_USER_ID = "local-user"


def _decode_json(value: Any) -> dict[str, Any]:
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    if isinstance(value, dict):
        return value
    return {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_conversation(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "personaId": row["persona_id"],
        "mode": row["mode"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _serialize_message(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "role": row["role"],
        "content": row["content"],
        "thinking": row["thinking"],
        "assistantVoice": _decode_json(row["assistant_voice"]),
        "createdAt": row["created_at"],
    }


class SQLiteStorage:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser()
        self.connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return True

    @property
    def connected(self) -> bool:
        return self.connection is not None

    async def connect(self) -> None:
        if self.connection is not None:
            return
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.database_path,
            check_same_thread=False,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        self.connection = connection
        await self.init_schema()

    async def close(self) -> None:
        if self.connection is None:
            return
        async with self._lock:
            connection = self.connection
            self.connection = None
            await asyncio.to_thread(connection.close)

    def require_connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("SQLite storage is not connected.")
        return self.connection

    async def init_schema(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._init_schema_sync)

    def _init_schema_sync(self) -> None:
        conn = self.require_connection()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                user_id TEXT PRIMARY KEY,
                model_settings TEXT NOT NULL DEFAULT '{}',
                vision_settings TEXT NOT NULL DEFAULT '{}',
                speech_input_settings TEXT NOT NULL DEFAULT '{}',
                voice_settings TEXT NOT NULL DEFAULT '{}',
                mode TEXT NOT NULL DEFAULT 'fast' CHECK (mode IN ('fast', 'thinking')),
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                persona_id TEXT NOT NULL,
                title TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'fast' CHECK (mode IN ('fast', 'thinking')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS conversations_user_updated_idx
                ON conversations (user_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                thinking TEXT NOT NULL DEFAULT '',
                assistant_voice TEXT NOT NULL DEFAULT '{}',
                position INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (conversation_id, position)
            );

            CREATE INDEX IF NOT EXISTS chat_messages_conversation_position_idx
                ON chat_messages (conversation_id, position ASC);
            """
        )
        app_settings_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(app_settings)").fetchall()
        }
        if "vision_settings" not in app_settings_columns:
            conn.execute("ALTER TABLE app_settings ADD COLUMN vision_settings TEXT NOT NULL DEFAULT '{}'")
        if "speech_input_settings" not in app_settings_columns:
            conn.execute("ALTER TABLE app_settings ADD COLUMN speech_input_settings TEXT NOT NULL DEFAULT '{}'")
        chat_message_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()
        }
        if "assistant_voice" not in chat_message_columns:
            conn.execute("ALTER TABLE chat_messages ADD COLUMN assistant_voice TEXT NOT NULL DEFAULT '{}'")

    async def get_settings(self, user_id: str) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_settings_sync, user_id)

    def _get_settings_sync(self, user_id: str) -> dict[str, Any] | None:
        conn = self.require_connection()
        row = conn.execute(
            """
            SELECT model_settings, vision_settings, speech_input_settings, voice_settings, mode, updated_at
            FROM app_settings
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "model": _decode_json(row["model_settings"]),
            "vision": _decode_json(row["vision_settings"]),
            "speechInput": _decode_json(row["speech_input_settings"]),
            "voice": _decode_json(row["voice_settings"]),
            "mode": row["mode"],
            "updatedAt": row["updated_at"],
        }

    async def save_settings(
        self,
        *,
        user_id: str,
        model: dict[str, Any],
        vision: dict[str, Any],
        speech_input: dict[str, Any],
        voice: dict[str, Any],
        mode: str,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_settings_sync, user_id, model, vision, speech_input, voice, mode)

    def _save_settings_sync(
        self,
        user_id: str,
        model: dict[str, Any],
        vision: dict[str, Any],
        speech_input: dict[str, Any],
        voice: dict[str, Any],
        mode: str,
    ) -> None:
        conn = self.require_connection()
        conn.execute(
            """
            INSERT INTO app_settings (user_id, model_settings, vision_settings, speech_input_settings, voice_settings, mode, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                model_settings = excluded.model_settings,
                vision_settings = excluded.vision_settings,
                speech_input_settings = excluded.speech_input_settings,
                voice_settings = excluded.voice_settings,
                mode = excluded.mode,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                json.dumps(model, ensure_ascii=False),
                json.dumps(vision, ensure_ascii=False),
                json.dumps(speech_input, ensure_ascii=False),
                json.dumps(voice, ensure_ascii=False),
                mode,
                _now_iso(),
            ),
        )

    async def list_conversations(self, *, user_id: str, limit: int = 30) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._list_conversations_sync, user_id, limit)

    def _list_conversations_sync(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        conn = self.require_connection()
        rows = conn.execute(
            """
            SELECT id, title, persona_id, mode, created_at, updated_at
            FROM conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [_serialize_conversation(row) for row in rows]

    async def create_conversation(
        self,
        *,
        user_id: str,
        title: str,
        persona_id: str,
        mode: str,
    ) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(self._create_conversation_sync, user_id, title, persona_id, mode)

    def _create_conversation_sync(
        self,
        user_id: str,
        title: str,
        persona_id: str,
        mode: str,
    ) -> dict[str, Any]:
        conn = self.require_connection()
        conversation_id = str(uuid4())
        clean_title = title.strip()[:80] or "新对话"
        now = _now_iso()
        conn.execute(
            """
            INSERT INTO conversations (id, user_id, title, persona_id, mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, user_id, clean_title, persona_id, mode, now, now),
        )
        row = conn.execute(
            """
            SELECT id, title, persona_id, mode, created_at, updated_at
            FROM conversations
            WHERE id = ?
            """,
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Failed to create conversation")
        return _serialize_conversation(row)

    async def get_conversation(self, *, user_id: str, conversation_id: UUID) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_conversation_sync, user_id, str(conversation_id))

    def _get_conversation_sync(self, user_id: str, conversation_id: str) -> dict[str, Any] | None:
        conn = self.require_connection()
        conversation = conn.execute(
            """
            SELECT id, title, persona_id, mode, created_at, updated_at
            FROM conversations
            WHERE id = ? AND user_id = ?
            """,
            (conversation_id, user_id),
        ).fetchone()
        if conversation is None:
            return None
        messages = conn.execute(
            """
            SELECT id, role, content, thinking, assistant_voice, created_at
            FROM chat_messages
            WHERE conversation_id = ?
            ORDER BY position ASC
            """,
            (conversation_id,),
        ).fetchall()
        payload = _serialize_conversation(conversation)
        payload["messages"] = [_serialize_message(row) for row in messages]
        return payload

    async def delete_conversation(self, *, user_id: str, conversation_id: UUID) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_conversation_sync, user_id, str(conversation_id))

    def _delete_conversation_sync(self, user_id: str, conversation_id: str) -> bool:
        conn = self.require_connection()
        cursor = conn.execute(
            """
            DELETE FROM conversations
            WHERE id = ? AND user_id = ?
            """,
            (conversation_id, user_id),
        )
        return cursor.rowcount > 0

    async def add_message(
        self,
        *,
        user_id: str,
        conversation_id: UUID,
        role: str,
        content: str,
        thinking: str = "",
        assistant_voice: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(
                self._add_message_sync,
                user_id,
                str(conversation_id),
                role,
                content,
                thinking,
                assistant_voice or {},
            )

    def _add_message_sync(
        self,
        user_id: str,
        conversation_id: str,
        role: str,
        content: str,
        thinking: str,
        assistant_voice: dict[str, Any],
    ) -> dict[str, Any]:
        conn = self.require_connection()
        message_id = str(uuid4())
        now = _now_iso()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conversation = conn.execute(
                """
                SELECT id
                FROM conversations
                WHERE id = ? AND user_id = ?
                """,
                (conversation_id, user_id),
            ).fetchone()
            if conversation is None:
                raise ValueError("Conversation not found")
            position = conn.execute(
                """
                SELECT COALESCE(MAX(position), -1) + 1
                FROM chat_messages
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO chat_messages (id, conversation_id, role, content, thinking, assistant_voice, position, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    conversation_id,
                    role,
                    content,
                    thinking,
                    json.dumps(assistant_voice if role == "assistant" else {}, ensure_ascii=False),
                    position,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE conversations
                SET updated_at = ?
                WHERE id = ?
                """,
                (now, conversation_id),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        row = conn.execute(
            """
            SELECT id, role, content, thinking, assistant_voice, created_at
            FROM chat_messages
            WHERE id = ?
            """,
            (message_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Failed to store message")
        return _serialize_message(row)

    async def update_assistant_message_voice(
        self,
        *,
        user_id: str,
        conversation_id: UUID,
        message_id: UUID,
        assistant_voice: dict[str, Any],
    ) -> bool:
        async with self._lock:
            return await asyncio.to_thread(
                self._update_assistant_message_voice_sync,
                user_id,
                str(conversation_id),
                str(message_id),
                assistant_voice,
            )

    def _update_assistant_message_voice_sync(
        self,
        user_id: str,
        conversation_id: str,
        message_id: str,
        assistant_voice: dict[str, Any],
    ) -> bool:
        conn = self.require_connection()
        now = _now_iso()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute(
                """
                UPDATE chat_messages
                SET assistant_voice = ?
                WHERE id = ?
                  AND conversation_id = ?
                  AND role = 'assistant'
                  AND EXISTS (
                    SELECT 1
                    FROM conversations
                    WHERE conversations.id = chat_messages.conversation_id
                      AND conversations.user_id = ?
                  )
                """,
                (json.dumps(assistant_voice, ensure_ascii=False), message_id, conversation_id, user_id),
            )
            if cursor.rowcount:
                conn.execute(
                    """
                    UPDATE conversations
                    SET updated_at = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (now, conversation_id, user_id),
                )
            conn.execute("COMMIT")
            return cursor.rowcount > 0
        except Exception:
            conn.execute("ROLLBACK")
            raise
