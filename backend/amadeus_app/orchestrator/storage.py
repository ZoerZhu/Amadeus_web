from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..storage import SQLiteStorage, _decode_json


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _decode_list(value: Any) -> list[Any]:
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return value if isinstance(value, list) else []


def _budget_snapshot(budget: dict[str, Any] | None = None) -> dict[str, Any]:
    data = dict(budget or {})
    max_rounds = int(data.get("maxRounds", data.get("max_rounds", 20)) or 20)
    max_tool_calls = int(data.get("maxToolCalls", data.get("max_tool_calls", 80)) or 80)
    max_runtime = int(data.get("maxRuntimeSeconds", data.get("max_runtime_seconds", 1800)) or 1800)
    max_sampling = int(data.get("maxSamplingDepth", data.get("max_sampling_depth", 3)) or 3)
    rounds = int(data.get("rounds", 0) or 0)
    tool_calls = int(data.get("toolCalls", data.get("tool_calls", 0)) or 0)
    sampling_depth = int(data.get("samplingDepth", data.get("sampling_depth", 0)) or 0)
    elapsed = int(data.get("elapsedSeconds", data.get("elapsed_seconds", 0)) or 0)
    return {
        "maxRounds": max_rounds,
        "maxToolCalls": max_tool_calls,
        "maxRuntimeSeconds": max_runtime,
        "maxSamplingDepth": max_sampling,
        "rounds": rounds,
        "toolCalls": tool_calls,
        "samplingDepth": sampling_depth,
        "elapsedSeconds": elapsed,
        "remainingRounds": max(0, max_rounds - rounds),
        "remainingToolCalls": max(0, max_tool_calls - tool_calls),
        "remainingSeconds": max(0, max_runtime - elapsed),
    }


async def create_task(
    storage: SQLiteStorage,
    *,
    user_id: str,
    title: str,
    prompt: str,
    workspace_path: str,
    conversation_id: str | None,
    active_skill_ids: list[str],
    settings: dict[str, Any],
    context: dict[str, Any] | None = None,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_id = uuid4().hex
    now = _now_iso()
    record = {
        "id": task_id,
        "user_id": user_id,
        "title": title or prompt[:60],
        "prompt": prompt,
        "workspace_path": workspace_path,
        "conversation_id": conversation_id,
        "status": "created",
        "active_skill_ids": _json_dumps(active_skill_ids),
        "settings_json": _json_dumps(settings),
        "context_json": _json_dumps(context or {}),
        "budget_json": _json_dumps(_budget_snapshot(budget)),
        "error": "",
        "created_at": now,
        "updated_at": now,
        "finished_at": None,
    }

    def _exec(conn) -> None:
        conn.execute(
            """
            INSERT INTO orchestrator_tasks
            (id, user_id, title, prompt, workspace_path, conversation_id, status,
             active_skill_ids, settings_json, context_json, budget_json, error,
             created_at, updated_at, finished_at)
            VALUES (:id, :user_id, :title, :prompt, :workspace_path, :conversation_id, :status,
                    :active_skill_ids, :settings_json, :context_json, :budget_json, :error,
                    :created_at, :updated_at, :finished_at)
            """,
            record,
        )

    await storage.run_in_thread(_exec)
    return await get_task(storage, task_id) or _serialize_task_record(record)


async def get_task(storage: SQLiteStorage, task_id: str) -> dict[str, Any] | None:
    def _exec(conn) -> dict[str, Any] | None:
        row = conn.execute("SELECT * FROM orchestrator_tasks WHERE id = ?", (task_id,)).fetchone()
        return _serialize_task_row(row) if row else None

    return await storage.run_in_thread(_exec)


async def list_tasks(
    storage: SQLiteStorage,
    *,
    user_id: str | None,
    conversation_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    def _exec(conn) -> list[dict[str, Any]]:
        if conversation_id:
            rows = conn.execute(
                "SELECT * FROM orchestrator_tasks WHERE conversation_id = ? ORDER BY updated_at DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        elif user_id:
            rows = conn.execute(
                "SELECT * FROM orchestrator_tasks WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM orchestrator_tasks ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_serialize_task_row(row) for row in rows]

    return await storage.run_in_thread(_exec)


async def list_legacy_tasks(
    storage: SQLiteStorage,
    *,
    user_id: str | None,
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
            rows = conn.execute("SELECT * FROM agent_tasks ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        tasks = []
        for row in rows:
            task = _serialize_legacy_task_row(row)
            task["runtimeVersion"] = "legacy_complex_agent"
            tasks.append(task)
        return tasks

    return await storage.run_in_thread(_exec)


async def get_legacy_task(storage: SQLiteStorage, task_id: str) -> dict[str, Any] | None:
    def _exec(conn) -> dict[str, Any] | None:
        row = conn.execute("SELECT * FROM agent_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        task = _serialize_legacy_task_row(row)
        task["runtimeVersion"] = "legacy_complex_agent"
        return task

    return await storage.run_in_thread(_exec)


async def list_legacy_events(
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
        return [_serialize_legacy_event_row(row) for row in rows]

    return await storage.run_in_thread(_exec)


async def list_legacy_artifacts(storage: SQLiteStorage, task_id: str) -> list[dict[str, Any]]:
    def _exec(conn) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM agent_artifacts WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        return [_serialize_artifact_row(row) for row in rows]

    return await storage.run_in_thread(_exec)


async def get_legacy_artifact(storage: SQLiteStorage, artifact_id: str) -> dict[str, Any] | None:
    def _exec(conn) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM agent_artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        return _serialize_artifact_row(row) if row else None

    return await storage.run_in_thread(_exec)


async def get_legacy_detail(storage: SQLiteStorage, task_id: str) -> dict[str, Any] | None:
    task = await get_legacy_task(storage, task_id)
    if task is None:
        return None
    events = await list_legacy_events(storage, task_id)
    artifacts = await list_legacy_artifacts(storage, task_id)
    task["eventCount"] = len(events)
    task["artifactCount"] = len(artifacts)
    return {"task": task, "events": events, "artifacts": artifacts}


async def update_task_status(
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
                UPDATE orchestrator_tasks
                SET status = ?, error = COALESCE(?, error), budget_json = ?,
                    updated_at = ?, finished_at = COALESCE(?, finished_at)
                WHERE id = ?
                """,
                (status, error, _json_dumps(_budget_snapshot(budget)), now, finished_at, task_id),
            )
        else:
            conn.execute(
                """
                UPDATE orchestrator_tasks
                SET status = ?, error = COALESCE(?, error), updated_at = ?,
                    finished_at = COALESCE(?, finished_at)
                WHERE id = ?
                """,
                (status, error, now, finished_at, task_id),
            )

    await storage.run_in_thread(_exec)


async def update_task_context(storage: SQLiteStorage, task_id: str, context: dict[str, Any]) -> None:
    now = _now_iso()

    def _exec(conn) -> None:
        conn.execute(
            """
            UPDATE orchestrator_tasks
            SET context_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (_json_dumps(context), now, task_id),
        )

    await storage.run_in_thread(_exec)


async def append_event(
    storage: SQLiteStorage,
    *,
    task_id: str,
    kind: str,
    role: str = "",
    name: str = "",
    status: str = "",
    summary: str = "",
    payload: dict[str, Any] | None = None,
    artifact_ids: list[str] | None = None,
) -> dict[str, Any]:
    now = _now_iso()

    def _exec(conn) -> dict[str, Any]:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM orchestrator_ledger_events WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        seq = int(row["next_seq"] if row else 1)
        conn.execute(
            """
            INSERT INTO orchestrator_ledger_events
            (task_id, seq, kind, role, name, status, summary, payload_json, artifact_ids_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                seq,
                kind,
                role,
                name,
                status,
                summary,
                _json_dumps(payload or {}),
                _json_dumps(artifact_ids or []),
                now,
            ),
        )
        conn.execute("UPDATE orchestrator_tasks SET updated_at = ? WHERE id = ?", (now, task_id))
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

    return await storage.run_in_thread(_exec)


async def list_events(storage: SQLiteStorage, task_id: str, *, after_seq: int = -1, limit: int = 1000) -> list[dict[str, Any]]:
    def _exec(conn) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT * FROM orchestrator_ledger_events
            WHERE task_id = ? AND seq > ?
            ORDER BY seq ASC
            LIMIT ?
            """,
            (task_id, after_seq, limit),
        ).fetchall()
        return [_serialize_event_row(row) for row in rows]

    return await storage.run_in_thread(_exec)


async def list_artifacts(storage: SQLiteStorage, task_id: str) -> list[dict[str, Any]]:
    def _exec(conn) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM orchestrator_artifacts WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
        return [_serialize_artifact_row(row) for row in rows]

    return await storage.run_in_thread(_exec)


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
        "size_bytes": int(size_bytes or 0),
        "description": description,
        "meta_json": _json_dumps(meta or {}),
        "created_at": now,
    }

    def _exec(conn) -> None:
        conn.execute(
            """
            INSERT INTO orchestrator_artifacts
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
        "sizeBytes": int(size_bytes or 0),
        "description": description,
        "meta": meta or {},
        "createdAt": now,
    }


async def get_artifact(storage: SQLiteStorage, artifact_id: str) -> dict[str, Any] | None:
    def _exec(conn) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM orchestrator_artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        return _serialize_artifact_row(row) if row else None

    return await storage.run_in_thread(_exec)


async def create_permission_request(
    storage: SQLiteStorage,
    *,
    task_id: str,
    tool_name: str,
    arguments_preview: str,
    risk_level: str = "confirm",
    payload: dict[str, Any] | None = None,
    question_type: str = "",
) -> dict[str, Any]:
    permission_id = uuid4().hex
    now = _now_iso()
    record = {
        "id": permission_id,
        "task_id": task_id,
        "tool_name": tool_name,
        "arguments_preview": arguments_preview[:2000],
        "risk_level": risk_level,
        "status": "pending",
        "reason": "",
        "payload_json": _json_dumps(payload or {}),
        "question_type": question_type,
        "created_at": now,
        "resolved_at": None,
    }

    def _exec(conn) -> None:
        conn.execute(
            """
            INSERT INTO orchestrator_permission_requests
            (id, task_id, tool_name, arguments_preview, risk_level, status, reason, payload_json, question_type, created_at, resolved_at)
            VALUES (:id, :task_id, :tool_name, :arguments_preview, :risk_level, :status, :reason, :payload_json, :question_type, :created_at, :resolved_at)
            """,
            record,
        )

    await storage.run_in_thread(_exec)
    return _serialize_permission_row(record)


async def get_permission_request(storage: SQLiteStorage, permission_id: str) -> dict[str, Any] | None:
    def _exec(conn) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM orchestrator_permission_requests WHERE id = ?",
            (permission_id,),
        ).fetchone()
        return _serialize_permission_row(row) if row else None

    return await storage.run_in_thread(_exec)


async def list_pending_permission_requests(storage: SQLiteStorage, task_id: str) -> list[dict[str, Any]]:
    def _exec(conn) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT * FROM orchestrator_permission_requests
            WHERE task_id = ? AND status = 'pending'
            ORDER BY created_at ASC
            """,
            (task_id,),
        ).fetchall()
        return [_serialize_permission_row(row) for row in rows]

    return await storage.run_in_thread(_exec)


async def update_permission_request_status(
    storage: SQLiteStorage,
    permission_id: str,
    *,
    status: str,
    reason: str = "",
) -> dict[str, Any] | None:
    now = _now_iso()

    def _exec(conn) -> dict[str, Any] | None:
        conn.execute(
            """
            UPDATE orchestrator_permission_requests
            SET status = ?, reason = ?, resolved_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (status, reason, now, permission_id),
        )
        row = conn.execute(
            "SELECT * FROM orchestrator_permission_requests WHERE id = ?",
            (permission_id,),
        ).fetchone()
        return _serialize_permission_row(row) if row else None

    return await storage.run_in_thread(_exec)


async def get_detail(storage: SQLiteStorage, task_id: str) -> dict[str, Any] | None:
    task = await get_task(storage, task_id)
    if task is None:
        return None
    events = await list_events(storage, task_id)
    artifacts = await list_artifacts(storage, task_id)
    task["eventCount"] = len(events)
    task["artifactCount"] = len(artifacts)
    return {"task": task, "events": events, "artifacts": artifacts}


def _serialize_task_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "title": row["title"],
        "prompt": row["prompt"],
        "status": row["status"],
        "workspacePath": row["workspace_path"],
        "conversationId": row["conversation_id"],
        "activeSkillIds": _decode_list(row["active_skill_ids"]),
        "settings": _decode_json(row["settings_json"]),
        "context": _decode_json(row["context_json"]),
        "budget": _budget_snapshot(_decode_json(row["budget_json"])),
        "error": row["error"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "finishedAt": row["finished_at"],
        "artifactCount": 0,
        "eventCount": 0,
        "runtimeVersion": "orchestrator_v2",
    }


def _serialize_task_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "userId": record["user_id"],
        "title": record["title"],
        "prompt": record["prompt"],
        "status": record["status"],
        "workspacePath": record["workspace_path"],
        "conversationId": record["conversation_id"],
        "activeSkillIds": _decode_list(record["active_skill_ids"]),
        "settings": _decode_json(record["settings_json"]),
        "context": _decode_json(record["context_json"]),
        "budget": _budget_snapshot(_decode_json(record["budget_json"])),
        "error": record["error"],
        "createdAt": record["created_at"],
        "updatedAt": record["updated_at"],
        "finishedAt": record["finished_at"],
        "artifactCount": 0,
        "eventCount": 0,
        "runtimeVersion": "orchestrator_v2",
    }


def _serialize_legacy_task_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "title": row["title"],
        "prompt": row["prompt"],
        "status": row["status"],
        "workspacePath": row["workspace_path"],
        "conversationId": row["conversation_id"],
        "activeSkillIds": _decode_list(row["active_skill_ids"]),
        "settings": _decode_json(row["settings_json"]),
        "budget": _budget_snapshot(_decode_json(row["budget_json"])),
        "error": row["error"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "finishedAt": row["finished_at"],
        "artifactCount": 0,
        "eventCount": 0,
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
        "payload": _decode_json(row["payload_json"]),
        "artifactIds": _decode_list(row["artifact_ids_json"]),
        "timestamp": row["created_at"],
    }


def _serialize_legacy_event_row(row) -> dict[str, Any]:
    return {
        "taskId": row["task_id"],
        "seq": row["seq"],
        "kind": row["kind"],
        "role": row["role"],
        "name": row["name"],
        "status": row["status"],
        "summary": row["summary"],
        "payload": _decode_json(row["payload"]),
        "artifactIds": _decode_list(row["artifact_ids"]),
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


def _serialize_permission_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "toolName": row["tool_name"],
        "argumentsPreview": row["arguments_preview"],
        "riskLevel": row["risk_level"],
        "status": row["status"],
        "reason": row["reason"],
        "payload": _decode_json(row["payload_json"]) if "payload_json" in row.keys() else {},
        "questionType": row["question_type"] if "question_type" in row.keys() else "",
        "createdAt": row["created_at"],
        "resolvedAt": row["resolved_at"],
    }
