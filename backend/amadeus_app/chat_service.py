from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

from .agent_registry import tool_registry
from .domain import ChatAttachment, ChatStreamRequest, ModelProviderPreset, ModelSettings, VoiceSettings
from .logging_config import get_logger
from .model_adapter import build_chat_payload, should_read_reasoning

_log = get_logger(__name__)
from .local_voice_service import local_voice_engine_url
from .personas import PersonaPreset, build_system_prompt, get_persona
from .providers import get_provider
from .runtime_config import effective_model_settings, effective_voice_settings
from .storage import DEFAULT_USER_ID, SQLiteStorage
from .text_cleaning import (
    EMOTION_RE,
    clean_display_text,
    clean_stream_chunk,
    strip_emotion_tags_for_display,
    to_speech_source_text,
)
from .vision.image_understand_agent import MAX_IMAGES as VISION_MAX_IMAGES
from .vision.image_understand_agent import run_image_understand_agent
from .voice_service import (
    synthesize_for_persona,
    synthesize_local_segment_pcm_for_persona,
    write_local_pcm_audio,
)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


VALID_EMOTIONS = {"neutral", "anger", "joy", "sadness", "shy", "smile", "surprise", "unhappy"}
LIVE2D_EMOTION_ALIASES = {"smile": "smile1"}
TOOL_EVENT_LOG_PREFIX = "__AMADEUS_TOOL_EVENT__ "
SPEECH_SEGMENT_TARGET_CHAR_COUNT = env_int("AMADEUS_SPEECH_SEGMENT_TARGET_CHARS", 30)
SPEECH_SEGMENT_MAX_CHAR_COUNT = env_int("AMADEUS_SPEECH_SEGMENT_MAX_CHARS", 55)
SPEECH_SEGMENT_HARD_BREAK_MIN_CHAR_COUNT = env_int("AMADEUS_SPEECH_SEGMENT_HARD_BREAK_MIN_CHARS", 20)
SPEECH_PREPARE_CONCURRENCY = 1
SPEECH_TTS_MAX_ATTEMPTS = 2
SPEECH_HARD_BREAKS = {"。", "！", "？", "!", "?", "；", ";", "\n"}
SPEECH_SOFT_BREAKS = {"，", ",", "、", " ", "：", ":"}
CHAT_TOOL_MAX_ROUNDS = env_int("AMADEUS_CHAT_TOOL_MAX_ROUNDS", 4)
CHAT_TOOL_RESULT_MAX_CHARS = env_int("AMADEUS_CHAT_TOOL_RESULT_MAX_CHARS", 12000)
CHAT_TOOL_EVENT_PREVIEW_CHARS = env_int("AMADEUS_CHAT_TOOL_EVENT_PREVIEW_CHARS", 900)
CHAT_WEB_SEARCH_RESULT_MAX_CHARS = env_int("AMADEUS_CHAT_WEB_SEARCH_RESULT_MAX_CHARS", 5000)
CHAT_DOC_WRITER_RESULT_MAX_CHARS = env_int("AMADEUS_CHAT_DOC_WRITER_RESULT_MAX_CHARS", 8000)


@dataclass
class PreparedVoiceSegment:
    index: int
    text: str
    url: str | None = None
    error: str = ""


@dataclass
class PreparedLocalVoiceSegment:
    index: int
    pcm: bytes = b""
    error: str = ""


@dataclass
class ModelToolCall:
    id: str
    name: str
    arguments: str
    index: int = 0


@dataclass
class ModelTurn:
    content: str
    tool_calls: list[ModelToolCall]
    finish_reason: str = ""


@dataclass
class ExecutedToolCall:
    call: ModelToolCall
    ok: bool
    summary: str
    content: str
    arguments: dict[str, Any]
    error: str = ""
    result_payload: dict[str, Any] | None = None


class SpeechSegmentBuffer:
    def __init__(self) -> None:
        self._buffer = ""

    def accept(self, chunk: str) -> list[str]:
        if chunk:
            self._buffer += chunk
        return self._flush(force=False)

    def finish(self) -> list[str]:
        return self._flush(force=True)

    def _flush(self, *, force: bool) -> list[str]:
        segments: list[str] = []
        while self._buffer:
            index = find_speech_segment_end_index(self._buffer, force=force)
            if index < 0:
                break
            segment = self._buffer[: index + 1]
            self._buffer = self._buffer[index + 1 :]
            if segment.strip():
                segments.append(segment)
        return segments


class SegmentedVoiceCoordinator:
    def __init__(
        self,
        *,
        persona: PersonaPreset,
        voice_settings: VoiceSettings,
        model_settings: ModelSettings,
        provider: ModelProviderPreset,
    ) -> None:
        self.persona = persona
        self.voice_settings = voice_settings
        self.model_settings = model_settings
        self.provider = provider
        self.segment_buffer = SpeechSegmentBuffer()
        self.semaphore = asyncio.Semaphore(SPEECH_PREPARE_CONCURRENCY)
        self.pending_segments: list[tuple[int, str]] = []
        self.current_task: asyncio.Task[PreparedVoiceSegment] | None = None
        self.prepared_to_emit: PreparedVoiceSegment | None = None
        self.next_segment_index = 0
        self.announced_voice = False
        self.enabled = self._is_ready_for_voice()
        self.synchronize_text = self.enabled and voice_settings.sync_text_output
        self.audio_urls_by_index: dict[int, str] = {}

    @property
    def should_delay_text(self) -> bool:
        return self.synchronize_text

    def assistant_voice_payload(self) -> dict[str, Any]:
        segments = [
            {"index": index, "url": url}
            for index, url in sorted(self.audio_urls_by_index.items())
            if url
        ]
        return {
            "audioUrls": [segment["url"] for segment in segments],
            "segments": segments,
        }

    def startup_events(self) -> list[str]:
        if not self.voice_settings.auto_play:
            return []
        if self.enabled:
            return []
        if self.voice_settings.tts_backend == "local":
            return [sse("voice_error", {"message": f"本地 CosyVoice2 引擎未就绪：{local_voice_engine_url()}"})]
        if not self.voice_settings.silicon_flow_api_key.strip():
            return [sse("voice_error", {"message": "需要先配置 SiliconFlow API Key。"})]
        return [sse("voice_error", {"message": "需要先配置 Voice URI 或可用的人格默认语音。"})]

    def accept(self, chunk: str) -> list[str]:
        if not self.enabled:
            return []
        events: list[str] = []
        for segment in self.segment_buffer.accept(chunk):
            events.extend(self._schedule_segment(segment))
        return events

    def finish(self) -> list[str]:
        if not self.enabled:
            return []
        events: list[str] = []
        for segment in self.segment_buffer.finish():
            events.extend(self._schedule_segment(segment))
        return events

    async def drain_ready(self) -> AsyncGenerator[str, None]:
        if not self.enabled:
            return
        while True:
            if self.prepared_to_emit is not None and self.current_task is not None:
                async for event in self._emit_segment_result(self.prepared_to_emit):
                    yield event
                self.prepared_to_emit = None
                continue

            if self.current_task is None or not self.current_task.done():
                break

            segment = self._take_current_task_result()
            if self.pending_segments:
                self._start_next_prepare()
                async for event in self._emit_segment_result(segment):
                    yield event
            else:
                self.prepared_to_emit = segment
                break

    async def drain_all(self) -> AsyncGenerator[str, None]:
        if not self.enabled:
            return
        while self.prepared_to_emit is not None or self.current_task is not None or self.pending_segments:
            if self.prepared_to_emit is not None:
                async for event in self._emit_segment_result(self.prepared_to_emit):
                    yield event
                self.prepared_to_emit = None
                continue

            if self.current_task is None:
                self._start_next_prepare()
                continue

            try:
                segment = await self.current_task
            except asyncio.CancelledError:
                raise
            except Exception as error:
                segment = PreparedVoiceSegment(
                    index=-1,
                    text="",
                    error=str(error) or error.__class__.__name__,
                )
            self.current_task = None
            if self.pending_segments:
                self._start_next_prepare()
            async for event in self._emit_segment_result(segment):
                yield event

    def cancel(self) -> None:
        if self.current_task is not None and not self.current_task.done():
            self.current_task.cancel()
        self.pending_segments.clear()
        self.prepared_to_emit = None

    def _is_ready_for_voice(self) -> bool:
        if not self.voice_settings.auto_play:
            return False
        if self.voice_settings.tts_backend == "local":
            return True
        if not self.voice_settings.silicon_flow_api_key.strip():
            return False
        voice_uri = self.voice_settings.cloned_voice_uri.strip() or (self.persona.tts_voice_id or "")
        return bool(voice_uri.strip())

    def _schedule_segment(self, segment: str) -> list[str]:
        events: list[str] = []
        if not self.announced_voice:
            events.append(sse("status", {"phase": "voice"}))
            self.announced_voice = True

        if not to_speech_source_text(segment):
            if self.synchronize_text:
                events.append(sse("content", {"text": segment}))
            return events

        self.pending_segments.append((self.next_segment_index, segment))
        self.next_segment_index += 1
        if self.current_task is None:
            self._start_next_prepare()
        if self.prepared_to_emit is not None and self.current_task is not None:
            events.extend(self._segment_events(self.prepared_to_emit))
            self.prepared_to_emit = None
        return events

    def _start_next_prepare(self) -> None:
        if self.current_task is not None or not self.pending_segments:
            return
        index, segment = self.pending_segments.pop(0)
        self.current_task = asyncio.create_task(self._prepare_segment(index, segment))

    def _take_current_task_result(self) -> PreparedVoiceSegment:
        if self.current_task is None:
            return PreparedVoiceSegment(index=-1, text="", error="语音任务不存在。")
        try:
            return self.current_task.result()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return PreparedVoiceSegment(
                index=-1,
                text="",
                error=str(error) or error.__class__.__name__,
            )
        finally:
            self.current_task = None

    async def _prepare_segment(self, index: int, text: str) -> PreparedVoiceSegment:
        if not to_speech_source_text(text):
            return PreparedVoiceSegment(index=index, text=text)

        last_error: Exception | None = None
        async with self.semaphore:
            for attempt in range(SPEECH_TTS_MAX_ATTEMPTS):
                try:
                    audio = await synthesize_for_persona(
                        text=text,
                        persona=self.persona,
                        voice_settings=self.voice_settings,
                        model_settings=self.model_settings,
                        provider=self.provider,
                    )
                    if audio is None:
                        return PreparedVoiceSegment(
                            index=index,
                            text=text,
                            error="语音服务未返回音频，请检查当前语音模型配置。",
                        )
                    return PreparedVoiceSegment(index=index, text=text, url=audio.url)
                except Exception as error:
                    last_error = error
                    if attempt < SPEECH_TTS_MAX_ATTEMPTS - 1:
                        await asyncio.sleep(0.45 * (attempt + 1))

        return PreparedVoiceSegment(
            index=index,
            text=text,
            error=str(last_error) if last_error else "语音生成失败。",
        )

    async def _emit_segment_result(
        self,
        segment: PreparedVoiceSegment,
    ) -> AsyncGenerator[str, None]:
        for event in self._segment_events(segment):
            yield event

    def _segment_events(self, segment: PreparedVoiceSegment) -> list[str]:
        events: list[str] = []
        if segment.url:
            if segment.index >= 0:
                self.audio_urls_by_index[segment.index] = segment.url
            events.append(
                sse(
                    "audio",
                    {
                        "url": segment.url,
                        "index": segment.index,
                        "text": segment.text if self.synchronize_text else "",
                        "syncText": self.synchronize_text,
                    },
                )
            )
        elif self.synchronize_text and segment.text:
            events.append(sse("content", {"text": segment.text}))

        if segment.error:
            events.append(
                sse(
                    "voice_error",
                    {
                        "message": f"第 {segment.index + 1} 段语音生成失败：{segment.error}",
                        "index": segment.index,
                    },
                )
            )
        return events


class LocalConcatenatedVoiceCoordinator:
    def __init__(
        self,
        *,
        persona: PersonaPreset,
        voice_settings: VoiceSettings,
        model_settings: ModelSettings,
        provider: ModelProviderPreset,
    ) -> None:
        self.persona = persona
        self.voice_settings = voice_settings
        self.model_settings = model_settings
        self.provider = provider
        self.segment_buffer = SpeechSegmentBuffer()
        self.pending_segments: list[tuple[int, str]] = []
        self.current_task: asyncio.Task[PreparedLocalVoiceSegment] | None = None
        self.prepared_segments: dict[int, bytes] = {}
        self.segment_errors: list[str] = []
        self.next_segment_index = 0
        self.announced_voice = False
        self.enabled = self.voice_settings.auto_play and self.voice_settings.tts_backend == "local"
        self.synchronize_text = False
        self.audio_urls_by_index: dict[int, str] = {}

    @property
    def should_delay_text(self) -> bool:
        return False

    def assistant_voice_payload(self) -> dict[str, Any]:
        segments = [
            {"index": index, "url": url}
            for index, url in sorted(self.audio_urls_by_index.items())
            if url
        ]
        return {
            "audioUrls": [segment["url"] for segment in segments],
            "segments": segments,
        }

    def startup_events(self) -> list[str]:
        if not self.voice_settings.auto_play:
            return []
        if self.enabled:
            return []
        return [sse("voice_error", {"message": f"本地 CosyVoice2 引擎未就绪：{local_voice_engine_url()}"})]

    def accept(self, chunk: str) -> list[str]:
        if not self.enabled:
            return []
        events: list[str] = []
        for segment in self.segment_buffer.accept(chunk):
            events.extend(self._collect_segment(segment))
        return events

    def finish(self) -> list[str]:
        if not self.enabled:
            return []
        events: list[str] = []
        for segment in self.segment_buffer.finish():
            events.extend(self._collect_segment(segment))
        return events

    async def drain_ready(self) -> AsyncGenerator[str, None]:
        if not self.enabled:
            if False:
                yield ""
            return
        while self.current_task is not None and self.current_task.done():
            self._take_current_task_result()
            self._start_next_prepare()
        if False:
            yield ""

    async def drain_all(self) -> AsyncGenerator[str, None]:
        if not self.enabled:
            if False:
                yield ""
            return

        while self.current_task is not None or self.pending_segments:
            if self.current_task is None:
                self._start_next_prepare()
                continue
            try:
                segment = await self.current_task
            except asyncio.CancelledError:
                raise
            except Exception as error:
                segment = PreparedLocalVoiceSegment(
                    index=-1,
                    error=str(error) or error.__class__.__name__,
                )
            self.current_task = None
            self._store_prepared_segment(segment)
            self._start_next_prepare()

        if self.segment_errors:
            yield sse(
                "voice_error",
                {"message": f"本地语音分块生成失败：{'; '.join(self.segment_errors[:3])}"},
            )

        ordered_pcm = [
            self.prepared_segments[index]
            for index in sorted(self.prepared_segments)
            if self.prepared_segments[index]
        ]
        if not ordered_pcm:
            yield sse("voice_error", {"message": "本地 CosyVoice2 未返回音频。"})
            return

        audio = write_local_pcm_audio(b"".join(ordered_pcm))
        self.audio_urls_by_index[0] = audio.url
        yield sse(
            "audio",
            {
                "url": audio.url,
                "index": 0,
                "text": "",
                "syncText": False,
            },
        )

    def cancel(self) -> None:
        if self.current_task is not None and not self.current_task.done():
            self.current_task.cancel()
        self.pending_segments.clear()
        self.prepared_segments.clear()
        self.segment_errors.clear()

    def _collect_segment(self, segment: str) -> list[str]:
        events: list[str] = []
        if not self.announced_voice:
            events.append(sse("status", {"phase": "voice"}))
            self.announced_voice = True

        if to_speech_source_text(segment):
            self.pending_segments.append((self.next_segment_index, segment))
            self.next_segment_index += 1
            self._start_next_prepare()
        return events

    def _start_next_prepare(self) -> None:
        if self.current_task is not None or not self.pending_segments:
            return
        index, segment = self.pending_segments.pop(0)
        self.current_task = asyncio.create_task(self._prepare_segment(index, segment))

    def _take_current_task_result(self) -> None:
        if self.current_task is None:
            return
        try:
            segment = self.current_task.result()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            segment = PreparedLocalVoiceSegment(
                index=-1,
                error=str(error) or error.__class__.__name__,
            )
        finally:
            self.current_task = None
        self._store_prepared_segment(segment)

    def _store_prepared_segment(self, segment: PreparedLocalVoiceSegment) -> None:
        if segment.error:
            self.segment_errors.append(
                f"第 {segment.index + 1} 段：{segment.error}" if segment.index >= 0 else segment.error
            )
            return
        if segment.pcm:
            self.prepared_segments[segment.index] = segment.pcm

    async def _prepare_segment(self, index: int, text: str) -> PreparedLocalVoiceSegment:
        if not to_speech_source_text(text):
            return PreparedLocalVoiceSegment(index=index)
        try:
            pcm = await synthesize_local_segment_pcm_for_persona(
                text=text,
                persona=self.persona,
                voice_settings=self.voice_settings,
                model_settings=self.model_settings,
                provider=self.provider,
            )
        except Exception as error:
            return PreparedLocalVoiceSegment(
                index=index,
                error=str(error) or error.__class__.__name__,
            )
        if not pcm:
            return PreparedLocalVoiceSegment(index=index, error="未返回音频")
        return PreparedLocalVoiceSegment(index=index, pcm=pcm)


VoiceCoordinator = SegmentedVoiceCoordinator | LocalConcatenatedVoiceCoordinator


def build_voice_coordinator(
    *,
    persona: PersonaPreset,
    voice_settings: VoiceSettings,
    model_settings: ModelSettings,
    provider: ModelProviderPreset,
) -> VoiceCoordinator:
    if voice_settings.tts_backend == "local":
        return LocalConcatenatedVoiceCoordinator(
            persona=persona,
            voice_settings=voice_settings,
            model_settings=model_settings,
            provider=provider,
        )
    return SegmentedVoiceCoordinator(
        persona=persona,
        voice_settings=voice_settings,
        model_settings=model_settings,
        provider=provider,
    )


def find_speech_segment_end_index(text: str, *, force: bool) -> int:
    if not text.strip():
        return -1

    meaningful_count = 0
    last_soft_break_index = -1
    last_hard_break_index = -1

    for index, char in enumerate(text):
        if is_meaningful_speech_char(char):
            meaningful_count += 1
        if char in SPEECH_SOFT_BREAKS:
            last_soft_break_index = index
        if char in SPEECH_HARD_BREAKS:
            last_hard_break_index = index
            if meaningful_count >= SPEECH_SEGMENT_HARD_BREAK_MIN_CHAR_COUNT:
                return index
        if meaningful_count >= SPEECH_SEGMENT_TARGET_CHAR_COUNT and char in SPEECH_SOFT_BREAKS:
            return index
        if meaningful_count >= SPEECH_SEGMENT_MAX_CHAR_COUNT:
            if last_soft_break_index >= 0:
                return last_soft_break_index
            if last_hard_break_index >= 0:
                return last_hard_break_index
            return index

    if not force:
        return -1
    if last_hard_break_index >= 0:
        return last_hard_break_index
    if last_soft_break_index >= 0:
        return last_soft_break_index
    return len(text) - 1


def is_meaningful_speech_char(char: str) -> bool:
    category = unicodedata.category(char)
    return category.startswith("L") or category.startswith("N")


@dataclass
class ChatStreamState:
    raw_buffer: str = ""
    displayed_buffer: str = ""
    emotion_fired: bool = False

    def accept_content(self, chunk: str) -> list[tuple[str, dict]]:
        cleaned = clean_display_text(clean_stream_chunk(chunk))
        if not cleaned:
            return []

        events: list[tuple[str, dict]] = []
        self.raw_buffer += cleaned
        if not self.emotion_fired:
            for match in EMOTION_RE.finditer(self.raw_buffer):
                emotion = match.group(1).lower()
                if emotion in VALID_EMOTIONS:
                    events.append(
                        (
                            "emotion",
                            {
                                "emotion": emotion,
                                "live2dEmotion": LIVE2D_EMOTION_ALIASES.get(emotion, emotion),
                            },
                        )
                    )
                    self.emotion_fired = True
                    break

        next_display_text = strip_emotion_tags_for_display(self.raw_buffer).lstrip("\r\n")
        if next_display_text.startswith(self.displayed_buffer):
            display_delta = next_display_text[len(self.displayed_buffer) :]
        else:
            display_delta = next_display_text
        self.displayed_buffer = next_display_text
        if display_delta:
            events.append(("content", {"text": display_delta}))
        return events


def sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def resolve_chat_settings(request: ChatStreamRequest) -> tuple[ModelProviderPreset, ModelSettings]:
    provider = get_provider(request.model.provider_name)
    settings = effective_model_settings(request.model, provider)
    provider = get_provider(settings.provider_name)
    settings = effective_model_settings(settings, provider)
    return provider, settings


def chat_tool_schemas() -> list[dict]:
    schemas: list[dict] = []
    for tool in tool_registry.list_tools():
        if tool.permission != "safe" or tool.handler is None:
            continue
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
        )
    return schemas


async def stream_model_turn(
    *,
    client: httpx.AsyncClient,
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    mode: str,
) -> AsyncGenerator[tuple[str, Any], None]:
    content_parts: list[str] = []
    tool_call_chunks: dict[int, dict[str, Any]] = {}
    finish_reason = ""

    async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
        if response.status_code < 200 or response.status_code >= 300:
            body = await response.aread()
            raise RuntimeError(f"HTTP {response.status_code} {body.decode('utf-8', 'ignore')[:500]}")

        async for line in response.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if not data or data == "[DONE]":
                continue
            try:
                item = json.loads(data)
            except json.JSONDecodeError:
                continue
            choice = (item.get("choices") or [{}])[0]
            finish_reason = str(choice.get("finish_reason") or finish_reason or "")
            delta = choice.get("delta") or {}
            if should_read_reasoning(mode):
                reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                reasoning = clean_display_text(clean_stream_chunk(str(reasoning)))
                if reasoning:
                    yield ("thinking", reasoning)

            content = delta.get("content") or ""
            if content:
                content_parts.append(str(content))
            accept_tool_call_delta(tool_call_chunks, delta)

    yield (
        "result",
        ModelTurn(
            content="".join(content_parts),
            tool_calls=build_model_tool_calls(tool_call_chunks),
            finish_reason=finish_reason,
        ),
    )


def accept_tool_call_delta(tool_call_chunks: dict[int, dict[str, Any]], delta: dict[str, Any]) -> None:
    for chunk in delta.get("tool_calls") or []:
        try:
            index = int(chunk.get("index", len(tool_call_chunks)))
        except (TypeError, ValueError):
            index = len(tool_call_chunks)
        current = tool_call_chunks.setdefault(
            index,
            {
                "id": "",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            },
        )
        if chunk.get("id"):
            current["id"] = str(chunk["id"])
        if chunk.get("type"):
            current["type"] = str(chunk["type"])
        function = chunk.get("function") or {}
        current_function = current.setdefault("function", {"name": "", "arguments": ""})
        if function.get("name"):
            current_function["name"] = f"{current_function.get('name', '')}{function['name']}"
        if "arguments" in function:
            current_function["arguments"] = f"{current_function.get('arguments', '')}{function.get('arguments') or ''}"


def build_model_tool_calls(tool_call_chunks: dict[int, dict[str, Any]]) -> list[ModelToolCall]:
    calls: list[ModelToolCall] = []
    for index in sorted(tool_call_chunks):
        item = tool_call_chunks[index]
        function = item.get("function") or {}
        name = str(function.get("name") or "").strip()
        if not name:
            continue
        calls.append(
            ModelToolCall(
                id=str(item.get("id") or f"tool_{index}_{uuid4().hex[:8]}"),
                name=name,
                arguments=str(function.get("arguments") or ""),
                index=index,
            )
        )
    return calls


def latest_user_message_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if getattr(message, "role", "") == "user" and str(getattr(message, "content", "")).strip():
            return str(getattr(message, "content", "")).strip()
    return ""


def build_forced_chat_tool_calls(user_request: str) -> list[ModelToolCall]:
    """Create deterministic tool calls when the user explicitly asks for tools.

    Normal chat still lets the model decide tool use with tool_choice=auto. This
    path only handles clear user directives such as "使用 websearch" or requests
    to generate/save a markdown/docx report.
    """
    text = user_request.strip()
    if not text:
        return []

    calls: list[ModelToolCall] = []
    if should_force_web_search(text):
        calls.append(
            make_forced_tool_call(
                "web_search",
                {
                    "query": infer_forced_search_query(text),
                    "freshness": infer_forced_freshness(text),
                    "maxResults": 10,
                    "fetchContent": True,
                },
                len(calls),
            )
        )
    if should_force_doc_writer(text):
        doc_args = build_doc_writer_fallback_arguments(raw_arguments="", user_request=text)
        doc_args.setdefault("writingMode", "advanced")
        doc_args.setdefault("llmWrite", True)
        if should_force_web_search(text):
            doc_args["useWebSearch"] = True
        calls.append(make_forced_tool_call("doc_writer", doc_args, len(calls)))
    return calls


def make_forced_tool_call(name: str, arguments: dict[str, Any], index: int) -> ModelToolCall:
    return ModelToolCall(
        id=f"forced_{name}_{uuid4().hex[:8]}",
        name=name,
        arguments=json.dumps(arguments, ensure_ascii=False),
        index=index,
    )


def should_force_web_search(text: str) -> bool:
    lowered = normalize_tool_directive_text(text)
    if re.search(r"(不用|不要用|不使用|禁止使用|禁用|without|do not use|don't use)\s*(web\s*search|web[-_]?search|websearch|网页搜索|网络搜索)", lowered):
        return False
    return has_explicit_tool_directive(
        lowered,
        aliases=("websearch", "web_search", "web search", "web-search", "网页搜索", "网络搜索", "web 搜索"),
    )


def should_force_doc_writer(text: str) -> bool:
    lowered = normalize_tool_directive_text(text)
    if has_explicit_tool_directive(
        lowered,
        aliases=("doc_writer", "docwriter", "doc writer", "文档写作", "写作工具", "报告工具"),
    ):
        return True
    if re.search(r"(写成|生成|保存|导出|输出|写一份|编写|起草).{0,50}(md|markdown|docx|xlsx|csv|txt|报告|文档)", lowered):
        return bool(re.search(r"(文件|保存|放在|目录|文件夹|output|generated_docs|[a-z]:\\)", lowered))
    if re.search(r"(md|markdown|docx|xlsx|csv|txt).{0,20}(报告|文档)", lowered):
        return bool(re.search(r"(文件|保存|放在|目录|文件夹|output|generated_docs|[a-z]:\\)", lowered))
    return False


def has_explicit_tool_directive(text: str, *, aliases: tuple[str, ...]) -> bool:
    verbs = r"(使用|调用|启用|走|用|强制使用|必须使用|use|call|invoke|run)"
    suffix = r"(工具|tool)?"
    for alias in aliases:
        pattern = re.escape(alias).replace(r"\ ", r"\s*").replace("_", r"[-_\s]*")
        if re.search(rf"{verbs}\s*{pattern}\s*{suffix}", text):
            return True
    return False


def normalize_tool_directive_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def infer_forced_search_query(text: str) -> str:
    query = normalize_tool_directive_text(text)
    query = re.sub(r"(使用|调用|启用|走|用|强制使用|必须使用|use|call|invoke|run)\s*(web\s*search|web[-_]?search|websearch|网页搜索|网络搜索|web\s*搜索)\s*[，,：:\s]*", "", query)
    query = re.sub(r"(写成|生成|保存|导出|输出|文件放在|保存到|输出到).*$", "", query).strip()
    query = re.sub(r"^(完成|帮我|请|请帮我)\s*", "", query)
    query = re.sub(r"^(查一下|查询|搜索|检索)\s*", "", query)
    query = query.strip(" ，,。；;：:")
    return query or text.strip()


def infer_forced_freshness(text: str) -> str:
    lowered = normalize_tool_directive_text(text)
    if any(keyword in lowered for keyword in ("现在", "当前", "今天", "最新", "today", "current", "latest", "now")):
        return "day"
    if any(keyword in lowered for keyword in ("本周", "最近一周", "week")):
        return "week"
    if any(keyword in lowered for keyword in ("本月", "最近一个月", "month")):
        return "month"
    return "any"


def extract_search_sources_for_doc_writer(executed: ExecutedToolCall) -> list[dict[str, Any]]:
    if executed.call.name != "web_search" or not executed.ok or not executed.result_payload:
        return []
    data = executed.result_payload.get("data")
    if not isinstance(data, dict):
        return []
    results = data.get("results")
    if not isinstance(results, list):
        return []
    return [dict(item) for item in results if isinstance(item, dict)]


def enrich_doc_writer_call_with_sources(call: ModelToolCall, sources: list[dict[str, Any]]) -> ModelToolCall:
    if call.name != "doc_writer" or not sources:
        return call
    try:
        args = parse_tool_arguments(call.arguments)
    except Exception:
        args = {}
    args["sources"] = sources
    args["useWebSearch"] = False
    return ModelToolCall(
        id=call.id,
        name=call.name,
        arguments=json.dumps(args, ensure_ascii=False),
        index=call.index,
    )


async def execute_model_tool_call(call: ModelToolCall, *, fallback_user_request: str = "") -> ExecutedToolCall:
    try:
        arguments = parse_tool_arguments(call.arguments)
    except Exception as error:
        message = str(error) or error.__class__.__name__
        if call.name == "doc_writer" and fallback_user_request.strip():
            arguments = build_doc_writer_fallback_arguments(
                raw_arguments=call.arguments,
                user_request=fallback_user_request,
            )
            if arguments:
                return await execute_registered_tool(
                    call,
                    arguments,
                    parse_warning=f"原始工具参数 JSON 不完整，已使用用户请求兜底：{message}",
                )
        return ExecutedToolCall(
            call=call,
            ok=False,
            summary=f"{call.name} 参数解析失败：{message}",
            content=json.dumps({"ok": False, "error": message}, ensure_ascii=False),
            arguments={},
            error=message,
            result_payload={"ok": False, "error": message},
        )

    return await execute_registered_tool(call, arguments)


async def execute_registered_tool(
    call: ModelToolCall,
    arguments: dict[str, Any],
    *,
    parse_warning: str = "",
) -> ExecutedToolCall:
    tool = tool_registry.get(call.name)
    if tool is None or tool.handler is None:
        return ExecutedToolCall(
            call=call,
            ok=False,
            summary=f"未知工具：{call.name}",
            content=json.dumps({"ok": False, "error": f"Unknown tool: {call.name}"}, ensure_ascii=False),
            arguments=arguments,
            error=f"Unknown tool: {call.name}",
            result_payload={"ok": False, "error": f"Unknown tool: {call.name}"},
        )
    if tool.permission != "safe":
        return ExecutedToolCall(
            call=call,
            ok=False,
            summary=f"工具需要确认，当前未执行：{call.name}",
            content=json.dumps({"ok": False, "error": "Tool requires confirmation"}, ensure_ascii=False),
            arguments=arguments,
            error="Tool requires confirmation",
            result_payload={"ok": False, "error": "Tool requires confirmation"},
        )

    try:
        result = await tool.handler(arguments)
        summary = str(result.get("summary") or f"{call.name} 执行完成。")
        if parse_warning:
            summary = f"{summary}（{parse_warning}）"
        payload = {
            "ok": True,
            "summary": summary,
            "data": result.get("data", {}),
        }
        return ExecutedToolCall(
            call=call,
            ok=True,
            summary=summary,
            content=truncate_json(payload, tool_result_max_chars(call.name)),
            arguments=arguments,
            result_payload=payload,
        )
    except Exception as error:
        message = str(error) or error.__class__.__name__
        return ExecutedToolCall(
            call=call,
            ok=False,
            summary=f"{call.name} 执行失败：{message}",
            content=json.dumps({"ok": False, "error": message}, ensure_ascii=False),
            arguments=arguments,
            error=message,
            result_payload={"ok": False, "error": message},
        )


def tool_result_max_chars(tool_name: str) -> int:
    if tool_name == "web_search":
        return CHAT_WEB_SEARCH_RESULT_MAX_CHARS
    if tool_name == "doc_writer":
        return CHAT_DOC_WRITER_RESULT_MAX_CHARS
    return CHAT_TOOL_RESULT_MAX_CHARS


def build_doc_writer_fallback_arguments(*, raw_arguments: str, user_request: str) -> dict[str, Any]:
    """Build a safe doc_writer request when streamed JSON arguments are truncated."""
    request_text = user_request.strip()
    if not request_text:
        return {}

    raw_fields = extract_partial_json_string_fields(
        raw_arguments,
        fields=("title", "topic", "instruction", "format", "outputPath", "fileName", "searchQuery"),
    )
    output_path = raw_fields.get("outputPath") or infer_output_path_from_text(request_text)
    doc_format = raw_fields.get("format") or infer_doc_format_from_text(request_text, output_path, raw_fields.get("fileName", ""))
    topic = raw_fields.get("topic") or infer_report_topic(request_text)
    title = raw_fields.get("title") or infer_report_title(request_text, topic)
    instruction = raw_fields.get("instruction") or request_text
    search_query = raw_fields.get("searchQuery") or topic or title

    args: dict[str, Any] = {
        "instruction": instruction,
        "topic": topic or title,
        "title": title,
        "format": doc_format or "md",
        "useWebSearch": should_doc_writer_fallback_search(request_text),
        "searchQuery": search_query,
        "save": True,
        "overwrite": False,
    }
    if output_path:
        args["outputPath"] = output_path
    if raw_fields.get("fileName"):
        args["fileName"] = raw_fields["fileName"]
    return args


def extract_partial_json_string_fields(raw: str, *, fields: tuple[str, ...]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in fields:
        match = re.search(rf'"{re.escape(field)}"\s*:\s*"((?:\\.|[^"\\])*)', raw)
        if not match:
            continue
        fragment = match.group(1)
        try:
            values[field] = json.loads(f'"{fragment}"')
        except json.JSONDecodeError:
            values[field] = fragment.replace(r"\/", "/").replace(r"\\", "\\")
    return {key: value.strip() for key, value in values.items() if value.strip()}


def infer_output_path_from_text(text: str) -> str:
    match = re.search(r"([A-Za-z]:\\[^\r\n\"'<>|，。；;]+?)(?=下|目录|文件夹|保存|$|\s|，|。|；|;)", text)
    if match:
        return match.group(1).strip()
    return ""


def infer_doc_format_from_text(text: str, output_path: str = "", file_name: str = "") -> str:
    haystack = " ".join([text, output_path, file_name]).lower()
    if re.search(r"\bdocx\b|\.docx", haystack):
        return "docx"
    if re.search(r"\bxlsx\b|\.xlsx", haystack):
        return "xlsx"
    if re.search(r"\bcsv\b|\.csv", haystack):
        return "csv"
    if re.search(r"\btxt\b|\.txt", haystack):
        return "txt"
    return "md"


def infer_report_topic(text: str) -> str:
    if re.search(r"(现在|当前|今天).{0,12}(几点|时间)|time|current time|what time", text, re.IGNORECASE):
        return "当前时间"
    match = re.search(r"关于\s*([^，。,.；;\n]+?)(?:最新|调研|报告|md|markdown|文档|$)", text, re.IGNORECASE)
    if match and match.group(1).strip():
        return match.group(1).strip()
    cleaned = re.sub(r"(使用|调用|启用|走|用|强制使用|必须使用)?\s*(web\s*search|web[-_]?search|websearch|网页搜索|网络搜索|web\s*搜索)", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"(查一下|查询|搜索|检索|完成|生成|写成\s*(md|markdown|docx|xlsx|csv|txt)?|写|编写|起草|一份|报告|文档|给我|文件放在.+$)", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ：:，,。.；;")
    return truncate_text(cleaned, 80) if cleaned else "调研报告"


def infer_report_title(text: str, topic: str) -> str:
    topic_text = topic.strip() or "调研"
    if any(keyword in text.lower() for keyword in ("最新", "latest")):
        return f"{topic_text} 最新调研报告"
    if any(keyword in text.lower() for keyword in ("调研", "research")):
        return f"{topic_text} 调研报告"
    if any(keyword in text.lower() for keyword in ("报告", "report")):
        return f"{topic_text} 报告"
    return truncate_text(text.strip(), 80) or "调研报告"


def should_doc_writer_fallback_search(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in ("最新", "当前", "调研", "研究", "资料", "来源", "引用", "latest", "research", "source"))


def build_tool_execution_fallback_response(executed: list[ExecutedToolCall], error_message: str) -> str:
    """Return a user-visible response when final model synthesis fails after tools ran."""
    successful_doc_writer = next((item for item in reversed(executed) if item.call.name == "doc_writer" and item.ok), None)
    if successful_doc_writer and successful_doc_writer.result_payload:
        payload = successful_doc_writer.result_payload
        data = payload.get("data") or {}
        summary = str(payload.get("summary") or successful_doc_writer.summary or "文档已生成。").strip()
        lines = [summary]
        output_path = data.get("outputPath") or data.get("absolutePath")
        absolute_path = data.get("absolutePath")
        if output_path:
            lines.extend(["", f"- 输出路径：{output_path}"])
        if absolute_path and absolute_path != output_path:
            lines.append(f"- 绝对路径：{absolute_path}")
        if data.get("usedWebSearch"):
            lines.append("- 已使用 Web Search 获取资料。")
        if error_message:
            lines.extend(["", f"注：模型最终总结阶段失败，已直接返回工具执行结果。错误：{error_message}"])
        return "\n".join(lines)

    successful = [item for item in executed if item.ok]
    if successful:
        lines = ["工具已经执行完成，但模型最终总结阶段失败。已直接返回工具摘要："]
        for item in successful[-5:]:
            lines.append(f"- {item.call.name}: {item.summary}")
        if error_message:
            lines.append(f"\n最终总结错误：{error_message}")
        return "\n".join(lines)
    return ""


def parse_tool_arguments(raw_arguments: str) -> dict[str, Any]:
    text = raw_arguments.strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"工具参数不是合法 JSON：{error}") from error
    if not isinstance(value, dict):
        raise ValueError("工具参数必须是 JSON object")
    return value


def safe_parse_tool_arguments(raw_arguments: str) -> dict[str, Any]:
    try:
        return parse_tool_arguments(raw_arguments)
    except Exception:
        return {}


def append_tool_messages(messages: list[dict[str, Any]], turn: ModelTurn, executed: list[ExecutedToolCall]) -> None:
    messages.append(
        {
            "role": "assistant",
            "content": turn.content or "",
            "tool_calls": [
                {
                    "id": item.call.id,
                    "type": "function",
                    "function": {
                        "name": item.call.name,
                        "arguments": item.call.arguments or "{}",
                    },
                }
                for item in executed
            ],
        }
    )
    for item in executed:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": item.call.id,
                "name": item.call.name,
                "content": item.content,
            }
        )


def tool_event_payload(
    call: ModelToolCall,
    *,
    status: str,
    arguments: dict[str, Any] | None = None,
    summary: str = "",
    result: str = "",
    result_payload: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    payload = {
        "id": call.id,
        "name": call.name,
        "status": status,
        "arguments": arguments or {},
        "summary": summary,
        "resultPreview": truncate_text(result, CHAT_TOOL_EVENT_PREVIEW_CHARS),
        "error": error,
    }
    if result_payload is not None:
        payload["result"] = result_payload
    return payload


def tool_thinking_line(payload: dict[str, Any]) -> str:
    status = payload.get("status")
    name = payload.get("name") or "tool"
    if status == "started":
        return f"调用工具 {name}。"
    if status == "completed":
        return f"工具 {name} 完成：{ensure_sentence(payload.get('summary') or '已返回结果')}"
    if status == "failed":
        return f"工具 {name} 失败：{ensure_sentence(payload.get('error') or payload.get('summary') or 'unknown')}"
    return f"工具 {name} 状态：{status}。"


def tool_event_log_line(payload: dict[str, Any]) -> str:
    return TOOL_EVENT_LOG_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def ensure_sentence(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "。"
    if text[-1] in {"。", ".", "!", "！", "?", "？"}:
        return text
    return f"{text}。"


def truncate_json(payload: dict[str, Any], limit: int) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    return truncate_text(text, limit)


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "\n...[truncated]"


def chat_image_attachments(attachments: list[ChatAttachment]) -> list[ChatAttachment]:
    return [
        attachment
        for attachment in attachments
        if attachment.path.strip()
        and (
            attachment.media_type == "image"
            or attachment.path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
        )
    ]


def build_vision_enriched_user_content(content: str, description: str, attachments: list[ChatAttachment]) -> str:
    image_lines = [
        f"- {attachment.original_filename or attachment.filename or attachment.path}: {attachment.path}"
        for attachment in attachments
    ]
    parts = [content.strip()]
    parts.append(
        "\n".join(
            [
                "<视觉理解摘要>",
                f"图片：{len(attachments)} 张",
                *image_lines,
                "",
                description.strip(),
                "</视觉理解摘要>",
            ]
        )
    )
    return "\n\n".join(part for part in parts if part.strip())


def vision_description_from_result(result: dict[str, Any]) -> str:
    data = result.get("data")
    if isinstance(data, dict):
        description = str(data.get("description") or "").strip()
        if description:
            return description
    return str(result.get("summary") or "").strip()


async def stream_chat(
    request: ChatStreamRequest,
    *,
    storage: SQLiteStorage | None = None,
) -> AsyncGenerator[str, None]:
    provider, settings = resolve_chat_settings(request)
    persona = get_persona(request.persona_id)
    voice_settings = effective_voice_settings(request.voice)
    stored_conversation_id = request.conversation_id
    chat_messages = [message.model_copy() for message in request.messages]
    last_user_request = latest_user_message_text(chat_messages)
    thinking_log: list[str] = []

    # Complex task routing: if the latest user message indicates a complex
    # multi-step task, create an Agent Task and emit a complex_task event
    # so the frontend can open the task panel. The task runs in the
    # background and writes its summary back to the conversation.
    try:
        from .complex_agent.chat_router import detect_complex_task_intent
        from .complex_agent.domain import AgentSettings
        from .complex_agent.task_hub import TaskBudget, agent_task_hub
        from .complex_agent.skills import load_skill_context
        from .complex_agent.runner import default_agent_runner
        from .complex_agent.tool_registry import sync_builtin_tools
        from .storage import DEFAULT_USER_ID as _DEFAULT_USER_ID

        last_user_msg = next(
            (m for m in reversed(chat_messages) if m.role == "user" and m.content.strip()),
            None,
        )
        if last_user_msg is not None and storage is not None and storage.connected:
            agent_settings_dict = (await storage.get_settings(_DEFAULT_USER_ID) or {}).get("agent") or {}
            agent_settings = AgentSettings.model_validate(agent_settings_dict)
            if agent_settings.enabled:
                enabled_skills = await __import__(
                    "amadeus_app.complex_agent.agent_storage", fromlist=["list_skill_packages"]
                ).list_skill_packages(storage, _DEFAULT_USER_ID)
                routing = detect_complex_task_intent(
                    last_user_msg.content,
                    enabled_skills=enabled_skills,
                    agent_settings=agent_settings,
                )
                if routing is not None:
                    sync_builtin_tools()
                    workspace = agent_settings.default_workspace or "."
                    skill_contexts = []
                    for skill_id in routing.get("skillIds", []):
                        skill = await __import__(
                            "amadeus_app.complex_agent.agent_storage", fromlist=["get_skill_package"]
                        ).get_skill_package(storage, skill_id)
                        if skill is None or not skill.get("enabled", True):
                            continue
                        from pathlib import Path
                        skill_dir = Path(skill["path"])
                        if skill_dir.exists():
                            try:
                                skill_contexts.append(load_skill_context(skill_dir))
                            except Exception as error:  # noqa: BLE001
                                _log.warning("failed to load skill %s: %s", skill_id, error)
                    budget = TaskBudget(
                        max_rounds=agent_settings.max_rounds,
                        max_tool_calls=agent_settings.max_tool_calls,
                        max_runtime_seconds=agent_settings.max_runtime_seconds,
                        max_sampling_depth=agent_settings.max_sampling_depth,
                    )
                    task = await agent_task_hub.create_task(
                        storage=storage,
                        user_id=_DEFAULT_USER_ID,
                        title=last_user_msg.content[:60],
                        prompt=last_user_msg.content,
                        workspace_path=workspace,
                        conversation_id=str(stored_conversation_id) if stored_conversation_id else None,
                        active_skill_ids=routing.get("skillIds", []),
                        settings=agent_settings.model_dump(by_alias=True),
                        budget=budget,
                    )
                    # Launch the agent in the background
                    from .complex_agent.domain import AgentTaskCreateRequest
                    agent_request = AgentTaskCreateRequest(
                        title=last_user_msg.content[:60],
                        prompt=last_user_msg.content,
                        workspacePath=workspace,
                        conversationId=stored_conversation_id,
                        skillIds=routing.get("skillIds", []),
                        model=request.model.model_dump(by_alias=True),
                        mode=request.mode,
                    )
                    background = asyncio.create_task(
                        default_agent_runner.run(
                            storage=storage,
                            task=task,
                            request=agent_request,
                            agent_settings=agent_settings,
                            skill_contexts=skill_contexts,
                        ),
                        name=f"agent-task-{task.task_id}",
                    )
                    await agent_task_hub.attach_background_task(task.task_id, background)
                    yield sse("complex_task", {
                        "taskId": task.task_id,
                        "title": task.title,
                        "reason": routing.get("reason", ""),
                        "skillIds": routing.get("skillIds", []),
                    })
                    return
    except Exception as error:  # noqa: BLE001
        _log.debug("Complex task routing failed (non-fatal): %s", error)

    image_attachments = chat_image_attachments(request.attachments)
    if image_attachments:
        last_user_message = next(
            (
                message
                for message in reversed(chat_messages)
                if message.role == "user" and message.content.strip()
            ),
            None,
        )
        if last_user_message is None:
            yield sse("error", {"message": "图片理解需要同时提供用户问题或说明。"})
            return
        if len(image_attachments) > VISION_MAX_IMAGES:
            yield sse("error", {"message": f"一次最多理解 {VISION_MAX_IMAGES} 张图片，请减少附件数量。"})
            return
        if not request.vision.enabled:
            yield sse("error", {"message": "视觉理解已关闭，请在设置中开启视觉后再发送图片。"})
            return

        vision_call = ModelToolCall(
            id=f"vision_{uuid4().hex[:8]}",
            name="image_understand",
            arguments=json.dumps(
                {
                    "attachments": [
                        attachment.model_dump(by_alias=True)
                        for attachment in image_attachments
                    ],
                    "prompt": last_user_message.content,
                },
                ensure_ascii=False,
            ),
        )
        started_payload = tool_event_payload(
            vision_call,
            status="started",
            arguments=safe_parse_tool_arguments(vision_call.arguments),
        )
        thinking_log.append(tool_thinking_line(started_payload))
        thinking_log.append(tool_event_log_line(started_payload))
        yield sse("status", {"phase": "vision"})
        yield sse("tool", started_payload)

        try:
            vision_result = await run_image_understand_agent(
                {
                    "attachments": [
                        attachment.model_dump(by_alias=True)
                        for attachment in image_attachments
                    ],
                    "prompt": last_user_message.content,
                    "model": request.model.model_dump(by_alias=True),
                    "vision": request.vision.model_dump(by_alias=True),
                }
            )
            vision_description = vision_description_from_result(vision_result)
            if not vision_description:
                raise RuntimeError("视觉模型未返回可用描述。")
        except Exception as error:
            message = str(error) or error.__class__.__name__
            failed_payload = tool_event_payload(
                vision_call,
                status="failed",
                arguments=safe_parse_tool_arguments(vision_call.arguments),
                summary="图片理解失败。",
                result=json.dumps({"ok": False, "error": message}, ensure_ascii=False),
                result_payload={"ok": False, "error": message},
                error=message,
            )
            thinking_log.append(tool_thinking_line(failed_payload))
            thinking_log.append(tool_event_log_line(failed_payload))
            yield sse("tool", failed_payload)
            yield sse("error", {"message": f"图片理解失败：{message}"})
            return

        completed_payload = tool_event_payload(
            vision_call,
            status="completed",
            arguments=safe_parse_tool_arguments(vision_call.arguments),
            summary=vision_result.get("summary") or "图片理解完成。",
            result=truncate_json({"ok": True, **vision_result}, CHAT_TOOL_RESULT_MAX_CHARS),
            result_payload={"ok": True, **vision_result},
        )
        thinking_log.append(tool_thinking_line(completed_payload))
        thinking_log.append(tool_event_log_line(completed_payload))
        yield sse("tool", completed_payload)
        last_user_message.content = build_vision_enriched_user_content(
            last_user_message.content,
            vision_description,
            image_attachments,
        )

    if storage is not None and stored_conversation_id is not None:
        last_user_message = next(
            (
                message
                for message in reversed(chat_messages)
                if message.role == "user" and message.content.strip()
            ),
            None,
        )
        if last_user_message is not None:
            try:
                await storage.add_message(
                    user_id=DEFAULT_USER_ID,
                    conversation_id=stored_conversation_id,
                    role="user",
                    content=last_user_message.content,
                )
            except Exception as error:
                yield sse("error", {"message": f"写入用户消息失败：{error}"})
                return

    if provider.name == "演示模型" or settings.base_url.startswith("local://"):
        demo_request = request.model_copy(update={"messages": chat_messages})
        async for chunk in _demo_stream(
            demo_request,
            storage=storage,
            persona=persona,
            voice_settings=voice_settings,
            model_settings=settings,
            provider=provider,
        ):
            yield chunk
        return

    if not settings.use_remote:
        yield sse("error", {"message": "请先启用远程 API，或选择 Ollama 等本地 OpenAI-compatible 服务。"})
        return
    if not provider.compatible:
        yield sse("error", {"message": f"{provider.name} 当前只提供配置项，专用协议后续接入。"})
        return
    if not settings.api_key and provider.name.lower() != "ollama":
        yield sse("error", {"message": "远程 API 需要先填写 API Key。"})
        return

    endpoint = f"{settings.base_url.rstrip('/')}/chat/completions"

    # 记忆检索与注入
    memory_context = ""
    session_summary_text: str | None = None
    if storage is not None and stored_conversation_id is not None:
        try:
            from ._common import get_memory_tree
            tree = get_memory_tree()
            if tree is not None:
                from .memory.injector import build_memory_context
                from .memory.query_rewriter import _detect_recall_triggers, is_profile_query
                from .memory.tree_search import search_memory_tree, get_user_profile_memories

                # 获取最后一条用户消息作为检索 query
                last_user_msg = ""
                for msg in reversed(chat_messages):
                    if msg.role == "user" and msg.content.strip():
                        last_user_msg = msg.content[:500]
                        break

                # 获取会话滚动摘要（用于 query 改写 + 注入，无论是否检索记忆都需要）
                try:
                    session_summary_text = await tree.get_session_summary(str(stored_conversation_id))
                except Exception:
                    session_summary_text = None

                # 记忆检索门控：
                # - 触发词出现 → 全量树检索
                # - 用户画像类问题（我叫什么/我偏好/按我的习惯）→ 轻量 profile 召回
                # - 普通具体问题 → 不检索
                gate_enabled = os.getenv("AMADEUS_MEMORY_GATE_ENABLED", "1").lower() not in ("0", "false", "no")
                need_full_search = bool(last_user_msg) and (
                    not gate_enabled or _detect_recall_triggers(last_user_msg)
                )
                need_profile_only = (
                    bool(last_user_msg)
                    and gate_enabled
                    and not need_full_search
                    and is_profile_query(last_user_msg)
                )

                if need_full_search:
                    # 构造最近消息 dict 列表（用于 query 改写）
                    recent_msgs_dict: list[dict] = [
                        {"role": m.role, "content": m.content}
                        for m in chat_messages[-6:]
                        if m.content.strip() and m.role in {"user", "assistant"}
                    ]

                    # 获取项目关联
                    project_id = await tree.get_conversation_project(str(stored_conversation_id))

                    # 获取项目名（用于 query 改写）
                    project_name: str | None = None
                    if project_id:
                        try:
                            projects = await tree.list_projects(DEFAULT_USER_ID)
                            for p in projects:
                                if p.get("id") == project_id:
                                    project_name = p.get("name")
                                    break
                        except Exception:
                            pass

                    # 检索记忆树（传递 provider/recent_messages/session_summary 用于 query 改写）
                    search_result = await search_memory_tree(
                        query=last_user_msg,
                        tree_store=tree,
                        settings=settings,
                        provider=provider,
                        conversation_id=str(stored_conversation_id),
                        project_id=project_id,
                        recent_messages=recent_msgs_dict,
                        session_summary=session_summary_text,
                        project_name=project_name,
                    )

                    # 无相关命中时不注入 MemoryContext（避免无关历史干扰模型）
                    result_dict = search_result.model_dump() if hasattr(search_result, "model_dump") else {}
                    leaf_nodes = result_dict.get("leaf_nodes", [])
                    if leaf_nodes:
                        # 获取用户画像
                        profile_memories = await get_user_profile_memories(tree)

                        # 构造记忆上下文（传递 user_profile）
                        memory_context = build_memory_context(
                            selected_paths=result_dict.get("selected_paths", []),
                            topic_nodes=result_dict.get("topic_nodes", []),
                            leaf_nodes=leaf_nodes,
                            session_summary=session_summary_text,
                            budget_chars=int(os.getenv("AMADEUS_MEMORY_CONTEXT_CHAR_BUDGET", "5000")),
                            user_profile=profile_memories,
                        )
                        if not memory_context.strip():
                            memory_context = ""
                    else:
                        _log.debug("记忆检索无相关命中，跳过注入: query=%r", last_user_msg[:60])

                elif need_profile_only:
                    # 用户画像类问题：轻量召回 profile memories，不做全量树检索
                    try:
                        profile_memories = await get_user_profile_memories(tree)
                        if profile_memories:
                            memory_context = build_memory_context(
                                selected_paths=[],
                                topic_nodes=[],
                                leaf_nodes=[],
                                session_summary=session_summary_text,
                                budget_chars=int(os.getenv("AMADEUS_MEMORY_CONTEXT_CHAR_BUDGET", "5000")),
                                user_profile=profile_memories,
                            )
                            if not memory_context.strip():
                                memory_context = ""
                            _log.debug("用户画像召回: %d 条 profile 记忆", len(profile_memories))
                    except Exception as profile_err:
                        _log.debug("Profile 召回失败 (non-fatal): %s", profile_err)
        except Exception as error:
            _log.debug("Memory retrieval failed (non-fatal): %s", error)

    payload_messages: list[dict[str, Any]] = [{"role": "system", "content": build_system_prompt(persona, request.mode, memory_context=memory_context)}]

    # 会话滚动摘要注入：当历史消息超过 12 条时，注入摘要替代硬截断
    if session_summary_text and len(chat_messages) > 12:
        payload_messages.append({
            "role": "system",
            "content": f"<PreviousConversationSummary>\n{session_summary_text}\n</PreviousConversationSummary>"
        })

    payload_messages.extend(
        {"role": message.role, "content": message.content}
        for message in chat_messages[-12:]
        if message.content.strip() and message.role in {"user", "assistant"}
    )
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    state = ChatStreamState()
    tools = chat_tool_schemas()
    voice_stream = build_voice_coordinator(
        persona=persona,
        voice_settings=voice_settings,
        model_settings=settings,
        provider=provider,
    )
    initial_payload = build_chat_payload(settings, provider, request.mode, payload_messages, tools=tools, stream=True)
    yield sse("status", {"phase": "connecting", "provider": provider.name, "model": initial_payload["model"]})
    for voice_event in voice_stream.startup_events():
        yield voice_event
    executed_tool_history: list[ExecutedToolCall] = []
    forced_tool_calls = build_forced_chat_tool_calls(last_user_request)
    if forced_tool_calls:
        thinking_log.append("检测到用户显式要求使用工具，已由后端强制执行对应工具。")
        yield sse("thinking", {"text": "检测到用户显式要求使用工具，正在先执行指定工具。\n"})
        forced_executed: list[ExecutedToolCall] = []
        forced_search_sources: list[dict[str, Any]] = []
        yield sse("status", {"phase": "tools", "forced": True})
        for raw_call in forced_tool_calls:
            call = enrich_doc_writer_call_with_sources(raw_call, forced_search_sources)
            started_payload = tool_event_payload(
                call,
                status="started",
                arguments=safe_parse_tool_arguments(call.arguments),
            )
            thinking_log.append(tool_thinking_line(started_payload))
            thinking_log.append(tool_event_log_line(started_payload))
            yield sse("tool", started_payload)

            executed = await execute_model_tool_call(call, fallback_user_request=last_user_request)
            forced_executed.append(executed)
            forced_search_sources.extend(extract_search_sources_for_doc_writer(executed))
            completed_payload = tool_event_payload(
                call,
                status="completed" if executed.ok else "failed",
                arguments=executed.arguments,
                summary=executed.summary,
                result=executed.content,
                result_payload=executed.result_payload,
                error=executed.error,
            )
            thinking_log.append(tool_thinking_line(completed_payload))
            thinking_log.append(tool_event_log_line(completed_payload))
            yield sse("tool", completed_payload)

        executed_tool_history.extend(forced_executed)
        append_tool_messages(
            payload_messages,
            ModelTurn(content="已按用户显式要求先执行指定工具。", tool_calls=[item.call for item in forced_executed]),
            forced_executed,
        )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=20.0, read=None)) as client:
            final_turn: ModelTurn | None = None
            for round_index in range(CHAT_TOOL_MAX_ROUNDS + 1):
                use_tools = tools if round_index < CHAT_TOOL_MAX_ROUNDS else None
                payload = build_chat_payload(
                    settings,
                    provider,
                    request.mode,
                    payload_messages,
                    tools=use_tools,
                    stream=True,
                )
                yield sse("status", {"phase": "streaming" if round_index == 0 else "finalizing"})
                turn: ModelTurn | None = None
                async for event_kind, event_payload in stream_model_turn(
                    client=client,
                    endpoint=endpoint,
                    headers=headers,
                    payload=payload,
                    mode=request.mode,
                ):
                    if event_kind == "thinking":
                        thinking_text = str(event_payload)
                        thinking_log.append(thinking_text)
                        yield sse("thinking", {"text": thinking_text})
                    elif event_kind == "result":
                        turn = event_payload

                if turn is None:
                    final_turn = ModelTurn(content="", tool_calls=[], finish_reason="")
                    break

                if turn.tool_calls and round_index < CHAT_TOOL_MAX_ROUNDS:
                    if turn.content.strip():
                        intermediate = f"模型在工具调用前形成了中间说明：{turn.content.strip()}"
                        thinking_log.append(intermediate)
                        yield sse("thinking", {"text": f"\n{intermediate}\n"})

                    executed_calls: list[ExecutedToolCall] = []
                    yield sse("status", {"phase": "tools"})
                    for call in turn.tool_calls:
                        started_payload = tool_event_payload(
                            call,
                            status="started",
                            arguments=safe_parse_tool_arguments(call.arguments),
                        )
                        thinking_log.append(tool_thinking_line(started_payload))
                        thinking_log.append(tool_event_log_line(started_payload))
                        yield sse("tool", started_payload)

                        executed = await execute_model_tool_call(call, fallback_user_request=last_user_request)
                        executed_calls.append(executed)
                        completed_payload = tool_event_payload(
                            call,
                            status="completed" if executed.ok else "failed",
                            arguments=executed.arguments,
                            summary=executed.summary,
                            result=executed.content,
                            result_payload=executed.result_payload,
                            error=executed.error,
                        )
                        thinking_log.append(tool_thinking_line(completed_payload))
                        thinking_log.append(tool_event_log_line(completed_payload))
                        yield sse("tool", completed_payload)

                    executed_tool_history.extend(executed_calls)
                    append_tool_messages(payload_messages, turn, executed_calls)
                    continue

                final_turn = turn
                break

            final_text = (final_turn.content if final_turn is not None else "").strip()
            if not final_text:
                final_text = "工具调用已完成，但模型没有返回可展示的最终回答。"

            async for output_event in _emit_generated_events(
                state.accept_content(final_text),
                voice_stream=voice_stream,
            ):
                yield output_event
    except httpx.ConnectError as error:
        fallback_text = build_tool_execution_fallback_response(
            executed_tool_history,
            f"连接模型服务失败：{error}",
        )
        if fallback_text:
            async for output_event in _emit_generated_events(
                state.accept_content(fallback_text),
                voice_stream=voice_stream,
            ):
                yield output_event
        else:
            voice_stream.cancel()
            yield sse("error", {"message": f"连接模型服务失败：{error}"})
            return
    except httpx.HTTPError as error:
        fallback_text = build_tool_execution_fallback_response(
            executed_tool_history,
            f"模型请求失败：{error}",
        )
        if fallback_text:
            async for output_event in _emit_generated_events(
                state.accept_content(fallback_text),
                voice_stream=voice_stream,
            ):
                yield output_event
        else:
            voice_stream.cancel()
            yield sse("error", {"message": f"模型请求失败：{error}"})
            return
    except Exception as error:
        fallback_text = build_tool_execution_fallback_response(
            executed_tool_history,
            str(error) or error.__class__.__name__,
        )
        if fallback_text:
            async for output_event in _emit_generated_events(
                state.accept_content(fallback_text),
                voice_stream=voice_stream,
            ):
                yield output_event
        else:
            voice_stream.cancel()
            yield sse("error", {"message": str(error) or error.__class__.__name__})
            return

    for voice_event in voice_stream.finish():
        yield voice_event
    async for voice_event in voice_stream.drain_all():
        yield voice_event

    assistant_voice = voice_stream.assistant_voice_payload()
    if storage is not None and stored_conversation_id is not None and state.displayed_buffer.strip():
        try:
            await storage.add_message(
                user_id=DEFAULT_USER_ID,
                conversation_id=stored_conversation_id,
                role="assistant",
                content=state.displayed_buffer,
                thinking="\n".join(item for item in thinking_log if item.strip()),
                assistant_voice=assistant_voice,
            )
        except Exception as error:
            yield sse("error", {"message": f"写入助手消息失败：{error}"})
            return

    # 异步记忆提取入队
    if storage is not None and stored_conversation_id is not None:
        try:
            from ._common import get_memory_tree
            tree = get_memory_tree()
            if tree is not None:
                # 按批次提取记忆：仅在累计到 N 轮后入队 ingest
                # AMADEUS_MEMORY_EXTRACT_INTERVAL=6 表示每 6 轮用户消息提取一次
                extract_interval = int(os.getenv("AMADEUS_MEMORY_EXTRACT_INTERVAL", "6"))
                # 从 SQLite 持久化消息统计用户消息数，不依赖请求携带的 messages 数量
                # 这样即使移动端/外部 API 只发最近一条消息也能正确触发
                user_msg_count = 0
                try:
                    from uuid import UUID as _UUID
                    conv = await storage.get_conversation(
                        user_id=DEFAULT_USER_ID,
                        conversation_id=_UUID(str(stored_conversation_id)),
                    )
                    if conv and conv.get("messages"):
                        user_msg_count = sum(
                            1 for m in conv["messages"] if m.get("role") == "user"
                        )
                except Exception:
                    # 降级：用请求中的消息数
                    user_msg_count = sum(1 for m in chat_messages if m.role == "user")
                if extract_interval > 0 and user_msg_count > 0 and user_msg_count % extract_interval == 0:
                    await tree.enqueue_job(
                        user_id=DEFAULT_USER_ID,
                        job_type="ingest",
                        conversation_id=str(stored_conversation_id),
                        payload={
                            "conversationId": str(stored_conversation_id),
                            "extractWindow": extract_interval * 2,  # 处理最近 N*2 条消息
                        },
                    )
        except Exception:
            pass  # 记忆入队失败不影响聊天

    yield sse("done", {"text": state.displayed_buffer, "assistantVoice": assistant_voice})


async def _demo_stream(
    request: ChatStreamRequest,
    *,
    storage: SQLiteStorage | None = None,
    persona,
    voice_settings,
    model_settings: ModelSettings,
    provider: ModelProviderPreset,
) -> AsyncGenerator[str, None]:
    state = ChatStreamState()
    voice_stream = build_voice_coordinator(
        persona=persona,
        voice_settings=voice_settings,
        model_settings=model_settings,
        provider=provider,
    )
    demo_text = (
        "[emotion:joy]\n"
        "这是本地演示流。模型、语音和 Live2D 的接口已经接好；填入 OpenAI-compatible API Key 后就会走云端生成。"
    )
    yield sse("status", {"phase": "streaming", "provider": "演示模型", "model": "amaduse-demo-stream"})
    for voice_event in voice_stream.startup_events():
        yield voice_event
    for index in range(0, len(demo_text), 4):
        await asyncio.sleep(0.035)
        async for output_event in _emit_generated_events(
            state.accept_content(demo_text[index : index + 4]),
            voice_stream=voice_stream,
        ):
            yield output_event
    for voice_event in voice_stream.finish():
        yield voice_event
    async for voice_event in voice_stream.drain_all():
        yield voice_event
    assistant_voice = voice_stream.assistant_voice_payload()
    if storage is not None and request.conversation_id is not None and state.displayed_buffer.strip():
        try:
            await storage.add_message(
                user_id=DEFAULT_USER_ID,
                conversation_id=request.conversation_id,
                role="assistant",
                content=state.displayed_buffer,
                assistant_voice=assistant_voice,
            )
        except Exception as error:
            voice_stream.cancel()
            yield sse("error", {"message": f"写入助手消息失败：{error}"})
            return
    yield sse("done", {"text": state.displayed_buffer, "assistantVoice": assistant_voice})


async def _emit_generated_events(
    events: list[tuple[str, dict]],
    *,
    voice_stream: VoiceCoordinator,
) -> AsyncGenerator[str, None]:
    for event, payload_item in events:
        if event != "content":
            yield sse(event, payload_item)
            continue

        text = str(payload_item.get("text", ""))
        scheduled_events = voice_stream.accept(text)
        if not voice_stream.should_delay_text:
            yield sse(event, payload_item)
        for scheduled_event in scheduled_events:
            yield scheduled_event

    async for voice_event in voice_stream.drain_ready():
        yield voice_event
