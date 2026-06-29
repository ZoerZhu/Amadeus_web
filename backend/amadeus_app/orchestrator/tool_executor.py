"""Tool execution: caching + parallel dispatch for the agent loop."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    result: dict[str, Any]
    cached_at: float
    tool_name: str
    tool_args: dict[str, Any]


def _make_key(tool_name: str, tool_args: dict[str, Any]) -> str:
    return f"{tool_name}:{json.dumps(tool_args, sort_keys=True, default=str)}"


class ToolCache:
    """Task-level tool result cache with path-based invalidation."""

    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry] = {}
        self._path_index: dict[str, set[str]] = {}
        self._hits: int = 0
        self._misses: int = 0

    def get(self, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any] | None:
        key = _make_key(tool_name, tool_args)
        entry = self._entries.get(key)
        if entry is not None:
            self._hits += 1
            return entry.result
        self._misses += 1
        return None

    def set(self, tool_name: str, tool_args: dict[str, Any], result: dict[str, Any]) -> None:
        key = _make_key(tool_name, tool_args)
        self._entries[key] = CacheEntry(
            result=result,
            cached_at=time.monotonic(),
            tool_name=tool_name,
            tool_args=tool_args,
        )
        # Record path index for read tools
        paths = _extract_read_paths(tool_name, tool_args)
        for path in paths:
            if path not in self._path_index:
                self._path_index[path] = set()
            self._path_index[path].add(key)

    def invalidate_paths(self, paths: list[str]) -> int:
        if not paths:
            return 0
        keys_to_remove: set[str] = set()
        for path in paths:
            # Exact match
            keys_to_remove.update(self._path_index.get(path, set()))
            # Directory prefix match (write src/a.py → also invalidate src/ scope)
            for indexed_path, key_set in self._path_index.items():
                if indexed_path.endswith("/") and path.startswith(indexed_path):
                    keys_to_remove.update(key_set)
        for key in keys_to_remove:
            self._entries.pop(key, None)
        # Clean up path_index
        self._path_index = {p: s for p, s in self._path_index.items() if s}
        return len(keys_to_remove)

    def invalidate_all(self) -> int:
        count = len(self._entries)
        self._entries.clear()
        self._path_index.clear()
        return count

    @property
    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses}


def _extract_read_paths(tool_name: str, tool_args: dict[str, Any]) -> list[str]:
    """Extract paths that a read tool touches, for path-based cache invalidation."""
    if tool_name == "file_read":
        path = str(tool_args.get("path") or "")
        return [path] if path else []
    if tool_name == "code_search":
        scope = str(tool_args.get("scope") or "")
        return [scope] if scope else []
    # web_search and others: no path index
    return []
