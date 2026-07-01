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
    code_change_budget: CodeChangeBudget | None = None,
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

    rel_path = guard.relative_to_workspace(target)

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

    # Enforce code-change budget for code/config files written by the main Agent
    if code_change_budget and code_change_budget.is_code(target):
        if old_content is None:
            added_lines = len(content.splitlines())
        else:
            added_lines = max(0, len(content.splitlines()) - len(old_content.splitlines()))
        if code_change_budget.would_exceed(rel_path, added_lines):
            return {
                "ok": False,
                "summary": (
                    f"Code change budget exceeded ({code_change_budget.max_files} files / "
                    f"{code_change_budget.max_lines} lines). Delegate complex changes to code_agent."
                ),
                "data": {"path": str(target)},
            }
        code_change_budget.record(rel_path, added_lines)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding=encoding)
    new_hash = compute_sha256(content)
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
    code_change_budget: CodeChangeBudget | None = None,
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

    rel_path = guard.relative_to_workspace(target)

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

    # Enforce code-change budget for code/config files appended by the main Agent
    if code_change_budget and code_change_budget.is_code(target):
        added_lines = max(0, len(new_content.splitlines()) - len(old_content.splitlines()))
        if code_change_budget.would_exceed(rel_path, added_lines):
            return {
                "ok": False,
                "summary": (
                    f"Code change budget exceeded ({code_change_budget.max_files} files / "
                    f"{code_change_budget.max_lines} lines). Delegate complex changes to code_agent."
                ),
                "data": {"path": str(target)},
            }
        code_change_budget.record(rel_path, added_lines)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_content, encoding=encoding)
    new_hash = compute_sha256(new_content)
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
    already_existed = target.exists()
    target.mkdir(parents=True, exist_ok=True)
    rel_path = guard.relative_to_workspace(target)
    if file_tracker and not already_existed:
        try:
            await file_tracker.record_mkdir(rel_path, source=source)
        except Exception:  # noqa: BLE001
            pass
    return {
        "ok": True,
        "summary": f"已创建目录 {target.name}。",
        "data": {"path": str(target)},
        "modified_paths": [rel_path],
    }


async def run_file_patch(
    *,
    path: str,
    old_text: str,
    new_text: str,
    workspace_root: str,
    expected_hash: str,
    encoding: str = "utf-8",
    allowed_external_paths: list[str] | None = None,
    file_tracker: Any = None,
    backup_store: FileBackupStore | None = None,
    code_change_budget: CodeChangeBudget | None = None,
    source: str = "file_patch",
) -> dict[str, Any]:
    """Apply an exact oldText→newText replacement. Requires a matching expectedHash."""
    guard = PathGuard(workspace_root, allowed_external_paths)
    try:
        target = guard.resolve_write(path)
        guard.ensure_not_denied(target)
        guard.ensure_writable_suffix(target)
    except ValueError as error:
        return {"ok": False, "summary": str(error), "data": {}}

    if not target.exists() or not target.is_file():
        return {"ok": False, "summary": f"file not found: {target}", "data": {}}

    try:
        old_content = target.read_text(encoding=encoding)
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "summary": f"cannot read file: {error}", "data": {}}

    if compute_sha256(old_content) != expected_hash:
        return {
            "ok": False,
            "summary": "expectedHash mismatch: file changed since last read. Re-read to get the current hash.",
            "data": {"path": str(target)},
        }

    occurrences = old_content.count(old_text)
    if occurrences == 0:
        return {
            "ok": False,
            "summary": "oldText not found in file. No changes made.",
            "data": {"path": str(target)},
        }
    if occurrences > 1:
        return {
            "ok": False,
            "summary": f"oldText matches {occurrences} locations; provide a more specific oldText to patch exactly one location.",
            "data": {"path": str(target), "matches": occurrences},
        }

    new_content = old_content.replace(old_text, new_text, 1)
    rel_path = guard.relative_to_workspace(target)

    # Enforce code-change budget for code/config files patched by the main Agent
    if code_change_budget and code_change_budget.is_code(target):
        added_lines = max(0, len(new_content.splitlines()) - len(old_content.splitlines()))
        # Count net changed lines as a simple proxy
        changed_lines = sum(
            1 for l in difflib.unified_diff(
                old_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
            ) if (l.startswith("+") and not l.startswith("+++")) or (l.startswith("-") and not l.startswith("---"))
        )
        if code_change_budget.would_exceed(rel_path, changed_lines):
            return {
                "ok": False,
                "summary": (
                    f"Code change budget exceeded ({code_change_budget.max_files} files / "
                    f"{code_change_budget.max_lines} lines). Delegate complex changes to code_agent."
                ),
                "data": {"path": str(target)},
            }
        code_change_budget.record(rel_path, changed_lines)

    if backup_store:
        backup_store.backup(target)
    target.write_text(new_content, encoding=encoding)
    new_hash = compute_sha256(new_content)
    diff_summary = _diff_summary(old_content, new_content)

    if file_tracker:
        try:
            await file_tracker.record_write(rel_path, old_content, new_content, source=source)
        except Exception:  # noqa: BLE001
            pass

    return {
        "ok": True,
        "summary": f"已 patch 文件 {target.name}。",
        "data": {"path": str(target), "newHash": new_hash, "diffSummary": diff_summary, "oldHash": expected_hash},
        "modified_paths": [rel_path],
    }


async def run_file_move(
    *,
    src: str,
    dst: str,
    workspace_root: str,
    allowed_external_paths: list[str] | None = None,
    file_tracker: Any = None,
    backup_store: FileBackupStore | None = None,
    source: str = "file_move",
) -> dict[str, Any]:
    """Move a file. Backs up source content first."""
    guard = PathGuard(workspace_root, allowed_external_paths)
    try:
        src_path = guard.resolve_write(src)
        dst_path = guard.resolve_write(dst)
        guard.ensure_not_denied(src_path)
        guard.ensure_not_denied(dst_path)
        guard.ensure_writable_suffix(dst_path)
    except ValueError as error:
        return {"ok": False, "summary": str(error), "data": {}}

    if not src_path.exists():
        return {"ok": False, "summary": f"source not found: {src_path}", "data": {}}

    src_rel = guard.relative_to_workspace(src_path)
    dst_rel = guard.relative_to_workspace(dst_path)

    old_dst_content = None
    if dst_path.exists():
        try:
            old_dst_content = dst_path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            old_dst_content = None
        if backup_store:
            backup_store.backup(dst_path)

    src_content = None
    if backup_store:
        src_content = backup_store.backup(src_path)

    # Read source content before move (file won't exist after) for tracker.
    src_text_content = None
    if file_tracker:
        try:
            src_text_content = src_path.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            src_text_content = None

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(src_path), str(dst_path))
    except OSError as error:
        return {"ok": False, "summary": f"move failed: {error}", "data": {}}

    if file_tracker:
        try:
            if old_dst_content is None:
                await file_tracker.record_move(src_rel, dst_rel, old_content=src_text_content, source=source)
            else:
                # Overwrite: record src delete + dst modification so rollback restores both.
                await file_tracker.record_delete(src_rel, old_content=src_text_content, source=source)
                new_dst_content = dst_path.read_text(encoding="utf-8", errors="replace")
                await file_tracker.record_write(dst_rel, old_dst_content, new_dst_content, source=source)
        except Exception:  # noqa: BLE001
            pass

    return {
        "ok": True,
        "summary": f"已移动 {src_path.name} → {dst_path.name}。",
        "data": {"path": str(dst_path), "src": str(src_path), "backup": src_content},
        "modified_paths": [src_rel, dst_rel],
    }


async def run_file_delete(
    *,
    path: str,
    workspace_root: str,
    allowed_external_paths: list[str] | None = None,
    file_tracker: Any = None,
    backup_store: FileBackupStore | None = None,
    source: str = "file_delete",
) -> dict[str, Any]:
    """Delete a regular file or an empty directory. Non-empty dirs are rejected."""
    guard = PathGuard(workspace_root, allowed_external_paths)
    try:
        target = guard.resolve_write(path)
        guard.ensure_not_denied(target)
    except ValueError as error:
        return {"ok": False, "summary": str(error), "data": {}}

    if not target.exists():
        return {"ok": False, "summary": f"path not found: {target}", "data": {}}

    rel_path = guard.relative_to_workspace(target)
    backup = None
    if target.is_file():
        if backup_store:
            backup = backup_store.backup(target)
        old_content = None
        if file_tracker:
            try:
                old_content = target.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                old_content = None
        try:
            target.unlink()
        except OSError as error:
            return {"ok": False, "summary": f"delete failed: {error}", "data": {}}
        if file_tracker:
            try:
                await file_tracker.record_delete(rel_path, old_content=old_content, source=source)
            except Exception:  # noqa: BLE001
                pass
    elif target.is_dir():
        try:
            children = list(target.iterdir())
        except OSError as error:
            return {"ok": False, "summary": f"cannot inspect directory: {error}", "data": {}}
        if children:
            return {
                "ok": False,
                "summary": f"cannot delete non-empty directory ({len(children)} entries). Remove contents first.",
                "data": {"path": str(target), "entryCount": len(children)},
            }
        try:
            target.rmdir()
        except OSError as error:
            return {"ok": False, "summary": f"delete failed: {error}", "data": {}}
    else:
        return {"ok": False, "summary": f"unsupported path type: {target}", "data": {}}

    return {
        "ok": True,
        "summary": f"已删除 {target.name}。",
        "data": {"path": str(target), "backup": backup},
        "modified_paths": [rel_path],
    }
