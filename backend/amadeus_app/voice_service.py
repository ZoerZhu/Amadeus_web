from __future__ import annotations

import base64
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

from .domain import ModelProviderPreset, ModelSettings, VoiceSettings
from .local_voice_service import (
    prepare_local_cosyvoice_payload,
    request_local_cosyvoice_pcm,
    synthesize_segments_with_local_cosyvoice,
    synthesize_with_local_cosyvoice,
    write_local_pcm_wav,
)
from .model_adapter import build_chat_payload
from .personas import KURISU_REFERENCE_TEXT, PersonaPreset
from .text_cleaning import clean_display_text, has_japanese_kana, to_speech_source_text, to_tts_input_text


BACKEND_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = BACKEND_DIR / "assets"
AUDIO_DIR_VALUE = os.getenv("AMADEUS_AUDIO_DIR", "").strip()
AUDIO_DIR = Path(AUDIO_DIR_VALUE).expanduser().resolve() if AUDIO_DIR_VALUE else BACKEND_DIR / "runtime" / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

SILICONFLOW_API_BASE_URL = "https://api.siliconflow.cn/v1"
SILICONFLOW_TTS_MODEL = "FunAudioLLM/CosyVoice2-0.5B"
SILICONFLOW_RESPONSE_FORMAT = "mp3"


@dataclass(frozen=True)
class AudioResult:
    file_path: Path
    url: str


async def synthesize_for_persona(
    *,
    text: str,
    persona: PersonaPreset,
    voice_settings: VoiceSettings,
    model_settings: ModelSettings,
    provider: ModelProviderPreset,
) -> AudioResult | None:
    if voice_settings.tts_backend == "local":
        local_audio = await synthesize_with_local_cosyvoice(
            text=text,
            persona=persona,
            voice_settings=voice_settings,
            model_settings=model_settings,
            provider=provider,
            audio_dir=AUDIO_DIR,
            translate=translate_for_speech,
        )
        if local_audio is None:
            return None
        file_path, url = local_audio
        return AudioResult(file_path=file_path, url=url)

    return await synthesize_with_siliconflow(
        text=text,
        persona=persona,
        voice_settings=voice_settings,
        model_settings=model_settings,
        provider=provider,
    )


async def synthesize_local_segments_for_persona(
    *,
    texts: list[str],
    persona: PersonaPreset,
    voice_settings: VoiceSettings,
    model_settings: ModelSettings,
    provider: ModelProviderPreset,
) -> AudioResult | None:
    local_audio = await synthesize_segments_with_local_cosyvoice(
        texts=texts,
        persona=persona,
        voice_settings=voice_settings,
        model_settings=model_settings,
        provider=provider,
        audio_dir=AUDIO_DIR,
        translate=translate_for_speech,
    )
    if local_audio is None:
        return None
    file_path, url = local_audio
    return AudioResult(file_path=file_path, url=url)


async def synthesize_local_segment_pcm_for_persona(
    *,
    text: str,
    persona: PersonaPreset,
    voice_settings: VoiceSettings,
    model_settings: ModelSettings,
    provider: ModelProviderPreset,
) -> bytes | None:
    payload = await prepare_local_cosyvoice_payload(
        text=text,
        persona=persona,
        voice_settings=voice_settings,
        model_settings=model_settings,
        provider=provider,
        translate=translate_for_speech,
    )
    if payload is None:
        return None
    return await request_local_cosyvoice_pcm(payload=payload, voice_settings=voice_settings)


def write_local_pcm_audio(pcm: bytes) -> AudioResult:
    file_path, url = write_local_pcm_wav(audio_dir=AUDIO_DIR, pcm=pcm)
    return AudioResult(file_path=file_path, url=url)


async def synthesize_with_siliconflow(
    *,
    text: str,
    persona: PersonaPreset,
    voice_settings: VoiceSettings,
    model_settings: ModelSettings,
    provider: ModelProviderPreset,
) -> AudioResult | None:
    api_key = voice_settings.silicon_flow_api_key.strip()
    voice = voice_settings.cloned_voice_uri.strip() or (persona.tts_voice_id or "")
    if not api_key or not voice:
        return None

    source_text = to_speech_source_text(text)
    if not source_text:
        return None

    speech_text = source_text
    if persona.voice_output_language_code == "ja":
        speech_text = await translate_for_speech(
            text=source_text,
            model_settings=model_settings,
            provider=provider,
        )
    speech_text = to_tts_input_text(speech_text)
    if not speech_text:
        return None

    payload = {
        "model": SILICONFLOW_TTS_MODEL,
        "input": speech_text,
        "voice": voice,
        "response_format": SILICONFLOW_RESPONSE_FORMAT,
        "sample_rate": 44100,
        "speed": voice_settings.speed,
        "gain": voice_settings.gain,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=20.0)) as client:
        response = await client.post(
            f"{SILICONFLOW_API_BASE_URL}/audio/speech",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"SiliconFlow 请求失败 HTTP {response.status_code}: {response.text[:500]}")

    filename = f"{uuid.uuid4().hex}.{SILICONFLOW_RESPONSE_FORMAT}"
    file_path = AUDIO_DIR / filename
    file_path.write_bytes(response.content)
    return AudioResult(file_path=file_path, url=f"/audio/{filename}")


async def clone_kurisu_voice(
    *,
    api_key: str,
    custom_name: str,
    reference_text: str = "",
) -> str:
    api_key = api_key.strip()
    if not api_key:
        raise ValueError("SiliconFlow API Key 不能为空")
    reference_text = reference_text.strip() or KURISU_REFERENCE_TEXT
    custom_name = custom_name.strip() or "siliconflow-kurisu-web"
    reference_file = ASSETS_DIR / "voices" / "kurisu-reference.mp3"
    audio_data = base64.b64encode(reference_file.read_bytes()).decode("ascii")
    fields = {
        "audio": f"data:audio/mpeg;base64,{audio_data}",
        "model": SILICONFLOW_TTS_MODEL,
        "customName": custom_name,
        "text": reference_text,
    }
    files = {name: (None, value) for name, value in fields.items()}
    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=20.0)) as client:
        response = await client.post(
            f"{SILICONFLOW_API_BASE_URL}/uploads/audio/voice",
            headers={"Authorization": f"Bearer {api_key}"},
            files=files,
        )
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"SiliconFlow 语音克隆失败 HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    uri = data.get("uri", "")
    if not uri:
        raise RuntimeError(f"语音克隆接口未返回 uri: {data}")
    return uri


async def translate_for_speech(
    *,
    text: str,
    model_settings: ModelSettings,
    provider: ModelProviderPreset,
) -> str:
    cleaned = to_speech_source_text(text)
    if not cleaned or has_japanese_kana(cleaned):
        return to_tts_input_text(cleaned)
    if not model_settings.use_remote or not provider.compatible:
        return to_tts_input_text(cleaned)
    if not model_settings.api_key and model_settings.provider_name.lower() != "ollama":
        return to_tts_input_text(cleaned)

    payload = build_chat_payload(
        model_settings,
        provider,
        "fast",
        [
            {
                "role": "system",
                "content": (
                    "你是专业翻译助手。把输入翻译成自然口语日语，只返回适合 TTS 直接朗读的日语译文。"
                    "不要解释，不要 Markdown，不要编号、项目符号、引号、括号、emoji、URL。"
                    "句子之间只使用：、，、。等自然停顿符号。"
                ),
            },
            {"role": "user", "content": cleaned},
        ],
        stream=False,
    )
    endpoint = f"{model_settings.base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if model_settings.api_key:
        headers["Authorization"] = f"Bearer {model_settings.api_key}"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=15.0)) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
        if response.status_code < 200 or response.status_code >= 300:
            return to_tts_input_text(cleaned)
        data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        translated = to_tts_input_text(clean_display_text(str(content))).strip()
        return translated or to_tts_input_text(cleaned)
    except Exception:
        return to_tts_input_text(cleaned)
