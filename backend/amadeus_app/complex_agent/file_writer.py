"""Controlled file writer with task-level snapshots.

Every write creates a snapshot of the existing file (if any) so the task
can be rolled back. Written files are recorded as artifacts.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..logging_config import get_logger
from . import agent_storage
from .tool_registry import UnifiedTool

_log = get_logger(__name__)


# Paths that must never be written by the agent (defense in depth)
BLOCKED_WRITE_PATHS = {
    ".env",
    ".env.local",
    ".git",
    ".gitignore",
    ".gitattributes",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
}


# Code file extensions that require OpenCode routing approval to write.
# When OpenCode is not allowed for a task, file_writer rejects these.
CODE_FILE_EXTENSIONS: set[str] = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".c", ".cpp", ".java", ".go",
    ".rs", ".cs", ".php", ".rb", ".swift", ".kt", ".vue", ".svelte",
}


def _is_blocked(path: Path) -> bool:
    parts = set(path.parts)
    return bool(parts & BLOCKED_WRITE_PATHS)


def _is_code_file(path: str) -> bool:
    """Check if the path has a code file extension."""
    suffix = Path(path).suffix.lower()
    return suffix in CODE_FILE_EXTENSIONS


def _resolve_workspace_path(workspace: str, relative_path: str) -> Path:
    """Resolve ``relative_path`` against ``workspace`` and reject traversal."""
    workspace_path = Path(workspace).expanduser().resolve()
    if not workspace_path.exists():
        workspace_path.mkdir(parents=True, exist_ok=True)
    candidate = (workspace_path / relative_path).resolve()
    try:
        candidate.relative_to(workspace_path)
    except ValueError as error:
        raise ValueError(f"path escapes workspace: {relative_path}") from error
    if _is_blocked(candidate.relative_to(workspace_path)):
        raise ValueError(f"write to blocked path denied: {relative_path}")
    return candidate


async def controlled_file_write(
    *,
    storage,
    task_id: str,
    workspace: str,
    path: str,
    content: str,
    mode: str = "write",
    encoding: str = "utf-8",
    artifact_root: str = "generated_docs/agent_artifacts",
    description: str = "",
) -> dict[str, Any]:
    """Write ``content`` to ``workspace/path`` after snapshotting the original.

    ``mode`` is one of:
    - ``write`` (default): overwrite or create.
    - ``append``: append to existing content.
    """
    target = _resolve_workspace_path(workspace, path)
    snapshot_dir = Path(artifact_root) / task_id / "snapshots"
    await agent_storage.create_file_snapshot(
        storage,
        task_id=task_id,
        path=str(target),
        backup_dir=snapshot_dir,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "append" and target.exists():
        existing = target.read_text(encoding=encoding, errors="replace")
        content = existing + content
    target.write_text(content, encoding=encoding)
    size = target.stat().st_size
    artifact = await agent_storage.create_artifact(
        storage,
        task_id=task_id,
        kind="file",
        name=target.name,
        path=str(target),
        mime_type=_guess_mime(target),
        size_bytes=size,
        description=description or f"wrote {len(content)} chars to {path}",
        meta={"mode": mode, "encoding": encoding, "relativePath": path},
    )
    _log.info("agent task %s wrote %s (%d bytes)", task_id, target, size)
    return {
        "ok": True,
        "summary": f"wrote {size} bytes to {path}",
        "data": {
            "path": str(target),
            "relativePath": path,
            "sizeBytes": size,
            "artifactId": artifact["id"],
        },
    }


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".json": "application/json",
        ".html": "text/html",
        ".css": "text/css",
        ".js": "application/javascript",
        ".ts": "application/typescript",
        ".py": "text/x-python",
        ".csv": "text/csv",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".pdf": "application/pdf",
    }.get(suffix, "application/octet-stream")


def make_file_writer_tool(
    *,
    storage,
    task_id: str,
    workspace: str,
    artifact_root: str,
    code_write_allowed_fn: "Callable[[], bool] | None" = None,
) -> UnifiedTool:
    """Build a task-scoped ``file_writer`` tool.

    When ``code_write_allowed_fn`` is provided and returns ``False``, writing
    files with code extensions (``.py``, ``.ts``, etc.) is rejected. This
    enforces the OpenCode routing policy: code modifications must go through
    ``opencode_delegate`` when it is available, or be output as text suggestions
    when it is not.
    """
    from typing import Callable  # local import to avoid top-level typing issues

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        content = str(args.get("content", ""))
        if not path:
            return {"ok": False, "error": "path is required"}
        mode = str(args.get("mode", "write")).lower()
        if mode not in {"write", "append"}:
            return {"ok": False, "error": f"invalid mode: {mode}"}
        # Code-file protection: when OpenCode is not allowed, reject code files.
        if code_write_allowed_fn is not None and not code_write_allowed_fn():
            if _is_code_file(path):
                return {
                    "ok": False,
                    "error": (
                        f"Writing code file '{path}' requires OpenCode routing approval. "
                        f"Use opencode_delegate for code modifications, or output "
                        f"analysis and suggestions as text instead."
                    ),
                }
        return await controlled_file_write(
            storage=storage,
            task_id=task_id,
            workspace=workspace,
            path=path,
            content=content,
            mode=mode,
            encoding=str(args.get("encoding", "utf-8")),
            artifact_root=artifact_root,
            description=str(args.get("description", "")),
        )

    return UnifiedTool(
        name="file_writer",
        description="Write or append text to a file inside the task workspace. "
                    "Creates a snapshot of the existing file for rollback. "
                    "Blocked paths: .env, .git, node_modules, .venv. "
                    "Code files (.py/.ts/.tsx/.js/etc.) require OpenCode routing approval.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path inside the workspace"},
                "content": {"type": "string", "description": "Text content to write"},
                "mode": {"type": "string", "enum": ["write", "append"], "description": "write (default) or append"},
                "encoding": {"type": "string", "description": "Text encoding, default utf-8"},
                "description": {"type": "string", "description": "Optional description for the artifact"},
            },
            "required": ["path", "content"],
        },
        handler=handler,
        source="builtin",
        permission="safe",
    )
