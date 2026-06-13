from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

import httpx

from ..domain import ChatAttachment, ModelSettings, VisionSettings
from ..file_tools.file_reader import ensure_allowed_path, relative_path, resolve_workspace_path
from ..model_adapter import build_chat_payload
from ..providers import get_provider
from ..runtime_config import effective_model_settings, effective_vision_settings


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
DEFAULT_PROMPT = (
    "请理解这些图片，提取对用户问题有帮助的事实。"
    "用简洁中文说明画面内容、可见文字、界面状态、关键对象和不确定处。"
)
MAX_IMAGES = 4
DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024


async def run_image_understand_agent(args: dict[str, Any]) -> dict[str, Any]:
    attachments = normalize_attachments(args)
    if not attachments:
        raise ValueError("image_understand_agent requires at least one image attachment")
    if len(attachments) > MAX_IMAGES:
        raise ValueError(f"image_understand_agent supports at most {MAX_IMAGES} images per request")

    model_settings = normalize_model_settings(args.get("modelSettings") or args.get("model"))
    base_provider = get_provider(model_settings.provider_name)
    model_settings = effective_model_settings(model_settings, base_provider)
    base_provider = get_provider(model_settings.provider_name)
    model_settings = effective_model_settings(model_settings, base_provider)

    vision_settings = normalize_vision_settings(args.get("visionSettings") or args.get("vision"))
    vision_provider = get_provider(vision_settings.provider_name or model_settings.provider_name or base_provider.name)
    vision_settings = effective_vision_settings(vision_settings, model_settings, vision_provider)
    vision_provider = get_provider(vision_settings.provider_name)

    if not vision_settings.enabled:
        raise ValueError("视觉理解已关闭。")
    if not vision_settings.use_remote:
        raise ValueError("视觉理解需要启用远程 OpenAI-compatible API。")
    if not vision_provider.compatible:
        raise ValueError(f"{vision_provider.name} 当前未接入视觉专用协议。")
    if not vision_settings.api_key and vision_provider.name.lower() != "ollama":
        raise ValueError("视觉理解需要配置 API Key。")

    image_parts = [load_image_part(attachment) for attachment in attachments]
    prompt = str(args.get("prompt") or args.get("instruction") or args.get("intent") or "").strip()
    description = await request_vision_description(
        prompt=prompt,
        image_parts=image_parts,
        model_settings=vision_settings,
        provider=vision_provider,
    )
    return {
        "summary": f"已理解 {len(image_parts)} 张图片。",
        "data": {
            "agent": "image_understand_agent",
            "description": description,
            "images": [
                {
                    "path": item["path"],
                    "filename": item["filename"],
                    "contentType": item["contentType"],
                    "sizeBytes": item["sizeBytes"],
                }
                for item in image_parts
            ],
            "provider": vision_provider.name,
            "model": vision_settings.model,
        },
    }


def normalize_attachments(args: dict[str, Any]) -> list[ChatAttachment]:
    raw_items = args.get("attachments") or args.get("images") or args.get("files") or []
    if isinstance(raw_items, (str, Path)):
        raw_items = [raw_items]
    if not isinstance(raw_items, list):
        raise ValueError("image attachments must be an array")

    attachments: list[ChatAttachment] = []
    for item in raw_items:
        if isinstance(item, ChatAttachment):
            attachments.append(item)
            continue
        if isinstance(item, (str, Path)):
            attachments.append(ChatAttachment(path=str(item), mediaType="image"))
            continue
        if isinstance(item, dict):
            attachments.append(ChatAttachment.model_validate(item))
            continue
        raise ValueError("image attachment must be a path or object")
    return [
        attachment
        for attachment in attachments
        if attachment.media_type == "image" or Path(attachment.path).suffix.lower() in IMAGE_SUFFIXES
    ]


def normalize_model_settings(value: Any) -> ModelSettings:
    if isinstance(value, ModelSettings):
        return value
    if isinstance(value, dict):
        return ModelSettings.model_validate(value)
    return ModelSettings()


def normalize_vision_settings(value: Any) -> VisionSettings:
    if isinstance(value, VisionSettings):
        return value
    if isinstance(value, dict):
        return VisionSettings.model_validate(value)
    return VisionSettings()


def load_image_part(attachment: ChatAttachment) -> dict[str, Any]:
    path = resolve_workspace_path(attachment.path)
    ensure_allowed_path(path)
    if not path.exists():
        raise FileNotFoundError(relative_path(path))
    if not path.is_file():
        raise ValueError(f"image attachment is not a file: {relative_path(path)}")
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError(f"unsupported image suffix: {path.suffix or path.name}")

    max_bytes = vision_image_max_bytes()
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"image exceeds {max_bytes} bytes: {relative_path(path)}")

    content_type = attachment.content_type or mimetypes.guess_type(path.name)[0] or "image/png"
    data = path.read_bytes()
    data_url = f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"
    return {
        "type": "image_url",
        "image_url": {"url": data_url},
        "path": relative_path(path),
        "filename": attachment.original_filename or attachment.filename or path.name,
        "contentType": content_type,
        "sizeBytes": size,
    }


async def request_vision_description(
    *,
    prompt: str,
    image_parts: list[dict[str, Any]],
    model_settings: VisionSettings,
    provider,
) -> str:
    user_text = DEFAULT_PROMPT
    if prompt:
        user_text = f"{DEFAULT_PROMPT}\n\n用户问题或上下文：\n{prompt}"
    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    content.extend(
        {"type": "image_url", "image_url": item["image_url"]}
        for item in image_parts
    )
    payload = build_chat_payload(
        model_settings,  # type: ignore[arg-type]
        provider,
        "fast",
        [
            {
                "role": "system",
                "content": (
                    "你是 Amadeus 的 image_understand_agent。"
                    "只根据图片中可见信息回答，不能编造。"
                    "输出中文，控制在 3 到 8 条短句内。"
                    "如果图片是屏幕截图，优先说明当前应用、可见文字、按钮状态、错误提示和可操作区域。"
                ),
            },
            {"role": "user", "content": content},
        ],
        stream=False,
    )
    if "max_tokens" in payload:
        payload["max_tokens"] = min(int(payload.get("max_tokens") or 900), 1200)
    elif "max_completion_tokens" in payload:
        payload["max_completion_tokens"] = min(int(payload.get("max_completion_tokens") or 900), 1200)
    else:
        payload["max_tokens"] = 900
    endpoint = f"{model_settings.base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if model_settings.api_key:
        headers["Authorization"] = f"Bearer {model_settings.api_key}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=20.0)) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"视觉模型请求失败 HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    message = (data.get("choices") or [{}])[0].get("message") or {}
    content_value = message.get("content", "")
    if isinstance(content_value, list):
        text = "\n".join(str(item.get("text") or "") for item in content_value if isinstance(item, dict))
    else:
        text = str(content_value)
    text = text.strip()
    if not text:
        raise RuntimeError("视觉模型未返回可用描述。")
    return text


def vision_image_max_bytes() -> int:
    raw = os.getenv("AMADEUS_VISION_IMAGE_MAX_BYTES", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAX_IMAGE_BYTES
    except ValueError:
        value = DEFAULT_MAX_IMAGE_BYTES
    return max(1, value)
