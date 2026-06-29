"""Compatibility storage facade for the retired complex-agent runtime.

New task execution and persistence lives in :mod:`amadeus_app.orchestrator`.
This module intentionally prevents new writes to the old ``agent_tasks`` runtime
tables while keeping read access for legacy records and keeping old MCP/Skill
configuration imports working during the migration window.
"""

from __future__ import annotations

from typing import Any, NoReturn

from ..orchestrator_integrations import storage as integration_storage
from ..orchestrator import storage as orchestrator_storage
from ..storage import SQLiteStorage


def _retired_runtime() -> NoReturn:
    raise RuntimeError("complex_agent storage runtime is retired; use /api/orchestrator/tasks instead")


# ---------------------------------------------------------------------------
# Retired task write APIs
# ---------------------------------------------------------------------------


async def create_agent_task(*_: Any, **__: Any) -> dict[str, Any]:
    _retired_runtime()


async def update_agent_task_status(*_: Any, **__: Any) -> None:
    _retired_runtime()


async def append_agent_task_event(*_: Any, **__: Any) -> dict[str, Any]:
    _retired_runtime()


async def create_artifact(*_: Any, **__: Any) -> dict[str, Any]:
    _retired_runtime()


async def create_file_snapshot(*_: Any, **__: Any) -> dict[str, Any]:
    _retired_runtime()


async def rollback_file_snapshots(*_: Any, **__: Any) -> dict[str, Any]:
    _retired_runtime()


async def create_permission_request(*_: Any, **__: Any) -> dict[str, Any]:
    _retired_runtime()


async def update_permission_request_status(*_: Any, **__: Any) -> dict[str, Any] | None:
    _retired_runtime()


# ---------------------------------------------------------------------------
# Legacy task read APIs
# ---------------------------------------------------------------------------


async def get_agent_task(storage: SQLiteStorage, task_id: str) -> dict[str, Any] | None:
    return await orchestrator_storage.get_legacy_task(storage, task_id)


async def list_agent_tasks(
    storage: SQLiteStorage,
    *,
    user_id: str | None = None,
    conversation_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return await orchestrator_storage.list_legacy_tasks(
        storage,
        user_id=user_id,
        conversation_id=conversation_id,
        limit=limit,
    )


async def list_agent_task_events(
    storage: SQLiteStorage,
    task_id: str,
    *,
    after_seq: int = -1,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    return await orchestrator_storage.list_legacy_events(storage, task_id, after_seq=after_seq, limit=limit)


async def get_artifact(storage: SQLiteStorage, artifact_id: str) -> dict[str, Any] | None:
    return await orchestrator_storage.get_legacy_artifact(storage, artifact_id)


async def list_artifacts(storage: SQLiteStorage, task_id: str) -> list[dict[str, Any]]:
    return await orchestrator_storage.list_legacy_artifacts(storage, task_id)


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


async def get_permission_request(storage: SQLiteStorage, perm_id: str) -> dict[str, Any] | None:
    def _exec(conn):
        return conn.execute(
            "SELECT * FROM agent_permission_requests WHERE id = ?",
            (perm_id,),
        ).fetchone()

    row = await storage.run_in_thread(_exec)
    return _serialize_permission_row(row) if row is not None else None


async def list_pending_permission_requests(storage: SQLiteStorage, task_id: str) -> list[dict[str, Any]]:
    def _exec(conn):
        return conn.execute(
            "SELECT * FROM agent_permission_requests WHERE task_id = ? AND status = 'pending' ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()

    rows = await storage.run_in_thread(_exec)
    return [_serialize_permission_row(row) for row in rows]


# ---------------------------------------------------------------------------
# MCP server compatibility
# ---------------------------------------------------------------------------


async def list_mcp_servers(storage: SQLiteStorage, user_id: str) -> list[dict[str, Any]]:
    return await integration_storage.list_mcp_servers(storage, user_id)


async def upsert_mcp_server(storage: SQLiteStorage, *, user_id: str, server: dict[str, Any]) -> dict[str, Any]:
    return await integration_storage.upsert_mcp_server(storage, user_id=user_id, server=server)


async def delete_mcp_server(storage: SQLiteStorage, server_id: str) -> bool:
    return await integration_storage.delete_mcp_server(storage, server_id)


# ---------------------------------------------------------------------------
# Skill package compatibility
# ---------------------------------------------------------------------------


async def list_skill_packages(storage: SQLiteStorage, user_id: str) -> list[dict[str, Any]]:
    return await integration_storage.list_skill_packages(storage, user_id)


async def get_skill_package(storage: SQLiteStorage, skill_id: str) -> dict[str, Any] | None:
    return await integration_storage.get_skill_package(storage, skill_id)


async def upsert_skill_package(storage: SQLiteStorage, *, user_id: str, skill: dict[str, Any]) -> dict[str, Any]:
    return await integration_storage.upsert_skill_package(storage, user_id=user_id, skill=skill)


async def delete_skill_package(storage: SQLiteStorage, skill_id: str) -> bool:
    return await integration_storage.delete_skill_package(storage, skill_id)


def _serialize_permission_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "toolName": row["tool_name"],
        "argumentsPreview": row["arguments_preview"],
        "riskLevel": row["risk_level"],
        "status": row["status"],
        "reason": row["reason"],
        "createdAt": row["created_at"],
        "resolvedAt": row["resolved_at"],
    }
