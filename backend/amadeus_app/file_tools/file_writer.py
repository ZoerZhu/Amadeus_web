"""Write/append/mkdir/patch/move/delete file tools with hash checks + backups."""
from __future__ import annotations

import difflib
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from .path_guard import PathGuard, compute_sha256

CODE_SUFFIXES = frozenset({
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh", ".ps1",
    ".bat", ".cmd", ".env", ".vue", ".svelte", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php",
})


def is_code_or_config(path: Path) -> bool:
    if path.name == ".env" or path.name.startswith(".env."):
        return True
    return path.suffix.lower() in CODE_SUFFIXES


class CodeChangeBudget:
    """Tracks the main Agent's direct code-change footprint (files + lines)."""

    def __init__(self, *, max_files: int = 5, max_lines: int = 300) -> None:
        self.max_files = max_files
        self.max_lines = max_lines
        self._files: set[str] = set()
        self._lines = 0

    def is_code(self, path: Path) -> bool:
        return is_code_or_config(path)

    def record(self, rel_path: str, added_lines: int) -> bool:
        """Record a code change. Returns True if within budget, False if exceeded."""
        self._files.add(rel_path)
        self._lines += max(0, int(added_lines))
        return len(self._files) <= self.max_files and self._lines <= self.max_lines

    def would_exceed(self, rel_path: str, added_lines: int) -> bool:
        files = self._files | {rel_path}
        lines = self._lines + max(0, int(added_lines))
        return len(files) > self.max_files or lines > self.max_lines


class FileBackupStore:
    """On-disk backup of file contents before destructive operations."""

    def __init__(self, app_data_dir: str, task_id: str) -> None:
        self._root = Path(app_data_dir).expanduser().resolve() / "file_backups" / task_id
        self._root.mkdir(parents=True, exist_ok=True)

    def backup(self, path: Path) -> dict[str, Any] | None:
        """Back up a file's current content. Returns None if file doesn't exist."""
        if not path.exists() or not path.is_file():
            return None
        backup_name = f"{uuid.uuid4().hex}_{path.name}"
        backup_path = self._root / backup_name
        try:
            shutil.copy2(path, backup_path)
        except OSError:
            return None
        return {
            "originalPath": str(path),
            "backupPath": str(backup_path),
            "sha256": compute_sha256(path.read_text(encoding="utf-8", errors="replace")),
        }

    def cleanup(self) -> None:
        """Remove all backups for this task."""
        try:
            shutil.rmtree(self._root)
        except OSError:
            pass


def _diff_summary(old_content: str | None, new_content: str) -> str:
    if old_content is None:
        return f"+{len(new_content.splitlines())} lines (new file)"
    diff = difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile="old", tofile="new",
    )
    diff_text = "".join(diff)
    added = sum(1 for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_text.splitlines() if l.startswith("-") and not l.startswith("---"))
    return f"+{added} -{removed} lines"


async def run_file_write(
    *,
    path: str,
    content: str,
    workspace_root: str,
    expected_hash: str | None = None,
    encoding: str = "utf-8",
    allowed_external_paths: list[str] | None = None,
    file_tracker: Any = None,
    backup_store: FileBackupStore | None = None,
    source: str = "file_write",
) -> dict[str, Any]:
    """Write content to a file. Overwrites require a matching expectedHash."""
    guard = PathGuard(workspace_root, allowed_external_paths)
    try:
        target = guard.resolve_write(path)
        guard.ensure_not_denied(target)
        guard.ensure_writable_suffix(target)
    except ValueError as error:
        return {"ok": False, "summary": str(error), "data": {}}

    old_content = None
    if target.exists():
        try:
            old_content = target.read_text(encoding=encoding)
        except Exception:  # noqa: BLE001
            old_content = None
        if expected_hash is None:
            return {
                "ok": False,
                "summary": "file_write overwriting an existing file requires expectedHash from a prior file_read.",
                "data": {"path": str(target)},
            }
        if compute_sha256(old_content or "") != expected_hash:
            return {
                "ok": False,
                "summary": "expectedHash mismatch: file changed since last read. Re-read the file to get the current hash.",
                "data": {"path": str(target)},
            }
        if backup_store:
            backup_store.backup(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding=encoding)
    new_hash = compute_sha256(content)
    rel_path = guard.relative_to_workspace(target)
    diff_summary = _diff_summary(old_content, content)

    if file_tracker:
        try:
            await file_tracker.record_write(rel_path, old_content, content, source=source)
        except Exception:  # noqa: BLE001
            pass

    return {
        "ok": True,
        "summary": f"已写入文件 {target.name}。",
        "data": {"path": str(target), "newHash": new_hash, "diffSummary": diff_summary, "oldHash": expected_hash},
        "modified_paths": [rel_path],
    }


async def run_file_append(
    *,
    path: str,
    content: str,
    workspace_root: str,
    expected_hash: str | None = None,
    encoding: str = "utf-8",
    allowed_external_paths: list[str] | None = None,
    file_tracker: Any = None,
    backup_store: FileBackupStore | None = None,
    source: str = "file_append",
) -> dict[str, Any]:
    """Append content to a file. Existing files require expectedHash."""
    guard = PathGuard(workspace_root, allowed_external_paths)
    try:
        target = guard.resolve_write(path)
        guard.ensure_not_denied(target)
        guard.ensure_writable_suffix(target)
    except ValueError as error:
        return {"ok": False, "summary": str(error), "data": {}}

    old_content = ""
    if target.exists():
        try:
            old_content = target.read_text(encoding=encoding)
        except Exception:  # noqa: BLE001
            old_content = ""
        if expected_hash is None:
            return {
                "ok": False,
                "summary": "file_append to an existing file requires expectedHash from a prior file_read.",
                "data": {"path": str(target)},
            }
        if compute_sha256(old_content) != expected_hash:
            return {
                "ok": False,
                "summary": "expectedHash mismatch: file changed since last read.",
                "data": {"path": str(target)},
            }
        if backup_store:
            backup_store.backup(target)

    new_content = old_content + content
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_content, encoding=encoding)
    new_hash = compute_sha256(new_content)
    rel_path = guard.relative_to_workspace(target)
    diff_summary = _diff_summary(old_content if old_content else None, new_content)

    if file_tracker:
        try:
            await file_tracker.record_write(rel_path, old_content or None, new_content, source=source)
        except Exception:  # noqa: BLE001
            pass

    return {
        "ok": True,
        "summary": f"已追加到文件 {target.name}。",
        "data": {"path": str(target), "newHash": new_hash, "diffSummary": diff_summary, "oldHash": expected_hash},
        "modified_paths": [rel_path],
    }


async def run_file_mkdir(
    *,
    path: str,
    workspace_root: str,
    allowed_external_paths: list[str] | None = None,
    file_tracker: Any = None,
    source: str = "file_mkdir",
) -> dict[str, Any]:
    """Create a directory (and parents). No-op if it already exists."""
    guard = PathGuard(workspace_root, allowed_external_paths)
    try:
        target = guard.resolve_write(path)
        guard.ensure_not_denied(target)
    except ValueError as error:
        return {"ok": False, "summary": str(error), "data": {}}
    target.mkdir(parents=True, exist_ok=True)
    rel_path = guard.relative_to_workspace(target)
    return {
        "ok": True,
        "summary": f"已创建目录 {target.name}。",
        "data": {"path": str(target)},
        "modified_paths": [rel_path],
    }
