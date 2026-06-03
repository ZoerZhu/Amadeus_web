from __future__ import annotations

from .domain import ChatMode, ModelProviderPreset, ModelSettings


def build_chat_payload(
    settings: ModelSettings,
    provider: ModelProviderPreset,
    mode: ChatMode,
    messages: list[dict[str, str]],
    *,
    tools: list[dict] | None = None,
    stream: bool = True,
) -> dict:
    request_model = resolve_request_model(settings, provider, mode)
    payload: dict = {
        "model": request_model,
        "stream": stream,
        "messages": messages,
    }

    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    apply_mode_parameters(payload, settings, provider, mode, request_model)
    return payload


def resolve_request_model(
    settings: ModelSettings,
    provider: ModelProviderPreset,
    mode: ChatMode,
) -> str:
    model = settings.model.strip() or provider.default_model
    if not is_deepseek_provider(settings, provider):
        return model
    if mode == "fast" and "reasoner" in model.lower():
        return "deepseek-chat"
    if mode == "thinking" and model.lower() == "deepseek-chat":
        return "deepseek-reasoner"
    return model


def apply_mode_parameters(
    payload: dict,
    settings: ModelSettings,
    provider: ModelProviderPreset,
    mode: ChatMode,
    request_model: str,
) -> None:
    if is_mimo_provider(settings, provider, request_model):
        payload["thinking"] = {"type": "enabled" if mode == "thinking" else "disabled"}
        payload["top_p"] = 0.95
        if mode == "fast":
            payload["temperature"] = 0.7
            payload["max_completion_tokens"] = 1024
        else:
            payload["max_completion_tokens"] = 4096
        return

    if is_openrouter_provider(settings, provider):
        if mode == "fast":
            payload["reasoning"] = {"effort": "none", "exclude": True}
            payload["max_tokens"] = 1024
            payload["temperature"] = 0.7
        else:
            payload["reasoning"] = {"effort": "medium", "exclude": False}
            payload["max_tokens"] = 4096
        return

    if is_dashscope_provider(settings, provider, request_model):
        payload["enable_thinking"] = mode == "thinking"
        if mode == "thinking":
            payload["thinking_budget"] = 1024
            payload["max_tokens"] = 4096
        else:
            payload["max_tokens"] = 1024
            payload["temperature"] = 0.7
        return

    if is_deepseek_provider(settings, provider):
        if mode == "fast":
            payload["max_tokens"] = 1024
            if "reasoner" not in request_model.lower():
                payload["temperature"] = 0.7
        else:
            payload["max_tokens"] = 4096
        return

    if is_openai_provider(settings, provider, request_model):
        if is_openai_reasoning_model(request_model):
            payload["reasoning_effort"] = "medium" if mode == "thinking" else "minimal"
            payload["max_completion_tokens"] = 4096 if mode == "thinking" else 1024
        else:
            payload["max_tokens"] = 2048 if mode == "thinking" else 1024
            if mode == "fast":
                payload["temperature"] = 0.7
        return

    payload["max_tokens"] = 2048 if mode == "thinking" else 1024
    if mode == "fast":
        payload["temperature"] = 0.7


def should_read_reasoning(mode: ChatMode) -> bool:
    return mode == "thinking"


def _haystack(settings: ModelSettings, provider: ModelProviderPreset, request_model: str = "") -> str:
    return " ".join(
        [
            settings.provider_name,
            provider.base_url,
            settings.base_url,
            settings.model,
            request_model,
        ]
    ).lower()


def is_mimo_provider(settings: ModelSettings, provider: ModelProviderPreset, request_model: str) -> bool:
    value = _haystack(settings, provider, request_model)
    return "mimo" in value or "xiaomi" in value or "小米" in value


def is_openrouter_provider(settings: ModelSettings, provider: ModelProviderPreset) -> bool:
    return settings.provider_name.lower() == "openrouter" or "openrouter.ai" in settings.base_url.lower()


def is_dashscope_provider(settings: ModelSettings, provider: ModelProviderPreset, request_model: str) -> bool:
    value = _haystack(settings, provider, request_model)
    return "dashscope" in value or "qwen" in value or "通义" in value


def is_deepseek_provider(settings: ModelSettings, provider: ModelProviderPreset) -> bool:
    return "deepseek" in _haystack(settings, provider)


def is_openai_provider(settings: ModelSettings, provider: ModelProviderPreset, request_model: str) -> bool:
    value = _haystack(settings, provider, request_model)
    return "openai" in value or "api.openai.com" in value


def is_openai_reasoning_model(model: str) -> bool:
    normalized = model.lower()
    return (
        normalized.startswith("o1")
        or normalized.startswith("o3")
        or normalized.startswith("o4")
        or normalized.startswith("gpt-5")
    )
