"""Code/text search: ripgrep-first with a pure-Python fallback."""
from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any

from .path_guard import DENIED_DIR_NAMES, PathGuard

# ripgrep emits ``<path>:<line>:<content>``. On Windows the path includes a
# drive-letter colon (``C:\\...\\a.py``), so a naive ``split(":", 2)`` would
# over-split. Match the ``:digits:`` separator to recover the path robustly.
_RG_LINE = re.compile(r"^(.+?):(\d+):(.*)$")

_SEARCHABLE_SUFFIXES = frozenset({
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".md", ".rst",
    ".txt", ".csv", ".log", ".html", ".css", ".scss", ".go", ".rs",
    ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb", ".php",
    ".sh", ".bash", ".ps1", ".vue", ".svelte", ".sql", ".xml",
})


def _ripgrep_available() -> bool:
    return shutil.which("rg") is not None


async def run_code_search(
    *,
    query: str,
    workspace_root: str,
    path: str = ".",
    max_results: int = 50,
    case_sensitive: bool = False,
    allowed_external_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Search code/text files. Tries ripgrep first, falls back to Python scan."""
    guard = PathGuard(workspace_root, allowed_external_paths)
    root_path = guard.resolve_read(path)
    max_results = max(1, int(max_results))

    if not query:
        return {"provider": "builtin", "query": query, "path": str(root_path), "results": []}

    if _ripgrep_available():
        results = await _search_with_ripgrep(query, root_path, max_results, case_sensitive)
        if results is not None:
            return {"provider": "ripgrep", "query": query, "path": str(root_path), "results": results}

    results = _search_with_python(query, root_path, max_results, case_sensitive)
    return {"provider": "builtin", "query": query, "path": str(root_path), "results": results}


async def _search_with_ripgrep(
    query: str, root_path: Path, max_results: int, case_sensitive: bool,
) -> list[dict[str, Any]] | None:
    """Run ripgrep; return None on failure so caller can fall back."""
    if root_path.is_file():
        args = ["rg", "--line-number", "--no-heading", "--color", "never"]
        if not case_sensitive:
            args.append("-i")
        args += ["--", query, str(root_path)]
    else:
        args = ["rg", "--line-number", "--no-heading", "--color", "never", "--max-count", str(max_results)]
        if not case_sensitive:
            args.append("-i")
        args += ["--", query, str(root_path)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode not in (0, 1):  # 1 = no matches, both OK
            return None
    except (OSError, FileNotFoundError):
        return None

    results: list[dict[str, Any]] = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        if len(results) >= max_results:
            break
        # rg format: path:line:content (regex tolerates drive-letter colons on Windows)
        m = _RG_LINE.match(line)
        if not m:
            continue
        p, lineno, content = m.group(1), m.group(2), m.group(3)
        results.append({"path": p, "line": int(lineno), "preview": content.strip()[:500]})
    return results


def _search_with_python(
    query: str, root_path: Path, max_results: int, case_sensitive: bool,
) -> list[dict[str, Any]]:
    needle = query if case_sensitive else query.lower()
    results: list[dict[str, Any]] = []
    paths = _iter_searchable_files(root_path)
    for p in paths:
        if len(results) >= max_results:
            break
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if len(results) >= max_results:
                break
            haystack = line if case_sensitive else line.lower()
            if needle in haystack:
                results.append({"path": str(p), "line": lineno, "preview": line.strip()[:500]})
    return results


def _iter_searchable_files(root: Path):
    if root.is_file():
        yield root
        return
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            children = list(current.iterdir())
        except (PermissionError, OSError):
            continue
        for child in children:
            if child.is_dir():
                if child.name in DENIED_DIR_NAMES or child.name.startswith("."):
                    continue
                stack.append(child)
            elif child.suffix.lower() in _SEARCHABLE_SUFFIXES or child.name == ".gitignore":
                yield child
