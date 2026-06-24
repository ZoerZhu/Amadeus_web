"""SQLite-backed persistence for complex agent tasks, artifacts,
MCP servers, skill packages and file snapshots.

All methods are async and delegate blocking work to a thread, mirroring
the pattern used by :class:`amadeus_app.storage.SQLiteStorage`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..storage import SQLiteStorage, _decode_json


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Agent tasks
# ---------------------------------------------------------------------------


async def create_agent_task(
    storage: SQLiteStorage,
    *,
    user_id: str,
    title: str,
    prompt: str,
    workspace_path: str,
    conversation_id: str | None,
    active_skill_ids: list[str],
    settings: dict[str, Any],
    budget: dict[str, Any],
) -> dict[str, Any]:
    task_id = uuid4().hex
    now = _now_iso()
    row = {
        "id": task_id,
        "user_id": user_id,
        "title": title,
        "prompt": prompt,
        "workspace_path": workspace_path,
        "conversation_id": conversation_id,
        "status": "created",
        "active_skill_ids": _json_dumps(active_skill_ids),
        "settings_json": _json_dumps(settings),
        "budget_json": _json_dumps(budget),
        "error": "",
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
    }

    def _exec(conn) -> None:
        conn.execute(
            """
            INSERT INTO agent_tasks
            (id, user_id, title, prompt, workspace_path, conversation_id, status,
             active_skill_ids, settings_json, budget_json, error, created_at, updated_at, finished_at)
            VALUES (:id, :user_id, :title, :prompt, :workspace_path, :conversation_id, :status,
                    :active_skill_ids, :settings_json, :budget_json, :error, :created_at, :updated_at, :finished_at)
            """,
            row,
        )

    await storage.run_in_thread(_exec)
    return await get_agent_task(storage, task_id) or row  # type: ignore[return-value]


async def get_agent_task(storage: SQLiteStorage, task_id: str) -> dict[str, Any] | None:
    def _exec(conn) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM agent_tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        return _serialize_task_row(row) if row else None

    return await storage.run_in_thread(_exec)


async def list_agent_tasks(
    storage: SQLiteStorage,
    *,
    user_id: str | None = None,
    conversation_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    def _exec(conn) -> list[dict[str, Any]]:
        if conversation_id:
            rows = conn.execute(
                "SELECT * FROM agent_tasks WHERE conversation_id = ? ORDER BY updated_at DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        elif user_id:
            rows = conn.execute(
                "SELECT * FROM agent_tasks WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_tasks ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_serialize_task_row(row) for row in rows]

    return await storage.run_in_thread(_exec)


async def update_agent_task_status(
    storage: SQLiteStorage,
    task_id: str,
    *,
    status: str,
    error: str | None = None,
    finished: bool = False,
    budget: dict[str, Any] | None = None,
) -> None:
    now = _now_iso()
    finished_at = now if finished else None

    def _exec(conn) -> None:
        if budget is not None:
            conn.execute(
                """
                UPDATE agent_tasks
                SET status = ?, error = COALESCE(?, error), budget_json = ?,
                    updated_at = ?, finished_at = COALESCE(?, finished_at)
                WHERE id = ?
                """,
                (status, error, _json_dumps(budget), now, finished_at, task_id),
            )
        else:
            conn.execute(
                """
                UPDATE agent_tasks
                SET status = ?, error = COALESCE(?, error), updated_at = ?,
                    finished_at = COALESCE(?, finished_at)
                WHERE id = ?
                """,
                (status, error, now, finished_at, task_id),
            )

    await storage.run_in_thread(_exec)


async def append_agent_task_event(
    storage: SQLiteStorage,
    *,
    task_id: str,
    seq: int,
    kind: str,
    role: str = "",
    name: str = "",
    status: str = "",
    summary: str = "",
    payload: dict[str, Any] | None = None,
    artifact_ids: list[str] | None = None,
) -> dict[str, Any]:
    now = _now_iso()
    record = {
        "task_id": task_id,
        "seq": seq,
        "kind": kind,
        "role": role,
        "name": name,
        "status": status,
        "summary": summary,
        "payload": _json_dumps(payload or {}),
        "artifact_ids": _json_dumps(artifact_ids or []),
        "created_at": now,
    }

    def _exec(conn) -> None:
        conn.execute(
            """
            INSERT INTO agent_task_events
            (task_id, seq, kind, role, name, status, summary, payload, artifact_ids, created_at)
            VALUES (:task_id, :seq, :kind, :role, :name, :status, :summary, :payload, :artifact_ids, :created_at)
            """,
            record,
        )
        conn.execute(
            "UPDATE agent_tasks SET updated_at = ? WHERE id = ?",
            (now, task_id),
        )

    await storage.run_in_thread(_exec)
    return {
        "taskId": task_id,
        "seq": seq,
        "kind": kind,
        "role": role,
        "name": name,
        "status": status,
        "summary": summary,
        "payload": payload or {},
        "artifactIds": artifact_ids or [],
        "timestamp": now,
    }


async def list_agent_task_events(
    storage: SQLiteStorage,
    task_id: str,
    *,
    after_seq: int = -1,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    def _exec(conn) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT * FROM agent_task_events
            WHERE task_id = ? AND seq > ?
            ORDER BY seq ASC
            LIMIT ?
            """,
            (task_id, after_seq, limit),
        ).fetchall()
        return [_serialize_event_row(row) for row in rows]

    return await storage.run_in_thread(_exec)


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


async def create_artifact(
    storage: SQLiteStorage,
    *,
    task_id: str,
    kind: str,
    name: str,
    path: str,
    mime_type: str = "application/octet-stream",
    size_bytes: int = 0,
    description: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact_id = uuid4().hex
    now = _now_iso()
    record = {
        "id": artifact_id,
        "task_id": task_id,
        "kind": kind,
        "name": name,
        "path": path,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "description": description,
        "meta_json": _json_dumps(meta or {}),
        "created_at": now,
    }

    def _exec(conn) -> None:
        conn.execute(
            """
            INSERT INTO agent_artifacts
            (id, task_id, kind, name, path, mime_type, size_bytes, description, meta_json, created_at)
            VALUES (:id, :task_id, :kind, :name, :path, :mime_type, :size_bytes, :description, :meta_json, :created_at)
            """,
            record,
        )

    await storage.run_in_thread(_exec)
    return {
        "id": artifact_id,
        "taskId": task_id,
        "kind": kind,
        "name": name,
        "path": path,
        "mimeType": mime_type,
        "sizeBytes": size_bytes,
        "description": description,
        "meta": meta or {},
        "createdAt": now,
    }


async def get_artifact(storage: SQLiteStorage, artifact_id: str) -> dict[str, Any] | None:
    def _exec(conn) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM agent_artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        return _serialize_artifact_row(row) if row else None

    return await storage.run_in_thread(_exec)


async def list_artifacts(storage: SQLiteStorage, task_id: str) -> list[dict[str, Any]]:
    def _exec(conn) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM agent_artifacts WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        return [_serialize_artifact_row(row) for row in rows]

    return await storage.run_in_thread(_exec)


# ---------------------------------------------------------------------------
# File snapshots (for rollback)
# ---------------------------------------------------------------------------


async def create_file_snapshot(
    storage: SQLiteStorage,
    *,
    task_id: str,
    path: str,
    backup_dir: Path,
) -> dict[str, Any]:
    """Copy ``path`` to ``backup_dir`` and record a snapshot row.

    If the source file does not exist, a sentinel snapshot is recorded
    so rollback can delete the file that was created by the task.
    """
    source = Path(path)
    snapshot_id = uuid4().hex
    now = _now_iso()
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{snapshot_id}_{source.name}"
    size_bytes = 0
    sha256 = ""
    if source.exists() and source.is_file():
        shutil.copy2(source, backup_path)
        size_bytes = source.stat().st_size
        sha256 = _hash_file(source)
        backup_path_str = str(backup_path)
    else:
        # Sentinel: file did not exist before the task wrote it.
        backup_path_str = ""
    record = {
        "id": snapshot_id,
        "task_id": task_id,
        "path": str(path),
        "backup_path": backup_path_str,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "created_at": now,
    }

    def _exec(conn) -> None:
        conn.execute(
            """
            INSERT INTO agent_file_snapshots
            (id, task_id, path, backup_path, size_bytes, sha256, created_at)
            VALUES (:id, :task_id, :path, :backup_path, :size_bytes, :sha256, :created_at)
            """,
            record,
        )

    await storage.run_in_thread(_exec)
    return {
        "id": snapshot_id,
        "taskId": task_id,
        "path": str(path),
        "backupPath": backup_path_str,
        "sizeBytes": size_bytes,
        "sha256": sha256,
        "createdAt": now,
    }


async def list_file_snapshots(storage: SQLiteStorage, task_id: str) -> list[dict[str, Any]]:
    def _exec(conn) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM agent_file_snapshots WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "taskId": row["task_id"],
                "path": row["path"],
                "backupPath": row["backup_path"],
                "sizeBytes": row["size_bytes"],
                "sha256": row["sha256"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    return await storage.run_in_thread(_exec)


async def rollback_file_snapshots(storage: SQLiteStorage, task_id: str) -> dict[str, Any]:
    """Restore all files snapshotted for ``task_id`` in reverse order."""
    snapshots = await list_file_snapshots(storage, task_id)
    restored = 0
    deleted = 0
    failed: list[str] = []
    for snap in reversed(snapshots):
        target = Path(snap["path"])
        backup = Path(snap["backupPath"]) if snap["backupPath"] else None
        try:
            if backup and backup.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, target)
                restored += 1
            else:
                # Sentinel: file was created by the task — remove it.
                if target.exists():
                    target.unlink()
                    deleted += 1
        except Exception as error:  # noqa: BLE001
            failed.append(f"{snap['path']}: {error}")
    await update_agent_task_status(storage, task_id, status="rolled_back", finished=True)
    return {"restored": restored, "deleted": deleted, "failed": failed}


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------


async def list_mcp_servers(storage: SQLiteStorage, user_id: str) -> list[dict[str, Any]]:
    def _exec(conn) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM mcp_servers WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [_serialize_mcp_row(row) for row in rows]

    return await storage.run_in_thread(_exec)


async def upsert_mcp_server(storage: SQLiteStorage, *, user_id: str, server: dict[str, Any]) -> dict[str, Any]:
    server_id = server.get("id") or uuid4().hex
    now = _now_iso()
    record = {
        "id": server_id,
        "user_id": user_id,
        "name": server.get("name", ""),
        "enabled": 1 if server.get("enabled", True) else 0,
        "transport": server.get("transport", "stdio"),
        "command": server.get("command", ""),
        "args_json": _json_dumps(server.get("args", [])),
        "cwd": server.get("cwd", ""),
        "env_json": _json_dumps(server.get("env", {})),
        "url": server.get("url", ""),
        "headers_json": _json_dumps(server.get("headers", {})),
        "auth_type": server.get("authType", server.get("auth_type", "none")),
        "auth_token": server.get("authToken", server.get("auth_token", "")),
        "allowed_tools_json": _json_dumps(server.get("allowedTools", server.get("allowed_tools", []))),
        "allowed_resources_json": _json_dumps(server.get("allowedResources", server.get("allowed_resources", []))),
        "trusted": 1 if server.get("trusted", False) else 0,
        "timeout_seconds": int(server.get("timeoutSeconds", server.get("timeout_seconds", 30))),
        "created_at": now,
        "updated_at": now,
    }

    def _exec(conn) -> None:
        conn.execute(
            """
            INSERT INTO mcp_servers
            (id, user_id, name, enabled, transport, command, args_json, cwd, env_json, url,
             headers_json, auth_type, auth_token, allowed_tools_json, allowed_resources_json,
             trusted, timeout_seconds, created_at, updated_at)
            VALUES (:id, :user_id, :name, :enabled, :transport, :command, :args_json, :cwd, :env_json, :url,
                    :headers_json, :auth_type, :auth_token, :allowed_tools_json, :allowed_resources_json,
                    :trusted, :timeout_seconds, :created_at, :updated_at)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name, enabled = excluded.enabled, transport = excluded.transport,
                command = excluded.command, args_json = excluded.args_json, cwd = excluded.cwd,
                env_json = excluded.env_json, url = excluded.url, headers_json = excluded.headers_json,
                auth_type = excluded.auth_type, auth_token = excluded.auth_token,
                allowed_tools_json = excluded.allowed_tools_json,
                allowed_resources_json = excluded.allowed_resources_json,
                trusted = excluded.trusted, timeout_seconds = excluded.timeout_seconds,
                updated_at = excluded.updated_at
            """,
            record,
        )

    await storage.run_in_thread(_exec)
    servers = await list_mcp_servers(storage, user_id)
    return next((s for s in servers if s["id"] == server_id), _serialize_mcp_record(record))


async def delete_mcp_server(storage: SQLiteStorage, server_id: str) -> bool:
    def _exec(conn) -> int:
        cursor = conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
        return cursor.rowcount

    rowcount = await storage.run_in_thread(_exec)
    return rowcount > 0


# ---------------------------------------------------------------------------
# Skill packages
# ---------------------------------------------------------------------------


async def list_skill_packages(storage: SQLiteStorage, user_id: str) -> list[dict[str, Any]]:
    def _exec(conn) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM skill_packages WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [_serialize_skill_row(row) for row in rows]

    return await storage.run_in_thread(_exec)


async def get_skill_package(storage: SQLiteStorage, skill_id: str) -> dict[str, Any] | None:
    def _exec(conn) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM skill_packages WHERE id = ?",
            (skill_id,),
        ).fetchone()
        return _serialize_skill_row(row) if row else None

    return await storage.run_in_thread(_exec)


async def upsert_skill_package(storage: SQLiteStorage, *, user_id: str, skill: dict[str, Any]) -> dict[str, Any]:
    skill_id = skill.get("id") or uuid4().hex
    now = _now_iso()
    record = {
        "id": skill_id,
        "user_id": user_id,
        "name": skill.get("name", ""),
        "version": skill.get("version", "0.1.0"),
        "enabled": 1 if skill.get("enabled", True) else 0,
        "source": skill.get("source", "local"),
        "path": skill.get("path", ""),
        "description": skill.get("description", ""),
        "triggers_json": _json_dumps(skill.get("triggers", [])),
        "required_mcp_servers_json": _json_dumps(skill.get("requiredMcpServers", skill.get("required_mcp_servers", []))),
        "tool_allowlist_json": _json_dumps(skill.get("toolAllowlist", skill.get("tool_allowlist", []))),
        "roles_json": _json_dumps(skill.get("roles", [])),
        "budgets_json": _json_dumps(skill.get("budgets", {})),
        "manifest_json": _json_dumps(skill.get("manifest", {})),
        "created_at": now,
        "updated_at": now,
    }

    def _exec(conn) -> None:
        conn.execute(
            """
            INSERT INTO skill_packages
            (id, user_id, name, version, enabled, source, path, description,
             triggers_json, required_mcp_servers_json, tool_allowlist_json, roles_json,
             budgets_json, manifest_json, created_at, updated_at)
            VALUES (:id, :user_id, :name, :version, :enabled, :source, :path, :description,
                    :triggers_json, :required_mcp_servers_json, :tool_allowlist_json, :roles_json,
                    :budgets_json, :manifest_json, :created_at, :updated_at)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name, version = excluded.version, enabled = excluded.enabled,
                source = excluded.source, path = excluded.path, description = excluded.description,
                triggers_json = excluded.triggers_json,
                required_mcp_servers_json = excluded.required_mcp_servers_json,
                tool_allowlist_json = excluded.tool_allowlist_json,
                roles_json = excluded.roles_json, budgets_json = excluded.budgets_json,
                manifest_json = excluded.manifest_json, updated_at = excluded.updated_at
            """,
            record,
        )

    await storage.run_in_thread(_exec)
    result = await get_skill_package(storage, skill_id)
    return result or _serialize_skill_record(record)


async def delete_skill_package(storage: SQLiteStorage, skill_id: str) -> bool:
    def _exec(conn) -> int:
        cursor = conn.execute("DELETE FROM skill_packages WHERE id = ?", (skill_id,))
        return cursor.rowcount

    rowcount = await storage.run_in_thread(_exec)
    return rowcount > 0


# ---------------------------------------------------------------------------
# Permission requests
# ---------------------------------------------------------------------------


async def create_permission_request(
    storage: SQLiteStorage,
    *,
    task_id: str,
    tool_name: str,
    arguments_preview: str,
    risk_level: str = "confirm",
) -> dict[str, Any]:
    """Insert a new pending permission request and return the serialized record."""
    perm_id = str(uuid4())
    now = _now_iso()
    record = {
        "id": perm_id,
        "task_id": task_id,
        "tool_name": tool_name,
        "arguments_preview": arguments_preview[:2000],
        "risk_level": risk_level,
        "status": "pending",
        "reason": "",
        "created_at": now,
        "resolved_at": None,
    }

    def _exec(conn) -> None:
        conn.execute(
            """
            INSERT INTO agent_permission_requests
                (id, task_id, tool_name, arguments_preview, risk_level,
                 status, reason, created_at, resolved_at)
            VALUES
                (:id, :task_id, :tool_name, :arguments_preview, :risk_level,
                 :status, :reason, :created_at, :resolved_at)
            """,
            record,
        )

    await storage.run_in_thread(_exec)
    return _serialize_permission_row(record)


async def get_permission_request(storage: SQLiteStorage, perm_id: str) -> dict[str, Any] | None:
    def _exec(conn):
        return conn.execute(
            "SELECT * FROM agent_permission_requests WHERE id = ?",
            (perm_id,),
        ).fetchone()

    row = await storage.run_in_thread(_exec)
    if row is None:
        return None
    return _serialize_permission_row(row)


async def list_pending_permission_requests(
    storage: SQLiteStorage,
    task_id: str,
) -> list[dict[str, Any]]:
    def _exec(conn):
        return conn.execute(
            "SELECT * FROM agent_permission_requests WHERE task_id = ? AND status = 'pending' ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()

    rows = await storage.run_in_thread(_exec)
    return [_serialize_permission_row(row) for row in rows]


async def update_permission_request_status(
    storage: SQLiteStorage,
    perm_id: str,
    *,
    status: str,
    reason: str = "",
) -> dict[str, Any] | None:
    now = _now_iso()

    def _exec(conn):
        conn.execute(
            "UPDATE agent_permission_requests SET status = ?, reason = ?, resolved_at = ? WHERE id = ? AND status = 'pending'",
            (status, reason, now, perm_id),
        )
        return conn.execute(
            "SELECT * FROM agent_permission_requests WHERE id = ?",
            (perm_id,),
        ).fetchone()

    row = await storage.run_in_thread(_exec)
    if row is None:
        return None
    return _serialize_permission_row(row)


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_task_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "title": row["title"],
        "prompt": row["prompt"],
        "workspacePath": row["workspace_path"],
        "conversationId": row["conversation_id"],
        "status": row["status"],
        "activeSkillIds": _decode_json(row["active_skill_ids"]),
        "settings": _decode_json(row["settings_json"]),
        "budget": _decode_json(row["budget_json"]),
        "error": row["error"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "finishedAt": row["finished_at"],
    }


def _serialize_event_row(row) -> dict[str, Any]:
    return {
        "taskId": row["task_id"],
        "seq": row["seq"],
        "kind": row["kind"],
        "role": row["role"],
        "name": row["name"],
        "status": row["status"],
        "summary": row["summary"],
        "payload": _decode_json(row["payload"]),
        "artifactIds": _decode_json(row["artifact_ids"]),
        "timestamp": row["created_at"],
    }


def _serialize_artifact_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "kind": row["kind"],
        "name": row["name"],
        "path": row["path"],
        "mimeType": row["mime_type"],
        "sizeBytes": row["size_bytes"],
        "description": row["description"],
        "meta": _decode_json(row["meta_json"]),
        "createdAt": row["created_at"],
    }


def _serialize_mcp_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "transport": row["transport"],
        "command": row["command"],
        "args": _decode_json(row["args_json"]),
        "cwd": row["cwd"],
        "env": _decode_json(row["env_json"]),
        "url": row["url"],
        "headers": _decode_json(row["headers_json"]),
        "authType": row["auth_type"],
        "authToken": row["auth_token"],
        "allowedTools": _decode_json(row["allowed_tools_json"]),
        "allowedResources": _decode_json(row["allowed_resources_json"]),
        "trusted": bool(row["trusted"]),
        "timeoutSeconds": row["timeout_seconds"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _serialize_mcp_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "name": record["name"],
        "enabled": bool(record["enabled"]),
        "transport": record["transport"],
        "command": record["command"],
        "args": _decode_json(record["args_json"]),
        "cwd": record["cwd"],
        "env": _decode_json(record["env_json"]),
        "url": record["url"],
        "headers": _decode_json(record["headers_json"]),
        "authType": record["auth_type"],
        "authToken": record["auth_token"],
        "allowedTools": _decode_json(record["allowed_tools_json"]),
        "allowedResources": _decode_json(record["allowed_resources_json"]),
        "trusted": bool(record["trusted"]),
        "timeoutSeconds": record["timeout_seconds"],
        "createdAt": record["created_at"],
        "updatedAt": record["updated_at"],
    }


def _serialize_skill_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "version": row["version"],
        "enabled": bool(row["enabled"]),
        "source": row["source"],
        "path": row["path"],
        "description": row["description"],
        "triggers": _decode_json(row["triggers_json"]),
        "requiredMcpServers": _decode_json(row["required_mcp_servers_json"]),
        "toolAllowlist": _decode_json(row["tool_allowlist_json"]),
        "roles": _decode_json(row["roles_json"]),
        "budgets": _decode_json(row["budgets_json"]),
        "manifest": _decode_json(row["manifest_json"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _serialize_skill_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "name": record["name"],
        "version": record["version"],
        "enabled": bool(record["enabled"]),
        "source": record["source"],
        "path": record["path"],
        "description": record["description"],
        "triggers": _decode_json(record["triggers_json"]),
        "requiredMcpServers": _decode_json(record["required_mcp_servers_json"]),
        "toolAllowlist": _decode_json(record["tool_allowlist_json"]),
        "roles": _decode_json(record["roles_json"]),
        "budgets": _decode_json(record["budgets_json"]),
        "manifest": _decode_json(record["manifest_json"]),
        "createdAt": record["created_at"],
        "updatedAt": record["updated_at"],
    }


def _serialize_permission_row(row) -> dict[str, Any]:
    """Serialize a permission request row (sqlite3.Row or dict) to camelCase."""
    # Support both sqlite3.Row (key access) and plain dict
    def _get(key):
        if isinstance(row, dict):
            return row.get(key)
        return row[key]

    return {
        "id": _get("id"),
        "taskId": _get("task_id"),
        "toolName": _get("tool_name"),
        "argumentsPreview": _get("arguments_preview"),
        "riskLevel": _get("risk_level"),
        "status": _get("status"),
        "reason": _get("reason"),
        "createdAt": _get("created_at"),
        "resolvedAt": _get("resolved_at"),
    }


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
