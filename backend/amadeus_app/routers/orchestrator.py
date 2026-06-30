"""API routes for the orchestrator v2 runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from .._common import enrich_orchestrator_media_request, require_storage
from ..orchestrator_integrations.mcp_client import mcp_manager
from ..domain import OrchestratorInvokeRequest
from ..event_stream import sse
from ..logging_config import get_logger
from ..orchestrator import storage as orchestrator_storage
from ..orchestrator.domain import (
    OrchestratorPermissionAnswerRequest,
    OrchestratorPermissionDecisionRequest,
    OrchestratorSettings,
    OrchestratorTaskControlRequest,
    OrchestratorTaskCreateRequest,
    OrchestratorTaskMessageRequest,
)
from ..orchestrator.invoke_service import invoke_orchestrator_request, orchestrator_capabilities_response
from ..orchestrator.runner import default_orchestrator_runner
from ..storage import DEFAULT_USER_ID

_log = get_logger(__name__)

router = APIRouter(prefix="/api/orchestrator", tags=["orchestrator"])


async def _get_orchestrator_settings() -> OrchestratorSettings:
    storage = require_storage()
    settings = await storage.get_settings(DEFAULT_USER_ID)
    agent_dict = (settings or {}).get("orchestrator") or (settings or {}).get("agent") or {}
    return OrchestratorSettings.model_validate(agent_dict)


@router.get("/capabilities")
async def get_orchestrator_capabilities() -> dict[str, Any]:
    settings = await _get_orchestrator_settings()
    response = orchestrator_capabilities_response(settings)
    response["summary"] = "Orchestrator capability catalog."
    response["data"]["streamEndpoint"] = "/api/orchestrator/tasks/stream"
    response["data"]["mcpCapabilities"] = mcp_manager.list_capability_cache()
    return response


@router.post("/invoke")
async def invoke_orchestrator(request: OrchestratorInvokeRequest) -> dict[str, Any]:
    enriched = await enrich_orchestrator_media_request(request)
    return await invoke_orchestrator_request(enriched)


def _budget_from_request(request: OrchestratorTaskCreateRequest, settings: OrchestratorSettings) -> dict[str, Any]:
    return {
        "maxRounds": request.max_rounds or settings.max_rounds,
        "maxToolCalls": request.max_tool_calls or settings.max_tool_calls,
        "maxRuntimeSeconds": request.max_runtime_seconds or settings.max_runtime_seconds,
        "maxSamplingDepth": settings.max_sampling_depth,
        "rounds": 0,
        "toolCalls": 0,
        "samplingDepth": 0,
        "elapsedSeconds": 0,
    }


def _attachment_payload(request: OrchestratorTaskCreateRequest | OrchestratorTaskMessageRequest) -> list[dict[str, Any]]:
    return [attachment.model_dump(by_alias=True) for attachment in request.attachments]


def _task_context_from_request(
    request: OrchestratorTaskCreateRequest,
    *,
    existing_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = dict(existing_context or {})
    attachments = _attachment_payload(request)
    all_attachments = [
        item for item in context.get("attachments", [])
        if isinstance(item, dict)
    ]
    all_attachments.extend(attachments)
    context.update({
        "mode": request.mode,
        "model": request.model,
        "attachments": all_attachments,
    })
    thread_history = [
        item for item in context.get("threadHistory", [])
        if isinstance(item, dict)
    ]
    thread_history.append({
        "role": "user",
        "text": request.prompt,
        "attachments": attachments,
    })
    context["threadHistory"] = thread_history[-20:]
    return context


async def _create_task_record(request: OrchestratorTaskCreateRequest) -> tuple[dict[str, Any], OrchestratorSettings]:
    settings = await _get_orchestrator_settings()
    if not settings.enabled:
        raise HTTPException(status_code=403, detail="orchestrator is disabled in settings")
    workspace = request.workspace_path or settings.default_workspace
    if not workspace:
        raise HTTPException(status_code=400, detail="workspacePath is required (or set defaultWorkspace in Orchestrator settings)")
    trust_mode = request.trust_mode if request.trust_mode is not None else settings.trust_mode
    settings_payload = {**settings.model_dump(by_alias=True), "trustMode": trust_mode}
    storage = require_storage()
    task = await orchestrator_storage.create_task(
        storage,
        user_id=DEFAULT_USER_ID,
        title=request.title or request.prompt[:60],
        prompt=request.prompt,
        workspace_path=workspace,
        conversation_id=str(request.conversation_id) if request.conversation_id else None,
        active_skill_ids=request.skill_ids,
        settings=settings_payload,
        context=_task_context_from_request(request),
        budget=_budget_from_request(request, settings),
    )
    return task, settings.model_copy(update={"trust_mode": bool(trust_mode)})


async def _run_task(task_id: str, request: OrchestratorTaskCreateRequest, settings: OrchestratorSettings) -> None:
    storage = require_storage()
    await default_orchestrator_runner.run(
        storage=storage,
        task_id=task_id,
        request=request,
        settings=settings,
    )


@router.post("/tasks")
async def create_orchestrator_task(request: OrchestratorTaskCreateRequest) -> dict[str, Any]:
    task, settings = await _create_task_record(request)
    asyncio.create_task(_run_task(task["id"], request, settings), name=f"orchestrator-task-{task['id']}")
    return {"ok": True, "taskId": task["id"], "id": task["id"]}


@router.post("/tasks/stream")
async def stream_orchestrator_task(request: OrchestratorTaskCreateRequest) -> StreamingResponse:
    task, settings = await _create_task_record(request)

    async def _stream():
        await _run_task(task["id"], request, settings)
        events = await orchestrator_storage.list_events(require_storage(), task["id"], after_seq=0)
        for event in events:
            yield sse(event["kind"], event)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Task-Id": task["id"],
        },
    )


@router.post("/tasks/{task_id}/messages")
async def append_orchestrator_task_message(task_id: str, request: OrchestratorTaskMessageRequest) -> dict[str, Any]:
    storage = require_storage()
    task = await orchestrator_storage.get_task(storage, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task.get("status") in {"running", "paused"}:
        raise HTTPException(status_code=409, detail=f"task is {task.get('status')}")
    if task.get("status") in {"cancelled", "rolled_back"}:
        raise HTTPException(status_code=409, detail=f"task cannot continue from {task.get('status')}")

    settings_payload = task.get("settings") if isinstance(task.get("settings"), dict) else {}
    settings = OrchestratorSettings.model_validate(settings_payload or (await _get_orchestrator_settings()).model_dump(by_alias=True))
    trust_mode = request.trust_mode if request.trust_mode is not None else settings.trust_mode
    settings = settings.model_copy(update={"trust_mode": bool(trust_mode)})

    task_request = OrchestratorTaskCreateRequest(
        title=str(task.get("title") or ""),
        prompt=request.prompt,
        attachments=request.attachments,
        workspacePath=str(task.get("workspacePath") or settings.default_workspace or ""),
        conversationId=task.get("conversationId") or None,
        skillIds=request.skill_ids or list(task.get("activeSkillIds") or []),
        model=request.model or (task.get("context") or {}).get("model", {}),
        mode=request.mode or str((task.get("context") or {}).get("mode") or "fast"),
        trustMode=trust_mode,
    )
    context = _task_context_from_request(
        task_request,
        existing_context=task.get("context") if isinstance(task.get("context"), dict) else {},
    )
    await orchestrator_storage.update_task_context(storage, task_id, context)
    asyncio.create_task(_run_task(task_id, task_request, settings), name=f"orchestrator-task-message-{task_id}")
    return {"ok": True, "taskId": task_id, "id": task_id}


@router.get("/tasks")
async def list_orchestrator_tasks(
    conversation_id: str | None = Query(default=None, alias="conversationId"),
    include_legacy: bool = Query(default=True, alias="includeLegacy"),
) -> dict[str, Any]:
    storage = require_storage()
    tasks = await orchestrator_storage.list_tasks(
        storage,
        user_id=DEFAULT_USER_ID,
        conversation_id=conversation_id,
    )
    if include_legacy:
        try:
            tasks.extend(
                await orchestrator_storage.list_legacy_tasks(
                    storage,
                    user_id=DEFAULT_USER_ID,
                    conversation_id=conversation_id,
                )
            )
        except Exception as error:  # noqa: BLE001
            _log.warning("failed to list legacy agent tasks: %s", error)
    tasks.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
    return {"tasks": tasks}


@router.get("/tasks/{task_id}")
async def get_orchestrator_task_detail(task_id: str) -> dict[str, Any]:
    storage = require_storage()
    detail = await orchestrator_storage.get_detail(storage, task_id)
    if detail is not None:
        return {"task": detail}
    legacy = await _legacy_task_detail(task_id)
    if legacy is not None:
        return {"task": legacy}
    raise HTTPException(status_code=404, detail="task not found")


@router.get("/tasks/{task_id}/events")
async def stream_orchestrator_task_events(task_id: str, after_seq: int = Query(default=0, alias="afterSeq")) -> StreamingResponse:
    async def _stream():
        current_seq = after_seq
        terminal = {"done", "failed", "cancelled", "rolled_back"}
        while True:
            storage = require_storage()
            events = await orchestrator_storage.list_events(storage, task_id, after_seq=current_seq)
            for event in events:
                current_seq = max(current_seq, int(event.get("seq") or current_seq))
                yield sse(event["kind"], event)
                if event.get("kind") in {"done", "error"}:
                    return
            task = await orchestrator_storage.get_task(storage, task_id)
            if task is None:
                return
            if task.get("status") in terminal or task.get("status") == "paused":
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/tasks/{task_id}/control")
async def control_orchestrator_task(task_id: str, request: OrchestratorTaskControlRequest) -> dict[str, Any]:
    storage = require_storage()
    task = await orchestrator_storage.get_task(storage, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if request.action == "rollback":
        if task["status"] not in ("done", "failed"):
            raise HTTPException(status_code=409, detail=f"cannot rollback task with status: {task['status']}")
        result = await default_orchestrator_runner.rollback_task(storage=storage, task_id=task_id)
        return result
    target_status = {
        "pause": "paused",
        "resume": "running",
        "cancel": "cancelled",
        "rollback": "rolled_back",
    }[request.action]
    await orchestrator_storage.append_event(
        storage,
        task_id=task_id,
        kind="status",
        role="coordinator",
        name="control",
        status=target_status,
        summary=request.reason or f"task {request.action}",
        payload={"action": request.action},
    )
    await orchestrator_storage.update_task_status(
        storage,
        task_id,
        status=target_status,
        finished=target_status in {"cancelled", "rolled_back"},
    )
    return {"ok": True}


@router.get("/tasks/{task_id}/permissions")
async def list_orchestrator_permissions(task_id: str) -> dict[str, Any]:
    task = await orchestrator_storage.get_task(require_storage(), task_id)
    if task is None:
        legacy = await orchestrator_storage.get_legacy_task(require_storage(), task_id)
        if legacy is None:
            raise HTTPException(status_code=404, detail="task not found")
    permissions = await orchestrator_storage.list_pending_permission_requests(require_storage(), task_id)
    return {"permissions": permissions}


@router.post("/permissions/{permission_id}/approve")
async def approve_orchestrator_permission(
    permission_id: str,
    request: OrchestratorPermissionDecisionRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    return await _decide_orchestrator_permission(
        permission_id,
        status="approved",
        reason=request.reason,
        background_tasks=background_tasks,
    )


@router.post("/permissions/{permission_id}/reject")
async def reject_orchestrator_permission(
    permission_id: str,
    request: OrchestratorPermissionDecisionRequest,
) -> dict[str, Any]:
    return await _decide_orchestrator_permission(permission_id, status="rejected", reason=request.reason)


@router.post("/tasks/{task_id}/permissions/{permission_id}/answer")
async def answer_orchestrator_permission(
    task_id: str,
    permission_id: str,
    request: OrchestratorPermissionAnswerRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    storage = require_storage()
    permission = await orchestrator_storage.get_permission_request(storage, permission_id)
    if permission is None:
        raise HTTPException(status_code=404, detail="permission request not found")
    if permission["taskId"] != task_id:
        raise HTTPException(status_code=404, detail="permission does not belong to this task")
    if permission["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"permission already {permission['status']}")

    updated = await orchestrator_storage.update_permission_answer(
        storage, permission_id, answer=request.answer
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="permission request not found")

    await orchestrator_storage.append_event(
        storage,
        task_id=task_id,
        kind="question",
        role="user",
        name="ask_user",
        status="answered",
        summary=f"User answered: {request.answer[:200]}",
        payload={"permissionId": permission_id, "answer": request.answer},
    )

    background_tasks.add_task(_resume_approved_permission, permission_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/rollback")
async def rollback_orchestrator_task(task_id: str) -> dict[str, Any]:
    storage = require_storage()
    task = await orchestrator_storage.get_task(storage, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task["status"] not in ("done", "failed"):
        raise HTTPException(status_code=409, detail=f"cannot rollback task with status: {task['status']}")

    result = await default_orchestrator_runner.rollback_task(storage=storage, task_id=task_id)
    return result


@router.get("/tasks/{task_id}/file-changes")
async def list_orchestrator_file_changes(task_id: str) -> dict[str, Any]:
    storage = require_storage()
    changes = await orchestrator_storage.list_file_changes(storage, task_id)
    return {"changes": changes}


async def _decide_orchestrator_permission(
    permission_id: str,
    *,
    status: str,
    reason: str,
    background_tasks: BackgroundTasks | None = None,
) -> dict[str, Any]:
    storage = require_storage()
    permission = await orchestrator_storage.get_permission_request(storage, permission_id)
    if permission is None:
        raise HTTPException(status_code=404, detail="permission request not found")
    if permission["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"permission already {permission['status']}")
    updated = await orchestrator_storage.update_permission_request_status(
        storage,
        permission_id,
        status=status,
        reason=reason,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="permission request not found")
    await orchestrator_storage.append_event(
        storage,
        task_id=updated["taskId"],
        kind="status",
        role="coordinator",
        name="permission",
        status=status,
        summary=f"{updated['toolName']} permission {status}.",
        payload={"permissionId": permission_id, "reason": reason},
    )
    if status == "rejected":
        await orchestrator_storage.update_task_status(
            storage,
            updated["taskId"],
            status="failed",
            error=f"{updated['toolName']} permission rejected",
            finished=True,
        )
    elif background_tasks is not None:
        background_tasks.add_task(_resume_approved_permission, permission_id)
    return updated


async def _resume_approved_permission(permission_id: str) -> None:
    storage = require_storage()
    await default_orchestrator_runner.resume_from_permission(storage=storage, permission_id=permission_id)


@router.get("/artifacts/{artifact_id}")
async def get_orchestrator_artifact(artifact_id: str) -> dict[str, Any]:
    storage = require_storage()
    artifact = await orchestrator_storage.get_artifact(storage, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return {"artifact": artifact}


@router.get("/artifacts/{artifact_id}/download")
async def download_orchestrator_artifact(artifact_id: str) -> FileResponse:
    storage = require_storage()
    artifact = await orchestrator_storage.get_artifact(storage, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    path = Path(artifact["path"]).resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail="artifact file missing")

    task = await orchestrator_storage.get_task(storage, str(artifact.get("taskId") or ""))
    allowed_roots: list[Path] = []
    workspace = str((task or {}).get("workspacePath") or "")
    if workspace:
        workspace_root = Path(workspace).resolve()
        allowed_roots.append(workspace_root)
    settings = await _get_orchestrator_settings()
    if settings.artifact_root:
        artifact_root = Path(settings.artifact_root).expanduser()
        if not artifact_root.is_absolute() and workspace:
            allowed_roots.append((Path(workspace) / artifact_root).resolve())
        allowed_roots.append(artifact_root.resolve())
    if not any(_is_within(path, root) for root in allowed_roots):
        raise HTTPException(status_code=403, detail="artifact path is outside allowed roots")
    return FileResponse(
        path=str(path),
        media_type=artifact["mimeType"],
        filename=artifact["name"],
    )


async def _legacy_task_detail(task_id: str) -> dict[str, Any] | None:
    return await orchestrator_storage.get_legacy_detail(require_storage(), task_id)


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False
