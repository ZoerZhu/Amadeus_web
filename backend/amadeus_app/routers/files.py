"""File upload and download routes."""

from __future__ import annotations

import binascii
import mimetypes

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .._common import (
    InMemoryUploadFile,
    decode_base64_payload,
    get_saved_settings_payload,
    save_upload_or_raise,
    upload_success_response,
)
from ..domain import ModelSettings, SpeechInputSettings
from ..file_tools.file_reader import ensure_allowed_path, resolve_workspace_path
from ..voice_input_service import save_and_transcribe_voice_input

router = APIRouter(prefix="/api", tags=["files"])


class FileUploadBase64Request:
    """Re-imported from main for router independence."""

    def __init__(self, *, filename: str, data_base64: str, content_type: str = "", device: str = "mobile", overwrite: bool = False) -> None:
        self.filename = filename
        self.data_base64 = data_base64
        self.content_type = content_type
        self.device = device
        self.overwrite = overwrite


class VoiceInputBase64Request:
    """Re-imported from main for router independence."""

    def __init__(
        self,
        *,
        filename: str,
        data_base64: str,
        content_type: str = "",
        duration_seconds: float = 0,
        use_host_settings: bool = True,
        model: ModelSettings | None = None,
        speech_input: SpeechInputSettings | None = None,
    ) -> None:
        self.filename = filename
        self.data_base64 = data_base64
        self.content_type = content_type
        self.duration_seconds = duration_seconds
        self.use_host_settings = use_host_settings
        self.model = model or ModelSettings()
        self.speech_input = speech_input or SpeechInputSettings()


@router.post("/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    device: str = Form("host"),
    overwrite: bool = Form(False),
) -> dict:
    uploaded = await save_upload_or_raise(file, device=device, overwrite=overwrite)
    return upload_success_response(uploaded)


@router.post("/files/upload-base64")
async def upload_file_base64(
    filename: str = Form(...),
    data_base64: str = Form(..., alias="dataBase64"),
    content_type: str = Form("", alias="contentType"),
    device: str = Form("mobile"),
    overwrite: bool = Form(False),
) -> dict:
    try:
        data = decode_base64_payload(data_base64)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=400, detail="Invalid base64 file payload") from error
    file = InMemoryUploadFile(
        filename=filename,
        content_type=content_type,
        data=data,
    )
    uploaded = await save_upload_or_raise(file, device=device, overwrite=overwrite)
    return upload_success_response(uploaded)


@router.get("/files/download")
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


@router.post("/voice-input/transcribe")
async def voice_input_transcribe(
    file: UploadFile = File(...),
    model: str = Form("{}"),
    speechInput: str = Form("{}"),
    durationSeconds: float = Form(0),
) -> dict:
    import json

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


@router.post("/voice-input/transcribe-base64")
async def voice_input_transcribe_base64(
    filename: str = Form(...),
    data_base64: str = Form(..., alias="dataBase64"),
    content_type: str = Form("", alias="contentType"),
    duration_seconds: float = Form(0, alias="durationSeconds"),
    use_host_settings: bool = Form(True, alias="useHostSettings"),
    model_json: str = Form("{}", alias="model"),
    speech_input_json: str = Form("{}", alias="speechInput"),
) -> dict:
    import json

    try:
        data = decode_base64_payload(data_base64)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=400, detail="Invalid base64 voice payload") from error

    stored_settings = await get_saved_settings_payload() if use_host_settings else {}
    model_payload = json.loads(model_json) if model_json.strip() else {}
    speech_payload = json.loads(speech_input_json) if speech_input_json.strip() else {}

    stored_model = stored_settings.get("model")
    stored_speech = stored_settings.get("speechInput")

    if use_host_settings and isinstance(stored_model, dict):
        model_payload = stored_model
    if use_host_settings and isinstance(stored_speech, dict):
        speech_payload = stored_speech

    file = InMemoryUploadFile(
        filename=filename,
        content_type=content_type,
        data=data,
    )
    try:
        result = await save_and_transcribe_voice_input(
            file=file,
            model_settings=ModelSettings.model_validate(model_payload),
            speech_input_settings=SpeechInputSettings.model_validate(speech_payload),
            duration_seconds=duration_seconds,
        )
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error
    return {
        "ok": result.get("ok", False),
        "text": result.get("text", ""),
        "audio": result.get("audio", {}),
        "error": result.get("error", ""),
    }
