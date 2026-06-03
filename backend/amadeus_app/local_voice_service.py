from __future__ import annotations

import array
import math
import os
import uuid
import wave
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx

from .domain import ModelProviderPreset, ModelSettings, VoiceSettings
from .personas import KURISU_REFERENCE_TEXT, PersonaPreset
from .text_cleaning import to_speech_source_text, to_tts_input_text


LOCAL_TTS_RESPONSE_FORMAT = "wav"
LOCAL_TTS_SAMPLE_RATE = 24000


def is_local_voice_enabled() -> bool:
    value = os.getenv("AMADEUS_LOCAL_TTS_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def local_voice_engine_url() -> str:
    return os.getenv("AMADEUS_LOCAL_TTS_URL", "http://127.0.0.1:8011").strip().rstrip("/")


async def synthesize_with_local_cosyvoice(
    *,
    text: str,
    persona: PersonaPreset,
    voice_settings: VoiceSettings,
    model_settings: ModelSettings,
    provider: ModelProviderPreset,
    audio_dir: Path,
    translate,
) -> tuple[Path, str] | None:
    return await synthesize_segments_with_local_cosyvoice(
        texts=[text],
        persona=persona,
        voice_settings=voice_settings,
        model_settings=model_settings,
        provider=provider,
        audio_dir=audio_dir,
        translate=translate,
    )


async def synthesize_segments_with_local_cosyvoice(
    *,
    texts: Sequence[str],
    persona: PersonaPreset,
    voice_settings: VoiceSettings,
    model_settings: ModelSettings,
    provider: ModelProviderPreset,
    audio_dir: Path,
    translate,
) -> tuple[Path, str] | None:
    pcm_parts: list[bytes] = []
    for text in texts:
        payload = await prepare_local_cosyvoice_payload(
            text=text,
            persona=persona,
            voice_settings=voice_settings,
            model_settings=model_settings,
            provider=provider,
            translate=translate,
        )
        if payload is None:
            continue

        pcm = await request_local_cosyvoice_pcm(payload=payload, voice_settings=voice_settings)
        if pcm:
            pcm_parts.append(pcm)

    if not pcm_parts:
        return None

    return write_local_pcm_wav(audio_dir=audio_dir, pcm=b"".join(pcm_parts))


def write_local_pcm_wav(
    *,
    audio_dir: Path,
    pcm: bytes,
) -> tuple[Path, str]:
    audio_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{LOCAL_TTS_RESPONSE_FORMAT}"
    file_path = audio_dir / filename
    with wave.open(str(file_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(LOCAL_TTS_SAMPLE_RATE)
        wav_file.writeframes(pcm)
    return file_path, f"/audio/{filename}"


async def prepare_local_cosyvoice_payload(
    *,
    text: str,
    persona: PersonaPreset,
    voice_settings: VoiceSettings,
    model_settings: ModelSettings,
    provider: ModelProviderPreset,
    translate,
) -> dict[str, Any] | None:
    source_text = to_speech_source_text(text)
    if not source_text:
        return None

    speech_text = source_text
    if persona.voice_output_language_code == "ja":
        speech_text = await translate(
            text=source_text,
            model_settings=model_settings,
            provider=provider,
        )
    speech_text = to_tts_input_text(speech_text)
    if not speech_text:
        return None

    return {
        "text": speech_text,
        "promptText": os.getenv("AMADEUS_COSYVOICE_PROMPT_TEXT", KURISU_REFERENCE_TEXT),
        "speed": voice_settings.speed,
        "stream": False,
        "textFrontend": persona.voice_output_language_code != "ja",
    }


async def request_local_cosyvoice_pcm(
    *,
    payload: dict[str, Any],
    voice_settings: VoiceSettings,
) -> bytes:
    endpoint = f"{local_voice_engine_url()}/v1/tts/stream"
    async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=20.0, read=None)) as client:
        response = await client.post(endpoint, json=payload)
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(
            f"本地 CosyVoice2 请求失败 HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )
    return _apply_gain(response.content, voice_settings.gain)


def _apply_gain(chunk: bytes, gain_db: float) -> bytes:
    if not chunk or not gain_db:
        return chunk
    factor = math.pow(10.0, gain_db / 20.0)
    samples = array.array("h")
    samples.frombytes(chunk)
    for index, sample in enumerate(samples):
        samples[index] = max(-32768, min(32767, int(sample * factor)))
    return samples.tobytes()
