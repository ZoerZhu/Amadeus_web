"""Chat streaming and desktop observation routes."""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .._common import require_storage
from ..chat_service import resolve_chat_settings, sse, stream_chat
from ..domain import ChatStreamRequest, DesktopObserveRequest
from ..model_adapter import build_chat_payload
from ..personas import build_system_prompt, get_persona
from ..storage import DEFAULT_USER_ID
from ..vision.image_understand_agent import run_image_understand_agent

router = APIRouter(prefix="/api", tags=["chat"])


# ---------------------------------------------------------------------------
# /api/chat/stream
# ---------------------------------------------------------------------------


@router.post("/chat/stream")
async def chat_stream(request: ChatStreamRequest) -> StreamingResponse:
    storage = require_storage() if require_storage().connected else None
    return StreamingResponse(
        stream_chat(request, storage=storage),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# /api/desktop/observe
# ---------------------------------------------------------------------------


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


@router.post("/desktop/observe")
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
