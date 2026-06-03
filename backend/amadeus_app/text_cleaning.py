from __future__ import annotations

import re
import unicodedata


EMOTION_RE = re.compile(r"\[emotion:(\w+)]", re.IGNORECASE)

FENCED_CODE_RE = re.compile(r"```[\s\S]*?```")
INLINE_CODE_RE = re.compile(r"`([^`]*)`")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)]\([^)]*\)")
URL_RE = re.compile(r"https?://\S+")
HEADING_PREFIX_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s*")
LIST_PREFIX_RE = re.compile(r"(?m)^\s*(?:[-*+]\s+|\d+[.)、]\s*)")
MARKDOWN_NOISE_RE = re.compile(r"[`*_~>#\[\]{}()（）\"“”‘’'「」『』【】《》<>|\\/]+")
WHITESPACE_RE = re.compile(r"\s+")


def clean_stream_chunk(value: str) -> str:
    if not value:
        return ""
    trimmed = value.strip()
    if trimmed.lower() == "null" or _all_null_tokens(trimmed):
        return ""
    return value


def clean_display_text(value: str) -> str:
    cleaned = re.sub(r"(?i)(null){2,}", "", value)
    return "" if cleaned.strip().lower() == "null" else cleaned


def strip_emotion_tags_for_display(value: str) -> str:
    without_complete_tags = EMOTION_RE.sub("", value)
    marker_start = without_complete_tags.rfind("[")
    if marker_start < 0:
        return without_complete_tags
    trailing_text = without_complete_tags[marker_start:]
    if "\n" in trailing_text:
        return without_complete_tags
    trailing = without_complete_tags[marker_start + 1 :].lower()
    full_marker_prefix = "emotion:"
    if full_marker_prefix.startswith(trailing) or trailing.startswith(full_marker_prefix):
        return without_complete_tags[:marker_start]
    return without_complete_tags


def to_speech_source_text(value: str) -> str:
    text = FENCED_CODE_RE.sub(" ", value)
    text = MARKDOWN_LINK_RE.sub(r"\1", text)
    text = INLINE_CODE_RE.sub(r"\1", text)
    text = URL_RE.sub(" ", text)
    text = HEADING_PREFIX_RE.sub("", text)
    text = LIST_PREFIX_RE.sub("", text)
    text = MARKDOWN_NOISE_RE.sub("", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "So")
    text = WHITESPACE_RE.sub(" ", text)
    return text.strip()


def to_tts_input_text(value: str) -> str:
    text = to_speech_source_text(value)
    text = re.sub(r"[.!?。！？]{4,}", "。", text)
    text = re.sub(r"[,，、]{3,}", "、", text)
    text = re.sub(r"[:：]{3,}", "：", text)
    return text.strip()


def has_japanese_kana(value: str) -> bool:
    return any("\u3040" <= ch <= "\u30ff" for ch in value)


def _all_null_tokens(value: str) -> bool:
    return bool(value) and len(value) % 4 == 0 and all(
        value[index : index + 4].lower() == "null" for index in range(0, len(value), 4)
    )
