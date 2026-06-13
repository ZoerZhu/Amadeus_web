from __future__ import annotations

import os

from .domain import ModelProviderPreset, ModelSettings, SpeechInputSettings, VisionSettings, VoiceSettings


DEFAULT_SPEECH_INPUT_PROVIDER = "小米 MiMo ASR"
DEFAULT_SPEECH_INPUT_BASE_URL = "https://api.xiaomimimo.com/v1"
DEFAULT_SPEECH_INPUT_MODEL = "mimo-v2.5-asr"


def effective_model_settings(settings: ModelSettings, provider: ModelProviderPreset) -> ModelSettings:
    env_provider = os.getenv("AMADEUS_PROVIDER", "").strip()
    provider_name = settings.provider_name or env_provider or provider.name
    return ModelSettings(
        providerName=provider_name,
        baseUrl=settings.base_url.strip() or os.getenv("AMADEUS_BASE_URL", "").strip() or provider.base_url,
        model=settings.model.strip() or os.getenv("AMADEUS_MODEL", "").strip() or provider.default_model,
        apiKey=settings.api_key.strip() or os.getenv("AMADEUS_API_KEY", "").strip(),
        useRemote=settings.use_remote,
    )


def effective_voice_settings(settings: VoiceSettings) -> VoiceSettings:
    return VoiceSettings(
        ttsBackend=settings.tts_backend,
        siliconFlowApiKey=settings.silicon_flow_api_key.strip()
        or os.getenv("SILICONFLOW_API_KEY", "").strip(),
        clonedVoiceUri=settings.cloned_voice_uri.strip()
        or os.getenv("SILICONFLOW_VOICE_URI", "").strip(),
        autoPlay=settings.auto_play,
        syncTextOutput=settings.sync_text_output,
        speed=settings.speed,
        gain=settings.gain,
    )


def effective_vision_settings(
    settings: VisionSettings,
    model_settings: ModelSettings,
    provider: ModelProviderPreset,
) -> VisionSettings:
    provider_name = (
        settings.provider_name.strip()
        or os.getenv("AMADEUS_VISION_PROVIDER", "").strip()
        or model_settings.provider_name.strip()
        or os.getenv("AMADEUS_PROVIDER", "").strip()
        or provider.name
    )
    return VisionSettings(
        enabled=settings.enabled,
        screenCaptureEnabled=settings.screen_capture_enabled,
        providerName=provider_name,
        baseUrl=(
            settings.base_url.strip()
            or os.getenv("AMADEUS_VISION_BASE_URL", "").strip()
            or model_settings.base_url.strip()
            or os.getenv("AMADEUS_BASE_URL", "").strip()
            or provider.base_url
        ),
        model=(
            settings.model.strip()
            or os.getenv("AMADEUS_VISION_MODEL", "").strip()
            or "gpt-4.1-mini"
        ),
        apiKey=(
            settings.api_key.strip()
            or os.getenv("AMADEUS_VISION_API_KEY", "").strip()
            or model_settings.api_key.strip()
            or os.getenv("AMADEUS_API_KEY", "").strip()
        ),
        useRemote=settings.use_remote,
    )


def effective_speech_input_settings(
    settings: SpeechInputSettings,
    model_settings: ModelSettings,
    provider: ModelProviderPreset,
) -> SpeechInputSettings:
    provider_name = (
        settings.provider_name.strip()
        or os.getenv("AMADEUS_SPEECH_INPUT_PROVIDER", "").strip()
        or DEFAULT_SPEECH_INPUT_PROVIDER
    )
    same_provider_as_base_model = provider_name.lower() == model_settings.provider_name.strip().lower()
    is_mimo_speech = "mimo" in provider_name.lower() or "小米" in provider_name
    is_mimo_base_model = "mimo" in model_settings.provider_name.lower() or "小米" in model_settings.provider_name
    is_siliconflow_speech = provider_name.lower().startswith("siliconflow")
    return SpeechInputSettings(
        enabled=settings.enabled,
        providerName=provider_name,
        baseUrl=(
            settings.base_url.strip()
            or os.getenv("AMADEUS_SPEECH_INPUT_BASE_URL", "").strip()
            or (model_settings.base_url.strip() if same_provider_as_base_model else "")
            or DEFAULT_SPEECH_INPUT_BASE_URL
        ),
        model=(
            settings.model.strip()
            or os.getenv("AMADEUS_SPEECH_INPUT_MODEL", "").strip()
            or DEFAULT_SPEECH_INPUT_MODEL
        ),
        apiKey=(
            settings.api_key.strip()
            or os.getenv("AMADEUS_SPEECH_INPUT_API_KEY", "").strip()
            or (os.getenv("MIMO_API_KEY", "").strip() if is_mimo_speech else "")
            or (model_settings.api_key.strip() if is_mimo_speech and is_mimo_base_model else "")
            or (os.getenv("AMADEUS_API_KEY", "").strip() if is_mimo_speech and is_mimo_base_model else "")
            or (os.getenv("SILICONFLOW_API_KEY", "").strip() if is_siliconflow_speech else "")
            or (model_settings.api_key.strip() if same_provider_as_base_model else "")
            or (os.getenv("AMADEUS_API_KEY", "").strip() if same_provider_as_base_model else "")
        ),
        language=settings.language.strip() or os.getenv("AMADEUS_SPEECH_INPUT_LANGUAGE", "").strip() or ("auto" if is_mimo_speech else ""),
        useRemote=settings.use_remote,
    )
