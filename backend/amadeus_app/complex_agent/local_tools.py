"""Read-only local security tools for the complex agent.

Provides three safe, workspace-scoped tools:

- ``code_search``: ripgrep-powered code search with Python fallback.
- ``local_git``: read-only Git operations (status/branch/log/diff/show).
- ``markitdown_convert``: convert PDF/Office/HTML/CSV files to Markdown.

All tools are read-only: they never mutate the workspace or the Git
repository. Paths are validated to stay inside the workspace.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from ..logging_config import get_logger
from .tool_registry import UnifiedTool

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def _resolve_workspace_path(workspace: str, path: str) -> Path:
    """Resolve ``path`` relative to ``workspace``, rejecting traversal."""
    ws = Path(workspace).expanduser().resolve()
    if not ws.exists():
        ws.mkdir(parents=True, exist_ok=True)
    if Path(path).is_absolute():
        full = Path(path).expanduser().resolve()
    else:
        full = (ws / path).expanduser().resolve()
    try:
        full.relative_to(ws)
    except ValueError as error:
        raise ValueError(f"path outside workspace: {path}") from error
    return full


# ---------------------------------------------------------------------------
# code_search
# ---------------------------------------------------------------------------


# Directories skipped by the Python fallback search.
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache"}


async def _run_code_search_rg(
    *,
    workspace: str,
    pattern: str,
    glob: str | None,
    max_results: int,
    context_lines: int,
    case_insensitive: bool,
) -> dict[str, Any] | None:
    """Run ripgrep; return result dict or ``None`` if rg is unavailable."""
    rg = shutil.which("rg")
    if rg is None:
        return None

    cmd: list[str] = [
        rg,
        "--line-number",
        "--with-filename",
        "--no-heading",
        "--color", "never",
        "--max-count", str(max_results),
    ]
    if case_insensitive:
        cmd.append("-i")
    if context_lines > 0:
        cmd.extend(["-C", str(context_lines)])
    if glob:
        cmd.extend(["--glob", glob])
    cmd.append(pattern)
    cmd.append(workspace)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        _log.warning("failed to launch rg: %s", error)
        return None

    stdout, _ = await proc.communicate()
    # rg exit code 1 means "no matches", which is fine; >1 is a real error.
    if proc.returncode is not None and proc.returncode > 1:
        return {
            "ok": False,
            "error": f"rg exited with code {proc.returncode}",
            "summary": "search failed",
        }

    text = stdout.decode("utf-8", errors="replace")
    matches = _parse_rg_output(text)
    if len(matches) > max_results:
        matches = matches[:max_results]
    total = len(matches)
    return {
        "ok": True,
        "result": {"matches": matches, "total": total},
        "summary": f"found {total} matches",
    }


def _parse_rg_output(text: str) -> list[dict[str, Any]]:
    """Parse ripgrep plain-text output into a list of match records.

    Handles both match lines (``path:line:content``) and context lines
    (``path-line-content``), robust to colons in Windows drive letters.
    """
    matches: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line or line == "--":
            continue
        m = re.match(r"^(.+?)([:\-])(\d+)\2(.*)$", line)
        if not m:
            continue
        path, sep, line_no, content = m.groups()
        matches.append({
            "file": path,
            "line": int(line_no),
            "content": content,
            "isContext": sep == "-",
        })
    return matches


async def _run_code_search_fallback(
    *,
    workspace: str,
    pattern: str,
    glob: str | None,
    max_results: int,
    context_lines: int,
    case_insensitive: bool,
) -> dict[str, Any]:
    """Python ``re``-based fallback when ripgrep is unavailable."""
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as error:
        return {
            "ok": False,
            "error": f"invalid regex: {error}",
            "summary": "invalid pattern",
        }

    ws = Path(workspace).resolve()
    glob_pattern = glob or "**/*"
    matches: list[dict[str, Any]] = []
    match_count = 0

    for file_path in ws.glob(glob_pattern):
        if match_count >= max_results:
            break
        if not file_path.is_file():
            continue
        try:
            rel = file_path.relative_to(ws)
        except ValueError:
            continue
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        for idx, line in enumerate(lines, start=1):
            if match_count >= max_results:
                break
            if regex.search(line):
                matches.append({
                    "file": str(file_path),
                    "line": idx,
                    "content": line,
                    "isContext": False,
                })
                match_count += 1
                if context_lines > 0:
                    start_ctx = max(0, idx - 1 - context_lines)
                    end_ctx = min(len(lines), idx + context_lines)
                    for ctx_idx in range(start_ctx, end_ctx):
                        if ctx_idx == idx - 1:
                            continue
                        matches.append({
                            "file": str(file_path),
                            "line": ctx_idx + 1,
                            "content": lines[ctx_idx],
                            "isContext": True,
                        })

    total = len(matches)
    return {
        "ok": True,
        "result": {"matches": matches, "total": total},
        "summary": f"found {total} matches",
    }


def make_code_search_tool(workspace: str) -> UnifiedTool:
    """Build a workspace-scoped ``code_search`` tool."""

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            return {
                "ok": False,
                "error": "pattern is required",
                "summary": "missing pattern",
            }
        glob = args.get("glob")
        if glob is not None:
            glob = str(glob)
        try:
            max_results = int(args.get("maxResults", 50))
            context_lines = int(args.get("contextLines", 0))
        except (TypeError, ValueError) as error:
            return {
                "ok": False,
                "error": f"invalid numeric argument: {error}",
                "summary": "invalid argument",
            }
        case_insensitive = bool(args.get("caseInsensitive", False))

        rg_result = await _run_code_search_rg(
            workspace=workspace,
            pattern=pattern,
            glob=glob,
            max_results=max_results,
            context_lines=context_lines,
            case_insensitive=case_insensitive,
        )
        if rg_result is not None:
            return rg_result
        _log.info("rg unavailable, falling back to Python re search")
        return await _run_code_search_fallback(
            workspace=workspace,
            pattern=pattern,
            glob=glob,
            max_results=max_results,
            context_lines=context_lines,
            case_insensitive=case_insensitive,
        )

    return UnifiedTool(
        name="code_search",
        description="在工作区代码中搜索文本模式（只读）。优先使用 ripgrep，不可用时降级到 Python 正则搜索。"
                    "返回匹配的文件、行号和内容，可选上下文行。",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则表达式搜索模式"},
                "glob": {"type": "string", "description": "可选的文件 glob 过滤，例如 *.py"},
                "maxResults": {
                    "type": "integer",
                    "description": "最大返回匹配数，默认 50",
                    "default": 50,
                },
                "contextLines": {
                    "type": "integer",
                    "description": "每个匹配前后的上下文行数，默认 0",
                    "default": 0,
                },
                "caseInsensitive": {
                    "type": "boolean",
                    "description": "是否忽略大小写，默认 false",
                    "default": False,
                },
            },
            "required": ["pattern"],
        },
        handler=handler,
        source="builtin",
        permission="safe",
    )


# ---------------------------------------------------------------------------
# local_git
# ---------------------------------------------------------------------------

_GIT_ALLOWED_ACTIONS = {"status", "branch", "log", "diff", "show"}


def make_local_git_tool(workspace: str) -> UnifiedTool:
    """Build a workspace-scoped read-only ``local_git`` tool."""

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action", "")).strip().lower()
        if not action:
            return {"ok": False, "error": "action is required", "summary": "missing action"}
        if action not in _GIT_ALLOWED_ACTIONS:
            return {
                "ok": False,
                "error": (
                    f"action not allowed: {action}; "
                    f"allowed: {sorted(_GIT_ALLOWED_ACTIONS)}"
                ),
                "summary": f"blocked git action: {action}",
            }

        git = shutil.which("git")
        if git is None:
            return {
                "ok": False,
                "error": "git is not installed",
                "summary": "git unavailable",
            }

        cmd: list[str] = [git]
        if action == "status":
            cmd.extend(["status", "--short"])
        elif action == "branch":
            cmd.extend(["branch", "--list", "-vv"])
        elif action == "log":
            try:
                max_count = int(args.get("maxCount", 20))
            except (TypeError, ValueError):
                max_count = 20
            cmd.extend(["log", f"--max-count={max_count}", "--oneline", "--decorate"])
        elif action == "diff":
            ref = args.get("ref")
            cmd.append("diff")
            if ref:
                cmd.append(str(ref))
        elif action == "show":
            ref = args.get("ref")
            if not ref:
                return {
                    "ok": False,
                    "error": "ref is required for show",
                    "summary": "missing ref",
                }
            cmd.extend(["show", str(ref)])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            return {
                "ok": False,
                "error": f"failed to launch git: {error}",
                "summary": "git launch failed",
            }

        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            return {
                "ok": False,
                "error": err or f"git {action} failed with code {proc.returncode}",
                "summary": f"git {action} failed",
            }

        output = stdout.decode("utf-8", errors="replace")
        if action == "status":
            summary = f"git status: {'clean' if not output.strip() else 'has changes'}"
        elif action == "branch":
            summary = "git branch listed"
        elif action == "log":
            summary = "git log retrieved"
        elif action == "diff":
            summary = "git diff retrieved"
        else:  # show
            summary = "git show retrieved"

        return {
            "ok": True,
            "result": {"output": output},
            "summary": summary,
        }

    return UnifiedTool(
        name="local_git",
        description="在工作区执行只读 Git 操作（status/branch/log/diff/show）。"
                    "禁止 checkout/reset/commit/push/merge 等写操作。",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "branch", "log", "diff", "show"],
                    "description": "Git 操作类型",
                },
                "ref": {
                    "type": "string",
                    "description": "用于 diff/show 的提交或引用",
                },
                "maxCount": {
                    "type": "integer",
                    "description": "log 操作的提交数，默认 20",
                    "default": 20,
                },
            },
            "required": ["action"],
        },
        handler=handler,
        source="builtin",
        permission="safe",
    )


# ---------------------------------------------------------------------------
# markitdown_convert
# ---------------------------------------------------------------------------


def _check_markitdown_available() -> bool:
    try:
        import markitdown  # noqa: F401
        return True
    except ImportError:
        return False


def make_markitdown_convert_tool(workspace: str) -> UnifiedTool:
    """Build a workspace-scoped ``markitdown_convert`` tool."""

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        if not path:
            return {"ok": False, "error": "path is required", "summary": "missing path"}
        try:
            max_chars = int(args.get("maxChars", 50000))
        except (TypeError, ValueError):
            max_chars = 50000

        try:
            full = _resolve_workspace_path(workspace, path)
        except ValueError as error:
            return {"ok": False, "error": str(error), "summary": "path rejected"}

        if not full.exists():
            return {
                "ok": False,
                "error": f"file not found: {path}",
                "summary": "file not found",
            }
        if not full.is_file():
            return {"ok": False, "error": f"not a file: {path}", "summary": "not a file"}

        if not _check_markitdown_available():
            return {
                "ok": False,
                "error": "markitdown is not installed. Install with: pip install markitdown[all]",
                "summary": "markitdown unavailable",
            }

        try:
            from markitdown import MarkItDown

            md = MarkItDown()
            result = md.convert(str(full))
            markdown = result.text_content or ""
        except Exception as error:  # noqa: BLE001
            return {
                "ok": False,
                "error": f"conversion failed: {error}",
                "summary": "conversion failed",
            }

        truncated = False
        if len(markdown) > max_chars:
            markdown = markdown[:max_chars]
            truncated = True

        char_count = len(markdown)
        summary = f"converted {full.name} to {char_count} chars markdown"
        if truncated:
            summary += f" (truncated at {max_chars})"

        return {
            "ok": True,
            "result": {
                "markdown": markdown,
                "charCount": char_count,
                "truncated": truncated,
            },
            "summary": summary,
        }

    return UnifiedTool(
        name="markitdown_convert",
        description="将工作区内的 PDF/Office/HTML/CSV 文件转换为 Markdown（只读）。"
                    "禁止读取 URL 和工作区外路径。需要安装 markitdown 包。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "工作区内文件路径"},
                "maxChars": {
                    "type": "integer",
                    "description": "返回 Markdown 的最大字符数，默认 50000",
                    "default": 50000,
                },
            },
            "required": ["path"],
        },
        handler=handler,
        source="builtin",
        permission="safe",
        meta={"requires": "markitdown"},
    )
