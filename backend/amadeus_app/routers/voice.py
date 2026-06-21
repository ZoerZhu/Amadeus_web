"""Voice synthesis and cloning routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .._common import require_storage
from ..chat_service import SegmentedVoiceCoordinator, resolve_chat_settings, sse
from ..domain import ChatStreamRequest, TaskSummaryVoiceRequest, VoiceCloneRequest, VoiceSynthesisRequest
from ..personas import get_persona
from ..runtime_config import effective_voice_settings
from ..storage import DEFAULT_USER_ID
from ..voice_service import clone_kurisu_voice, prepare_task_summary_speech_text, synthesize_for_persona

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.post("/synthesize")
async def voice_synthesize(request: VoiceSynthesisRequest) -> dict:
    provider, settings = resolve_chat_settings(
        ChatStreamRequest(
            messages=[],
            personaId=request.persona_id,
            model=request.model,
            voice=request.voice,
        )
    )
    try:
        audio = await synthesize_for_persona(
            text=request.text,
            persona=get_persona(request.persona_id),
            voice_settings=effective_voice_settings(request.voice),
            model_settings=settings,
            provider=provider,
        )
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error
    if audio is None:
        raise HTTPException(status_code=400, detail="语音服务未返回音频，请检查当前语音模型配置。")
    assistant_voice = {
        "audioUrls": [audio.url],
        "segments": [{"index": 0, "url": audio.url}],
    }
    if request.conversation_id is not None and request.message_id is not None:
        try:
            await require_storage().update_assistant_message_voice(
                user_id=DEFAULT_USER_ID,
                conversation_id=request.conversation_id,
                message_id=request.message_id,
                assistant_voice=assistant_voice,
            )
        except Exception:
            pass
    return {"audioUrl": audio.url, "audioUrls": [audio.url], "assistantVoice": assistant_voice}


@router.post("/task-summary")
async def voice_task_summary(request: TaskSummaryVoiceRequest) -> dict:
    provider, settings = resolve_chat_settings(
        ChatStreamRequest(
            messages=[],
            personaId=request.persona_id,
            model=request.model,
            voice=request.voice,
        )
    )
    persona = get_persona(request.persona_id)
    try:
        speech_text = await prepare_task_summary_speech_text(
            summary=request.summary,
            persona=persona,
            model_settings=settings,
            provider=provider,
        )
        audio = await synthesize_for_persona(
            text=speech_text,
            persona=persona,
            voice_settings=effective_voice_settings(request.voice),
            model_settings=settings,
            provider=provider,
        )
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error
    if audio is None:
        raise HTTPException(status_code=400, detail="语音服务未返回音频，请检查当前语音模型配置。")
    return {"audioUrl": audio.url, "speechText": speech_text}


@router.post("/task-summary/stream")
async def voice_task_summary_stream(request: TaskSummaryVoiceRequest):
    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        stream_task_summary_voice(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def stream_task_summary_voice(request: TaskSummaryVoiceRequest):
    provider, settings = resolve_chat_settings(
        ChatStreamRequest(
            messages=[],
            personaId=request.persona_id,
            model=request.model,
            voice=request.voice,
        )
    )
    persona = get_persona(request.persona_id)
    try:
        speech_text = await prepare_task_summary_speech_text(
            summary=request.summary,
            persona=persona,
            model_settings=settings,
            provider=provider,
        )
        voice_settings = effective_voice_settings(request.voice).model_copy(
            update={"auto_play": True, "sync_text_output": True}
        )
        voice_stream = SegmentedVoiceCoordinator(
            persona=persona,
            voice_settings=voice_settings,
            model_settings=settings,
            provider=provider,
        )
        for event in voice_stream.startup_events():
            yield event
        for event in voice_stream.accept(speech_text):
            yield event
        for event in voice_stream.finish():
            yield event
        async for event in voice_stream.drain_all():
            yield event
        yield sse("done", {"speechText": speech_text})
    except Exception as error:
        yield sse("error", {"message": str(error) or error.__class__.__name__})


@router.post("/clone")
async def voice_clone(request: VoiceCloneRequest) -> dict:
    try:
        uri = await clone_kurisu_voice(
            api_key=request.silicon_flow_api_key,
            custom_name=request.custom_name,
            reference_text=request.reference_text,
        )
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error
    return {"voiceUri": uri}
