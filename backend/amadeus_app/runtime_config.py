from __future__ import annotations

import os

from .domain import ModelProviderPreset, ModelSettings, VoiceSettings


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
