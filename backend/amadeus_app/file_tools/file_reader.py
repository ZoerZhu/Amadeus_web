from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Literal


FileReaderAction = Literal["read", "list", "stat"]
WorkspacePath = Path(__file__).resolve().parents[3]
DEFAULT_MAX_BYTES = 128 * 1024
MAX_BYTES_LIMIT = 1024 * 1024
DEFAULT_MAX_ENTRIES = 200
MAX_ENTRIES_LIMIT = 1000
DENIED_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".playwright-mcp",
}
DENIED_FILE_NAMES = {
    ".env",
}
DENIED_SUFFIXES = {
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".crt",
    ".cer",
    ".sqlite",
    ".db",
    ".pyc",
    ".moc3",
}
TEXT_SUFFIXES = {
    ".txt",
    ".log",
    ".rst",
    ".md",
    ".markdown",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".env.example",
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".css",
    ".html",
    ".xml",
    ".csv",
    ".tsv",
    ".sql",
    ".sh",
    ".ps1",
    ".bat",
    ".gitignore",
}
UPLOAD_DOCUMENT_SUFFIXES = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
}
READABLE_DOCUMENT_SUFFIXES = {
    ".pdf",
    ".docx",
    ".xls",
    ".xlsx",
}


async def run_file_reader(args: dict[str, Any]) -> dict[str, Any]:
    action = normalize_action(args.get("action") or args.get("operation") or "read")
    path_text = str(args.get("path") or args.get("filePath") or args.get("file") or ".").strip() or "."
    target = resolve_workspace_path(path_text)
    ensure_allowed_path(target)

    if action == "read":
        result = read_file(
            target,
            start_line=optional_int(args.get("startLine") or args.get("start_line")),
            end_line=optional_int(args.get("endLine") or args.get("end_line")),
            max_bytes=clamp_int(args.get("maxBytes") or args.get("max_bytes"), DEFAULT_MAX_BYTES, 1, MAX_BYTES_LIMIT),
            include_line_numbers=bool(args.get("includeLineNumbers", args.get("include_line_numbers", False))),
        )
        return {
            "summary": f"已读取 {result['path']}（{result['lineCount']} 行，truncated={result['truncated']}）。",
            "data": result,
        }
    if action == "list":
        result = list_directory(
            target,
            recursive=bool(args.get("recursive", False)),
            include_hidden=bool(args.get("includeHidden", args.get("include_hidden", False))),
            max_entries=clamp_int(args.get("maxEntries") or args.get("max_entries"), DEFAULT_MAX_ENTRIES, 1, MAX_ENTRIES_LIMIT),
        )
        return {
            "summary": f"已列出 {result['path']}，返回 {len(result['entries'])} 个条目。",
            "data": result,
        }
    result = stat_path(target)
    return {
        "summary": f"已读取 {result['path']} 的元信息。",
        "data": result,
    }


def read_file(
    path: Path,
    *,
    start_line: int | None,
    end_line: int | None,
    max_bytes: int,
    include_line_numbers: bool,
) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(relative_path(path))
    if not path.is_file():
        raise ValueError("file_reader read action requires a file path")
    ensure_readable_file(path)
    size = path.stat().st_size
    text, encoding, truncated = extract_readable_text(path, max_bytes=max_bytes)
    lines = text.splitlines()
    total_lines = len(lines)

    start = max((start_line or 1), 1)
    end = min((end_line or total_lines), total_lines)
    if end < start:
        selected: list[str] = []
    else:
        selected = lines[start - 1 : end]

    if include_line_numbers:
        selected = [f"{index + start}: {line}" for index, line in enumerate(selected)]

    return {
        "action": "read",
        "path": relative_path(path),
        "type": "file",
        "sizeBytes": size,
        "encoding": encoding,
        "mimeType": mimetypes.guess_type(path.name)[0] or "text/plain",
        "startLine": start,
        "endLine": end,
        "lineCount": len(selected),
        "totalLineCount": total_lines,
        "truncated": truncated,
        "content": "\n".join(selected),
    }


def list_directory(path: Path, *, recursive: bool, include_hidden: bool, max_entries: int) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(relative_path(path))
    if not path.is_dir():
        raise ValueError("file_reader list action requires a directory path")
    entries: list[dict[str, Any]] = []
    skipped = 0
    for item in iter_allowed_directory_items(path, recursive=recursive):
        if len(entries) >= max_entries:
            skipped += 1
            continue
        if not include_hidden and any(part.startswith(".") for part in item.relative_to(path).parts):
            skipped += 1
            continue
        try:
            ensure_allowed_path(item)
        except ValueError:
            skipped += 1
            continue
        stat = item.stat()
        entries.append(
            {
                "path": relative_path(item),
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
                "sizeBytes": stat.st_size if item.is_file() else 0,
                "modifiedAt": modified_time(stat.st_mtime),
            }
        )
    entries.sort(key=lambda entry: (entry["type"] != "directory", entry["path"].lower()))
    return {
        "action": "list",
        "path": relative_path(path),
        "type": "directory",
        "recursive": recursive,
        "entries": entries,
        "skipped": skipped,
        "truncated": skipped > 0,
    }


def iter_allowed_directory_items(path: Path, *, recursive: bool):
    if not recursive:
        yield from path.iterdir()
        return

    stack = [path]
    while stack:
        current = stack.pop()
        for item in current.iterdir():
            try:
                ensure_allowed_path(item)
            except ValueError:
                continue
            yield item
            if item.is_dir():
                stack.append(item)


def stat_path(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(relative_path(path))
    stat = path.stat()
    return {
        "action": "stat",
        "path": relative_path(path),
        "type": "directory" if path.is_dir() else "file",
        "sizeBytes": stat.st_size if path.is_file() else 0,
        "modifiedAt": modified_time(stat.st_mtime),
        "mimeType": mimetypes.guess_type(path.name)[0] or "",
        "readableAsText": path.is_file() and is_readable_file(path),
    }


def resolve_workspace_path(path_text: str) -> Path:
    candidate = Path(path_text)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (WorkspacePath / candidate).resolve()
    if not is_relative_to(resolved, WorkspacePath):
        raise ValueError("file_reader path must stay inside the workspace")
    return resolved


def ensure_allowed_path(path: Path) -> None:
    rel_parts = path.relative_to(WorkspacePath).parts
    for part in rel_parts:
        if part in DENIED_DIR_NAMES:
            raise ValueError(f"file_reader cannot access restricted directory: {part}")
    if path.name in DENIED_FILE_NAMES:
        raise ValueError(f"file_reader cannot access restricted file: {path.name}")
    if path.name.startswith(".env.") and path.name != ".env.example":
        raise ValueError(f"file_reader cannot access restricted file: {path.name}")
    if path.suffix.lower() in DENIED_SUFFIXES:
        raise ValueError(f"file_reader cannot access restricted file type: {path.suffix}")


def ensure_allowed_text_file(path: Path) -> None:
    if not is_text_file(path):
        raise ValueError(f"file_reader only reads text-like files; unsupported suffix: {path.suffix or path.name}")


def ensure_readable_file(path: Path) -> None:
    suffix = path.suffix.lower()
    if is_text_file(path) or suffix in READABLE_DOCUMENT_SUFFIXES:
        return
    if suffix == ".doc":
        raise ValueError("file_reader can store .doc uploads, but reading legacy Word .doc requires converting it to .docx or text first")
    raise ValueError(f"file_reader cannot read unsupported suffix: {path.suffix or path.name}")


def is_text_file(path: Path) -> bool:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name in TEXT_SUFFIXES or suffix in TEXT_SUFFIXES:
        return True
    guessed = mimetypes.guess_type(path.name)[0] or ""
    return guessed.startswith("text/")


def is_readable_file(path: Path) -> bool:
    return is_text_file(path) or path.suffix.lower() in READABLE_DOCUMENT_SUFFIXES


def extract_readable_text(path: Path, *, max_bytes: int) -> tuple[str, str, bool]:
    if is_text_file(path):
        size = path.stat().st_size
        read_bytes = path.read_bytes()[:max_bytes]
        text, encoding = decode_text(read_bytes)
        return text, encoding, size > len(read_bytes)

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(path, max_chars=max_bytes)
    if suffix == ".docx":
        return extract_docx_text(path, max_chars=max_bytes)
    if suffix == ".xlsx":
        return extract_xlsx_text(path, max_chars=max_bytes)
    if suffix == ".xls":
        return extract_xls_text(path, max_chars=max_bytes)
    raise ValueError(f"file_reader cannot read unsupported suffix: {path.suffix or path.name}")


def limit_text(text: str, *, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip(), True


def extract_pdf_text(path: Path, *, max_chars: int) -> tuple[str, str, bool]:
    try:
        from pypdf import PdfReader
    except Exception as error:
        raise RuntimeError("读取 PDF 需要安装 pypdf，请运行 pip install pypdf。") from error

    reader = PdfReader(str(path))
    parts: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            parts.append(f"--- Page {index} ---\n{text.strip()}")
        if sum(len(part) for part in parts) > max_chars:
            break
    text, truncated = limit_text("\n\n".join(parts), max_chars=max_chars)
    return text, "pdf-text", truncated


def extract_docx_text(path: Path, *, max_chars: int) -> tuple[str, str, bool]:
    try:
        from docx import Document
    except Exception as error:
        raise RuntimeError("读取 DOCX 需要安装 python-docx，请运行 pip install python-docx。") from error

    document = Document(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table_index, table in enumerate(document.tables, start=1):
        rows = []
        for row in table.rows:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            rows.append("\t".join(cells))
        if rows:
            parts.append(f"--- Table {table_index} ---\n" + "\n".join(rows))
    text, truncated = limit_text("\n\n".join(parts), max_chars=max_chars)
    return text, "docx-text", truncated


def extract_xlsx_text(path: Path, *, max_chars: int) -> tuple[str, str, bool]:
    try:
        from openpyxl import load_workbook
    except Exception as error:
        raise RuntimeError("读取 XLSX 需要安装 openpyxl，请运行 pip install openpyxl。") from error

    workbook = load_workbook(str(path), read_only=True, data_only=True)
    parts: list[str] = []
    try:
        for sheet in workbook.worksheets:
            rows: list[str] = [f"--- Sheet: {sheet.title} ---"]
            for row in sheet.iter_rows(values_only=True):
                values = ["" if value is None else str(value) for value in row]
                if any(value.strip() for value in values):
                    rows.append("\t".join(values).rstrip())
                if sum(len(part) for part in parts) + sum(len(item) for item in rows) > max_chars:
                    break
            parts.append("\n".join(rows))
            if sum(len(part) for part in parts) > max_chars:
                break
    finally:
        workbook.close()
    text, truncated = limit_text("\n\n".join(parts), max_chars=max_chars)
    return text, "xlsx-text", truncated


def extract_xls_text(path: Path, *, max_chars: int) -> tuple[str, str, bool]:
    try:
        import xlrd
    except Exception as error:
        raise RuntimeError("读取 XLS 需要安装 xlrd，请运行 pip install xlrd。") from error

    workbook = xlrd.open_workbook(str(path), on_demand=True)
    parts: list[str] = []
    try:
        for sheet_name in workbook.sheet_names():
            sheet = workbook.sheet_by_name(sheet_name)
            rows: list[str] = [f"--- Sheet: {sheet_name} ---"]
            for row_index in range(sheet.nrows):
                values = [str(sheet.cell_value(row_index, col_index)) for col_index in range(sheet.ncols)]
                if any(value.strip() for value in values):
                    rows.append("\t".join(values).rstrip())
                if sum(len(part) for part in parts) + sum(len(item) for item in rows) > max_chars:
                    break
            parts.append("\n".join(rows))
            if sum(len(part) for part in parts) > max_chars:
                break
    finally:
        workbook.release_resources()
    text, truncated = limit_text("\n\n".join(parts), max_chars=max_chars)
    return text, "xls-text", truncated


def decode_text(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def normalize_action(value: Any) -> FileReaderAction:
    action = str(value or "read").strip().lower()
    if action in {"read", "read_file", "file"}:
        return "read"
    if action in {"list", "ls", "dir", "list_dir", "directory"}:
        return "list"
    if action in {"stat", "metadata", "info"}:
        return "stat"
    raise ValueError(f"Unsupported file_reader action: {action}")


def relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WorkspacePath)).replace("\\", "/") or "."
    except ValueError:
        return str(path)


def modified_time(timestamp: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(timestamp).astimezone().isoformat()


def optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
