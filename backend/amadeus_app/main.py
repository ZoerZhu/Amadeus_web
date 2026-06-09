from __future__ import annotations

import os
import mimetypes
import base64
import binascii
import asyncio
import socket
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from .agent_service import capabilities_response, invoke_agent_request
from .chat_service import SegmentedVoiceCoordinator, resolve_chat_settings, sse, stream_chat
from .code_tasks.opencode_runner import (
    reply_code_task_question_by_task,
    reply_opencode_question,
    send_remote_code_task_message,
    stop_all_opencode_processes,
    stream_opencode_task,
    wait_for_code_task_result,
)
from .code_tasks.task_hub import code_task_hub
from .domain import (
    AgentInvokeRequest,
    ChatStreamRequest,
    CodeTaskMobileQuestionReplyRequest,
    CodeTaskQuestionReplyRequest,
    CodeTaskRemoteMessageRequest,
    CodeTaskSyncRequest,
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
    await code_task_hub.cancel_background_tasks()
    await stop_all_opencode_processes()
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


@app.post("/api/code-tasks/question/reply")
async def code_task_question_reply(request: CodeTaskQuestionReplyRequest) -> dict:
    try:
        return await reply_opencode_question(request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error


@app.post("/api/code-tasks/sync")
async def sync_code_tasks(request: CodeTaskSyncRequest) -> dict:
    synced = await code_task_hub.sync_snapshots(
        [task.model_dump(by_alias=True) for task in request.tasks]
    )
    return {"ok": True, "tasks": synced}


@app.get("/api/mobile/connect-info")
async def mobile_connect_info(request: Request) -> dict:
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    addresses = local_network_addresses()
    return {
        "scheme": request.url.scheme,
        "port": port,
        "addresses": addresses,
        "urls": [f"{request.url.scheme}://{address}:{port}" for address in addresses],
        "endpoints": {
            "tasks": "/api/mobile/code-tasks",
            "events": "/api/mobile/code-tasks/{taskId}/events",
            "websocket": "/api/mobile/code-tasks/{taskId}/ws",
            "message": "/api/mobile/code-tasks/{taskId}/messages",
            "questionReply": "/api/mobile/code-tasks/{taskId}/question/reply",
        },
    }


@app.get("/api/mobile/code-tasks")
async def mobile_code_tasks() -> dict:
    return {"tasks": await code_task_hub.list_tasks()}


@app.get("/api/mobile/code-tasks/{task_id}")
async def mobile_code_task_detail(task_id: str) -> dict:
    task = await code_task_hub.get_detail(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Code task not found")
    return {"task": task}


@app.get("/api/mobile/code-tasks/{task_id}/wait")
async def mobile_code_task_wait(
    task_id: str,
    afterSeq: int = 0,
    ignoreQuestionRequestId: str = "",
    timeoutSeconds: int = 180,
) -> dict:
    try:
        return await wait_for_code_task_result(
            task_id,
            after_seq=afterSeq,
            ignore_question_request_id=ignoreQuestionRequestId,
            timeout_seconds=timeoutSeconds,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error


@app.get("/api/mobile/code-tasks/{task_id}/events")
async def mobile_code_task_events(task_id: str, replay: bool = True) -> StreamingResponse:
    task = await code_task_hub.get_detail(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Code task not found")
    return StreamingResponse(
        stream_mobile_code_task_events(task_id, replay=replay),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/mobile/code-tasks/{task_id}/messages")
async def mobile_code_task_message(task_id: str, request: CodeTaskRemoteMessageRequest) -> dict:
    try:
        return await send_remote_code_task_message(task_id, request)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error


@app.post("/api/mobile/code-tasks/{task_id}/question/reply")
async def mobile_code_task_question_reply(task_id: str, request: CodeTaskMobileQuestionReplyRequest) -> dict:
    try:
        return await reply_code_task_question_by_task(task_id, request)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error


@app.websocket("/api/mobile/code-tasks/{task_id}/ws")
async def mobile_code_task_websocket(websocket: WebSocket, task_id: str) -> None:
    await websocket.accept()
    send_lock = asyncio.Lock()
    task = await code_task_hub.get_detail(task_id)
    if task is None:
        async with send_lock:
            await websocket.send_json({"type": "error", "message": "Code task not found"})
        await websocket.close(code=1008)
        return
    async with send_lock:
        await websocket.send_json({"type": "task", "task": task})

    sender = asyncio.create_task(send_mobile_ws_events(websocket, task_id, send_lock))
    receiver = asyncio.create_task(receive_mobile_ws_commands(websocket, task_id, send_lock))
    done, pending = await asyncio.wait(
        {sender, receiver},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task_item in pending:
        task_item.cancel()
    for task_item in done:
        try:
            await task_item
        except WebSocketDisconnect:
            pass
        except Exception:
            pass


def local_network_addresses() -> list[str]:
    addresses: list[str] = []

    def add(address: str) -> None:
        if not address or address.startswith("127.") or address == "0.0.0.0":
            return
        if address not in addresses:
            addresses.append(address)

    try:
        hostname = socket.gethostname()
        for address in socket.gethostbyname_ex(hostname)[2]:
            add(address)
    except Exception:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            add(str(sock.getsockname()[0]))
    except Exception:
        pass
    return addresses


async def stream_mobile_code_task_events(task_id: str, *, replay: bool):
    task = await code_task_hub.get_detail(task_id)
    if task is not None:
        task_snapshot = dict(task)
        task_snapshot.pop("events", None)
        yield sse("task", {"task": task_snapshot})
    try:
        async for record in code_task_hub.subscribe(task_id, replay=replay):
            yield sse(record.event, record.payload)
    except KeyError:
        yield sse("error", {"message": "Code task not found", "taskId": task_id})


async def send_mobile_ws_events(websocket: WebSocket, task_id: str, send_lock: asyncio.Lock) -> None:
    async for record in code_task_hub.subscribe(task_id, replay=False):
        async with send_lock:
            await websocket.send_json(
                {
                    "type": "event",
                    "event": record.event,
                    "payload": record.payload,
                    "seq": record.seq,
                    "createdAt": record.created_at,
                }
            )


async def receive_mobile_ws_commands(websocket: WebSocket, task_id: str, send_lock: asyncio.Lock) -> None:
    while True:
        try:
            data = await websocket.receive_json()
        except WebSocketDisconnect:
            raise
        except Exception as error:
            async with send_lock:
                await websocket.send_json({"type": "error", "message": str(error) or "Invalid websocket payload"})
            continue

        if not isinstance(data, dict):
            async with send_lock:
                await websocket.send_json({"type": "error", "message": "WebSocket payload must be an object."})
            continue

        command_type = str(data.get("type") or data.get("action") or "").strip()
        try:
            if command_type in {"message", "prompt", "send_message"}:
                result = await send_remote_code_task_message(
                    task_id,
                    CodeTaskRemoteMessageRequest(
                        prompt=str(data.get("prompt") or data.get("text") or ""),
                        title=str(data.get("title") or ""),
                        agent=str(data.get("agent") or ""),
                        providerId=str(data.get("providerId") or data.get("provider_id") or ""),
                        modelId=str(data.get("modelId") or data.get("model_id") or ""),
                        autoApprove=parse_bool(data.get("autoApprove") if "autoApprove" in data else data.get("auto_approve")),
                        timeoutSeconds=parse_int(data.get("timeoutSeconds") or data.get("timeout_seconds"), 1800),
                    ),
                )
                async with send_lock:
                    await websocket.send_json({"type": "ack", "action": "message", "result": result})
                continue

            if command_type in {"question_reply", "answer_question"}:
                result = await reply_code_task_question_by_task(
                    task_id,
                    CodeTaskMobileQuestionReplyRequest(
                        requestId=str(data.get("requestId") or data.get("request_id") or ""),
                        answers=data.get("answers") if isinstance(data.get("answers"), list) else [],
                        v2=parse_bool(data.get("v2"), default=True),
                        reject=parse_bool(data.get("reject")),
                    ),
                )
                async with send_lock:
                    await websocket.send_json({"type": "ack", "action": "question_reply", "result": result})
                continue

            async with send_lock:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "Unsupported command type. Use message or question_reply.",
                    }
                )
        except Exception as error:
            async with send_lock:
                await websocket.send_json({"type": "error", "message": str(error) or error.__class__.__name__})


def parse_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


@app.post("/api/voice/task-summary/stream")
async def voice_task_summary_stream(request: TaskSummaryVoiceRequest) -> StreamingResponse:
    return StreamingResponse(
        stream_task_summary_voice(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def stream_task_summary_voice(request: TaskSummaryVoiceRequest):
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
        voice_settings = effective_voice_settings(request.voice).model_copy(
            update={"auto_play": True, "sync_text_output": True}
        )
        voice_stream = SegmentedVoiceCoordinator(
            persona=persona,
            voice_settings=voice_settings,
            model_settings=settings,
            provider=provider,
        )
        for event in voice_stream.startup_events():
            yield event
        for event in voice_stream.accept(speech_text):
            yield event
        for event in voice_stream.finish():
            yield event
        async for event in voice_stream.drain_all():
            yield event
        yield sse("done", {"speechText": speech_text})
    except Exception as error:
        yield sse("error", {"message": str(error) or error.__class__.__name__})


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
