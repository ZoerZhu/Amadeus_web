from __future__ import annotations

import mimetypes
import os
import base64
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from .domain import ModelSettings, SpeechInputSettings
from .providers import get_provider
from .runtime_config import effective_model_settings, effective_speech_input_settings


WORKSPACE_PATH_VALUE = os.getenv("AMADEUS_WORKSPACE_PATH", "").strip()
WORKSPACE_PATH = Path(WORKSPACE_PATH_VALUE).expanduser().resolve() if WORKSPACE_PATH_VALUE else Path(__file__).resolve().parents[2]
USER_VOICE_DIR_VALUE = os.getenv("AMADEUS_USER_VOICE_DIR", "").strip()
USER_VOICE_DIR = Path(USER_VOICE_DIR_VALUE).expanduser() if USER_VOICE_DIR_VALUE else Path("user_voice")
if not USER_VOICE_DIR.is_absolute():
    USER_VOICE_DIR = (WORKSPACE_PATH / USER_VOICE_DIR).resolve()
USER_VOICE_DIR.mkdir(parents=True, exist_ok=True)

VOICE_INPUT_MAX_BYTES = 25 * 1024 * 1024
VOICE_INPUT_CHUNK_SIZE = 1024 * 1024
VOICE_INPUT_EXTENSIONS = {
    ".webm",
    ".wav",
    ".mp3",
    ".m4a",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".ogg",
    ".oga",
}


class VoiceInputError(ValueError):
    pass


async def save_and_transcribe_voice_input(
    *,
    file: Any,
    model_settings: ModelSettings,
    speech_input_settings: SpeechInputSettings,
    duration_seconds: float = 0,
) -> dict[str, Any]:
    audio = await save_user_voice_file(file=file, duration_seconds=duration_seconds)
    try:
        text = await transcribe_user_voice_file(
            audio_path=Path(audio["absolutePath"]),
            model_settings=model_settings,
            speech_input_settings=speech_input_settings,
        )
    except Exception as error:
        return {
            "ok": False,
            "text": "",
            "audio": public_audio_payload(audio),
            "error": str(error) or error.__class__.__name__,
        }
    return {
        "ok": True,
        "text": text,
        "audio": public_audio_payload(audio),
        "error": "",
    }


async def save_user_voice_file(*, file: Any, duration_seconds: float) -> dict[str, Any]:
    raw_filename = str(getattr(file, "filename", "") or "").strip()
    suffix = Path(raw_filename).suffix.lower() or suffix_from_content_type(str(getattr(file, "content_type", "") or ""))
    if suffix not in VOICE_INPUT_EXTENSIONS:
        raise VoiceInputError(f"Unsupported voice input file type: {suffix or raw_filename or 'unknown'}")

    date_text = current_date_text()
    target_dir = USER_VOICE_DIR / date_text
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"user-voice-{datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%H%M%S')}-{uuid4().hex[:10]}{suffix}"
    target_path = (target_dir / filename).resolve()
    ensure_inside_user_voice_dir(target_path)

    size = 0
    try:
        with target_path.open("wb") as output:
            while True:
                chunk = await file.read(VOICE_INPUT_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > voice_input_max_bytes():
                    raise VoiceInputError(f"Voice input exceeds {voice_input_max_bytes()} bytes")
                output.write(chunk)
    except Exception:
        target_path.unlink(missing_ok=True)
        raise
    finally:
        close = getattr(file, "close", None)
        if close is not None:
            await close()

    rel_path = relative_user_voice_path(target_path)
    content_type = normalize_content_type(str(getattr(file, "content_type", "") or "")) or mimetypes.guess_type(filename)[0] or "audio/webm"
    return {
        "absolutePath": str(target_path),
        "path": rel_path,
        "url": f"/user-voice/{rel_path}",
        "filename": filename,
        "contentType": content_type,
        "sizeBytes": size,
        "durationSeconds": max(0, duration_seconds),
        "uploadedAt": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
    }


async def transcribe_user_voice_file(
    *,
    audio_path: Path,
    model_settings: ModelSettings,
    speech_input_settings: SpeechInputSettings,
) -> str:
    provider = get_provider(model_settings.provider_name)
    effective_model = effective_model_settings(model_settings, provider)
    provider = get_provider(effective_model.provider_name)
    effective_model = effective_model_settings(effective_model, provider)
    speech_provider = get_provider(speech_input_settings.provider_name or effective_model.provider_name or provider.name)
    settings = effective_speech_input_settings(speech_input_settings, effective_model, speech_provider)
    speech_provider = get_provider(settings.provider_name)

    if not settings.enabled:
        raise VoiceInputError("语音输入转写已关闭。")
    if not settings.use_remote:
        raise VoiceInputError("语音输入转写需要启用远程 OpenAI-compatible API。")
    if not speech_provider.compatible:
        raise VoiceInputError(f"{speech_provider.name} 当前未接入语音输入专用协议。")
    if not settings.api_key and speech_provider.name.lower() != "ollama":
        raise VoiceInputError("语音输入转写需要配置 API Key。")
    if not audio_path.exists():
        raise FileNotFoundError(str(audio_path))

    if is_mimo_asr_settings(settings):
        return await transcribe_with_mimo_asr(audio_path=audio_path, settings=settings)

    endpoint = f"{settings.base_url.rstrip('/')}/audio/transcriptions"
    headers: dict[str, str] = {}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    data = {
        "model": settings.model,
    }
    if should_send_openai_transcription_options(settings):
        data["response_format"] = "json"
    if settings.language and should_send_language_parameter(settings):
        data["language"] = settings.language
    content_type = mimetypes.guess_type(audio_path.name)[0] or "audio/webm"
    with audio_path.open("rb") as input_file:
        files = {"file": (audio_path.name, input_file, content_type)}
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
            response = await client.post(endpoint, headers=headers, data=data, files=files)
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"语音输入转写失败 HTTP {response.status_code}: {response.text[:500]}")
    payload = response.json()
    text = str(payload.get("text") or "").strip()
    if not text:
        raise RuntimeError("语音输入转写未返回文本。")
    return text


async def transcribe_with_mimo_asr(*, audio_path: Path, settings: SpeechInputSettings) -> str:
    suffix = audio_path.suffix.lower()
    if suffix not in {".wav", ".mp3"}:
        raise VoiceInputError("MiMo 语音识别仅支持 wav/mp3，请重新录音或转换格式。")

    content_type = "audio/wav" if suffix == ".wav" else "audio/mpeg"
    audio_base64 = base64.b64encode(audio_path.read_bytes()).decode("ascii")
    data_url = f"data:{content_type};base64,{audio_base64}"
    endpoint = f"{settings.base_url.rstrip('/')}/chat/completions"
    headers = {
        "api-key": settings.api_key,
        "Content-Type": "application/json",
    }
    user_payload: dict[str, Any] = {
        "type": "input_audio",
        "input_audio": {
            "data": data_url,
        },
    }
    payload = {
        "model": settings.model,
        "messages": [
            {
                "role": "user",
                "content": [user_payload],
            }
        ],
    }
    if settings.language:
        payload["asr_options"] = {"language": settings.language}
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"MiMo 语音识别失败 HTTP {response.status_code}: {response.text[:500]}")
    payload = response.json()
    message = ((payload.get("choices") or [{}])[0].get("message") or {})
    content = extract_message_text(message.get("content"))
    if not content:
        raise RuntimeError("MiMo 语音识别未返回文本。")
    return content


def extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "".join(parts).strip()
    return str(content or "").strip()


def is_mimo_asr_settings(settings: SpeechInputSettings) -> bool:
    haystack = f"{settings.provider_name} {settings.base_url} {settings.model}".lower()
    return "mimo" in haystack or "xiaomimimo" in haystack or "小米" in settings.provider_name


def should_send_openai_transcription_options(settings: SpeechInputSettings) -> bool:
    haystack = f"{settings.provider_name} {settings.base_url} {settings.model}".lower()
    return "openai" in haystack or "whisper" in haystack


def should_send_language_parameter(settings: SpeechInputSettings) -> bool:
    haystack = f"{settings.provider_name} {settings.base_url} {settings.model}".lower()
    return "openai" in haystack or "whisper" in haystack


def public_audio_payload(audio: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in audio.items() if key != "absolutePath"}


def suffix_from_content_type(content_type: str) -> str:
    value = normalize_content_type(content_type)
    if value in {"audio/webm", "video/webm"}:
        return ".webm"
    if value in {"audio/wav", "audio/wave", "audio/x-wav"}:
        return ".wav"
    if value in {"audio/mpeg", "audio/mp3"}:
        return ".mp3"
    if value in {"audio/mp4", "audio/x-m4a"}:
        return ".m4a"
    if value in {"audio/ogg", "application/ogg"}:
        return ".ogg"
    return ".webm"


def voice_input_max_bytes() -> int:
    raw = os.getenv("AMADEUS_VOICE_INPUT_MAX_BYTES", "").strip()
    try:
        value = int(raw) if raw else VOICE_INPUT_MAX_BYTES
    except ValueError:
        value = VOICE_INPUT_MAX_BYTES
    return max(1, value)


def ensure_inside_user_voice_dir(path: Path) -> None:
    try:
        path.relative_to(USER_VOICE_DIR)
    except ValueError as error:
        raise VoiceInputError("Voice input path must stay inside user voice directory") from error


def relative_user_voice_path(path: Path) -> str:
    return str(path.resolve().relative_to(USER_VOICE_DIR)).replace("\\", "/")


def current_date_text() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def normalize_content_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()
