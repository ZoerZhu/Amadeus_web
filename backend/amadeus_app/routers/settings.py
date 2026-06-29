"""Settings, providers, personas, and legacy invoke routes."""

from __future__ import annotations

from fastapi import APIRouter, Response

from .._common import enrich_orchestrator_media_request, require_storage
from ..domain import OrchestratorInvokeRequest, SettingsSaveRequest
from ..orchestrator.domain import OrchestratorSettings
from ..orchestrator.invoke_service import orchestrator_capabilities_response, invoke_orchestrator_request
from ..personas import list_personas
from ..providers import PROVIDER_PRESETS
from ..storage import DEFAULT_USER_ID

router = APIRouter(prefix="/api", tags=["settings"])


def _mark_legacy_agent_endpoint(response: Response, replacement: str) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["X-Amadeus-Replacement"] = replacement


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
        orchestrator=request.orchestrator,
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
async def agent_capabilities(response: Response) -> dict:
    _mark_legacy_agent_endpoint(response, "/api/orchestrator/capabilities")
    storage = require_storage()
    saved_settings = await storage.get_settings(DEFAULT_USER_ID)
    orchestrator_dict = (saved_settings or {}).get("orchestrator") or (saved_settings or {}).get("agent") or {}
    orchestrator_settings = OrchestratorSettings.model_validate(orchestrator_dict)
    return orchestrator_capabilities_response(orchestrator_settings)


@router.post("/agent/invoke")
async def agent_invoke(request: OrchestratorInvokeRequest, response: Response) -> dict:
    _mark_legacy_agent_endpoint(response, "/api/orchestrator/invoke")
    enriched = await enrich_orchestrator_media_request(request)
    return await invoke_orchestrator_request(enriched)
