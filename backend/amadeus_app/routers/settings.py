"""Settings, providers, personas, and agent routes."""

from __future__ import annotations

from fastapi import APIRouter

from .._common import enrich_agent_media_request, require_storage
from ..agent_service import capabilities_response, invoke_agent_request
from ..domain import AgentInvokeRequest, SettingsSaveRequest
from ..personas import list_personas
from ..providers import PROVIDER_PRESETS
from ..storage import DEFAULT_USER_ID

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings")
async def get_settings() -> dict:
    storage = require_storage()
    settings = await storage.get_settings(DEFAULT_USER_ID)
    return {"settings": settings}


@router.put("/settings")
async def save_settings(request: SettingsSaveRequest) -> dict:
    await require_storage().save_settings(
        user_id=DEFAULT_USER_ID,
        model=request.model.model_dump(by_alias=True),
        vision=request.vision.model_dump(by_alias=True),
        speech_input=request.speech_input.model_dump(by_alias=True),
        voice=request.voice.model_dump(by_alias=True),
        desktop_assistant=request.desktop_assistant.model_dump(by_alias=True),
        mode=request.mode,
        agent=request.agent,
        mcp_servers=request.mcp_servers,
    )
    return {"ok": True}


@router.get("/providers")
async def providers() -> dict:
    return {"providers": [item.model_dump(by_alias=True) for item in PROVIDER_PRESETS]}


@router.get("/personas")
async def personas() -> dict:
    return {"personas": list_personas()}


@router.get("/agent/capabilities")
async def agent_capabilities() -> dict:
    return capabilities_response()


@router.post("/agent/invoke")
async def agent_invoke(request: AgentInvokeRequest) -> dict:
    enriched = await enrich_agent_media_request(request)
    return await invoke_agent_request(enriched)
