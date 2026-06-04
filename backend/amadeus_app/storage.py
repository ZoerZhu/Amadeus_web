from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg


DEFAULT_USER_ID = "local-user"


def _decode_jsonb(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, dict):
        return value
    return {}


def _isoformat(value: datetime) -> str:
    return value.isoformat()


def _serialize_conversation(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "personaId": row["persona_id"],
        "mode": row["mode"],
        "createdAt": _isoformat(row["created_at"]),
        "updatedAt": _isoformat(row["updated_at"]),
    }


def _serialize_message(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "role": row["role"],
        "content": row["content"],
        "thinking": row["thinking"],
        "createdAt": _isoformat(row["created_at"]),
    }


class PostgresStorage:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url.strip()
        self.pool: asyncpg.Pool | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.database_url)

    @property
    def connected(self) -> bool:
        return self.pool is not None

    async def connect(self) -> None:
        if not self.enabled:
            return
        self.pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5)
        await self.init_schema()

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    def require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("PostgreSQL storage is not configured. Set DATABASE_URL or AMADEUS_DATABASE_URL.")
        return self.pool

    async def init_schema(self) -> None:
        pool = self.require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    user_id text PRIMARY KEY,
                    model_settings jsonb NOT NULL DEFAULT '{}'::jsonb,
                    voice_settings jsonb NOT NULL DEFAULT '{}'::jsonb,
                    mode text NOT NULL DEFAULT 'fast' CHECK (mode IN ('fast', 'thinking')),
                    updated_at timestamptz NOT NULL DEFAULT now()
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    id uuid PRIMARY KEY,
                    user_id text NOT NULL,
                    persona_id text NOT NULL,
                    title text NOT NULL,
                    mode text NOT NULL DEFAULT 'fast' CHECK (mode IN ('fast', 'thinking')),
                    created_at timestamptz NOT NULL DEFAULT now(),
                    updated_at timestamptz NOT NULL DEFAULT now()
                );

                CREATE INDEX IF NOT EXISTS conversations_user_updated_idx
                    ON conversations (user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS chat_messages (
                    id uuid PRIMARY KEY,
                    conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role text NOT NULL CHECK (role IN ('user', 'assistant')),
                    content text NOT NULL,
                    thinking text NOT NULL DEFAULT '',
                    position integer NOT NULL,
                    created_at timestamptz NOT NULL DEFAULT now(),
                    UNIQUE (conversation_id, position)
                );

                CREATE INDEX IF NOT EXISTS chat_messages_conversation_position_idx
                    ON chat_messages (conversation_id, position ASC);
                """
            )

    async def get_settings(self, user_id: str) -> dict[str, Any] | None:
        pool = self.require_pool()
        row = await pool.fetchrow(
            """
            SELECT model_settings, voice_settings, mode, updated_at
            FROM app_settings
            WHERE user_id = $1
            """,
            user_id,
        )
        if row is None:
            return None
        return {
            "model": _decode_jsonb(row["model_settings"]),
            "voice": _decode_jsonb(row["voice_settings"]),
            "mode": row["mode"],
            "updatedAt": _isoformat(row["updated_at"]),
        }

    async def save_settings(
        self,
        *,
        user_id: str,
        model: dict[str, Any],
        voice: dict[str, Any],
        mode: str,
    ) -> None:
        pool = self.require_pool()
        await pool.execute(
            """
            INSERT INTO app_settings (user_id, model_settings, voice_settings, mode, updated_at)
            VALUES ($1, $2::jsonb, $3::jsonb, $4, now())
            ON CONFLICT (user_id) DO UPDATE SET
                model_settings = EXCLUDED.model_settings,
                voice_settings = EXCLUDED.voice_settings,
                mode = EXCLUDED.mode,
                updated_at = now()
            """,
            user_id,
            json.dumps(model, ensure_ascii=False),
            json.dumps(voice, ensure_ascii=False),
            mode,
        )

    async def list_conversations(self, *, user_id: str, limit: int = 30) -> list[dict[str, Any]]:
        pool = self.require_pool()
        rows = await pool.fetch(
            """
            SELECT id, title, persona_id, mode, created_at, updated_at
            FROM conversations
            WHERE user_id = $1
            ORDER BY updated_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
        return [_serialize_conversation(row) for row in rows]

    async def create_conversation(
        self,
        *,
        user_id: str,
        title: str,
        persona_id: str,
        mode: str,
    ) -> dict[str, Any]:
        pool = self.require_pool()
        conversation_id = uuid4()
        clean_title = title.strip()[:80] or "新对话"
        row = await pool.fetchrow(
            """
            INSERT INTO conversations (id, user_id, title, persona_id, mode)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, title, persona_id, mode, created_at, updated_at
            """,
            conversation_id,
            user_id,
            clean_title,
            persona_id,
            mode,
        )
        if row is None:
            raise RuntimeError("Failed to create conversation")
        return _serialize_conversation(row)

    async def get_conversation(self, *, user_id: str, conversation_id: UUID) -> dict[str, Any] | None:
        pool = self.require_pool()
        async with pool.acquire() as conn:
            conversation = await conn.fetchrow(
                """
                SELECT id, title, persona_id, mode, created_at, updated_at
                FROM conversations
                WHERE id = $1 AND user_id = $2
                """,
                conversation_id,
                user_id,
            )
            if conversation is None:
                return None
            messages = await conn.fetch(
                """
                SELECT id, role, content, thinking, created_at
                FROM chat_messages
                WHERE conversation_id = $1
                ORDER BY position ASC
                """,
                conversation_id,
            )
        payload = _serialize_conversation(conversation)
        payload["messages"] = [_serialize_message(row) for row in messages]
        return payload

    async def delete_conversation(self, *, user_id: str, conversation_id: UUID) -> bool:
        pool = self.require_pool()
        row = await pool.fetchrow(
            """
            DELETE FROM conversations
            WHERE id = $1 AND user_id = $2
            RETURNING id
            """,
            conversation_id,
            user_id,
        )
        return row is not None

    async def add_message(
        self,
        *,
        user_id: str,
        conversation_id: UUID,
        role: str,
        content: str,
        thinking: str = "",
    ) -> dict[str, Any]:
        pool = self.require_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                conversation = await conn.fetchrow(
                    """
                    SELECT id
                    FROM conversations
                    WHERE id = $1 AND user_id = $2
                    FOR UPDATE
                    """,
                    conversation_id,
                    user_id,
                )
                if conversation is None:
                    raise ValueError("Conversation not found")
                position = await conn.fetchval(
                    """
                    SELECT COALESCE(MAX(position), -1) + 1
                    FROM chat_messages
                    WHERE conversation_id = $1
                    """,
                    conversation_id,
                )
                row = await conn.fetchrow(
                    """
                    INSERT INTO chat_messages (id, conversation_id, role, content, thinking, position)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING id, role, content, thinking, created_at
                    """,
                    uuid4(),
                    conversation_id,
                    role,
                    content,
                    thinking,
                    position,
                )
                await conn.execute(
                    """
                    UPDATE conversations
                    SET updated_at = now()
                    WHERE id = $1
                    """,
                    conversation_id,
                )
        if row is None:
            raise RuntimeError("Failed to store message")
        return _serialize_message(row)
