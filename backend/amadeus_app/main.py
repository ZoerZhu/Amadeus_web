from __future__ import annotations

import os
import mimetypes
import base64
import binascii
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from .agent_service import capabilities_response, invoke_agent_request
from .chat_service import resolve_chat_settings, stream_chat
from .code_tasks.opencode_runner import stream_opencode_task
from .domain import (
    AgentInvokeRequest,
    ChatStreamRequest,
    CodeTaskStreamRequest,
    ConversationCreateRequest,
    SettingsSaveRequest,
    TaskSummaryVoiceRequest,
    VoiceCloneRequest,
    VoiceSynthesisRequest,
)
from .personas import get_persona, list_personas
from .providers import PROVIDER_PRESETS
from .file_tools.file_reader import ensure_allowed_path, resolve_workspace_path
from .runtime_config import effective_voice_settings
from .storage import DEFAULT_USER_ID, SQLiteStorage
from .uploads.file_upload_service import (
    UploadConflictError,
    UploadTooLargeError,
    UploadValidationError,
    save_uploaded_text_file,
)
from .voice_service import AUDIO_DIR, clone_kurisu_voice, prepare_task_summary_speech_text, synthesize_for_persona


ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIST = ROOT_DIR / "dist"
load_dotenv(ROOT_DIR / ".env")

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("application/octet-stream", ".moc3")

SQLITE_PATH_VALUE = os.getenv("AMADEUS_SQLITE_PATH", "").strip()
SQLITE_PATH = Path(SQLITE_PATH_VALUE) if SQLITE_PATH_VALUE else ROOT_DIR / "agent_state" / "amadeus_web.sqlite3"
if not SQLITE_PATH.is_absolute():
    SQLITE_PATH = ROOT_DIR / SQLITE_PATH
storage = SQLiteStorage(SQLITE_PATH)

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


class FileUploadBase64Request(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    filename: str
    data_base64: str = Field(alias="dataBase64")
    content_type: str = Field(default="", alias="contentType")
    device: str = "mobile"
    overwrite: bool = False


class InMemoryUploadFile:
    def __init__(self, *, filename: str, content_type: str, data: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self._data = data
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._data):
            return b""
        if size is None or size < 0:
            size = len(self._data) - self._offset
        end = min(self._offset + size, len(self._data))
        chunk = self._data[self._offset : end]
        self._offset = end
        return chunk

    async def close(self) -> None:
        self._offset = len(self._data)


@app.on_event("startup")
async def startup() -> None:
    await storage.connect()


@app.on_event("shutdown")
async def shutdown() -> None:
    await storage.close()


def require_storage() -> SQLiteStorage:
    if not storage.connected:
        raise HTTPException(
            status_code=503,
            detail="SQLite 存储未连接；请检查 AMADEUS_SQLITE_PATH 是否可写。",
        )
    return storage


def upload_success_response(uploaded: dict) -> dict:
    return {
        "ok": True,
        "summary": f"已上传 {uploaded['filename']} 到 {uploaded['path']}，可交给 file_reader 读取。",
        "file": uploaded,
        "data": {
            "file": uploaded,
        },
    }


async def save_upload_or_raise(file: object, *, device: str, overwrite: bool) -> dict:
    try:
        return await save_uploaded_text_file(file, device=device, overwrite=overwrite)
    except UploadConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except UploadTooLargeError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except UploadValidationError as error:
        raise HTTPException(status_code=415, detail=str(error)) from error


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}


@app.get("/api/storage")
async def storage_status() -> dict:
    return {
        "enabled": storage.enabled,
        "connected": storage.connected,
        "type": "sqlite",
        "path": str(storage.database_path),
    }


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


@app.post("/api/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    device: str = Form("host"),
    overwrite: bool = Form(False),
) -> dict:
    uploaded = await save_upload_or_raise(file, device=device, overwrite=overwrite)
    return upload_success_response(uploaded)


@app.post("/api/files/upload-base64")
async def upload_file_base64(request: FileUploadBase64Request) -> dict:
    try:
        payload = request.data_base64.strip()
        if "," in payload and payload.lower().startswith("data:"):
            payload = payload.split(",", 1)[1]
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=400, detail="Invalid base64 file payload") from error
    file = InMemoryUploadFile(
        filename=request.filename,
        content_type=request.content_type,
        data=data,
    )
    uploaded = await save_upload_or_raise(file, device=request.device, overwrite=request.overwrite)
    return upload_success_response(uploaded)


@app.get("/api/files/download")
async def download_file(path: str) -> FileResponse:
    path_text = path.strip()
    if not path_text:
        raise HTTPException(status_code=400, detail="Missing file path")
    try:
        target = resolve_workspace_path(path_text)
        ensure_allowed_path(target)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Path is not a file")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type, filename=target.name)


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


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: UUID) -> dict:
    deleted = await require_storage().delete_conversation(user_id=DEFAULT_USER_ID, conversation_id=conversation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


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


@app.post("/api/code-tasks/stream")
async def code_task_stream(request: CodeTaskStreamRequest) -> StreamingResponse:
    return StreamingResponse(
        stream_opencode_task(request),
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


@app.post("/api/voice/task-summary")
async def voice_task_summary(request: TaskSummaryVoiceRequest) -> dict:
    provider, settings = resolve_chat_settings(
        ChatStreamRequest(
            messages=[],
            personaId=request.persona_id,
            model=request.model,
            voice=request.voice,
        )
    )
    persona = get_persona(request.persona_id)
    try:
        speech_text = await prepare_task_summary_speech_text(
            summary=request.summary,
            persona=persona,
            model_settings=settings,
            provider=provider,
        )
        audio = await synthesize_for_persona(
            text=speech_text,
            persona=persona,
            voice_settings=effective_voice_settings(request.voice),
            model_settings=settings,
            provider=provider,
        )
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error
    if audio is None:
        raise HTTPException(status_code=400, detail="语音服务未返回音频，请检查当前语音模型配置。")
    return {"audioUrl": audio.url, "speechText": speech_text}


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
