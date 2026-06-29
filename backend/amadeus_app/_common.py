"""Shared utilities used by main.py and routers."""

from __future__ import annotations

import base64
import binascii
import json
import socket
from typing import Any

from fastapi import HTTPException, UploadFile

from .domain import ModelSettings, OrchestratorInvokeRequest, SpeechInputSettings
from .uploads.file_upload_service import (
    UploadConflictError,
    UploadTooLargeError,
    UploadValidationError,
    save_uploaded_text_file,
)

# ---------------- storage bridge (deferred import to avoid circular deps) ----------------


def _get_storage_raw():
    """Return the global SQLiteStorage singleton set by main.py."""
    from . import main as _main

    if not _main._global_storage or not _main._global_storage.connected:
        raise HTTPException(status_code=503, detail="SQLite 存储未连接；请检查 AMADEUS_SQLITE_PATH 是否可写。")
    return _main._global_storage


def require_storage():
    """Backward-compatible alias for _get_storage_raw()."""
    return _get_storage_raw()


def get_memory_tree():
    """返回全局 MemoryTreeStore 单例，未初始化时返回 None。"""
    from . import main as _main
    return _main._global_memory_tree


# ---------------- InMemoryUploadFile ----------------


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


# ---------------- base64 / upload helpers ----------------


def decode_base64_payload(data_base64: str) -> bytes:
    payload = data_base64.strip()
    if "," in payload and payload.lower().startswith("data:"):
        payload = payload.split(",", 1)[1]
    return base64.b64decode(payload, validate=True)


async def get_saved_settings_payload() -> dict[str, Any]:
    storage = _get_storage_raw()
    try:
        from .storage import DEFAULT_USER_ID

        return await storage.get_settings(DEFAULT_USER_ID) or {}
    except Exception:
        return {}


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


async def enrich_orchestrator_media_request(request: OrchestratorInvokeRequest) -> OrchestratorInvokeRequest:
    if not _orchestrator_request_uses_image_understand(request):
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


def _orchestrator_request_uses_image_understand(request: OrchestratorInvokeRequest) -> bool:
    target = request.target.strip().lower()
    if target in {"image_understand", "image_understand_agent"}:
        return True
    payload_text = json.dumps(request.payload, ensure_ascii=False).lower()
    raw_text = json.dumps(request.raw_arguments, ensure_ascii=False).lower()
    return "image_understand" in payload_text or "image_understand" in raw_text


# Backward-compatible alias for older routers/imports.
enrich_agent_media_request = enrich_orchestrator_media_request


# ---------------- network helpers ----------------


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
