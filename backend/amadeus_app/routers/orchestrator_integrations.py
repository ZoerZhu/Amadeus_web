"""API routes for MCP servers and skill packages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile

from .._common import require_storage
from ..orchestrator_integrations import skills as skills_module
from ..orchestrator_integrations import storage as integration_storage
from ..orchestrator_integrations.builtin_skills import (
    get_builtin_skill_info,
    install_builtin_skills,
    list_builtin_skill_ids,
)
from ..orchestrator_integrations.domain import (
    McpServerConfig,
    McpServerTestRequest,
    McpServerUpsertRequest,
    SkillImportRequest,
    SkillUpsertRequest,
)
from ..orchestrator_integrations.mcp_client import mcp_manager
from ..orchestrator_integrations.mcp_presets import get_preset, list_presets
from ..orchestrator_integrations.skills import DEFAULT_SKILLS_DIR
from ..logging_config import get_logger
from ..storage import DEFAULT_USER_ID

_log = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["orchestrator-integrations"])


@router.get("/mcp/servers")
async def list_mcp_servers() -> dict:
    storage = require_storage()
    servers = await integration_storage.list_mcp_servers(storage, DEFAULT_USER_ID)
    for server in servers:
        client = mcp_manager.get(server["id"])
        server["connected"] = client.connected if client else False
    return {"servers": servers}


@router.put("/mcp/servers")
async def upsert_mcp_server(request: McpServerUpsertRequest) -> dict:
    storage = require_storage()
    server_dict = request.server.model_dump(by_alias=True)
    saved = await integration_storage.upsert_mcp_server(storage, user_id=DEFAULT_USER_ID, server=server_dict)
    return {"server": saved}


@router.delete("/mcp/servers/{server_id}")
async def delete_mcp_server(server_id: str) -> dict:
    storage = require_storage()
    await mcp_manager.disconnect_server(server_id)
    deleted = await integration_storage.delete_mcp_server(storage, server_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="mcp server not found")
    return {"ok": True}


@router.post("/mcp/servers/test")
async def test_mcp_server(request: McpServerTestRequest) -> dict:
    config = request.server
    if not config.id:
        config.id = f"test-{request.server.name or 'server'}"
    return await mcp_manager.test_connection(config)


@router.post("/mcp/servers/{server_id}/connect")
async def connect_mcp_server(server_id: str) -> dict:
    storage = require_storage()
    servers = await integration_storage.list_mcp_servers(storage, DEFAULT_USER_ID)
    server = next((item for item in servers if item["id"] == server_id), None)
    if server is None:
        raise HTTPException(status_code=404, detail="mcp server not found")
    config = McpServerConfig.model_validate(server)
    client = await mcp_manager.connect_server(config)
    return {"ok": True, "toolCount": len(client.tools), "resourceCount": len(client.resources)}


@router.post("/mcp/servers/{server_id}/disconnect")
async def disconnect_mcp_server(server_id: str) -> dict:
    return {"ok": await mcp_manager.disconnect_server(server_id)}


@router.get("/mcp/presets")
async def list_mcp_presets() -> dict[str, Any]:
    return {"presets": list_presets()}


@router.get("/mcp/presets/{preset_id}")
async def get_mcp_preset(preset_id: str) -> dict[str, Any]:
    preset = get_preset(preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="preset not found")
    return preset


@router.get("/mcp/capabilities")
async def list_mcp_capabilities() -> dict[str, Any]:
    return {"capabilities": mcp_manager.list_capability_cache()}


@router.get("/skills")
async def list_skills() -> dict:
    storage = require_storage()
    skills = await integration_storage.list_skill_packages(storage, DEFAULT_USER_ID)
    return {"skills": skills}


@router.get("/skills/builtin")
async def list_builtin_skills() -> dict[str, Any]:
    return {"skills": [get_builtin_skill_info(skill_id) for skill_id in list_builtin_skill_ids()]}


@router.post("/skills/install-builtin")
async def install_builtin_skills_endpoint() -> dict[str, Any]:
    skills_dir = Path(DEFAULT_SKILLS_DIR)
    installed = install_builtin_skills(skills_dir)
    storage = require_storage()
    for skill_id in installed:
        skill_dir = skills_dir / skill_id
        try:
            info = skills_module.build_package_info(skill_dir, source="local")
        except Exception as error:  # noqa: BLE001
            _log.warning("failed to build info for built-in skill %s: %s", skill_id, error)
            continue
        await integration_storage.upsert_skill_package(
            storage,
            user_id=DEFAULT_USER_ID,
            skill=info.model_dump(by_alias=True),
        )
    return {"installed": installed}


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
    saved = await integration_storage.upsert_skill_package(
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
    saved = await integration_storage.upsert_skill_package(
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
    saved = await integration_storage.upsert_skill_package(storage, user_id=DEFAULT_USER_ID, skill=skill_dict)
    return {"skill": saved}


@router.delete("/skills/{skill_id}")
async def delete_skill(skill_id: str) -> dict:
    storage = require_storage()
    skill = await integration_storage.get_skill_package(storage, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="skill not found")
    try:
        skills_module.remove_skill_files(Path(skill["path"]))
    except Exception:  # noqa: BLE001
        pass
    deleted = await integration_storage.delete_skill_package(storage, skill_id)
    return {"ok": deleted}
