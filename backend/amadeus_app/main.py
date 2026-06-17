from __future__ import annotations

import os
import mimetypes
import base64
import binascii
import asyncio
import json
import socket
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field

from .agent_service import capabilities_response, invoke_agent_request
from .chat_service import SegmentedVoiceCoordinator, resolve_chat_settings, sse, stream_chat
from .code_tasks.opencode_runner import (
    drain_opencode_task,
    reply_code_task_question_by_task,
    reply_opencode_question,
    send_remote_code_task_message,
    stop_all_opencode_processes,
    stream_opencode_task,
    wait_for_code_task_result,
)
from .code_tasks.task_hub import code_task_hub, utc_now_iso
from .domain import (
    AgentInvokeRequest,
    ChatStreamRequest,
    CodeTaskMobileQuestionReplyRequest,
    CodeTaskQuestionReplyRequest,
    CodeTaskRemoteMessageRequest,
    CodeTaskSyncRequest,
    CodeTaskStreamRequest,
    CodeTaskViewPublishRequest,
    ConversationCreateRequest,
    DesktopObserveRequest,
    SettingsSaveRequest,
    ModelSettings,
    SpeechInputSettings,
    TaskSummaryVoiceRequest,
    VisionUnderstandRequest,
    VoiceCloneRequest,
    VoiceSynthesisRequest,
)
from .personas import get_persona, list_personas
from .providers import PROVIDER_PRESETS
from .file_tools.file_reader import ensure_allowed_path, resolve_workspace_path
from .model_adapter import build_chat_payload
from .personas import build_system_prompt
from .runtime_config import effective_voice_settings
from .storage import DEFAULT_USER_ID, SQLiteStorage
from .uploads.file_upload_service import (
    UploadConflictError,
    UploadTooLargeError,
    UploadValidationError,
    save_uploaded_text_file,
)
from .vision.image_understand_agent import run_image_understand_agent
from .voice_input_service import USER_VOICE_DIR, save_and_transcribe_voice_input
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
app.mount("/user-voice", StaticFiles(directory=USER_VOICE_DIR), name="user_voice")


class FileUploadBase64Request(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    filename: str
    data_base64: str = Field(alias="dataBase64")
    content_type: str = Field(default="", alias="contentType")
    device: str = "mobile"
    overwrite: bool = False


class VoiceInputBase64Request(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    filename: str
    data_base64: str = Field(alias="dataBase64")
    content_type: str = Field(default="", alias="contentType")
    duration_seconds: float = Field(default=0, alias="durationSeconds")
    use_host_settings: bool = Field(default=True, alias="useHostSettings")
    model: ModelSettings = Field(default_factory=ModelSettings)
    speech_input: SpeechInputSettings = Field(default_factory=SpeechInputSettings, alias="speechInput")


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
    if uploaded.get("mediaType") == "image":
        next_step = "可交给 image_understand_agent 理解。"
    elif uploaded.get("mediaType") == "audio":
        next_step = "可交给语音输入模型转写。"
    else:
        next_step = "可交给 file_reader 读取。"
    return {
        "ok": True,
        "summary": f"已上传 {uploaded['filename']} 到 {uploaded['path']}，{next_step}",
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


def decode_base64_payload(data_base64: str) -> bytes:
    payload = data_base64.strip()
    if "," in payload and payload.lower().startswith("data:"):
        payload = payload.split(",", 1)[1]
    return base64.b64decode(payload, validate=True)


async def get_saved_settings_payload() -> dict[str, Any]:
    if not storage.connected:
        return {}
    try:
        return await storage.get_settings(DEFAULT_USER_ID) or {}
    except Exception:
        return {}


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
    return await invoke_agent_request(await enrich_agent_media_request(request))


async def enrich_agent_media_request(request: AgentInvokeRequest) -> AgentInvokeRequest:
    if not agent_request_uses_image_understand(request):
        return request
    stored_settings = await get_saved_settings_payload()
    payload = dict(request.payload)
    if "model" not in payload and isinstance(stored_settings.get("model"), dict):
        payload["model"] = stored_settings["model"]
    if "vision" not in payload and isinstance(stored_settings.get("vision"), dict):
        payload["vision"] = stored_settings["vision"]
    if payload == request.payload:
        return request
    return request.model_copy(update={"payload": payload})


def agent_request_uses_image_understand(request: AgentInvokeRequest) -> bool:
    target = request.target.strip().lower()
    if target in {"image_understand", "image_understand_agent"}:
        return True
    payload_text = json.dumps(request.payload, ensure_ascii=False).lower()
    raw_text = json.dumps(request.raw_arguments, ensure_ascii=False).lower()
    return "image_understand" in payload_text or "image_understand" in raw_text


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
        data = decode_base64_payload(request.data_base64)
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
        vision=request.vision.model_dump(by_alias=True),
        speech_input=request.speech_input.model_dump(by_alias=True),
        voice=request.voice.model_dump(by_alias=True),
        desktop_assistant=request.desktop_assistant.model_dump(by_alias=True),
        mode=request.mode,
    )
    return {"ok": True}


@app.post("/api/voice-input/transcribe")
async def voice_input_transcribe(
    file: UploadFile = File(...),
    model: str = Form("{}"),
    speechInput: str = Form("{}"),
    durationSeconds: float = Form(0),
) -> dict:
    try:
        model_payload = json.loads(model) if model.strip() else {}
        speech_payload = json.loads(speechInput) if speechInput.strip() else {}
        result = await save_and_transcribe_voice_input(
            file=file,
            model_settings=ModelSettings.model_validate(model_payload),
            speech_input_settings=SpeechInputSettings.model_validate(speech_payload),
            duration_seconds=durationSeconds,
        )
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Invalid voice input settings JSON") from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error
    return {
        "ok": result.get("ok", False),
        "text": result.get("text", ""),
        "audio": result.get("audio", {}),
        "error": result.get("error", ""),
    }


@app.post("/api/voice-input/transcribe-base64")
async def voice_input_transcribe_base64(request: VoiceInputBase64Request) -> dict:
    try:
        data = decode_base64_payload(request.data_base64)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=400, detail="Invalid base64 voice payload") from error

    stored_settings = await get_saved_settings_payload() if request.use_host_settings else {}
    model_payload = stored_settings.get("model") if isinstance(stored_settings.get("model"), dict) else request.model.model_dump(by_alias=True)
    speech_payload = (
        stored_settings.get("speechInput")
        if isinstance(stored_settings.get("speechInput"), dict)
        else request.speech_input.model_dump(by_alias=True)
    )
    file = InMemoryUploadFile(
        filename=request.filename,
        content_type=request.content_type,
        data=data,
    )
    try:
        result = await save_and_transcribe_voice_input(
            file=file,
            model_settings=ModelSettings.model_validate(model_payload),
            speech_input_settings=SpeechInputSettings.model_validate(speech_payload),
            duration_seconds=request.duration_seconds,
        )
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error
    return {
        "ok": result.get("ok", False),
        "text": result.get("text", ""),
        "audio": result.get("audio", {}),
        "error": result.get("error", ""),
    }


@app.post("/api/vision/understand")
async def vision_understand(request: VisionUnderstandRequest) -> dict:
    try:
        result = await run_image_understand_agent(
            {
                "prompt": request.prompt,
                "attachments": [item.model_dump(by_alias=True) for item in request.attachments],
                "model": request.model.model_dump(by_alias=True),
                "vision": request.vision.model_dump(by_alias=True),
            }
        )
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error
    return {
        "ok": True,
        "summary": result.get("summary") or "图片理解完成。",
        "data": result.get("data", {}),
    }


def parse_desktop_observe_decision(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"shouldRespond": bool(content.strip()), "response": content.strip()}
    return {
        "shouldRespond": bool(data.get("shouldRespond")),
        "response": str(data.get("response") or "").strip(),
        "emotion": str(data.get("emotion") or "neutral").strip() or "neutral",
        "reason": str(data.get("reason") or "").strip(),
    }


async def decide_desktop_observation(request: DesktopObserveRequest, vision_summary: str) -> dict[str, Any]:
    provider, settings = resolve_chat_settings(
        ChatStreamRequest(
            messages=[],
            personaId=request.persona_id,
            mode=request.mode,
            model=request.model,
        )
    )
    if not settings.use_remote:
        return {"shouldRespond": False, "response": "", "emotion": "neutral", "reason": "远程模型未启用。"}
    if not provider.compatible:
        return {"shouldRespond": False, "response": "", "emotion": "neutral", "reason": f"{provider.name} 不支持当前协议。"}
    if not settings.api_key and provider.name.lower() != "ollama":
        return {"shouldRespond": False, "response": "", "emotion": "neutral", "reason": "主模型缺少 API Key。"}

    user_prompt = request.prompt.strip() or "这是一次自动桌面观察。"
    messages = [
        {"role": "system", "content": build_system_prompt(get_persona(request.persona_id), request.mode)},
        {
            "role": "system",
            "content": (
                "你是 exe 桌面助手的主动观察决策器。"
                "你会收到屏幕视觉摘要和可能的用户语音意图。"
                "仅当画面中出现明显需要提醒、用户明确需要你看屏幕、或你能提供简短有用反馈时才回应。"
                "普通静态桌面、无变化、无用户请求时不要回应。"
                "必须只输出 JSON："
                "{\"shouldRespond\": boolean, \"response\": string, \"emotion\": string, \"reason\": string}。"
                "response 使用中文，控制在 1 到 2 句，不要提到你在看截图或调用模型。"
            ),
        },
        {
            "role": "user",
            "content": "\n\n".join(
                [
                    f"用户语音/触发说明：{user_prompt}",
                    f"屏幕视觉摘要：\n{vision_summary.strip() or '无可用摘要。'}",
                ]
            ),
        },
    ]
    payload = build_chat_payload(settings, provider, request.mode, messages, stream=False)
    endpoint = f"{settings.base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=15.0)) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"桌面观察决策失败 HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    return parse_desktop_observe_decision(str(content))


@app.post("/api/desktop/observe")
async def desktop_observe(request: DesktopObserveRequest) -> dict:
    vision_summary = ""
    if request.attachments:
        try:
            result = await run_image_understand_agent(
                {
                    "prompt": request.prompt,
                    "attachments": [item.model_dump(by_alias=True) for item in request.attachments],
                    "model": request.model.model_dump(by_alias=True),
                    "vision": request.vision.model_dump(by_alias=True),
                }
            )
            data = result.get("data")
            if isinstance(data, dict):
                vision_summary = str(data.get("description") or "").strip()
            vision_summary = vision_summary or str(result.get("summary") or "").strip()
        except Exception as error:
            raise HTTPException(status_code=502, detail=f"桌面观察视觉理解失败：{error}") from error

    try:
        decision = await decide_desktop_observation(request, vision_summary)
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error

    user_message = None
    assistant_message = None
    if (
        request.save_response
        and request.conversation_id is not None
        and decision["shouldRespond"]
        and decision["response"]
    ):
        observation_content = "\n\n".join(
            part
            for part in [
                request.prompt.strip() or "桌面自动观察",
                "<桌面视觉摘要>",
                vision_summary.strip(),
                "</桌面视觉摘要>",
            ]
            if part.strip()
        )
        user_message = await require_storage().add_message(
            user_id=DEFAULT_USER_ID,
            conversation_id=request.conversation_id,
            role="user",
            content=observation_content,
        )
        assistant_message = await require_storage().add_message(
            user_id=DEFAULT_USER_ID,
            conversation_id=request.conversation_id,
            role="assistant",
            content=decision["response"],
        )
    return {
        "ok": True,
        "shouldRespond": decision["shouldRespond"] and bool(decision["response"]),
        "response": decision["response"],
        "emotion": decision["emotion"],
        "reason": decision["reason"],
        "visionSummary": vision_summary,
        "userMessage": user_message,
        "assistantMessage": assistant_message,
    }


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


@app.get("/api/code-tasks/events")
async def code_task_events(replay: bool = False) -> StreamingResponse:
    return StreamingResponse(
        stream_code_task_events(replay=replay),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
            "bridgeWebSocket": "/api/mobile/bridge/ws",
            "bridgeTaskFallback": "/api/mobile/bridge/task",
            "bridgeQuestionReplyFallback": "/api/mobile/bridge/question/reply",
            "taskViews": "/api/mobile/task-views",
            "taskViewEvents": "/api/mobile/task-views/events",
        },
    }


@app.post("/api/mobile/task-views")
async def publish_mobile_task_view(request: CodeTaskViewPublishRequest) -> dict:
    task_id = request.task_id.strip() or str(request.snapshot.get("taskId") or "").strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="Missing taskId")
    record = await code_task_hub.publish_view_snapshot(task_id, request.snapshot)
    print(f"[mobile-view] published {describe_mobile_view_record(record)}", flush=True)
    return {"ok": True, "seq": record.seq, "taskId": task_id}


@app.get("/api/mobile/task-views")
async def mobile_task_views() -> dict:
    return {"views": await code_task_hub.list_view_snapshots()}


@app.get("/api/mobile/task-views/events")
async def mobile_task_view_events(replay: bool = True) -> StreamingResponse:
    return StreamingResponse(
        stream_mobile_task_view_events(replay=replay),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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


@app.post("/api/mobile/bridge/task")
async def mobile_bridge_task(request: Request) -> dict:
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("WebSocket fallback payload must be an object.")
        return await handle_mobile_bridge_task_command(data)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error


@app.post("/api/mobile/bridge/question/reply")
async def mobile_bridge_question_reply(request: Request) -> dict:
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("WebSocket fallback payload must be an object.")
        return await handle_mobile_bridge_question_reply(data)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
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


@app.websocket("/api/mobile/bridge/ws")
async def mobile_bridge_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    client = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
    print(f"[mobile-bridge] client connected {client}", flush=True)
    send_lock = asyncio.Lock()
    async with send_lock:
        await websocket.send_json(
            {
                "type": "hello",
                "protocolVersion": 2,
                "serverTime": utc_now_iso(),
                "message": "Amadeus mobile bridge connected.",
                "commands": ["task", "question_reply", "ping"],
                "events": ["task_view", "ack", "error", "pong"],
            }
        )

    sender = asyncio.create_task(send_mobile_bridge_views(websocket, send_lock))
    receiver = asyncio.create_task(receive_mobile_bridge_commands(websocket, send_lock))
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
    print(f"[mobile-bridge] client disconnected {client}", flush=True)


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


def describe_mobile_view_record(record: Any) -> str:
    raw_snapshot = getattr(record, "snapshot", {}) or {}
    snapshot = raw_snapshot if isinstance(raw_snapshot, dict) else {}
    raw_view = snapshot.get("view")
    raw_task = snapshot.get("task")
    view = raw_view if isinstance(raw_view, dict) else {}
    task = raw_task if isinstance(raw_task, dict) else {}
    kind = str(snapshot.get("kind") or "unknown")
    status = str(snapshot.get("status") or task.get("status") or "")
    running = snapshot.get("running")
    turn_count = 0
    event_count = 0
    if view:
        turn_count = int(view.get("turnCount") or 0)
        event_count = int(view.get("eventCount") or 0)
    task_id = str(getattr(record, "task_id", "") or snapshot.get("taskId") or "")
    return (
        f"seq={getattr(record, 'seq', '-')}"
        f" taskId={task_id or '-'}"
        f" kind={kind}"
        f" status={status or '-'}"
        f" running={running}"
        f" turns={turn_count}"
        f" events={event_count}"
    )


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


async def stream_code_task_events(*, replay: bool):
    async for record in code_task_hub.subscribe_all(replay=replay):
        yield sse(record.event, record.payload)


async def stream_mobile_task_view_events(*, replay: bool):
    async for record in code_task_hub.subscribe_view_snapshots(replay=replay):
        yield sse("task_view", record.to_dict())


async def send_mobile_bridge_views(websocket: WebSocket, send_lock: asyncio.Lock) -> None:
    async for record in code_task_hub.subscribe_view_snapshots(replay=True):
        async with send_lock:
            payload = record.to_dict()
            payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            payload_size = len(payload_text.encode("utf-8"))
            print(f"[mobile-bridge] send {describe_mobile_view_record(record)} bytes={payload_size}", flush=True)
            await websocket.send_text(payload_text)


async def receive_mobile_bridge_commands(websocket: WebSocket, send_lock: asyncio.Lock) -> None:
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
        client_request_id = str(data.get("clientRequestId") or data.get("requestId") or "")
        try:
            if command_type in {"ping", "heartbeat"}:
                async with send_lock:
                    print(f"[mobile-bridge] pong clientRequestId={client_request_id or '-'}", flush=True)
                    await websocket.send_json(
                        {
                            "type": "pong",
                            "clientRequestId": client_request_id or None,
                            "serverTime": utc_now_iso(),
                        }
                    )
                continue

            if command_type in {"task", "send_task", "message", "prompt"}:
                result = await handle_mobile_bridge_task_command(data)
                async with send_lock:
                    await websocket.send_json(
                        {
                            "type": "ack",
                            "action": "task",
                            "clientRequestId": client_request_id or None,
                            "result": result,
                        }
                    )
                continue

            if command_type in {"question_reply", "answer_question", "option", "options"}:
                result = await handle_mobile_bridge_question_reply(data)
                async with send_lock:
                    await websocket.send_json(
                        {
                            "type": "ack",
                            "action": "question_reply",
                            "clientRequestId": client_request_id or None,
                            "result": result,
                        }
                    )
                continue

            async with send_lock:
                await websocket.send_json(
                    {
                        "type": "error",
                        "clientRequestId": client_request_id or None,
                        "message": "Unsupported command type. Use task or question_reply.",
                    }
                )
        except Exception as error:
            async with send_lock:
                await websocket.send_json(
                    {
                        "type": "error",
                        "clientRequestId": client_request_id or None,
                        "message": str(error) or error.__class__.__name__,
                    }
                )


async def handle_mobile_bridge_task_command(data: dict[str, Any]) -> dict[str, Any]:
    task_id = str(data.get("taskId") or data.get("task_id") or "").strip()
    prompt = str(data.get("prompt") or data.get("text") or data.get("intent") or "").strip()
    if not prompt:
        raise ValueError("任务内容不能为空。")

    task = await code_task_hub.get_summary(task_id) if task_id else None
    if task_id and task is not None:
        return await send_remote_code_task_message(
            task_id,
            CodeTaskRemoteMessageRequest(
                prompt=prompt,
                title=str(data.get("title") or ""),
                agent=str(data.get("agent") or ""),
                providerId=str(data.get("providerId") or data.get("provider_id") or ""),
                modelId=str(data.get("modelId") or data.get("model_id") or ""),
                autoApprove=parse_bool(data.get("autoApprove") if "autoApprove" in data else data.get("auto_approve")),
                timeoutSeconds=parse_int(data.get("timeoutSeconds") or data.get("timeout_seconds"), 1800),
            ),
        )

    next_task_id = task_id or uuid4().hex
    workspace_path = str(data.get("workspacePath") or data.get("workspace_path") or "")
    title = str(data.get("title") or "").strip() or prompt.replace("\n", " ")[:32] or "OpenCode 任务"
    await code_task_hub.ensure_task(
        task_id=next_task_id,
        title=title,
        prompt=prompt,
        workspace_path=workspace_path,
        status="queued",
        running=True,
        session_id=str(data.get("sessionId") or data.get("session_id") or ""),
    )
    request = CodeTaskStreamRequest(
        taskId=next_task_id,
        title=title,
        prompt=prompt,
        workspacePath=workspace_path,
        agent=str(data.get("agent") or ""),
        providerId=str(data.get("providerId") or data.get("provider_id") or ""),
        modelId=str(data.get("modelId") or data.get("model_id") or ""),
        sessionId=str(data.get("sessionId") or data.get("session_id") or ""),
        source="mobile",
        autoApprove=parse_bool(data.get("autoApprove") if "autoApprove" in data else data.get("auto_approve")),
        timeoutSeconds=parse_int(data.get("timeoutSeconds") or data.get("timeout_seconds"), 1800),
    )
    background_task = asyncio.create_task(drain_opencode_task(request))
    await code_task_hub.attach_background_task(next_task_id, background_task)
    return {
        "ok": True,
        "taskId": next_task_id,
        "mode": "background",
        "summary": "已在主机端创建并启动 OpenCode 任务。",
    }


async def handle_mobile_bridge_question_reply(data: dict[str, Any]) -> dict[str, Any]:
    task_id = str(data.get("taskId") or data.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("question_reply 缺少 taskId。")
    return await reply_code_task_question_by_task(
        task_id,
        CodeTaskMobileQuestionReplyRequest(
            requestId=str(data.get("requestId") or data.get("request_id") or ""),
            answers=normalize_mobile_question_answers(data),
            v2=parse_bool(data.get("v2"), default=True),
            reject=parse_bool(data.get("reject")),
        ),
    )


def normalize_mobile_question_answers(data: dict[str, Any]) -> list[list[str]]:
    answers = data.get("answers")
    if isinstance(answers, list):
        normalized: list[list[str]] = []
        for answer in answers:
            if isinstance(answer, list):
                normalized.append([str(item).strip() for item in answer if str(item).strip()])
            elif str(answer).strip():
                normalized.append([str(answer).strip()])
        return normalized
    answer = data.get("answer")
    if isinstance(answer, list):
        return [[str(item).strip() for item in answer if str(item).strip()]]
    if str(answer or "").strip():
        return [[str(answer).strip()]]
    return []


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
    assistant_voice = {
        "audioUrls": [audio.url],
        "segments": [{"index": 0, "url": audio.url}],
    }
    if request.conversation_id is not None and request.message_id is not None:
        try:
            await require_storage().update_assistant_message_voice(
                user_id=DEFAULT_USER_ID,
                conversation_id=request.conversation_id,
                message_id=request.message_id,
                assistant_voice=assistant_voice,
            )
        except Exception:
            pass
    return {"audioUrl": audio.url, "audioUrls": [audio.url], "assistantVoice": assistant_voice}


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
