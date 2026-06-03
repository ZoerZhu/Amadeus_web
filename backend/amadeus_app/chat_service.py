from __future__ import annotations

import asyncio
import json
import os
import unicodedata
from dataclasses import dataclass
from typing import AsyncGenerator

import httpx

from .domain import ChatStreamRequest, ModelProviderPreset, ModelSettings, VoiceSettings
from .model_adapter import build_chat_payload, should_read_reasoning
from .personas import PersonaPreset, build_system_prompt, get_persona
from .providers import get_provider
from .runtime_config import effective_model_settings, effective_voice_settings
from .storage import DEFAULT_USER_ID, PostgresStorage
from .text_cleaning import (
    EMOTION_RE,
    clean_display_text,
    clean_stream_chunk,
    strip_emotion_tags_for_display,
    to_speech_source_text,
)
from .local_voice_service import local_voice_engine_url
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
SPEECH_SEGMENT_TARGET_CHAR_COUNT = env_int("AMADEUS_SPEECH_SEGMENT_TARGET_CHARS", 30)
SPEECH_SEGMENT_MAX_CHAR_COUNT = env_int("AMADEUS_SPEECH_SEGMENT_MAX_CHARS", 55)
SPEECH_SEGMENT_HARD_BREAK_MIN_CHAR_COUNT = env_int("AMADEUS_SPEECH_SEGMENT_HARD_BREAK_MIN_CHARS", 20)
SPEECH_PREPARE_CONCURRENCY = 1
SPEECH_TTS_MAX_ATTEMPTS = 2
SPEECH_HARD_BREAKS = {"。", "！", "？", "!", "?", "；", ";", "\n"}
SPEECH_SOFT_BREAKS = {"，", ",", "、", " ", "：", ":"}


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

    @property
    def should_delay_text(self) -> bool:
        return self.synchronize_text

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

    @property
    def should_delay_text(self) -> bool:
        return False

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
            match = EMOTION_RE.search(self.raw_buffer)
            if match:
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


async def stream_chat(
    request: ChatStreamRequest,
    *,
    storage: PostgresStorage | None = None,
) -> AsyncGenerator[str, None]:
    provider, settings = resolve_chat_settings(request)
    persona = get_persona(request.persona_id)
    voice_settings = effective_voice_settings(request.voice)
    stored_conversation_id = request.conversation_id

    if storage is not None and stored_conversation_id is not None:
        last_user_message = next(
            (
                message
                for message in reversed(request.messages)
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
        async for chunk in _demo_stream(
            request,
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
    payload_messages = [{"role": "system", "content": build_system_prompt(persona, request.mode)}]
    payload_messages.extend(
        {"role": message.role, "content": message.content}
        for message in request.messages[-12:]
        if message.content.strip() and message.role in {"user", "assistant"}
    )
    payload = build_chat_payload(settings, provider, request.mode, payload_messages, stream=True)
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    state = ChatStreamState()
    voice_stream = build_voice_coordinator(
        persona=persona,
        voice_settings=voice_settings,
        model_settings=settings,
        provider=provider,
    )
    yield sse("status", {"phase": "connecting", "provider": provider.name, "model": payload["model"]})
    for voice_event in voice_stream.startup_events():
        yield voice_event
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=20.0, read=None)) as client:
            async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    body = await response.aread()
                    yield sse(
                        "error",
                        {"message": f"HTTP {response.status_code} {body.decode('utf-8', 'ignore')[:500]}"},
                    )
                    return

                yield sse("status", {"phase": "streaming"})
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
                    delta = choice.get("delta") or {}
                    if should_read_reasoning(request.mode):
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                        reasoning = clean_display_text(clean_stream_chunk(str(reasoning)))
                        if reasoning:
                            yield sse("thinking", {"text": reasoning})
                    content = delta.get("content") or ""
                    async for output_event in _emit_generated_events(
                        state.accept_content(str(content)),
                        voice_stream=voice_stream,
                    ):
                        yield output_event
    except httpx.ConnectError as error:
        voice_stream.cancel()
        yield sse("error", {"message": f"连接模型服务失败：{error}"})
        return
    except httpx.HTTPError as error:
        voice_stream.cancel()
        yield sse("error", {"message": f"模型请求失败：{error}"})
        return
    except Exception as error:
        voice_stream.cancel()
        yield sse("error", {"message": str(error) or error.__class__.__name__})
        return

    for voice_event in voice_stream.finish():
        yield voice_event
    async for voice_event in voice_stream.drain_all():
        yield voice_event

    if storage is not None and stored_conversation_id is not None and state.displayed_buffer.strip():
        try:
            await storage.add_message(
                user_id=DEFAULT_USER_ID,
                conversation_id=stored_conversation_id,
                role="assistant",
                content=state.displayed_buffer,
            )
        except Exception as error:
            yield sse("error", {"message": f"写入助手消息失败：{error}"})
            return

    yield sse("done", {"text": state.displayed_buffer})


async def _demo_stream(
    request: ChatStreamRequest,
    *,
    storage: PostgresStorage | None = None,
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
    if storage is not None and request.conversation_id is not None and state.displayed_buffer.strip():
        try:
            await storage.add_message(
                user_id=DEFAULT_USER_ID,
                conversation_id=request.conversation_id,
                role="assistant",
                content=state.displayed_buffer,
            )
        except Exception as error:
            voice_stream.cancel()
            yield sse("error", {"message": f"写入助手消息失败：{error}"})
            return
    for voice_event in voice_stream.finish():
        yield voice_event
    async for voice_event in voice_stream.drain_all():
        yield voice_event
    yield sse("done", {"text": state.displayed_buffer})


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
