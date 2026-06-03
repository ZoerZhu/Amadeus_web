from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from .agent_service import capabilities_response, invoke_agent_request
from .chat_service import resolve_chat_settings, stream_chat
from .domain import (
    AgentInvokeRequest,
    ChatStreamRequest,
    ConversationCreateRequest,
    SettingsSaveRequest,
    VoiceCloneRequest,
    VoiceSynthesisRequest,
)
from .personas import get_persona, list_personas
from .providers import PROVIDER_PRESETS
from .runtime_config import effective_voice_settings
from .storage import DEFAULT_USER_ID, PostgresStorage
from .voice_service import AUDIO_DIR, clone_kurisu_voice, synthesize_for_persona


ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIST = ROOT_DIR / "dist"
load_dotenv(ROOT_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip() or os.getenv("AMADEUS_DATABASE_URL", "").strip()
storage = PostgresStorage(DATABASE_URL)

app = FastAPI(title="Amadeus Web", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")


@app.on_event("startup")
async def startup() -> None:
    await storage.connect()


@app.on_event("shutdown")
async def shutdown() -> None:
    await storage.close()


def require_storage() -> PostgresStorage:
    if not storage.connected:
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL 未配置或未连接；请设置 DATABASE_URL 或 AMADEUS_DATABASE_URL。",
        )
    return storage


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/api/storage")
async def storage_status() -> dict:
    return {"enabled": storage.enabled, "connected": storage.connected}


@app.get("/api/providers")
async def providers() -> dict:
    return {"providers": [item.model_dump(by_alias=True) for item in PROVIDER_PRESETS]}


@app.get("/api/personas")
async def personas() -> dict:
    return {"personas": list_personas()}


@app.get("/api/agent/capabilities")
async def agent_capabilities() -> dict:
    return capabilities_response()


@app.post("/api/agent/invoke")
async def agent_invoke(request: AgentInvokeRequest) -> dict:
    return await invoke_agent_request(request)


@app.get("/api/settings")
async def get_settings() -> dict:
    settings = await require_storage().get_settings(DEFAULT_USER_ID)
    return {"settings": settings}


@app.put("/api/settings")
async def save_settings(request: SettingsSaveRequest) -> dict:
    await require_storage().save_settings(
        user_id=DEFAULT_USER_ID,
        model=request.model.model_dump(by_alias=True),
        voice=request.voice.model_dump(by_alias=True),
        mode=request.mode,
    )
    return {"ok": True}


@app.get("/api/conversations")
async def conversations() -> dict:
    return {"conversations": await require_storage().list_conversations(user_id=DEFAULT_USER_ID)}


@app.post("/api/conversations")
async def create_conversation(request: ConversationCreateRequest) -> dict:
    conversation = await require_storage().create_conversation(
        user_id=DEFAULT_USER_ID,
        title=request.title,
        persona_id=request.persona_id,
        mode=request.mode,
    )
    return {"conversation": conversation}


@app.get("/api/conversations/{conversation_id}")
async def conversation(conversation_id: UUID) -> dict:
    item = await require_storage().get_conversation(user_id=DEFAULT_USER_ID, conversation_id=conversation_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"conversation": item}


@app.post("/api/chat/stream")
async def chat_stream(request: ChatStreamRequest) -> StreamingResponse:
    return StreamingResponse(
        stream_chat(request, storage=storage if storage.connected else None),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/voice/synthesize")
async def voice_synthesize(request: VoiceSynthesisRequest) -> dict:
    provider, settings = resolve_chat_settings(
        ChatStreamRequest(
            messages=[],
            personaId=request.persona_id,
            model=request.model,
            voice=request.voice,
        )
    )
    try:
        audio = await synthesize_for_persona(
            text=request.text,
            persona=get_persona(request.persona_id),
            voice_settings=effective_voice_settings(request.voice),
            model_settings=settings,
            provider=provider,
        )
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error
    if audio is None:
        raise HTTPException(status_code=400, detail="语音服务未返回音频，请检查当前语音模型配置。")
    return {"audioUrl": audio.url}


@app.post("/api/voice/clone")
async def voice_clone(request: VoiceCloneRequest) -> dict:
    try:
        uri = await clone_kurisu_voice(
            api_key=request.silicon_flow_api_key,
            custom_name=request.custom_name,
            reference_text=request.reference_text,
        )
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error
    return {"voiceUri": uri}


if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
