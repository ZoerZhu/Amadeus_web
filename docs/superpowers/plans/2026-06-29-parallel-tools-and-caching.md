# 并行工具调用 + 工具结果缓存 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Agent Loop 中串行的 tool_calls 执行改为读类并行+写类串行，并加入 Task 级工具结果缓存避免重复调用。

**Architecture:** 新建 `tool_executor.py` 模块，包含 `ToolCache`（缓存+路径失效）和 `ToolExecutor`（并行调度+顺序合并）。`_run_loop` 中串行循环替换为 `execute_batch` 调用。`_emit_tool_result` 的 message append 逻辑拆分到 `execute_batch` 合并阶段按序执行。

**Tech Stack:** Python 3.12 / asyncio / pytest + pytest-asyncio / React + TypeScript

## Global Constraints

- Python 3.12+, 使用 `from __future__ import annotations`
- venv Python 路径：`e:\Amadeus\Amadeus_web\.venv\Scripts\python.exe`
- 缓存生命周期：Task 级，任务结束自动 GC
- 缓存失效：路径级精确失效（file_write 报告 modified_paths）；shell_exec/code_agent 保守全清
- 并行策略：读类工具并行（`file_read`, `code_search`, `web_search`），写类工具串行
- 并发上限：无上限
- 权限阻塞向上传播（保持 pause 语义），其他异常隔离（不中断同批次其他工具）
- OpenAI API 要求 tool messages 按 assistant tool_calls 顺序排列 —— 并行执行后必须按原序 append

---

### Task 1: ToolCache 模块

**Files:**
- Create: `backend/amadeus_app/orchestrator/tool_executor.py`
- Test: `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: nothing (standalone module)
- Produces: `ToolCache` class, `CacheEntry` dataclass, `_make_key()` function

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_loop.py`:

```python
# ---------------------------------------------------------------------------
# ToolCache tests
# ---------------------------------------------------------------------------

from backend.amadeus_app.orchestrator.tool_executor import ToolCache, CacheEntry


def test_tool_cache_hit_miss():
    """Same args hit, different args miss."""
    cache = ToolCache()
    result1 = {"ok": True, "summary": "found", "data": {"content": "hello"}}
    cache.set("file_read", {"path": "src/app.tsx"}, result1)

    # Same args → hit
    hit = cache.get("file_read", {"path": "src/app.tsx"})
    assert hit is not None
    assert hit["data"]["content"] == "hello"

    # Different args → miss
    miss = cache.get("file_read", {"path": "src/other.tsx"})
    assert miss is None

    # Stats
    assert cache.stats["hits"] == 1
    assert cache.stats["misses"] == 1


def test_tool_cache_invalidate_by_path():
    """file_write to a.py invalidates file_read cache for a.py."""
    cache = ToolCache()
    cache.set("file_read", {"path": "src/a.py"}, {"ok": True, "summary": "x", "data": {}})
    cache.set("file_read", {"path": "src/b.py"}, {"ok": True, "summary": "y", "data": {}})

    count = cache.invalidate_paths(["src/a.py"])
    assert count == 1

    # a.py cache gone, b.py still cached
    assert cache.get("file_read", {"path": "src/a.py"}) is None
    assert cache.get("file_read", {"path": "src/b.py"}) is not None


def test_tool_cache_invalidate_all_on_shell_exec():
    """invalidate_all clears everything."""
    cache = ToolCache()
    cache.set("file_read", {"path": "a.py"}, {"ok": True, "summary": "x", "data": {}})
    cache.set("code_search", {"query": "auth", "scope": "src/"}, {"ok": True, "summary": "y", "data": {}})

    count = cache.invalidate_all()
    assert count == 2

    assert cache.get("file_read", {"path": "a.py"}) is None
    assert cache.get("code_search", {"query": "auth", "scope": "src/"}) is None


def test_tool_cache_path_prefix_match():
    """Writing src/a.py invalidates code_search cached under scope src/."""
    cache = ToolCache()
    # code_search with scope "src/" is indexed under "src/"
    cache.set("code_search", {"query": "auth", "scope": "src/"}, {"ok": True, "summary": "y", "data": {}})
    # file_read for src/a.py is indexed under "src/a.py"
    cache.set("file_read", {"path": "src/a.py"}, {"ok": True, "summary": "x", "data": {}})

    # Invalidate by path "src/a.py" should also hit the "src/" directory prefix
    count = cache.invalidate_paths(["src/a.py"])
    assert count == 2  # both the exact match and the directory-prefix match


def test_tool_cache_stats():
    """Hits and misses are tracked correctly."""
    cache = ToolCache()
    cache.set("file_read", {"path": "a.py"}, {"ok": True, "summary": "x", "data": {}})

    cache.get("file_read", {"path": "a.py"})   # hit
    cache.get("file_read", {"path": "a.py"})   # hit
    cache.get("file_read", {"path": "b.py"})   # miss

    assert cache.stats["hits"] == 2
    assert cache.stats["misses"] == 1


def test_tool_cache_invalidate_empty_paths():
    """invalidate_paths with empty list returns 0."""
    cache = ToolCache()
    cache.set("file_read", {"path": "a.py"}, {"ok": True, "summary": "x", "data": {}})
    assert cache.invalidate_paths([]) == 0
    assert cache.get("file_read", {"path": "a.py"}) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `& 'e:\Amadeus\Amadeus_web\.venv\Scripts\python.exe' -m pytest tests/test_agent_loop.py::test_tool_cache_hit_miss tests/test_agent_loop.py::test_tool_cache_invalidate_by_path tests/test_agent_loop.py::test_tool_cache_invalidate_all_on_shell_exec tests/test_agent_loop.py::test_tool_cache_path_prefix_match tests/test_agent_loop.py::test_tool_cache_stats tests/test_agent_loop.py::test_tool_cache_invalidate_empty_paths -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.amadeus_app.orchestrator.tool_executor'`

- [ ] **Step 3: Create tool_executor.py with ToolCache**

Create `backend/amadeus_app/orchestrator/tool_executor.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `& 'e:\Amadeus\Amadeus_web\.venv\Scripts\python.exe' -m pytest tests/test_agent_loop.py::test_tool_cache_hit_miss tests/test_agent_loop.py::test_tool_cache_invalidate_by_path tests/test_agent_loop.py::test_tool_cache_invalidate_all_on_shell_exec tests/test_agent_loop.py::test_tool_cache_path_prefix_match tests/test_agent_loop.py::test_tool_cache_stats tests/test_agent_loop.py::test_tool_cache_invalidate_empty_paths -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/amadeus_app/orchestrator/tool_executor.py tests/test_agent_loop.py
git commit -m "feat: add ToolCache with path-based invalidation for agent loop"
```

---

### Task 2: ToolExecutor — parallel dispatch + cache integration

**Files:**
- Modify: `backend/amadeus_app/orchestrator/tool_executor.py`
- Test: `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `ToolCache` from Task 1, `AgentLoopRunner._execute_tool_call` (existing), `AgentLoopRunner._append_tool_message` (to be added in Task 3), `AgentLoopPermissionBlocked` (existing)
- Produces: `ToolExecutor` class, `ToolCallResult` dataclass, `_READ_ONLY_TOOLS` constant

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_loop.py`:

```python
# ---------------------------------------------------------------------------
# ToolExecutor tests
# ---------------------------------------------------------------------------

from backend.amadeus_app.orchestrator.tool_executor import (
    ToolCache,
    ToolCallResult,
    ToolExecutor,
)
from backend.amadeus_app.orchestrator.agent_loop_runner import AgentLoopRunner


@pytest.mark.asyncio
async def test_execute_batch_parallel_reads(agent_storage):
    """3 read tools execute in parallel, results returned in original order."""
    from unittest.mock import AsyncMock

    cache = ToolCache()
    runner = AgentLoopRunner()
    executor = ToolExecutor(runner=runner, cache=cache)

    tool_calls = [
        {"id": "1", "name": "file_read", "arguments": {"path": "a.py"}},
        {"id": "2", "name": "file_read", "arguments": {"path": "b.py"}},
        {"id": "3", "name": "file_read", "arguments": {"path": "c.py"}},
    ]

    call_order: list[str] = []

    async def mock_execute_tool_call(*, storage, task_id, tool_name, tool_args,
                                      tool_call_id, gateway, context, loop_ctx,
                                      emit, approved_permission=None):
        call_order.append(tool_call_id)
        await asyncio.sleep(0.05)  # Simulate I/O
        return {
            "ok": True,
            "summary": f"read {tool_args['path']}",
            "data": {"content": f"content of {tool_args['path']}"},
        }

    async def mock_emit(**kwargs):
        return {"id": "evt_1"}

    runner._execute_tool_call = mock_execute_tool_call  # type: ignore

    from backend.amadeus_app.orchestrator.agent_loop_context import AgentLoopContext
    loop_ctx = AgentLoopContext(
        system_prompt="test",
        messages=[],
        tool_schemas=[],
        mcp_snapshot={},
    )

    results = await executor.execute_batch(
        tool_calls=tool_calls,
        gateway=None,  # type: ignore
        context=None,  # type: ignore
        loop_ctx=loop_ctx,
        emit=mock_emit,
        storage=None,
        task_id="test",
    )

    # All 3 results returned
    assert len(results) == 3
    # Results in original order
    assert results[0].tool_call_id == "1"
    assert results[1].tool_call_id == "2"
    assert results[2].tool_call_id == "3"
    # None from cache (first time)
    assert all(not r.from_cache for r in results)


@pytest.mark.asyncio
async def test_execute_batch_cached_result_emits_events(agent_storage):
    """Cached results emit tool_call/tool_result events with cached=True."""
    from unittest.mock import AsyncMock

    cache = ToolCache()
    runner = AgentLoopRunner()
    executor = ToolExecutor(runner=runner, cache=cache)

    # Pre-populate cache
    cached_result = {
        "ok": True,
        "summary": "cached content",
        "data": {"content": "hello"},
    }
    cache.set("file_read", {"path": "a.py"}, cached_result)

    tool_calls = [{"id": "1", "name": "file_read", "arguments": {"path": "a.py"}}]

    emitted_events: list[dict] = []

    async def mock_emit(**kwargs):
        emitted_events.append(kwargs)
        return {"id": "evt_1"}

    from backend.amadeus_app.orchestrator.agent_loop_context import AgentLoopContext
    loop_ctx = AgentLoopContext(
        system_prompt="test", messages=[], tool_schemas=[], mcp_snapshot={},
    )

    results = await executor.execute_batch(
        tool_calls=tool_calls,
        gateway=None,  # type: ignore
        context=None,  # type: ignore
        loop_ctx=loop_ctx,
        emit=mock_emit,
        storage=None,
        task_id="test",
    )

    assert len(results) == 1
    assert results[0].from_cache is True
    # Events were emitted
    assert any(e.get("kind") == "tool_call" for e in emitted_events)
    assert any(e.get("kind") == "tool_result" for e in emitted_events)
    # tool_call event has cached=True
    tool_call_events = [e for e in emitted_events if e.get("kind") == "tool_call"]
    assert tool_call_events[0]["payload"]["cached"] is True


@pytest.mark.asyncio
async def test_execute_batch_mixed_read_write(agent_storage):
    """Reads run in parallel, writes run serially after reads."""
    from unittest.mock import AsyncMock

    cache = ToolCache()
    runner = AgentLoopRunner()
    executor = ToolExecutor(runner=runner, cache=cache)

    tool_calls = [
        {"id": "1", "name": "file_read", "arguments": {"path": "a.py"}},
        {"id": "2", "name": "file_write", "arguments": {"path": "out.txt", "content": "x"}},
    ]

    execution_log: list[str] = []

    async def mock_execute_tool_call(*, storage, task_id, tool_name, tool_args,
                                      tool_call_id, gateway, context, loop_ctx,
                                      emit, approved_permission=None):
        execution_log.append(f"start:{tool_name}:{tool_call_id}")
        await asyncio.sleep(0.02)
        execution_log.append(f"end:{tool_name}:{tool_call_id}")
        return {
            "ok": True,
            "summary": f"{tool_name} done",
            "data": {},
            "modified_paths": ["out.txt"] if tool_name == "file_write" else [],
        }

    async def mock_emit(**kwargs):
        return {"id": "evt_1"}

    runner._execute_tool_call = mock_execute_tool_call  # type: ignore

    from backend.amadeus_app.orchestrator.agent_loop_context import AgentLoopContext
    loop_ctx = AgentLoopContext(
        system_prompt="test", messages=[], tool_schemas=[], mcp_snapshot={},
    )

    results = await executor.execute_batch(
        tool_calls=tool_calls,
        gateway=None,  # type: ignore
        context=None,  # type: ignore
        loop_ctx=loop_ctx,
        emit=mock_emit,
        storage=None,
        task_id="test",
    )

    assert len(results) == 2
    assert results[0].tool_call_id == "1"  # file_read first
    assert results[1].tool_call_id == "2"  # file_write second


@pytest.mark.asyncio
async def test_execute_batch_preserves_message_order(agent_storage):
    """After parallel execution, loop_ctx.messages are in tool_calls order."""
    from unittest.mock import AsyncMock

    cache = ToolCache()
    runner = AgentLoopRunner()
    executor = ToolExecutor(runner=runner, cache=cache)

    tool_calls = [
        {"id": "tc_1", "name": "file_read", "arguments": {"path": "a.py"}},
        {"id": "tc_2", "name": "file_read", "arguments": {"path": "b.py"}},
        {"id": "tc_3", "name": "file_read", "arguments": {"path": "c.py"}},
    ]

    async def mock_execute_tool_call(*, storage, task_id, tool_name, tool_args,
                                      tool_call_id, gateway, context, loop_ctx,
                                      emit, approved_permission=None):
        # Variable delay so parallel completion order differs from call order
        delays = {"tc_1": 0.03, "tc_2": 0.01, "tc_3": 0.02}
        await asyncio.sleep(delays.get(tool_call_id, 0.01))
        return {
            "ok": True,
            "summary": f"read {tool_args['path']}",
            "data": {"content": tool_args["path"]},
        }

    async def mock_emit(**kwargs):
        return {"id": "evt_1"}

    runner._execute_tool_call = mock_execute_tool_call  # type: ignore
    runner._append_tool_message = lambda self_inner, loop_ctx_inner, tool_call_id_inner, tool_name_inner, result_inner: loop_ctx_inner.messages.append({  # type: ignore
        "role": "tool",
        "tool_call_id": tool_call_id_inner,
        "name": tool_name_inner,
        "content": str(result_inner.get("summary", "")),
    })

    from backend.amadeus_app.orchestrator.agent_loop_context import AgentLoopContext
    loop_ctx = AgentLoopContext(
        system_prompt="test", messages=[], tool_schemas=[], mcp_snapshot={},
    )

    await executor.execute_batch(
        tool_calls=tool_calls,
        gateway=None,  # type: ignore
        context=None,  # type: ignore
        loop_ctx=loop_ctx,
        emit=mock_emit,
        storage=None,
        task_id="test",
    )

    # Messages are in original tool_calls order, not completion order
    assert len(loop_ctx.messages) == 3
    assert loop_ctx.messages[0]["tool_call_id"] == "tc_1"
    assert loop_ctx.messages[1]["tool_call_id"] == "tc_2"
    assert loop_ctx.messages[2]["tool_call_id"] == "tc_3"


@pytest.mark.asyncio
async def test_execute_batch_error_isolation(agent_storage):
    """One read tool failing does not affect other read tools."""
    from unittest.mock import AsyncMock

    cache = ToolCache()
    runner = AgentLoopRunner()
    executor = ToolExecutor(runner=runner, cache=cache)

    tool_calls = [
        {"id": "1", "name": "file_read", "arguments": {"path": "a.py"}},
        {"id": "2", "name": "file_read", "arguments": {"path": "ERROR"}},
        {"id": "3", "name": "file_read", "arguments": {"path": "c.py"}},
    ]

    async def mock_execute_tool_call(*, storage, task_id, tool_name, tool_args,
                                      tool_call_id, gateway, context, loop_ctx,
                                      emit, approved_permission=None):
        if "ERROR" in str(tool_args.get("path", "")):
            raise RuntimeError("simulated network error")
        return {
            "ok": True,
            "summary": f"read {tool_args['path']}",
            "data": {"content": "ok"},
        }

    async def mock_emit(**kwargs):
        return {"id": "evt_1"}

    runner._execute_tool_call = mock_execute_tool_call  # type: ignore
    runner._append_tool_message = lambda self_inner, loop_ctx_inner, tool_call_id_inner, tool_name_inner, result_inner: loop_ctx_inner.messages.append({  # type: ignore
        "role": "tool",
        "tool_call_id": tool_call_id_inner,
        "name": tool_name_inner,
        "content": str(result_inner.get("summary", "")),
    })

    from backend.amadeus_app.orchestrator.agent_loop_context import AgentLoopContext
    loop_ctx = AgentLoopContext(
        system_prompt="test", messages=[], tool_schemas=[], mcp_snapshot={},
    )

    results = await executor.execute_batch(
        tool_calls=tool_calls,
        gateway=None,  # type: ignore
        context=None,  # type: ignore
        loop_ctx=loop_ctx,
        emit=mock_emit,
        storage=None,
        task_id="test",
    )

    assert len(results) == 3
    assert results[0].result["ok"] is True   # a.py succeeded
    assert results[1].result["ok"] is False  # ERROR failed
    assert results[2].result["ok"] is True   # c.py succeeded


@pytest.mark.asyncio
async def test_execute_batch_permission_blocked_propagates(agent_storage):
    """AgentLoopPermissionBlocked propagates up, pausing the batch."""
    from unittest.mock import AsyncMock
    from backend.amadeus_app.orchestrator.agent_loop_runner import AgentLoopPermissionBlocked

    cache = ToolCache()
    runner = AgentLoopRunner()
    executor = ToolExecutor(runner=runner, cache=cache)

    tool_calls = [
        {"id": "1", "name": "file_read", "arguments": {"path": "a.py"}},
        {"id": "2", "name": "shell_exec", "arguments": {"command": "rm -rf /"}},
    ]

    async def mock_execute_tool_call(*, storage, task_id, tool_name, tool_args,
                                      tool_call_id, gateway, context, loop_ctx,
                                      emit, approved_permission=None):
        if tool_name == "shell_exec":
            raise AgentLoopPermissionBlocked(tool_name, "perm_123")
        return {"ok": True, "summary": "ok", "data": {}}

    async def mock_emit(**kwargs):
        return {"id": "evt_1"}

    runner._execute_tool_call = mock_execute_tool_call  # type: ignore

    from backend.amadeus_app.orchestrator.agent_loop_context import AgentLoopContext
    loop_ctx = AgentLoopContext(
        system_prompt="test", messages=[], tool_schemas=[], mcp_snapshot={},
    )

    with pytest.raises(AgentLoopPermissionBlocked):
        await executor.execute_batch(
            tool_calls=tool_calls,
            gateway=None,  # type: ignore
            context=None,  # type: ignore
            loop_ctx=loop_ctx,
            emit=mock_emit,
            storage=None,
            task_id="test",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `& 'e:\Amadeus\Amadeus_web\.venv\Scripts\python.exe' -m pytest tests/test_agent_loop.py::test_execute_batch_parallel_reads tests/test_agent_loop.py::test_execute_batch_cached_result_emits_events tests/test_agent_loop.py::test_execute_batch_mixed_read_write tests/test_agent_loop.py::test_execute_batch_preserves_message_order tests/test_agent_loop.py::test_execute_batch_error_isolation tests/test_agent_loop.py::test_execute_batch_permission_blocked_propagates -v`
Expected: FAIL with `ImportError: cannot import name 'ToolExecutor' from 'backend.amadeus_app.orchestrator.tool_executor'`

- [ ] **Step 3: Add ToolExecutor to tool_executor.py**

Append to `backend/amadeus_app/orchestrator/tool_executor.py` (after the existing ToolCache code):

```python
import asyncio
from dataclasses import dataclass, field


_READ_ONLY_TOOLS = frozenset({"file_read", "code_search", "web_search"})


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
            if isinstance(result, type(None)):
                continue
            tc = read_calls[i]
            if isinstance(result, Exception):
                # Check if it's a permission block
                from .agent_loop_runner import AgentLoopPermissionBlocked
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
                    "round": loop_ctx.rounds + 1,
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `& 'e:\Amadeus\Amadeus_web\.venv\Scripts\python.exe' -m pytest tests/test_agent_loop.py::test_execute_batch_parallel_reads tests/test_agent_loop.py::test_execute_batch_cached_result_emits_events tests/test_agent_loop.py::test_execute_batch_mixed_read_write tests/test_agent_loop.py::test_execute_batch_preserves_message_order tests/test_agent_loop.py::test_execute_batch_error_isolation tests/test_agent_loop.py::test_execute_batch_permission_blocked_propagates -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/amadeus_app/orchestrator/tool_executor.py tests/test_agent_loop.py
git commit -m "feat: add ToolExecutor with parallel read dispatch and cache integration"
```

---

### Task 3: Integrate ToolExecutor into AgentLoopRunner

**Files:**
- Modify: `backend/amadeus_app/orchestrator/agent_loop_runner.py:73-77` (AgentLoopRunner.__init__)
- Modify: `backend/amadeus_app/orchestrator/agent_loop_runner.py:376-410` (_run_loop tool_calls loop)
- Modify: `backend/amadeus_app/orchestrator/agent_loop_runner.py:618-721` (_execute_tool_call return type)
- Modify: `backend/amadeus_app/orchestrator/agent_loop_runner.py:810-845` (_emit_tool_result: remove message append)
- Modify: `backend/amadeus_app/orchestrator/agent_loop_runner.py.py:943-977` (_finish_task: add cache stats)
- Test: `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `ToolExecutor`, `ToolCache` from Task 1+2
- Produces: Modified `_execute_tool_call` returning `dict[str, Any] | None`, new `_append_tool_message` method, `_finish_task` with cacheStats

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_loop.py`:

```python
# ---------------------------------------------------------------------------
# AgentLoopRunner integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_loop_finish_after_other_tools(agent_storage):
    """finish tool runs after non-finish tools in the same batch."""
    from unittest.mock import patch, AsyncMock

    execution_order: list[str] = []

    async def mock_call_model_with_retry(*, loop_ctx, task_model, fallback_model, mode, emit):
        execution_order.append("model_call")
        return {
            "content": "I'll read a file then finish.",
            "tool_calls": [
                {"id": "tc_1", "name": "file_read", "arguments": {"path": "test.py"}},
                {"id": "tc_2", "name": "finish", "arguments": {"summary": "Done"}},
            ],
            "tool_calls_raw": [],
            "finish_reason": "stop",
        }

    async def mock_execute_tool_call(*, storage, task_id, tool_name, tool_args,
                                      tool_call_id, gateway, context, loop_ctx,
                                      emit, approved_permission=None):
        execution_order.append(f"execute:{tool_name}")
        return {"ok": True, "summary": f"{tool_name} done", "data": {}}

    async def mock_emit(**kwargs):
        return {"id": "evt_1"}

    with tempfile.TemporaryDirectory() as tmp:
        task = await orch_storage.create_task(
            agent_storage,
            user_id="test-user",
            title="test finish order",
            prompt="test",
            workspace_path=tmp,
            conversation_id=None,
            active_skill_ids=[],
            settings=OrchestratorSettings(agent_loop_enabled=True).model_dump(by_alias=True),
            context={},
            budget={},
        )
        runner = OrchestratorRunner()

        with patch.object(runner.agent_loop_runner, "_call_model_with_retry", new=mock_call_model_with_retry):
            with patch.object(runner.agent_loop_runner, "_execute_tool_call", new=mock_execute_tool_call):
                request = OrchestratorTaskCreateRequest(prompt="test", workspace_path=tmp)
                settings = OrchestratorSettings(agent_loop_enabled=True)
                await runner.run(storage=agent_storage, task_id=task["id"], request=request, settings=settings)

    # file_read executed before finish
    assert "execute:file_read" in execution_order
    assert execution_order.index("execute:file_read") < execution_order.index("model_call") if "model_call" in execution_order[1:] else True
    # Task completed
    stored_task = await orch_storage.get_task(agent_storage, task["id"])
    assert stored_task["status"] == "done"


@pytest.mark.asyncio
async def test_run_loop_cache_hit_on_second_round(agent_storage):
    """Second identical file_read call hits cache, not _execute_tool_call."""
    from unittest.mock import patch

    execute_count = [0]

    async def mock_call_model_with_retry(*, loop_ctx, task_model, fallback_model, mode, emit):
        if execute_count[0] == 0:
            # Round 1: read file, then finish
            return {
                "content": "Reading file.",
                "tool_calls": [
                    {"id": "tc_1", "name": "file_read", "arguments": {"path": "app.py"}},
                ],
                "tool_calls_raw": [],
                "finish_reason": "stop",
            }
        else:
            # Round 2: read same file again, then finish
            return {
                "content": "Reading same file again.",
                "tool_calls": [
                    {"id": "tc_2", "name": "file_read", "arguments": {"path": "app.py"}},
                    {"id": "tc_3", "name": "finish", "arguments": {"summary": "Done"}},
                ],
                "tool_calls_raw": [],
                "finish_reason": "stop",
            }

    async def mock_execute_tool_call(*, storage, task_id, tool_name, tool_args,
                                      tool_call_id, gateway, context, loop_ctx,
                                      emit, approved_permission=None):
        execute_count[0] += 1
        return {"ok": True, "summary": f"read {tool_args['path']}", "data": {"content": "hello"}}

    async def mock_emit(**kwargs):
        return {"id": "evt_1"}

    with tempfile.TemporaryDirectory() as tmp:
        task = await orch_storage.create_task(
            agent_storage,
            user_id="test-user",
            title="test cache",
            prompt="test",
            workspace_path=tmp,
            conversation_id=None,
            active_skill_ids=[],
            settings=OrchestratorSettings(agent_loop_enabled=True).model_dump(by_alias=True),
            context={},
            budget={},
        )
        runner = OrchestratorRunner()

        with patch.object(runner.agent_loop_runner, "_call_model_with_retry", new=mock_call_model_with_retry):
            with patch.object(runner.agent_loop_runner, "_execute_tool_call", new=mock_execute_tool_call):
                request = OrchestratorTaskCreateRequest(prompt="test", workspace_path=tmp)
                settings = OrchestratorSettings(agent_loop_enabled=True)
                await runner.run(storage=agent_storage, task_id=task["id"], request=request, settings=settings)

    # file_read was executed only once (first round); second round hit cache
    assert execute_count[0] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `& 'e:\Amadeus\Amadeus_web\.venv\Scripts\python.exe' -m pytest tests/test_agent_loop.py::test_run_loop_finish_after_other_tools tests/test_agent_loop.py::test_run_loop_cache_hit_on_second_round -v`
Expected: FAIL

- [ ] **Step 3: Modify AgentLoopRunner — init, _run_loop, _execute_tool_call, _emit_tool_result, _finish_task**

In `backend/amadeus_app/orchestrator/agent_loop_runner.py`:

**3a. Add import at top (after line 29):**

```python
from .tool_executor import ToolCache, ToolExecutor
```

**3b. Modify `__init__` (lines 73-77):**

Replace:
```python
    def __init__(
        self,
        capability_registry: CapabilityRegistry | None = None,
    ) -> None:
        self.capability_registry = capability_registry or default_capability_registry
```

With:
```python
    def __init__(
        self,
        capability_registry: CapabilityRegistry | None = None,
    ) -> None:
        self.capability_registry = capability_registry or default_capability_registry
        self.tool_executor = ToolExecutor(runner=self, cache=ToolCache())
```

**3c. Modify `_run_loop` (lines 376-410):**

Replace the block:
```python
            tool_calls = turn.get("tool_calls", [])
            if not tool_calls:
                break

            for tc in tool_calls:
                budget.tool_calls += 1
                if budget.is_exhausted():
                    break

                tool_name = tc["name"]
                tool_args = tc.get("arguments", {})

                if tool_name == "finish":
                    await emit(
                        kind="message",
                        role="assistant",
                        name="final_answer",
                        status="done",
                        summary=str(tool_args.get("summary") or "Task complete.")[:200],
                        payload=tool_args,
                    )
                    return True

                await self._execute_tool_call(
                    storage=storage,
                    task_id=task_id,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    tool_call_id=tc.get("id", ""),
                    gateway=gateway,
                    context=context,
                    loop_ctx=loop_ctx,
                    emit=emit,
                    approved_permission=approved_permission,
                )
```

With:
```python
            tool_calls = turn.get("tool_calls", [])
            if not tool_calls:
                break

            # Separate finish from other tools (finish runs after others)
            finish_tc = next((tc for tc in tool_calls if tc["name"] == "finish"), None)
            non_finish_calls = [tc for tc in tool_calls if tc["name"] != "finish"]

            budget.tool_calls += len(tool_calls)
            if budget.is_exhausted():
                break

            # Batch-execute non-finish tools (reads parallel, writes serial, cache integrated)
            if non_finish_calls:
                await self.tool_executor.execute_batch(
                    tool_calls=non_finish_calls,
                    gateway=gateway,
                    context=context,
                    loop_ctx=loop_ctx,
                    emit=emit,
                    approved_permission=approved_permission,
                    storage=storage,
                    task_id=task_id,
                )

            # finish runs after other tools
            if finish_tc:
                tool_args = finish_tc.get("arguments", {})
                await emit(
                    kind="message",
                    role="assistant",
                    name="final_answer",
                    status="done",
                    summary=str(tool_args.get("summary") or "Task complete.")[:200],
                    payload=tool_args,
                )
                return True
```

**3d. Modify `_execute_tool_call` return type (line 631):**

Replace `-> None:` with `-> dict[str, Any] | None:` on the `_execute_tool_call` method signature (line 631).

**3e. Add `return result` at end of `_execute_tool_call` (after line 721):**

Replace:
```python
        result = await self.capability_registry.execute(tool_name, tool_args, context)
        await self._emit_tool_result(
            emit=emit, tool_name=tool_name, tool_call_id=tool_call_id,
            result=result, loop_ctx=loop_ctx,
        )
```

With:
```python
        result = await self.capability_registry.execute(tool_name, tool_args, context)
        await self._emit_tool_result(
            emit=emit, tool_name=tool_name, tool_call_id=tool_call_id,
            result=result, loop_ctx=loop_ctx,
        )
        return result
```

**3f. Also add `return result` to the shell_exec fast-path (after line 671):**

Replace:
```python
                result = await self.capability_registry.execute("shell_exec", tool_args, context)
                await self._emit_tool_result(
                    emit=emit, tool_name=tool_name, tool_call_id=tool_call_id,
                    result=result, loop_ctx=loop_ctx,
                )
                return
```

With:
```python
                result = await self.capability_registry.execute("shell_exec", tool_args, context)
                await self._emit_tool_result(
                    emit=emit, tool_name=tool_name, tool_call_id=tool_call_id,
                    result=result, loop_ctx=loop_ctx,
                )
                return result
```

**3g. Also add `return result` to the mcp path in `_execute_tool_call` (after line 659):**

Replace:
```python
            await self._execute_raw_mcp(
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
            return
```

With:
```python
            result = await self._execute_raw_mcp(
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
            return result
```

**3h. Modify `_execute_raw_mcp` return type (line 736):**

Replace `-> None:` with `-> dict[str, Any] | None:` on the `_execute_raw_mcp` method signature.

**3i. Add `return result` at end of `_execute_raw_mcp` (after line 808):**

Replace:
```python
        await self._emit_tool_result(
            emit=emit, tool_name=tool_name, tool_call_id=tool_call_id,
            result=result, loop_ctx=loop_ctx,
        )
```

With:
```python
        await self._emit_tool_result(
            emit=emit, tool_name=tool_name, tool_call_id=tool_call_id,
            result=result, loop_ctx=loop_ctx,
        )
        return result
```

**3j. Modify `_emit_tool_result` — remove message append (lines 839-845):**

Replace:
```python
    async def _emit_tool_result(
        self,
        *,
        emit,
        tool_name: str,
        tool_call_id: str,
        result: dict[str, Any],
        loop_ctx: AgentLoopContext,
    ) -> None:
        """Emit tool_result event and add observation to context."""
        ok = bool(result.get("ok", True))
        summary = str(result.get("summary") or "")
        data = result.get("data", {})

        await emit(
            kind="tool_result",
            role="worker",
            name=tool_name,
            status="done" if ok else "error",
            summary=summary[:500],
            payload={
                "toolCallId": tool_call_id,
                "ok": ok,
                "data": _truncate_payload(data),
                "artifactIds": _string_list(result.get("artifactIds")),
            },
            artifact_ids=_string_list(result.get("artifactIds")),
        )

        observation = self._compress_observation(tool_name, summary, data, ok)
        loop_ctx.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": observation,
        })
```

With:
```python
    async def _emit_tool_result(
        self,
        *,
        emit,
        tool_name: str,
        tool_call_id: str,
        result: dict[str, Any],
        loop_ctx: AgentLoopContext,
    ) -> None:
        """Emit tool_result event. Message append is handled by ToolExecutor."""
        ok = bool(result.get("ok", True))
        summary = str(result.get("summary") or "")
        data = result.get("data", {})

        await emit(
            kind="tool_result",
            role="worker",
            name=tool_name,
            status="done" if ok else "error",
            summary=summary[:500],
            payload={
                "toolCallId": tool_call_id,
                "ok": ok,
                "data": _truncate_payload(data),
                "artifactIds": _string_list(result.get("artifactIds")),
                "fromCache": bool(result.get("from_cache", False)),
            },
            artifact_ids=_string_list(result.get("artifactIds")),
        )

    def _append_tool_message(
        self,
        loop_ctx: AgentLoopContext,
        tool_call_id: str,
        tool_name: str,
        result: dict[str, Any],
    ) -> None:
        """Append tool observation to loop_ctx.messages in order."""
        ok = bool(result.get("ok", True))
        summary = str(result.get("summary") or "")
        data = result.get("data", {})
        observation = self._compress_observation(tool_name, summary, data, ok)
        loop_ctx.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": observation,
        })
```

**3k. Modify `_finish_task` — add cacheStats (lines 958-968):**

Replace:
```python
        await emit(
            kind="done",
            role="summarizer",
            name="summary",
            status="done",
            summary="任务已由 agent loop 完成编排和能力执行。",
            payload={
                "elapsedSeconds": elapsed,
                "rounds": budget.rounds,
                "toolCalls": budget.tool_calls,
            },
        )
```

With:
```python
        await emit(
            kind="done",
            role="summarizer",
            name="summary",
            status="done",
            summary="任务已由 agent loop 完成编排和能力执行。",
            payload={
                "elapsedSeconds": elapsed,
                "rounds": budget.rounds,
                "toolCalls": budget.tool_calls,
                "cacheStats": self.tool_executor.cache.stats if self.tool_executor else {"hits": 0, "misses": 0},
            },
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `& 'e:\Amadeus\Amadeus_web\.venv\Scripts\python.exe' -m pytest tests/test_agent_loop.py -v --tb=short`
Expected: PASS (all tests, including existing ones)

- [ ] **Step 5: Commit**

```bash
git add backend/amadeus_app/orchestrator/agent_loop_runner.py tests/test_agent_loop.py
git commit -m "feat: integrate ToolExecutor into agent loop, refactor _emit_tool_result to separate message append"
```

---

### Task 4: file_write modified_paths + frontend

**Files:**
- Modify: `backend/amadeus_app/orchestrator/capability_adapters.py:247-252` (file_write return)
- Modify: `src/types.ts` (no changes needed — payload is `Record<string, unknown>`)
- Modify: `src/components/TaskWorkspace.tsx` (cache badge rendering)
- Test: `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `ToolExecutor._execute_single_write` reads `modified_paths` from result (Task 2)
- Produces: `file_write` result with `modified_paths` field, frontend cache badges

- [ ] **Step 1: Write the failing test for modified_paths**

Append to `tests/test_agent_loop.py`:

```python
@pytest.mark.asyncio
async def test_file_write_returns_modified_paths():
    """file_write result includes modified_paths for cache invalidation."""
    import tempfile
    from pathlib import Path
    from backend.amadeus_app.orchestrator.capability_adapters import (
        CapabilityExecutionContext,
        default_capability_registry,
    )
    from backend.amadeus_app.orchestrator.domain import OrchestratorSettings

    with tempfile.TemporaryDirectory() as tmp:
        settings = OrchestratorSettings(default_workspace=tmp)
        context = CapabilityExecutionContext(
            task_id="test",
            prompt="test",
            workspace_path=tmp,
            settings=settings,
            metadata={},
            storage=None,
            emit_event=None,  # type: ignore
        )
        registry = default_capability_registry

        result = await registry.execute(
            "file_write",
            {"path": "output.txt", "content": "hello world"},
            context,
        )

        assert result["ok"] is True
        assert "modified_paths" in result
        assert "output.txt" in result["modified_paths"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& 'e:\Amadeus\Amadeus_web\.venv\Scripts\python.exe' -m pytest tests/test_agent_loop.py::test_file_write_returns_modified_paths -v`
Expected: FAIL with `KeyError: 'modified_paths'` or `assert "modified_paths" in result` fails

- [ ] **Step 3: Add modified_paths to file_write return**

In `backend/amadeus_app/orchestrator/capability_adapters.py`, find the `_file_write` function return (around line 247):

Replace:
```python
    return {
        "ok": True,
        "summary": f"已写入文件 {target_path.name}。",
        "data": {"path": str(target_path), "artifact": artifact},
        "artifactIds": artifact_ids,
    }
```

With:
```python
    # Compute relative path for cache invalidation
    try:
        rel_path = str(target_path.relative_to(workspace_root))
    except ValueError:
        rel_path = str(target_path)
    return {
        "ok": True,
        "summary": f"已写入文件 {target_path.name}。",
        "data": {"path": str(target_path), "artifact": artifact},
        "artifactIds": artifact_ids,
        "modified_paths": [rel_path],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& 'e:\Amadeus\Amadeus_web\.venv\Scripts\python.exe' -m pytest tests/test_agent_loop.py::test_file_write_returns_modified_paths -v`
Expected: PASS

- [ ] **Step 5: Add cache badge to TaskWorkspace.tsx**

In `src/components/TaskWorkspace.tsx`, find the tool_call/tool_result rendering section (search for `tool_call` or `tool_result` in the event rendering). Add cache badges after the tool name display.

Find the section where `tool_call` and `tool_result` events are rendered (inside the event list map). Add after the tool name:

```tsx
{event.payload?.cached && (
  <span className="text-xs text-green-600 ml-1">cached</span>
)}
{event.payload?.fromCache && (
  <span className="text-xs text-green-600 ml-1">from cache</span>
)}
```

Find the `done` event rendering section. Add cache stats:

```tsx
{event.payload?.cacheStats && (
  <span className="text-xs text-gray-500 ml-2">
    缓存 {Number(event.payload.cacheStats.hits)} 命中 / {Number(event.payload.cacheStats.misses)} 未命中
  </span>
)}
```

- [ ] **Step 6: Run full test suite + frontend build check**

Run: `& 'e:\Amadeus\Amadeus_web\.venv\Scripts\python.exe' -m pytest tests/test_agent_loop.py -v --tb=short`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add backend/amadeus_app/orchestrator/capability_adapters.py src/components/TaskWorkspace.tsx tests/test_agent_loop.py
git commit -m "feat: add modified_paths to file_write, render cache badges in frontend"
```

---

### Task 5: Regression tests

**Files:**
- Test: `tests/test_agent_loop.py`

- [ ] **Step 1: Run full test suite**

Run: `& 'e:\Amadeus\Amadeus_web\.venv\Scripts\python.exe' -m pytest tests/test_agent_loop.py -v --tb=short`
Expected: ALL PASS

- [ ] **Step 2: Run any other test suites**

Run: `& 'e:\Amadeus\Amadeus_web\.venv\Scripts\python.exe' -m pytest tests/ -v --tb=short -x`
Expected: ALL PASS

- [ ] **Step 3: Check for TypeScript errors**

Run: `cd e:\Amadeus\Amadeus_web ; npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Commit if any fixes were needed**

If any fixes were needed during regression:
```bash
git add -A
git commit -m "fix: regression fixes from parallel tools and caching integration"
```

If no fixes needed, skip this step.
