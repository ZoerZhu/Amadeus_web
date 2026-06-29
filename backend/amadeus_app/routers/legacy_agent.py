"""Legacy API routes for the retired agent task runtime.

Endpoints kept during migration:
- POST   /api/agent/tasks/stream       deprecated task creation
- GET    /api/agent/tasks              deprecated task list
- GET    /api/agent/tasks/{id}         deprecated task detail
- POST   /api/agent/tasks/{id}/control deprecated control
- GET    /api/agent/tasks/{id}/events  deprecated event stream
- GET    /api/artifacts/{id}           legacy artifact metadata
- GET    /api/artifacts/{id}/download  legacy artifact download
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .._common import require_storage
from ..orchestrator import storage as orchestrator_storage
from ..orchestrator.domain import OrchestratorSettings
from ..storage import DEFAULT_USER_ID

router = APIRouter(prefix="/api", tags=["legacy-agent"])


def _deprecated_agent_task_api() -> None:
    raise HTTPException(
        status_code=410,
        detail="Deprecated agent task API. Use /api/orchestrator/tasks instead.",
        headers={
            "Deprecation": "true",
            "X-Amadeus-Replacement": "/api/orchestrator/tasks",
        },
    )


# ---------------------------------------------------------------------------
# Retired task runtime
# ---------------------------------------------------------------------------


@router.post("/agent/tasks/stream")
async def create_agent_task_stream() -> None:
    _deprecated_agent_task_api()


@router.post("/agent/tasks")
async def create_agent_task() -> None:
    _deprecated_agent_task_api()


@router.get("/agent/tasks")
async def list_agent_tasks() -> None:
    _deprecated_agent_task_api()


@router.get("/agent/tasks/{task_id}")
async def get_agent_task_detail(task_id: str) -> None:
    _deprecated_agent_task_api()


@router.post("/agent/tasks/{task_id}/control")
async def control_agent_task(task_id: str) -> None:
    _deprecated_agent_task_api()


@router.get("/agent/tasks/{task_id}/events")
async def stream_agent_task_events(task_id: str) -> None:
    _deprecated_agent_task_api()


# ---------------------------------------------------------------------------
# Legacy artifacts
# ---------------------------------------------------------------------------


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str) -> dict:
    storage = require_storage()
    artifact = await orchestrator_storage.get_legacy_artifact(storage, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return {"artifact": artifact}


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(artifact_id: str) -> FileResponse:
    storage = require_storage()
    artifact = await orchestrator_storage.get_legacy_artifact(storage, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    path = Path(artifact["path"]).resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail="artifact file missing")
    # Path boundary check: the artifact must live inside its task's workspace
    # or the configured artifact root. This prevents dirty DB rows from
    # enabling arbitrary file downloads.
    task = await orchestrator_storage.get_legacy_task(storage, artifact.get("taskId", ""))
    allowed_roots: list[Path] = []
    if task:
        workspace = task.get("workspacePath", "")
        if workspace:
            allowed_roots.append(Path(workspace).resolve())
    app_settings = await storage.get_settings(DEFAULT_USER_ID)
    agent_dict = (app_settings or {}).get("orchestrator") or (app_settings or {}).get("agent") or {}
    agent_settings = OrchestratorSettings.model_validate(agent_dict)
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
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Retired permission queue
# ---------------------------------------------------------------------------


@router.get("/agent/tasks/{task_id}/permissions")
async def list_pending_permissions(task_id: str) -> dict[str, Any]:
    _deprecated_agent_task_api()


@router.post("/agent/permissions/{permission_id}/approve")
async def approve_permission(permission_id: str) -> None:
    _deprecated_agent_task_api()


@router.post("/agent/permissions/{permission_id}/reject")
async def reject_permission(permission_id: str) -> None:
    _deprecated_agent_task_api()
