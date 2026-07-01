"""Tool execution: caching + parallel dispatch for the agent loop."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
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
        for raw_path in paths:
            path = raw_path.replace("\\", "/")
            # Exact match (file reads index the exact file path)
            keys_to_remove.update(self._path_index.get(path, set()))
            # Directory prefix match: write src/a.py → invalidate src/ scope
            for indexed_path, key_set in self._path_index.items():
                # Root scope ("") is invalidated by any write
                if indexed_path == "":
                    keys_to_remove.update(key_set)
                elif indexed_path.endswith("/") and path.startswith(indexed_path):
                    keys_to_remove.update(key_set)
        for key in keys_to_remove:
            self._entries.pop(key, None)
        # Clean up stale keys from path_index sets
        for path_keys in self._path_index.values():
            path_keys.difference_update(keys_to_remove)
        # Drop empty sets
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


def _normalize_read_path(path: str) -> str:
    """Normalize a read-tool path for cache indexing.

    - "." or "" → "" (workspace root; any write invalidates it)
    - "src" → "src/" (directory; prefix-matches "src/a.py")
    - "src/a.py" → "src/a.py" (file; exact match)
    All paths use forward slashes.
    """
    path = path.strip().replace("\\", "/")
    if not path or path == ".":
        return ""
    # Directory paths get a trailing slash for prefix matching
    if not path.endswith("/"):
        # Heuristic: no suffix means directory; if it has a suffix, it's a file
        # (Path("src").suffix == "" → directory; Path("src/a.py").suffix == ".py" → file)
        from pathlib import PurePosixPath
        if not PurePosixPath(path).suffix:
            path = path + "/"
    return path


def _extract_read_paths(tool_name: str, tool_args: dict[str, Any]) -> list[str]:
    """Extract paths that a read tool touches, for path-based cache invalidation."""
    if tool_name in ("file_read", "file_list"):
        path = str(tool_args.get("path") or "")
        return [_normalize_read_path(path)] if path else [""]
    if tool_name == "code_search":
        path = str(tool_args.get("path") or "")
        if not path:
            return [""]
        return [_normalize_read_path(path)]
    # web_search and others: no path index
    return []


_READ_ONLY_TOOLS = frozenset({"file_read", "file_list", "code_search", "web_search"})


@dataclass
class ToolCallResult:
    tool_call_id: str
    tool_name: str
    result: dict[str, Any]
    from_cache: bool = False
    modified_paths: list[str] = field(default_factory=list)


class ToolExecutor:
    """Batch-execute tool_calls: reads in parallel, writes serially."""

    def __init__(self, *, runner, cache: ToolCache) -> None:
        self.runner = runner
        self.cache = cache

    async def execute_batch(
        self,
        *,
        tool_calls: list[dict[str, Any]],
        gateway,
        context,
        loop_ctx,
        emit,
        approved_permission: dict[str, Any] | None = None,
        storage=None,
        task_id: str = "",
    ) -> list[ToolCallResult]:
        from .agent_loop_runner import AgentLoopPermissionBlocked

        # Classify
        read_calls = [tc for tc in tool_calls if tc["name"] in _READ_ONLY_TOOLS]
        write_calls = [tc for tc in tool_calls if tc["name"] not in _READ_ONLY_TOOLS]

        # Execute reads in parallel
        read_tasks = [
            self._execute_single_read(
                tc=tc, gateway=gateway, context=context, loop_ctx=loop_ctx,
                emit=emit, approved_permission=approved_permission,
                storage=storage, task_id=task_id,
            )
            for tc in read_calls
        ]
        read_results_raw = await asyncio.gather(*read_tasks, return_exceptions=True)

        # Process read results, handling exceptions
        read_results: list[ToolCallResult] = []
        for i, result in enumerate(read_results_raw):
            tc = read_calls[i]
            if isinstance(result, Exception):
                # Check if it's a permission block
                if isinstance(result, AgentLoopPermissionBlocked):
                    raise result
                # Other exception: isolate, emit error event
                await emit(
                    kind="tool_result",
                    role="worker",
                    name=tc["name"],
                    status="error",
                    summary=str(result)[:500],
                    payload={
                        "toolCallId": tc.get("id", ""),
                        "ok": False,
                        "data": {"error": str(result)},
                    },
                )
                read_results.append(ToolCallResult(
                    tool_call_id=tc.get("id", ""),
                    tool_name=tc["name"],
                    result={"ok": False, "summary": str(result), "data": {}},
                    from_cache=False,
                    modified_paths=[],
                ))
            else:
                read_results.append(result)

        # Execute writes serially
        write_results: list[ToolCallResult] = []
        for tc in write_calls:
            result = await self._execute_single_write(
                tc=tc, gateway=gateway, context=context, loop_ctx=loop_ctx,
                emit=emit, approved_permission=approved_permission,
                storage=storage, task_id=task_id,
            )
            write_results.append(result)
            # Invalidate cache based on modified_paths
            modified = result.modified_paths
            if modified:
                self.cache.invalidate_paths(modified)
            else:
                self.cache.invalidate_all()

        # Merge in original order and append messages
        all_results = read_results + write_results
        result_map: dict[str, ToolCallResult] = {r.tool_call_id: r for r in all_results}
        ordered: list[ToolCallResult] = []
        for tc in tool_calls:
            tc_id = tc.get("id", "")
            if tc_id in result_map:
                ordered.append(result_map[tc_id])
                # Append tool message in order
                r = result_map[tc_id]
                self.runner._append_tool_message(
                    loop_ctx, tc_id, tc["name"], r.result,
                )
        return ordered

    async def _execute_single_read(
        self, *, tc, gateway, context, loop_ctx, emit,
        approved_permission, storage, task_id,
    ) -> ToolCallResult:
        tool_name = tc["name"]
        tool_args = tc.get("arguments", {})
        tool_call_id = tc.get("id", "")

        # Check cache
        cached = self.cache.get(tool_name, tool_args)
        if cached is not None:
            # Emit cached events
            await emit(
                kind="tool_call",
                role="worker",
                name=tool_name,
                status="started",
                summary=f"Calling {tool_name} (cached).",
                payload={
                    "toolCallId": tool_call_id,
                    "arguments": tool_args,
                    "round": loop_ctx.rounds,
                    "cached": True,
                },
            )
            await emit(
                kind="tool_result",
                role="worker",
                name=tool_name,
                status="done",
                summary=str(cached.get("summary", ""))[:500] + " (from cache)",
                payload={
                    "toolCallId": tool_call_id,
                    "ok": True,
                    "data": cached.get("data", {}),
                    "fromCache": True,
                },
            )
            return ToolCallResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                result=cached,
                from_cache=True,
                modified_paths=[],
            )

        # Cache miss: execute
        result = await self.runner._execute_tool_call(
            storage=storage,
            task_id=task_id,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            gateway=gateway,
            context=context,
            loop_ctx=loop_ctx,
            emit=emit,
            approved_permission=approved_permission,
        )
        # Cache successful read results
        if result and result.get("ok", True):
            self.cache.set(tool_name, tool_args, result)
        return ToolCallResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            result=result or {"ok": False, "summary": "No result", "data": {}},
            from_cache=False,
            modified_paths=[],
        )

    async def _execute_single_write(
        self, *, tc, gateway, context, loop_ctx, emit,
        approved_permission, storage, task_id,
    ) -> ToolCallResult:
        tool_name = tc["name"]
        tool_args = tc.get("arguments", {})
        tool_call_id = tc.get("id", "")

        result = await self.runner._execute_tool_call(
            storage=storage,
            task_id=task_id,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            gateway=gateway,
            context=context,
            loop_ctx=loop_ctx,
            emit=emit,
            approved_permission=approved_permission,
        )
        modified = []
        if result and result.get("modified_paths"):
            modified = result["modified_paths"]
        return ToolCallResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            result=result or {"ok": False, "summary": "No result", "data": {}},
            from_cache=False,
            modified_paths=modified,
        )
