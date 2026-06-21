"""API routes for the complex agent / MCP / skills subsystem.

Endpoints:
- POST   /api/agent/tasks/stream       create + stream a complex task
- GET    /api/agent/tasks              list tasks
- GET    /api/agent/tasks/{id}         task detail (events + artifacts)
- POST   /api/agent/tasks/{id}/control pause / resume / cancel / rollback
- GET    /api/agent/tasks/{id}/events  SSE stream of live events
- GET    /api/mcp/servers              list MCP servers
- PUT    /api/mcp/servers              upsert an MCP server
- DELETE /api/mcp/servers/{id}         delete an MCP server
- POST   /api/mcp/servers/{id}/test    test connection (or test a body config)
- GET    /api/skills                   list skill packages
- POST   /api/skills/import            import from Git URL
- POST   /api/skills/import-zip        import from uploaded ZIP
- PUT    /api/skills/{id}              upsert / enable / disable
- DELETE /api/skills/{id}              delete a skill package
- GET    /api/artifacts/{id}           artifact metadata
- GET    /api/artifacts/{id}/download  download artifact file
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from .._common import require_storage
from ..logging_config import get_logger
from ..storage import DEFAULT_USER_ID
from ..complex_agent import agent_storage
from ..complex_agent import skills as skills_module
from ..complex_agent.agent import run_agent_task
from ..complex_agent.domain import (
    AgentSettings,
    AgentTaskControlRequest,
    AgentTaskCreateRequest,
    McpServerConfig,
    McpServerTestRequest,
    McpServerUpsertRequest,
    PermissionDecisionRequest,
    SkillImportRequest,
    SkillPackageInfo,
    SkillUpsertRequest,
)
from ..complex_agent.mcp_client import McpClient, mcp_manager
from ..complex_agent.skills import DEFAULT_SKILLS_DIR, load_skill_context
from ..complex_agent.task_hub import TERMINAL_STATUSES, TaskBudget, agent_task_hub, sse

_log = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["complex-agent"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_agent_settings() -> AgentSettings:
    storage = require_storage()
    import asyncio

    async def _get():
        settings = await storage.get_settings(DEFAULT_USER_ID)
        agent_dict = (settings or {}).get("agent") or {}
        return AgentSettings.model_validate(agent_dict)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        # Sync context (e.g. tests) — run a fresh loop
        return asyncio.run(_get())
    # We're inside an async route; the caller should await. This helper is
    # only used from sync contexts, so fall back to defaults if no loop.
    return AgentSettings()


async def _get_agent_settings_async() -> AgentSettings:
    storage = require_storage()
    settings = await storage.get_settings(DEFAULT_USER_ID)
    agent_dict = (settings or {}).get("agent") or {}
    return AgentSettings.model_validate(agent_dict)


async def _load_skill_contexts(skill_ids: list[str]) -> list:
    contexts = []
    for skill_id in skill_ids:
        skill = await agent_storage.get_skill_package(require_storage(), skill_id)
        if skill is None or not skill.get("enabled", True):
            continue
        skill_dir = Path(skill["path"])
        if not skill_dir.exists():
            continue
        try:
            ctx = load_skill_context(skill_dir)
            contexts.append(ctx)
        except Exception as error:  # noqa: BLE001
            _log.warning("failed to load skill %s: %s", skill_id, error)
    return contexts


# ---------------------------------------------------------------------------
# Agent tasks
# ---------------------------------------------------------------------------


@router.post("/agent/tasks/stream")
async def create_agent_task_stream(request: AgentTaskCreateRequest) -> StreamingResponse:
    storage = require_storage()
    agent_settings = await _get_agent_settings_async()
    if not agent_settings.enabled:
        raise HTTPException(status_code=403, detail="complex agent is disabled in settings")

    workspace = request.workspace_path or agent_settings.default_workspace
    if not workspace:
        raise HTTPException(status_code=400, detail="workspacePath is required (or set defaultWorkspace in agent settings)")

    skill_contexts = await _load_skill_contexts(request.skill_ids)
    budget = TaskBudget(
        max_rounds=request.max_rounds or agent_settings.max_rounds,
        max_tool_calls=request.max_tool_calls or agent_settings.max_tool_calls,
        max_runtime_seconds=request.max_runtime_seconds or agent_settings.max_runtime_seconds,
        max_sampling_depth=agent_settings.max_sampling_depth,
    )
    trust_mode = request.trust_mode if request.trust_mode is not None else agent_settings.trust_mode
    settings_dict = {
        **agent_settings.model_dump(by_alias=True),
        "trustMode": trust_mode,
    }

    task = await agent_task_hub.create_task(
        storage=storage,
        user_id=DEFAULT_USER_ID,
        title=request.title or request.prompt[:60],
        prompt=request.prompt,
        workspace_path=workspace,
        conversation_id=str(request.conversation_id) if request.conversation_id else None,
        active_skill_ids=request.skill_ids,
        settings=settings_dict,
        budget=budget,
    )

    async def _run_and_stream():
        # Start the agent in the background
        background = asyncio.create_task(
            run_agent_task(
                storage=storage,
                task=task,
                request=request,
                agent_settings=agent_settings,
                skill_contexts=skill_contexts,
            ),
            name=f"agent-task-{task.task_id}",
        )
        await agent_task_hub.attach_background_task(task.task_id, background)

        async def _event_stream():
            try:
                # Replay persisted events first to avoid missing the terminal
                # event if the background task finishes before subscribe().
                seen_terminal = False
                replay_events = await agent_storage.list_agent_task_events(
                    storage, task.task_id, after_seq=-1
                )
                for event in replay_events:
                    yield sse(event["kind"], event)
                    if event.get("kind") in {"done", "error"} or event.get("status") in TERMINAL_STATUSES:
                        seen_terminal = True
                        break
                if seen_terminal:
                    return
                # Stream live events as they are emitted
                async for record in agent_task_hub.subscribe(task.task_id, replay=False):
                    yield sse(record["kind"], record)
            finally:
                # Ensure the background task completes before closing the stream
                try:
                    await background
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001
                    pass

        async for chunk in _event_stream():
            yield chunk

    return StreamingResponse(
        _run_and_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Task-Id": task.task_id,
        },
    )


@router.get("/agent/tasks")
async def list_agent_tasks(
    conversation_id: str | None = Query(default=None, alias="conversationId"),
) -> dict:
    storage = require_storage()
    records = await agent_task_hub.list_tasks(
        storage=storage,
        user_id=DEFAULT_USER_ID,
        conversation_id=conversation_id,
    )
    return {"tasks": records}


@router.get("/agent/tasks/{task_id}")
async def get_agent_task_detail(task_id: str) -> dict:
    storage = require_storage()
    detail = await agent_task_hub.get_detail(storage=storage, task_id=task_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="task not found")
    return {"task": detail}


@router.post("/agent/tasks/{task_id}/control")
async def control_agent_task(task_id: str, request: AgentTaskControlRequest) -> dict:
    storage = require_storage()
    result = await agent_task_hub.control(
        storage=storage,
        task_id=task_id,
        action=request.action,
        reason=request.reason,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "control failed"))
    return result


@router.get("/agent/tasks/{task_id}/events")
async def stream_agent_task_events(task_id: str, after_seq: int = Query(default=0, alias="afterSeq")) -> StreamingResponse:
    storage = require_storage()
    managed = agent_task_hub.get(task_id)

    async def _stream():
        # Replay persisted events first
        events = await agent_storage.list_agent_task_events(storage, task_id, after_seq=after_seq)
        terminal_seen = False
        for event in events:
            if event["seq"] <= after_seq:
                continue
            yield sse(event["kind"], event)
            # Stop if we've already reached a terminal event in the replay
            if event.get("kind") in {"done", "error"} or event.get("status") in TERMINAL_STATUSES:
                terminal_seen = True
                break
        if terminal_seen:
            return
        # If the task is no longer in memory (process restarted), stop here.
        if managed is None:
            return
        # If the task is already in a terminal status, don't block waiting for live events.
        if managed.status in TERMINAL_STATUSES:
            return
        # Then stream live events
        async for record in agent_task_hub.subscribe(task_id, replay=False):
            if record["seq"] <= after_seq:
                continue
            yield sse(record["kind"], record)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------


@router.get("/mcp/servers")
async def list_mcp_servers() -> dict:
    storage = require_storage()
    servers = await agent_storage.list_mcp_servers(storage, DEFAULT_USER_ID)
    # Augment with live connection status
    for server in servers:
        client = mcp_manager.get(server["id"])
        server["connected"] = client.connected if client else False
    return {"servers": servers}


@router.put("/mcp/servers")
async def upsert_mcp_server(request: McpServerUpsertRequest) -> dict:
    storage = require_storage()
    server_dict = request.server.model_dump(by_alias=True)
    saved = await agent_storage.upsert_mcp_server(storage, user_id=DEFAULT_USER_ID, server=server_dict)
    return {"server": saved}


@router.delete("/mcp/servers/{server_id}")
async def delete_mcp_server(server_id: str) -> dict:
    storage = require_storage()
    await mcp_manager.disconnect_server(server_id)
    deleted = await agent_storage.delete_mcp_server(storage, server_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="mcp server not found")
    return {"ok": True}


@router.post("/mcp/servers/test")
async def test_mcp_server(request: McpServerTestRequest) -> dict:
    """Test a server config (does not persist). Accepts body with or without id."""
    config = request.server
    if not config.id:
        config.id = f"test-{request.server.name or 'server'}"
    result = await mcp_manager.test_connection(config)
    return result


@router.post("/mcp/servers/{server_id}/connect")
async def connect_mcp_server(server_id: str) -> dict:
    storage = require_storage()
    servers = await agent_storage.list_mcp_servers(storage, DEFAULT_USER_ID)
    server = next((s for s in servers if s["id"] == server_id), None)
    if server is None:
        raise HTTPException(status_code=404, detail="mcp server not found")
    config = McpServerConfig.model_validate(server)
    client = await mcp_manager.connect_server(config)
    return {"ok": True, "toolCount": len(client.tools), "resourceCount": len(client.resources)}


@router.post("/mcp/servers/{server_id}/disconnect")
async def disconnect_mcp_server(server_id: str) -> dict:
    ok = await mcp_manager.disconnect_server(server_id)
    return {"ok": ok}


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


@router.get("/skills")
async def list_skills() -> dict:
    storage = require_storage()
    skills = await agent_storage.list_skill_packages(storage, DEFAULT_USER_ID)
    return {"skills": skills}


@router.post("/skills/import")
async def import_skill_git(request: SkillImportRequest) -> dict:
    if not request.git_url:
        raise HTTPException(status_code=400, detail="gitUrl is required")
    try:
        info = await skills_module.import_skill_from_git(
            request.git_url,
            branch=request.branch,
            subdirectory=request.subdirectory,
        )
    except Exception as error:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(error)) from error
    storage = require_storage()
    saved = await agent_storage.upsert_skill_package(
        storage,
        user_id=DEFAULT_USER_ID,
        skill=info.model_dump(by_alias=True),
    )
    return {"skill": saved}


@router.post("/skills/import-zip")
async def import_skill_zip(file: UploadFile = File(...)) -> dict:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="file must be a .zip")
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)
    try:
        info = skills_module.import_skill_from_zip(tmp_path)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    storage = require_storage()
    saved = await agent_storage.upsert_skill_package(
        storage,
        user_id=DEFAULT_USER_ID,
        skill=info.model_dump(by_alias=True),
    )
    return {"skill": saved}


@router.put("/skills/{skill_id}")
async def upsert_skill(skill_id: str, request: SkillUpsertRequest) -> dict:
    storage = require_storage()
    skill_dict = request.skill.model_dump(by_alias=True)
    skill_dict["id"] = skill_id
    saved = await agent_storage.upsert_skill_package(storage, user_id=DEFAULT_USER_ID, skill=skill_dict)
    return {"skill": saved}


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str) -> dict:
    storage = require_storage()
    skill = await agent_storage.get_skill_package(storage, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill not found")
    # Remove files
    try:
        skills_module.remove_skill_files(Path(skill["path"]))
    except Exception:  # noqa: BLE001
        pass
    deleted = await agent_storage.delete_skill_package(storage, skill_id)
    return {"ok": deleted}


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str) -> dict:
    storage = require_storage()
    artifact = await agent_storage.get_artifact(storage, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return {"artifact": artifact}


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(artifact_id: str) -> FileResponse:
    storage = require_storage()
    artifact = await agent_storage.get_artifact(storage, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    path = Path(artifact["path"]).resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail="artifact file missing")
    # Path boundary check: the artifact must live inside its task's workspace
    # or the configured artifact root. This prevents dirty DB rows from
    # enabling arbitrary file downloads.
    task = await agent_storage.get_agent_task(storage, artifact.get("taskId", ""))
    allowed_roots: list[Path] = []
    if task:
        workspace = task.get("workspacePath", "")
        if workspace:
            allowed_roots.append(Path(workspace).resolve())
    # Also allow the configured artifact root and its snapshots subdirectory
    from ..complex_agent.domain import AgentSettings

    agent_settings = await _get_agent_settings_async()
    if agent_settings.artifact_root:
        allowed_roots.append(Path(agent_settings.artifact_root).resolve())
    if not any(_is_within(path, root) for root in allowed_roots):
        raise HTTPException(status_code=403, detail="artifact path is outside allowed roots")
    return FileResponse(
        path=str(path),
        media_type=artifact["mimeType"],
        filename=artifact["name"],
    )


def _is_within(child: Path, parent: Path) -> bool:
    """Return True if ``child`` is inside ``parent`` (both resolved)."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Permission queue
# ---------------------------------------------------------------------------


@router.get("/agent/tasks/{task_id}/permissions")
async def list_pending_permissions(task_id: str) -> dict[str, Any]:
    """List pending permission requests for a task."""
    storage = require_storage()
    perms = await agent_storage.list_pending_permission_requests(storage, task_id)
    return {"permissions": perms}


@router.post("/agent/permissions/{permission_id}/approve")
async def approve_permission(permission_id: str, request: PermissionDecisionRequest) -> dict[str, Any]:
    """Approve a pending permission request."""
    storage = require_storage()
    perm = await agent_storage.get_permission_request(storage, permission_id)
    if perm is None:
        raise HTTPException(status_code=404, detail="permission request not found")
    if perm["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"permission already {perm['status']}")
    # Resolve in-memory task
    task = agent_task_hub.get(perm["taskId"])
    if task is not None:
        task.resolve_permission(permission_id, "approved")
    updated = await agent_storage.update_permission_request_status(
        storage, permission_id, status="approved", reason=request.reason
    )
    return updated or {"id": permission_id, "status": "approved"}


@router.post("/agent/permissions/{permission_id}/reject")
async def reject_permission(permission_id: str, request: PermissionDecisionRequest) -> dict[str, Any]:
    """Reject a pending permission request."""
    storage = require_storage()
    perm = await agent_storage.get_permission_request(storage, permission_id)
    if perm is None:
        raise HTTPException(status_code=404, detail="permission request not found")
    if perm["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"permission already {perm['status']}")
    # Resolve in-memory task
    task = agent_task_hub.get(perm["taskId"])
    if task is not None:
        task.resolve_permission(permission_id, "rejected")
    updated = await agent_storage.update_permission_request_status(
        storage, permission_id, status="rejected", reason=request.reason
    )
    return updated or {"id": permission_id, "status": "rejected"}
