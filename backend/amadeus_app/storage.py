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


def _mcp_server_key(server: dict[str, Any]) -> str:
    name = str(server.get("name") or "").strip().lower()
    if name:
        return f"name:{name}"
    if server.get("transport") == "http":
        return f"http:{str(server.get('url') or '').strip().lower()}"
    args = "\0".join(str(arg).strip() for arg in server.get("args", []) if str(arg).strip())
    return f"stdio:{str(server.get('command') or '').strip().lower()}:{args}"


def _clean_mcp_servers(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        server_id = str(item.get("id") or "")
        if not server_id or server_id.startswith("draft-"):
            continue
        key = _mcp_server_key(item)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
    return cleaned


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
        if str(self.database_path) != ":memory:":
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

    async def run_in_thread(self, func, /, *args, **kwargs):
        """Run a blocking callable against the SQLite connection in a worker thread.

        The callable receives the connection as its first positional argument
        when called without extra args; otherwise it is called with ``*args``.
        Acquires the storage lock to serialize writes.
        """
        async with self._lock:
            conn = self.require_connection()
            if args or kwargs:
                return await asyncio.to_thread(func, *args, **kwargs)
            return await asyncio.to_thread(func, conn)

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
                desktop_assistant_settings TEXT NOT NULL DEFAULT '{}',
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

            -- ===== Memory System Tables =====

            CREATE TABLE IF NOT EXISTS memory_nodes (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                parent_id       TEXT REFERENCES memory_nodes(id) ON DELETE CASCADE,
                node_type       TEXT NOT NULL
                                CHECK (node_type IN ('root', 'domain', 'topic', 'cluster', 'leaf')),
                domain          TEXT NOT NULL DEFAULT '',
                label           TEXT NOT NULL,
                path            TEXT NOT NULL DEFAULT '',
                depth           INTEGER NOT NULL DEFAULT 0,
                summary         TEXT NOT NULL DEFAULT '',
                full_content    TEXT NOT NULL DEFAULT '',
                category        TEXT NOT NULL DEFAULT 'general'
                                CHECK (category IN ('general','fact','decision','preference','summary','technical','task_state','tool_result')),
                keywords        TEXT NOT NULL DEFAULT '[]',
                project_id      TEXT,
                conversation_id TEXT,
                source_message_ids TEXT NOT NULL DEFAULT '[]',
                source_summary  TEXT NOT NULL DEFAULT '',
                importance      REAL NOT NULL DEFAULT 0.5,
                confidence      REAL NOT NULL DEFAULT 0.7,
                access_count    INTEGER NOT NULL DEFAULT 0,
                leaf_count      INTEGER NOT NULL DEFAULT 0,
                last_accessed_at TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                expires_at      TEXT,
                is_active       INTEGER NOT NULL DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS memory_nodes_parent_idx
                ON memory_nodes (user_id, parent_id, is_active);
            CREATE INDEX IF NOT EXISTS memory_nodes_domain_idx
                ON memory_nodes (user_id, domain, node_type, is_active);
            CREATE INDEX IF NOT EXISTS memory_nodes_project_idx
                ON memory_nodes (project_id, is_active) WHERE project_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS memory_nodes_path_idx
                ON memory_nodes (user_id, path);
            CREATE INDEX IF NOT EXISTS memory_nodes_updated_idx
                ON memory_nodes (user_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS memory_edges (
                parent_id       TEXT NOT NULL REFERENCES memory_nodes(id) ON DELETE CASCADE,
                child_id        TEXT NOT NULL REFERENCES memory_nodes(id) ON DELETE CASCADE,
                relation        TEXT NOT NULL DEFAULT 'contains'
                                CHECK (relation IN ('contains','references','supersedes','conflicts_with')),
                weight          REAL NOT NULL DEFAULT 1.0,
                created_at      TEXT NOT NULL,
                PRIMARY KEY (parent_id, child_id, relation)
            );

            CREATE TABLE IF NOT EXISTS memory_embedding_meta (
                node_id         TEXT PRIMARY KEY REFERENCES memory_nodes(id) ON DELETE CASCADE,
                vector_kind     TEXT NOT NULL CHECK (vector_kind IN ('node','leaf')),
                embedding_model TEXT NOT NULL,
                embedding_dim   INTEGER NOT NULL,
                content_hash    TEXT NOT NULL DEFAULT '',
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS projects (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                name        TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                workspace_path TEXT NOT NULL DEFAULT '',
                color       TEXT NOT NULL DEFAULT '#8b5cf6',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS projects_user_idx
                ON projects (user_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS conversation_projects (
                conversation_id TEXT PRIMARY KEY
                                REFERENCES conversations(id) ON DELETE CASCADE,
                project_id      TEXT NOT NULL
                                REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS memory_jobs (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                conversation_id TEXT,
                job_type        TEXT NOT NULL
                                CHECK (job_type IN ('ingest', 'summarize_node', 'reembed', 'merge', 'cleanup')),
                status          TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'running', 'done', 'failed')),
                payload         TEXT NOT NULL DEFAULT '{}',
                attempts        INTEGER NOT NULL DEFAULT 0,
                last_error      TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS memory_jobs_status_idx
                ON memory_jobs (status, updated_at ASC);

            -- ===== Legacy Agent task tables (read-only compatibility) =====

            CREATE TABLE IF NOT EXISTS agent_tasks (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                title           TEXT NOT NULL DEFAULT '',
                prompt          TEXT NOT NULL DEFAULT '',
                workspace_path  TEXT NOT NULL DEFAULT '',
                conversation_id TEXT,
                status          TEXT NOT NULL DEFAULT 'created'
                                CHECK (status IN ('created','running','paused','cancelled','done','failed','rolled_back')),
                active_skill_ids TEXT NOT NULL DEFAULT '[]',
                settings_json   TEXT NOT NULL DEFAULT '{}',
                budget_json     TEXT NOT NULL DEFAULT '{}',
                error           TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                finished_at     TEXT
            );

            CREATE INDEX IF NOT EXISTS agent_tasks_user_updated_idx
                ON agent_tasks (user_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS agent_tasks_conversation_idx
                ON agent_tasks (conversation_id) WHERE conversation_id IS NOT NULL;

            CREATE TABLE IF NOT EXISTS agent_task_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id         TEXT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
                seq             INTEGER NOT NULL,
                kind            TEXT NOT NULL
                                CHECK (kind IN ('message','status','plan','step','tool','mcp','browser','artifact','question','sampling','opencode_routing','error','done','agent_thought_summary','tool_call','tool_result','command','working_set')),
                role            TEXT NOT NULL DEFAULT '',
                name            TEXT NOT NULL DEFAULT '',
                status          TEXT NOT NULL DEFAULT '',
                summary         TEXT NOT NULL DEFAULT '',
                payload         TEXT NOT NULL DEFAULT '{}',
                artifact_ids    TEXT NOT NULL DEFAULT '[]',
                created_at      TEXT NOT NULL,
                UNIQUE (task_id, seq)
            );

            CREATE INDEX IF NOT EXISTS agent_task_events_task_seq_idx
                ON agent_task_events (task_id, seq ASC);

            CREATE TABLE IF NOT EXISTS agent_artifacts (
                id              TEXT PRIMARY KEY,
                task_id         TEXT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
                kind            TEXT NOT NULL DEFAULT 'file'
                                CHECK (kind IN ('file','screenshot','document','log','diff','snapshot')),
                name            TEXT NOT NULL,
                path            TEXT NOT NULL,
                mime_type       TEXT NOT NULL DEFAULT 'application/octet-stream',
                size_bytes      INTEGER NOT NULL DEFAULT 0,
                description     TEXT NOT NULL DEFAULT '',
                meta_json       TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS agent_artifacts_task_idx
                ON agent_artifacts (task_id, created_at ASC);

            -- ===== MCP / Skills integration tables =====

            CREATE TABLE IF NOT EXISTS mcp_servers (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                name            TEXT NOT NULL,
                enabled         INTEGER NOT NULL DEFAULT 1,
                transport       TEXT NOT NULL DEFAULT 'stdio'
                                CHECK (transport IN ('stdio','http')),
                command         TEXT NOT NULL DEFAULT '',
                args_json       TEXT NOT NULL DEFAULT '[]',
                cwd             TEXT NOT NULL DEFAULT '',
                env_json        TEXT NOT NULL DEFAULT '{}',
                url             TEXT NOT NULL DEFAULT '',
                headers_json    TEXT NOT NULL DEFAULT '{}',
                auth_type       TEXT NOT NULL DEFAULT 'none'
                                CHECK (auth_type IN ('none','bearer','api_key')),
                auth_token      TEXT NOT NULL DEFAULT '',
                allowed_tools_json   TEXT NOT NULL DEFAULT '[]',
                allowed_resources_json TEXT NOT NULL DEFAULT '[]',
                trusted         INTEGER NOT NULL DEFAULT 0,
                timeout_seconds INTEGER NOT NULL DEFAULT 30,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS mcp_servers_user_idx
                ON mcp_servers (user_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS skill_packages (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                name            TEXT NOT NULL,
                version         TEXT NOT NULL DEFAULT '0.1.0',
                enabled         INTEGER NOT NULL DEFAULT 1,
                source          TEXT NOT NULL DEFAULT 'local'
                                CHECK (source IN ('zip','git','local')),
                path            TEXT NOT NULL,
                description     TEXT NOT NULL DEFAULT '',
                triggers_json   TEXT NOT NULL DEFAULT '[]',
                required_mcp_servers_json TEXT NOT NULL DEFAULT '[]',
                tool_allowlist_json TEXT NOT NULL DEFAULT '[]',
                roles_json      TEXT NOT NULL DEFAULT '[]',
                budgets_json    TEXT NOT NULL DEFAULT '{}',
                manifest_json   TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS skill_packages_user_idx
                ON skill_packages (user_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS agent_file_snapshots (
                id              TEXT PRIMARY KEY,
                task_id         TEXT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
                path            TEXT NOT NULL,
                backup_path     TEXT NOT NULL,
                size_bytes      INTEGER NOT NULL DEFAULT 0,
                sha256          TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS agent_file_snapshots_task_idx
                ON agent_file_snapshots (task_id, created_at ASC);
            CREATE INDEX IF NOT EXISTS agent_file_snapshots_path_idx
                ON agent_file_snapshots (task_id, path);

            CREATE TABLE IF NOT EXISTS agent_permission_requests (
                id              TEXT PRIMARY KEY,
                task_id         TEXT NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
                tool_name       TEXT NOT NULL,
                arguments_preview TEXT NOT NULL DEFAULT '',
                risk_level      TEXT NOT NULL DEFAULT 'confirm'
                                CHECK (risk_level IN ('safe', 'confirm', 'dangerous')),
                status          TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'approved', 'rejected', 'timeout')),
                reason          TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL,
                resolved_at     TEXT
            );

            CREATE INDEX IF NOT EXISTS agent_permission_requests_task_idx
                ON agent_permission_requests (task_id, status, created_at);

            -- ===== Orchestrator v2 Tables =====

            CREATE TABLE IF NOT EXISTS orchestrator_tasks (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                title           TEXT NOT NULL DEFAULT '',
                prompt          TEXT NOT NULL DEFAULT '',
                workspace_path  TEXT NOT NULL DEFAULT '',
                conversation_id TEXT,
                status          TEXT NOT NULL DEFAULT 'created'
                                CHECK (status IN ('created','running','paused','cancelled','done','failed','rolled_back')),
                active_skill_ids TEXT NOT NULL DEFAULT '[]',
                settings_json   TEXT NOT NULL DEFAULT '{}',
                context_json    TEXT NOT NULL DEFAULT '{}',
                budget_json     TEXT NOT NULL DEFAULT '{}',
                error           TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                finished_at     TEXT
            );

            CREATE INDEX IF NOT EXISTS orchestrator_tasks_user_updated_idx
                ON orchestrator_tasks (user_id, updated_at DESC);
            CREATE INDEX IF NOT EXISTS orchestrator_tasks_conversation_idx
                ON orchestrator_tasks (conversation_id) WHERE conversation_id IS NOT NULL;

            CREATE TABLE IF NOT EXISTS orchestrator_ledger_events (
                task_id         TEXT NOT NULL REFERENCES orchestrator_tasks(id) ON DELETE CASCADE,
                seq             INTEGER NOT NULL,
                kind            TEXT NOT NULL
                                CHECK (kind IN ('message','status','plan','step','tool','mcp','browser','artifact','question','sampling','opencode_routing','error','done','agent_thought_summary','tool_call','tool_result','command','working_set')),
                role            TEXT NOT NULL DEFAULT '',
                name            TEXT NOT NULL DEFAULT '',
                status          TEXT NOT NULL DEFAULT '',
                summary         TEXT NOT NULL DEFAULT '',
                payload_json    TEXT NOT NULL DEFAULT '{}',
                artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                created_at      TEXT NOT NULL,
                PRIMARY KEY (task_id, seq)
            );

            CREATE INDEX IF NOT EXISTS orchestrator_ledger_task_seq_idx
                ON orchestrator_ledger_events (task_id, seq ASC);

            CREATE TABLE IF NOT EXISTS orchestrator_artifacts (
                id              TEXT PRIMARY KEY,
                task_id         TEXT NOT NULL REFERENCES orchestrator_tasks(id) ON DELETE CASCADE,
                kind            TEXT NOT NULL DEFAULT 'file'
                                CHECK (kind IN ('file','screenshot','document','log','diff','snapshot')),
                name            TEXT NOT NULL,
                path            TEXT NOT NULL,
                mime_type       TEXT NOT NULL DEFAULT 'application/octet-stream',
                size_bytes      INTEGER NOT NULL DEFAULT 0,
                description     TEXT NOT NULL DEFAULT '',
                meta_json       TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS orchestrator_artifacts_task_idx
                ON orchestrator_artifacts (task_id, created_at ASC);

            CREATE TABLE IF NOT EXISTS orchestrator_permission_requests (
                id              TEXT PRIMARY KEY,
                task_id         TEXT NOT NULL REFERENCES orchestrator_tasks(id) ON DELETE CASCADE,
                tool_name       TEXT NOT NULL,
                arguments_preview TEXT NOT NULL DEFAULT '',
                risk_level      TEXT NOT NULL DEFAULT 'confirm'
                                CHECK (risk_level IN ('safe', 'confirm', 'dangerous')),
                status          TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'approved', 'rejected', 'timeout')),
                reason          TEXT NOT NULL DEFAULT '',
                payload_json    TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT NOT NULL,
                resolved_at     TEXT
            );

            CREATE INDEX IF NOT EXISTS orchestrator_permission_requests_task_idx
                ON orchestrator_permission_requests (task_id, status, created_at ASC);
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
        if "desktop_assistant_settings" not in app_settings_columns:
            conn.execute("ALTER TABLE app_settings ADD COLUMN desktop_assistant_settings TEXT NOT NULL DEFAULT '{}'")
        if "agent_settings" not in app_settings_columns:
            conn.execute("ALTER TABLE app_settings ADD COLUMN agent_settings TEXT NOT NULL DEFAULT '{}'")
        if "mcp_servers_json" not in app_settings_columns:
            conn.execute("ALTER TABLE app_settings ADD COLUMN mcp_servers_json TEXT NOT NULL DEFAULT '[]'")
        chat_message_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()
        }
        if "assistant_voice" not in chat_message_columns:
            conn.execute("ALTER TABLE chat_messages ADD COLUMN assistant_voice TEXT NOT NULL DEFAULT '{}'")
        orchestrator_permission_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(orchestrator_permission_requests)").fetchall()
        }
        if "payload_json" not in orchestrator_permission_columns:
            conn.execute(
                "ALTER TABLE orchestrator_permission_requests ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'"
            )
        ledger_schema = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'orchestrator_ledger_events'
            """
        ).fetchone()
        if ledger_schema and "'message'" not in str(ledger_schema["sql"] or ""):
            conn.executescript(
                """
                ALTER TABLE orchestrator_ledger_events RENAME TO orchestrator_ledger_events_old;

                CREATE TABLE orchestrator_ledger_events (
                    task_id         TEXT NOT NULL REFERENCES orchestrator_tasks(id) ON DELETE CASCADE,
                    seq             INTEGER NOT NULL,
                    kind            TEXT NOT NULL
                                    CHECK (kind IN ('message','status','plan','step','tool','mcp','browser','artifact','question','sampling','opencode_routing','error','done','agent_thought_summary','tool_call','tool_result','command','working_set')),
                    role            TEXT NOT NULL DEFAULT '',
                    name            TEXT NOT NULL DEFAULT '',
                    status          TEXT NOT NULL DEFAULT '',
                    summary         TEXT NOT NULL DEFAULT '',
                    payload_json    TEXT NOT NULL DEFAULT '{}',
                    artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at      TEXT NOT NULL,
                    PRIMARY KEY (task_id, seq)
                );

                INSERT INTO orchestrator_ledger_events
                (task_id, seq, kind, role, name, status, summary, payload_json, artifact_ids_json, created_at)
                SELECT task_id, seq, kind, role, name, status, summary, payload_json, artifact_ids_json, created_at
                FROM orchestrator_ledger_events_old;

                DROP TABLE orchestrator_ledger_events_old;

                CREATE INDEX IF NOT EXISTS orchestrator_ledger_task_seq_idx
                    ON orchestrator_ledger_events (task_id, seq ASC);
                """
            )

        # ----- Memory: FTS5 + sqlite-vec -----
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                USING fts5(
                    node_id UNINDEXED,
                    node_type UNINDEXED,
                    path,
                    label,
                    summary,
                    full_content,
                    keywords,
                    tokenize = 'unicode61'
                )
            """
        )
        try:
            from .memory.vector_store import ensure_vec_tables, load_vec_extension

            load_vec_extension(conn)
            ensure_vec_tables(conn)
        except Exception:
            import traceback

            traceback.print_exc()
            # sqlite-vec 不可用时记忆系统降级为纯 FTS

    async def get_settings(self, user_id: str) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_settings_sync, user_id)

    def _get_settings_sync(self, user_id: str) -> dict[str, Any] | None:
        conn = self.require_connection()
        row = conn.execute(
            """
            SELECT model_settings, vision_settings, speech_input_settings, voice_settings, desktop_assistant_settings, agent_settings, mcp_servers_json, mode, updated_at
            FROM app_settings
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        orchestrator_settings = _decode_json(row["agent_settings"]) if "agent_settings" in row.keys() else {}
        return {
            "model": _decode_json(row["model_settings"]),
            "vision": _decode_json(row["vision_settings"]),
            "speechInput": _decode_json(row["speech_input_settings"]),
            "voice": _decode_json(row["voice_settings"]),
            "desktopAssistant": _decode_json(row["desktop_assistant_settings"]),
            "orchestrator": orchestrator_settings,
            "agent": orchestrator_settings,
            "mcpServers": _clean_mcp_servers(_decode_json(row["mcp_servers_json"]) if "mcp_servers_json" in row.keys() else []),
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
        desktop_assistant: dict[str, Any],
        mode: str,
        orchestrator: dict[str, Any] | None = None,
        agent: dict[str, Any] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._save_settings_sync,
                user_id,
                model,
                vision,
                speech_input,
                voice,
                desktop_assistant,
                mode,
                orchestrator,
                agent,
                mcp_servers,
            )

    def _save_settings_sync(
        self,
        user_id: str,
        model: dict[str, Any],
        vision: dict[str, Any],
        speech_input: dict[str, Any],
        voice: dict[str, Any],
        desktop_assistant: dict[str, Any],
        mode: str,
        orchestrator: dict[str, Any] | None,
        agent: dict[str, Any] | None,
        mcp_servers: list[dict[str, Any]] | None,
    ) -> None:
        conn = self.require_connection()
        # The physical column is still named agent_settings for migration
        # compatibility; new callers should pass orchestrator.
        existing = conn.execute(
            "SELECT agent_settings, mcp_servers_json FROM app_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        existing_agent = _decode_json(existing["agent_settings"]) if existing else {}
        existing_mcp = _clean_mcp_servers(_decode_json(existing["mcp_servers_json"]) if existing else [])
        agent_value = orchestrator if orchestrator is not None else agent if agent is not None else existing_agent
        mcp_value = _clean_mcp_servers(mcp_servers) if mcp_servers is not None else existing_mcp
        conn.execute(
            """
            INSERT INTO app_settings (user_id, model_settings, vision_settings, speech_input_settings, voice_settings, desktop_assistant_settings, agent_settings, mcp_servers_json, mode, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                model_settings = excluded.model_settings,
                vision_settings = excluded.vision_settings,
                speech_input_settings = excluded.speech_input_settings,
                voice_settings = excluded.voice_settings,
                desktop_assistant_settings = excluded.desktop_assistant_settings,
                agent_settings = excluded.agent_settings,
                mcp_servers_json = excluded.mcp_servers_json,
                mode = excluded.mode,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                json.dumps(model, ensure_ascii=False),
                json.dumps(vision, ensure_ascii=False),
                json.dumps(speech_input, ensure_ascii=False),
                json.dumps(voice, ensure_ascii=False),
                json.dumps(desktop_assistant, ensure_ascii=False),
                json.dumps(agent_value, ensure_ascii=False),
                json.dumps(mcp_value, ensure_ascii=False),
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
