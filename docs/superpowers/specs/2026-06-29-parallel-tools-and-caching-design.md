# 并行工具调用 + 工具结果缓存 设计文档

**日期:** 2026-06-29
**状态:** Draft

## 目标

解决 Agent Loop 中两个工具执行效率缺口：

1. **并行工具调用** — 模型一轮返回多个 tool_calls 时，读类工具并行执行，写类工具串行执行，缩短任务总时长。
2. **工具结果缓存** — 同一任务内重复的只读工具调用（如 `file_read(".")` 多次执行）直接返回缓存结果，避免冗余计算。

## 架构

新建 `tool_executor.py` 模块，封装缓存层与并行调度逻辑。`AgentLoopRunner._run_loop` 中的串行 `for tc in tool_calls` 循环替换为 `ToolExecutor.execute_batch()` 调用。两个核心组件：

- **ToolCache** — Task 级缓存，键为 `(tool_name, json.dumps(tool_args, sort_keys=True))`，值为完整 tool result dict。通过路径索引实现精确失效。
- **ToolExecutor** — 批量执行调度器。读类工具用 `asyncio.gather` 并行执行（无并发上限），写类工具串行执行。结果按原始 tool_calls 顺序返回，保证 `loop_ctx.messages` 的顺序正确性。

## 技术栈

Python 3.12 / asyncio / httpx / pytest + pytest-asyncio / React + TypeScript

## 设计决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 缓存生命周期 | Task 级 | 任务结束自动 GC，不会读到过期数据，最安全 |
| 缓存失效策略 | 路径级精确失效 | file_write 修改 `a.py` 只失效触及 `a.py` 的缓存条目；shell_exec/code_agent 保守全清 |
| 并行策略 | 读写分离 | 读类工具并行、写类工具串行，避免写冲突 |
| 并发上限 | 无上限 | 读工具通常是轻量 I/O，不设上限最大化并行度 |
| 架构方案 | 独立 ToolExecutor 模块 | agent_loop_runner.py 已有 ~1000 行，不在里面堆砌并行逻辑；ToolExecutor 可独立测试 |

---

## 组件设计

### 1. ToolCache

**文件:** `backend/amadeus_app/orchestrator/tool_executor.py`

**职责:** Task 级工具结果缓存，支持路径级精确失效。

#### 数据结构

```python
@dataclass
class CacheEntry:
    result: dict[str, Any]      # 完整 tool result {ok, summary, data, artifactIds}
    cached_at: float            # time.monotonic()
    tool_name: str
    tool_args: dict[str, Any]

class ToolCache:
    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry] = {}     # cache_key → entry
        self._path_index: dict[str, set[str]] = {}     # path → set of cache_keys
        self._hits: int = 0
        self._misses: int = 0
```

#### 缓存键

```python
def _make_key(tool_name: str, tool_args: dict[str, Any]) -> str:
    return f"{tool_name}:{json.dumps(tool_args, sort_keys=True, default=str)}"
```

排除 `tool_call_id`（每次调用唯一，不参与缓存键）。

#### 路径索引

正向索引：缓存读结果时同时记录"这次读取触及了哪些路径"。

- `file_read(path="src/app.tsx")` → `_path_index["src/app.tsx"].add(cache_key)`
- `code_search(query="auth", scope="src/")` → `_path_index["src/"].add(cache_key)`（目录级）
- `web_search(query="...")` → 无路径索引（不可失效，只随任务结束失效）

路径提取规则：
- `file_read` → 取 `path` 参数
- `code_search` → 取 `scope` 参数（目录级失效）
- `web_search` → 无路径

#### 接口

```python
def get(self, tool_name: str, tool_args: dict[str, Any]) -> dict[str, Any] | None:
    """查缓存。命中时 _hits += 1，未命中时 _misses += 1。"""

def set(self, tool_name: str, tool_args: dict[str, Any], result: dict[str, Any]) -> None:
    """存入缓存并记录路径索引。"""

def invalidate_paths(self, paths: list[str]) -> int:
    """失效触及给定路径的缓存条目，返回失效数量。
    精确匹配 + 目录前缀匹配。"""

def invalidate_all(self) -> int:
    """全清缓存（shell_exec / code_agent 写操作时保守使用）。"""

@property
def stats(self) -> dict[str, int]:
    """返回 {"hits": hits, "misses": misses}。"""
```

#### 失效逻辑

```python
def invalidate_paths(self, paths: list[str]) -> int:
    if not paths:
        return 0
    keys_to_remove: set[str] = set()
    for path in paths:
        # 精确匹配
        keys_to_remove.update(self._path_index.get(path, set()))
        # 目录前缀匹配（写 src/a.py 时也失效 src/ 下的 code_search）
        for indexed_path, key_set in self._path_index.items():
            if indexed_path.endswith("/") and path.startswith(indexed_path):
                keys_to_remove.update(key_set)
    for key in keys_to_remove:
        self._entries.pop(key, None)
    # 清理 path_index 中的空条目
    self._path_index = {p: s for p, s in self._path_index.items() if s}
    return len(keys_to_remove)
```

### 2. ToolExecutor

**文件:** `backend/amadeus_app/orchestrator/tool_executor.py`

**职责:** 批量执行 tool_calls，读类并行、写类串行，结果按原始顺序返回。

#### 数据结构

```python
@dataclass
class ToolCallResult:
    tool_call_id: str
    tool_name: str
    result: dict[str, Any]          # {ok, summary, data, artifactIds}
    from_cache: bool                # 是否命中缓存
    modified_paths: list[str]      # 写工具返回的修改路径
```

#### 只读工具分类

```python
_READ_ONLY_TOOLS = frozenset({"file_read", "code_search", "web_search"})
```

硬编码只读工具列表。其余所有工具（含 `shell_exec`, `code_agent`, `file_write`, `desktop_screenshot` 等）视为写类工具，串行执行。

#### 接口

```python
class ToolExecutor:
    def __init__(
        self,
        runner: "AgentLoopRunner",
        cache: ToolCache,
    ) -> None: ...

    async def execute_batch(
        self,
        tool_calls: list[dict[str, Any]],
        gateway: CapabilityGateway,
        context: CapabilityExecutionContext,
        loop_ctx: AgentLoopContext,
        emit,
        approved_permission: dict[str, Any] | None = None,
        storage=None,
        task_id: str = "",
    ) -> list[ToolCallResult]: ...
```

#### 执行流程

1. **finish 已在 `_run_loop` 中分离**：ToolExecutor 只接收非 finish 工具。
2. **分类**：`read_calls`（`_READ_ONLY_TOOLS` 中的）和 `write_calls`（其余）。
3. **读类并行**：`asyncio.gather(*read_tasks)`，无并发上限。每个调用先查 ToolCache。
   - 命中 → 发射带 `cached: True` 标记的 `tool_call`/`tool_result` 事件，返回缓存结果。
   - 未命中 → 委托给 `runner._execute_tool_call` 执行（该方法现在返回 result dict），存入缓存。
4. **写类串行**：逐个执行，执行后收集 `modified_paths`。
   - 有 `modified_paths` → `cache.invalidate_paths(modified_paths)`
   - 无 `modified_paths` → `cache.invalidate_all()`（shell_exec / code_agent）
5. **合并**：`_merge_in_order` 按原始顺序返回结果，并调用 `runner._append_tool_message` 按序 append tool messages 到 `loop_ctx.messages`。

#### 并行中的异常处理

```python
read_results = await asyncio.gather(*read_tasks, return_exceptions=True)

for i, result in enumerate(read_results):
    if isinstance(result, AgentLoopPermissionBlocked):
        raise result  # 权限阻塞：向上传播，保持 pause 语义
    if isinstance(result, Exception):
        # 其他异常：转为 tool_result error 事件，不中断其他工具
        tool_name = read_calls[i]["name"]
        await emit(
            kind="tool_result", role="worker", name=tool_name,
            status="error", summary=str(result)[:500],
            payload={"toolCallId": read_calls[i]["id"], "ok": False, "data": {"error": str(result)}},
        )
        read_results[i] = ToolCallResult(
            tool_call_id=read_calls[i]["id"],
            tool_name=tool_name,
            result={"ok": False, "summary": str(result), "data": {}},
            from_cache=False, modified_paths=[],
        )
```

原则：**权限阻塞传播，其他异常隔离**。一个读工具的网络超时不影响其他工具。

### 3. AgentLoopRunner 集成

**文件:** `backend/amadeus_app/orchestrator/agent_loop_runner.py`

#### `_run_loop` 改动

当前串行循环：
```python
for tc in tool_calls:
    budget.tool_calls += 1
    if budget.is_exhausted():
        break
    tool_name = tc["name"]
    tool_args = tc.get("arguments", {})
    if tool_name == "finish":
        await emit(...)
        return True
    await self._execute_tool_call(...)
```

替换为：
```python
# 分离 finish 工具（保持与串行模式相同的语义：finish 之前的工具先执行）
finish_tc = next((tc for tc in tool_calls if tc["name"] == "finish"), None)
non_finish_calls = [tc for tc in tool_calls if tc["name"] != "finish"]

# 预检 budget
budget.tool_calls += len(tool_calls)
if budget.is_exhausted():
    break

# 批量执行非 finish 工具
if non_finish_calls:
    await self.tool_executor.execute_batch(
        tool_calls=non_finish_calls,
        gateway=gateway, context=context, loop_ctx=loop_ctx,
        emit=emit, approved_permission=approved_permission,
        storage=storage, task_id=task_id,
    )

# finish 在其他工具执行完后短路
if finish_tc:
    tool_args = finish_tc.get("arguments", {})
    await emit(kind="message", role="assistant", name="final_answer", ...)
    return True
```

#### `_execute_tool_call` 改动

当前 `_execute_tool_call` 返回 `None`，结果通过 `_emit_tool_result` 内部处理。

改动：**返回 result dict**，供 `ToolExecutor` 在合并阶段使用。

```python
async def _execute_tool_call(self, ...) -> dict[str, Any] | None:
    """执行工具调用，返回 result dict。"""
    # ... 权限检查、gateway 决策等逻辑不变 ...
    result = await self.capability_registry.execute(tool_name, tool_args, context)
    await self._emit_tool_result(
        emit=emit, tool_name=tool_name, tool_call_id=tool_call_id,
        result=result, loop_ctx=loop_ctx,
    )
    return result  # 新增：返回 result 供 ToolExecutor 使用
```

#### `_emit_tool_result` 改动

当前 `_emit_tool_result` 同时做两件事：发射事件 + append message。

改动：**拆分为两步**——`_emit_tool_result` 只发射事件，message append 移到 `execute_batch` 的合并阶段按顺序执行。

```python
async def _emit_tool_result(self, *, emit, tool_name, tool_call_id, result, loop_ctx) -> None:
    """发射 tool_result 事件。不再 append message —— 由 ToolExecutor 按序 append。"""
    ok = bool(result.get("ok", True))
    summary = str(result.get("summary") or "")
    data = result.get("data", {})
    await emit(
        kind="tool_result", role="worker", name=tool_name,
        status="done" if ok else "error",
        summary=summary[:500],
        payload={
            "toolCallId": tool_call_id, "ok": ok,
            "data": _truncate_payload(data),
            "artifactIds": _string_list(result.get("artifactIds")),
            "fromCache": bool(result.get("from_cache", False)),
        },
        artifact_ids=_string_list(result.get("artifactIds")),
    )
    # NOTE: message append moved to ToolExecutor._merge_in_order

def _append_tool_message(self, loop_ctx, tool_call_id, tool_name, result) -> None:
    """按序 append tool message 到 loop_ctx.messages。"""
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

`ToolExecutor._merge_in_order` 在合并结果时调用 `runner._append_tool_message` 按原始顺序逐个 append。

#### `_finish_task` 改动

done 事件 payload 新增缓存统计：
```python
payload = {
    ...,
    "cacheStats": self.tool_executor.cache.stats if self.tool_executor else {"hits": 0, "misses": 0}
}
```

#### `ToolExecutor` 初始化

在 `AgentLoopRunner.run` 和 `resume_from_permission` 中初始化：
```python
self.tool_executor = ToolExecutor(
    runner=self,
    cache=ToolCache(),
)
```

### 4. capability_adapters.py 改动

`file_write` 返回值新增 `modified_paths`：
```python
# file_write 执行后
result = {
    "ok": True,
    "summary": f"Wrote {target_path}",
    "data": {"path": str(target_path), "artifact": artifact},
    "artifactIds": [artifact["id"]] if artifact else [],
    "modified_paths": [str(target_path.relative_to(workspace_root))],
}
```

shell_exec 和 code_agent 不返回 `modified_paths`（空列表），触发保守全清。

### 5. 前端改动

#### `src/types.ts`

```typescript
// tool_call 事件 payload 新增
interface ToolCallPayload {
  // ...existing fields...
  cached?: boolean;
}

// tool_result 事件 payload 新增
interface ToolResultPayload {
  // ...existing fields...
  fromCache?: boolean;
}

// done 事件 payload 新增
interface DonePayload {
  // ...existing fields...
  cacheStats?: {
    hits: number;
    misses: number;
  };
}
```

#### `src/components/TaskWorkspace.tsx`

tool_call/tool_result 事件渲染新增缓存标记：
```tsx
{event.payload?.cached && (
  <span className="text-xs text-green-600 ml-1">cached</span>
)}
{event.payload?.fromCache && (
  <span className="text-xs text-green-600 ml-1">from cache</span>
)}
```

done 事件渲染新增缓存统计：
```tsx
{event.payload?.cacheStats && (
  <span>缓存 {cacheStats.hits} 命中 / {cacheStats.misses} 未命中</span>
)}
```

---

## 测试策略

### 后端测试 (`tests/test_agent_loop.py`)

| 测试 | 验证内容 |
|---|---|
| `test_tool_cache_hit_miss` | 相同参数命中缓存，不同参数不命中 |
| `test_tool_cache_invalidate_by_path` | file_write 后触及该路径的 file_read 缓存失效 |
| `test_tool_cache_invalidate_all_on_shell_exec` | shell_exec 后全清缓存 |
| `test_tool_cache_path_prefix_match` | 写 `src/a.py` 时 `src/` 下的 code_search 缓存也失效 |
| `test_tool_cache_stats` | hits/misses 统计正确 |
| `test_execute_batch_parallel_reads` | 3 个读工具并行执行，结果按序返回 |
| `test_execute_batch_mixed_read_write` | 读并行 + 写串行，写在所有读之后执行 |
| `test_execute_batch_preserves_message_order` | 并行执行后 loop_ctx.messages 顺序与 tool_calls 一致 |
| `test_execute_batch_finish_excluded_from_batch` | finish 在 _run_loop 中分离，不进入 execute_batch |
| `test_run_loop_finish_after_other_tools` | finish 前的非 finish 工具先执行，再处理 finish |
| `test_execute_batch_cached_result_emits_events` | 缓存命中时发射带 cached 标记的事件 |
| `test_execute_batch_error_isolation` | 一个读工具失败不影响其他工具 |
| `test_execute_batch_permission_blocked_propagates` | 权限阻塞向上传播 |

### 前端

无需单独测试（可选字段渲染，不改变现有逻辑流）。

---

## 不做的事（YAGNI）

- 不做缓存 TTL（Task 级生命周期已足够）
- 不做缓存持久化（任务结束即丢弃）
- 不做跨任务缓存共享（Conversation 级已被排除）
- 不做 shell_exec 命令分析来提取修改路径（太复杂，保守全清）
- 不做 code_agent 路径追踪（OpenCode 路径不透明，保守全清）
- 不做并发上限控制（读工具通常不重）
- 不做前端 diff 查看器（属于功能 7 的范围）

---

## 文件结构

| 文件 | 操作 | 改动内容 |
|---|---|---|
| `backend/amadeus_app/orchestrator/tool_executor.py` | 新建 | ToolCache + ToolExecutor + ToolCallResult |
| `backend/amadeus_app/orchestrator/agent_loop_runner.py` | 修改 | _run_loop 用 execute_batch 替换串行循环；_emit_tool_result 拆分 append 逻辑；_finish_task 加缓存统计 |
| `backend/amadeus_app/orchestrator/capability_adapters.py` | 修改 | file_write 返回值新增 modified_paths |
| `src/types.ts` | 修改 | ToolCallPayload/ToolResultPayload/DonePayload 新增可选字段 |
| `src/components/TaskWorkspace.tsx` | 修改 | 缓存标记渲染 + done 统计展示 |
| `tests/test_agent_loop.py` | 扩展 | 缓存命中/失效/并行执行/消息顺序测试 |
