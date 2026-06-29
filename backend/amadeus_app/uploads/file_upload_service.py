from __future__ import annotations

import mimetypes
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from ..file_tools.file_reader import (
    DENIED_FILE_NAMES,
    DENIED_SUFFIXES,
    READABLE_DOCUMENT_SUFFIXES,
    TEXT_SUFFIXES,
    UPLOAD_DOCUMENT_SUFFIXES,
)


WORKSPACE_PATH_VALUE = os.getenv("AMADEUS_WORKSPACE_PATH", "").strip()
WorkspacePath = Path(WORKSPACE_PATH_VALUE).expanduser().resolve() if WORKSPACE_PATH_VALUE else Path(__file__).resolve().parents[3]
DEFAULT_UPLOAD_DIR = Path("agent_uploads")
DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_BYTES_LIMIT = 50 * 1024 * 1024
UPLOAD_CHUNK_SIZE = 1024 * 1024
INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
DENIED_UPLOAD_DIR_PARTS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    "backend/runtime",
}
TEXT_TYPE_BY_SUFFIX = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".rst": "markdown",
    ".txt": "plain_text",
    ".log": "plain_text",
    ".json": "json",
    ".jsonl": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "config",
    ".ini": "config",
    ".cfg": "config",
    ".conf": "config",
    ".env.example": "config",
    ".gitignore": "config",
    ".csv": "csv",
    ".tsv": "csv",
    ".py": "code_python",
    ".pyi": "code_python",
    ".ts": "code_typescript",
    ".tsx": "code_typescript",
    ".js": "code_javascript",
    ".jsx": "code_javascript",
    ".css": "stylesheet",
    ".html": "html",
    ".xml": "xml",
    ".sql": "sql",
    ".sh": "shell",
    ".ps1": "shell",
    ".bat": "shell",
    ".pdf": "pdf",
    ".doc": "word_legacy",
    ".docx": "word",
    ".xls": "excel_legacy",
    ".xlsx": "excel",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".wav": "audio",
    ".mp3": "audio",
    ".m4a": "audio",
    ".mp4": "audio",
    ".mpeg": "audio",
    ".mpga": "audio",
    ".ogg": "audio",
    ".oga": "audio",
    ".webm": "audio",
}
TEXT_TYPE_BY_MIME = {
    "application/json": "json",
    "application/x-ndjson": "json",
    "application/yaml": "yaml",
    "application/x-yaml": "yaml",
    "text/csv": "csv",
    "text/tab-separated-values": "csv",
    "text/markdown": "markdown",
    "text/html": "html",
    "application/xml": "xml",
    "text/xml": "xml",
    "text/css": "stylesheet",
    "text/plain": "plain_text",
    "application/pdf": "pdf",
    "application/msword": "word_legacy",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word",
    "application/vnd.ms-excel": "excel_legacy",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "excel",
    "image/png": "image",
    "image/jpeg": "image",
    "image/webp": "image",
    "image/gif": "image",
    "audio/wav": "audio",
    "audio/wave": "audio",
    "audio/x-wav": "audio",
    "audio/mpeg": "audio",
    "audio/mp3": "audio",
    "audio/mp4": "audio",
    "audio/x-m4a": "audio",
    "audio/ogg": "audio",
    "audio/webm": "audio",
    "video/webm": "audio",
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".mp4", ".mpeg", ".mpga", ".ogg", ".oga", ".webm"}


class UploadValidationError(ValueError):
    """Raised when an upload violates validation rules."""


class UploadConflictError(FileExistsError):
    """Raised when preserving the uploaded filename would overwrite a file."""


class UploadTooLargeError(UploadValidationError):
    """Raised when the uploaded file exceeds the configured size limit."""


async def save_uploaded_text_file(
    file: Any,
    *,
    device: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    original_filename = str(getattr(file, "filename", "") or "").strip()
    filename = safe_uploaded_filename(original_filename)
    content_type = normalize_content_type(str(getattr(file, "content_type", "") or ""))
    text_type = classify_text_type(filename, content_type)
    if not text_type:
        raise UploadValidationError(f"Unsupported upload file type: {filename}")

    target_dir = upload_target_dir(device=device, text_type=text_type)
    target_path = (target_dir / filename).resolve()
    ensure_inside_workspace(target_path)
    if target_path.exists() and not overwrite:
        raise UploadConflictError(f"Uploaded file already exists: {relative_path(target_path)}")

    target_dir.mkdir(parents=True, exist_ok=True)
    temp_path = target_dir / f".{filename}.{uuid4().hex}.tmp"
    size = await write_upload_to_temp(file, temp_path)
    if is_image_upload(filename, content_type):
        if not looks_like_image(temp_path):
            temp_path.unlink(missing_ok=True)
            raise UploadValidationError(f"Uploaded file content is not a supported image: {filename}")
    elif should_validate_text_content(filename) and not looks_like_text(temp_path):
        temp_path.unlink(missing_ok=True)
        raise UploadValidationError(f"Uploaded file content is not text-like: {filename}")

    if target_path.exists() and not overwrite:
        temp_path.unlink(missing_ok=True)
        raise UploadConflictError(f"Uploaded file already exists: {relative_path(target_path)}")
    temp_path.replace(target_path)

    return {
        "device": normalize_device(device),
        "date": current_date_text(),
        "textType": text_type,
        "originalFilename": original_filename,
        "filename": filename,
        "path": relative_path(target_path),
        "contentType": content_type or mimetypes.guess_type(filename)[0] or "text/plain",
        "sizeBytes": size,
        "uploadedAt": current_time_iso(),
        "mediaType": media_type_for_upload(filename, text_type),
        "readableByFileReader": is_readable_by_file_reader(filename),
        "visionReadable": is_vision_readable(filename, content_type),
    }


async def write_upload_to_temp(file: Any, temp_path: Path) -> int:
    max_bytes = upload_max_bytes()
    size = 0
    try:
        with temp_path.open("wb") as output:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLargeError(f"Uploaded file exceeds {max_bytes} bytes")
                output.write(chunk)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        close = getattr(file, "close", None)
        if close is not None:
            await close()
    return size


def upload_target_dir(*, device: str, text_type: str) -> Path:
    root = upload_root_dir()
    return root / normalize_device(device) / current_date_text() / text_type


def upload_root_dir() -> Path:
    raw = os.getenv("AMADEUS_UPLOAD_DIR", "").strip()
    candidate = Path(raw) if raw else DEFAULT_UPLOAD_DIR
    resolved = candidate.resolve() if candidate.is_absolute() else (WorkspacePath / candidate).resolve()
    ensure_inside_workspace(resolved)
    rel_parts = resolved.relative_to(WorkspacePath).parts
    for index, part in enumerate(rel_parts):
        joined = "/".join(rel_parts[: index + 1])
        if part in DENIED_UPLOAD_DIR_PARTS or joined in DENIED_UPLOAD_DIR_PARTS:
            raise UploadValidationError(f"Upload directory cannot use restricted path: {part}")
    return resolved


def safe_uploaded_filename(raw_filename: str) -> str:
    filename = raw_filename.replace("\\", "/").split("/")[-1].strip()
    if not filename:
        raise UploadValidationError("Uploaded file must include a filename")
    filename = INVALID_FILENAME_CHARS_RE.sub("_", filename).rstrip(" .")
    if not filename or filename in {".", ".."}:
        raise UploadValidationError("Uploaded filename is not valid")
    name = filename.lower()
    suffix = Path(filename).suffix.lower()
    if name in DENIED_FILE_NAMES or (name.startswith(".env.") and name != ".env.example"):
        raise UploadValidationError(f"Uploaded filename is restricted: {filename}")
    if suffix in DENIED_SUFFIXES:
        raise UploadValidationError(f"Uploaded file suffix is restricted: {suffix}")
    return filename


def classify_text_type(filename: str, content_type: str) -> str:
    lower_name = filename.lower()
    if lower_name in TEXT_TYPE_BY_SUFFIX:
        return TEXT_TYPE_BY_SUFFIX[lower_name]

    suffix = Path(filename).suffix.lower()
    if suffix in TEXT_TYPE_BY_SUFFIX:
        return TEXT_TYPE_BY_SUFFIX[suffix]
    if lower_name not in TEXT_SUFFIXES and suffix not in TEXT_SUFFIXES and suffix not in UPLOAD_DOCUMENT_SUFFIXES:
        guessed = mimetypes.guess_type(filename)[0] or ""
        if not guessed.startswith("text/") and guessed not in TEXT_TYPE_BY_MIME and content_type not in TEXT_TYPE_BY_MIME:
            return ""

    if content_type in TEXT_TYPE_BY_MIME:
        return TEXT_TYPE_BY_MIME[content_type]
    guessed = mimetypes.guess_type(filename)[0] or ""
    if guessed in TEXT_TYPE_BY_MIME:
        return TEXT_TYPE_BY_MIME[guessed]
    if guessed.startswith("text/") or content_type.startswith("text/"):
        return "plain_text"
    return "other_text"


def should_validate_text_content(filename: str) -> bool:
    suffix = Path(filename).suffix.lower()
    return suffix not in UPLOAD_DOCUMENT_SUFFIXES and suffix not in IMAGE_SUFFIXES and suffix not in AUDIO_SUFFIXES


def is_readable_by_file_reader(filename: str) -> bool:
    suffix = Path(filename).suffix.lower()
    if suffix in AUDIO_SUFFIXES:
        return False
    return suffix not in UPLOAD_DOCUMENT_SUFFIXES or suffix in READABLE_DOCUMENT_SUFFIXES


def is_image_upload(filename: str, content_type: str) -> bool:
    suffix = Path(filename).suffix.lower()
    return suffix in IMAGE_SUFFIXES or normalize_content_type(content_type).startswith("image/")


def is_vision_readable(filename: str, content_type: str) -> bool:
    return is_image_upload(filename, content_type)


def media_type_for_upload(filename: str, text_type: str) -> str:
    if text_type == "image" or Path(filename).suffix.lower() in IMAGE_SUFFIXES:
        return "image"
    if text_type == "audio" or Path(filename).suffix.lower() in AUDIO_SUFFIXES:
        return "audio"
    if Path(filename).suffix.lower() in UPLOAD_DOCUMENT_SUFFIXES:
        return "document"
    return "text"


def looks_like_text(path: Path) -> bool:
    data = path.read_bytes()[:8192]
    if b"\x00" in data:
        return False
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            data.decode(encoding)
            return True
        except UnicodeDecodeError:
            continue
    return False


def looks_like_image(path: Path) -> bool:
    data = path.read_bytes()[:16]
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith((b"GIF87a", b"GIF89a")):
        return True
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True
    return False


def normalize_device(value: str) -> str:
    device = str(value or "host").strip().lower()
    if device in {"host", "web", "desktop", "local", "pc"}:
        return "host"
    raise UploadValidationError("Upload device must be host")


def normalize_content_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def upload_max_bytes() -> int:
    raw = os.getenv("AMADEUS_UPLOAD_MAX_BYTES", "").strip()
    try:
        parsed = int(raw) if raw else DEFAULT_MAX_UPLOAD_BYTES
    except ValueError:
        parsed = DEFAULT_MAX_UPLOAD_BYTES
    return min(max(parsed, 1), MAX_UPLOAD_BYTES_LIMIT)


def ensure_inside_workspace(path: Path) -> None:
    if not is_relative_to(path, WorkspacePath):
        raise UploadValidationError("Upload path must stay inside the workspace")


def relative_path(path: Path) -> str:
    return str(path.resolve().relative_to(WorkspacePath)).replace("\\", "/")


def current_date_text() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def current_time_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
