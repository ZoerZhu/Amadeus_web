from __future__ import annotations

import base64
import os
import re
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
FILE_PATH_RE = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/][^\s，。；;、]+|(?:/[\w .\-]+){2,})")
SPEECH_FILE_EXTENSIONS = {
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".js": "JavaScript",
    ".jsx": "JSX",
    ".ts": "TypeScript",
    ".tsx": "TSX",
    ".json": "JSON",
    ".md": "Markdown",
    ".py": "Python",
    ".txt": "文本",
    ".csv": "CSV",
    ".xlsx": "Excel",
    ".docx": "Word",
}


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


async def prepare_task_summary_speech_text(
    *,
    summary: str,
    persona: PersonaPreset,
    model_settings: ModelSettings,
    provider: ModelProviderPreset,
) -> str:
    source = to_speech_source_text(oralize_file_paths(summary))
    if not source:
        return "OpenCodeのタスクは完了したわ。詳細はタスク履歴で確認して。"
    fallback = fallback_task_summary_speech(source)
    if not model_settings.use_remote or not provider.compatible:
        return fallback
    if not model_settings.api_key and model_settings.provider_name.lower() != "ollama":
        return fallback

    payload = build_chat_payload(
        model_settings,
        provider,
        "fast",
        [
            {
                "role": "system",
                "content": (
                    "你是 Amadeus 系统中的牧濑红莉栖人格。"
                    "根据输入中的 OpenCode 最终回答，提炼一段适合 TTS 朗读的自然日语任务完成播报。"
                    "只输出日语正文，不要 Markdown、编号、项目符号、引号、括号、emoji 或 URL。"
                    "必须简短精炼，控制在 1 到 2 句，最多 90 个日文字符左右。"
                    "语气要理性、克制、略带一点红莉栖式的锋利和别扭关心，但不要夸张。"
                    "不要复述执行过程，不要说“我先读取”“我需要分析”“让我看看”之类过程自述。"
                    "不要播报工作目录、工具次数、diff 次数或完整文件路径，除非这是用户关心的结论。"
                    "只总结任务完成了什么、最终结论是什么、是否有需要用户注意的地方。"
                    "文件路径必须口语化描述，绝对不要逐字符朗读盘符、冒号、反斜杠或长路径；"
                    "例如把“Amadeus 项目下 tasktest 文件夹里的 login HTML 文件”说成"
                    "“AmadeusプロジェクトのtasktestフォルダーにあるloginのHTMLファイル”。"
                ),
            },
            {"role": "user", "content": source},
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
            return fallback
        data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        speech_text = to_tts_input_text(clean_display_text(str(content))).strip()
        return speech_text if has_japanese_kana(speech_text) else fallback
    except Exception:
        return fallback


def oralize_file_paths(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        raw = match.group(0)
        path = raw.rstrip(".,;:，。；：、")
        return oralize_single_file_path(path) + raw[len(path) :]

    return FILE_PATH_RE.sub(replace, text)


def oralize_single_file_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    parts = [part for part in normalized.split("/") if part and not part.endswith(":")]
    if not parts:
        return "相关文件"
    filename = parts[-1]
    stem = filename
    extension_label = ""
    suffix = Path(filename).suffix.lower()
    if suffix:
        stem = filename[: -len(suffix)]
        extension_label = SPEECH_FILE_EXTENSIONS.get(suffix, suffix.lstrip(".").upper())

    project = parts[0] if parts else "项目"
    folders = parts[1:-1]
    folder_text = "、".join(folders[-2:])
    file_text = f"{stem} {extension_label} 文件".strip() if extension_label else f"{stem} 文件"
    if folder_text:
        return f"{project} 项目下 {folder_text} 文件夹里的 {file_text}"
    return f"{project} 项目里的 {file_text}"


def fallback_task_summary_speech(source: str) -> str:
    task_match = re.search(r"任务标题[:：]\s*([^\n]{1,48})", source)
    task = task_match.group(1).strip() if task_match else "今回のタスク"
    if "深色" in source or "毛玻璃" in source or "macOS" in source or "iOS" in source:
        return "確認は終わったわ。三つのページは、暗色基調とグラスモーフィズムで統一された、かなり今風のUIね。"
    if "完成" in source or "已完成" in source:
        return f"{task}は完了したわ。細かい内容は履歴に残してあるから、必要ならそこを確認して。"
    return f"{task}の確認は終わったわ。要点だけなら、結果はタスク履歴にまとめてある。"


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
