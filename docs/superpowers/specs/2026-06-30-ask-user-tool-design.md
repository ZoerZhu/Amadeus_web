# Agent 向用户提问 设计文档

**日期:** 2026-06-30
**状态:** Approved

## 目标

让 Agent 在执行过程中遇到不确定决策时，能主动向用户提问并等待回答，而非盲目猜测或停止执行。

典型场景：
- Agent 要修改配置文件，不确定用哪个值 → 问用户
- Agent 要删除文件，想确认范围 → 问用户
- Agent 要选择实现方案（A/B/C）→ 问用户

## 架构

复用现有**权限暂停/恢复**基础设施。新增 `ask_user` 工具，执行时创建一条 `permission_request`（`question_type` 区分），抛出 `AgentLoopPermissionBlocked` 暂停 Loop。用户回答后，`resume_from_permission` 将答案注入为 tool result，Loop 继续执行。

## 技术栈

Python 3.12 / asyncio / SQLite / FastAPI / React + TypeScript

## 设计决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 复用基础设施 | permission_requests 表 + AgentLoopPermissionBlocked | 避免新建并行暂停机制，已有 pause/resume 流程成熟 |
| 区分 question vs permission | 新增 `question_type` 字段 | 同一表存两种暂停原因，前端按 type 渲染不同卡片 |
| 答案注入方式 | 作为 tool result 注入 messages | 与其他工具结果格式一致，模型能自然继续推理 |
| 选项限制 | 最多 4 个选项 + "其他"（自由输入） | 防止选项过多导致 UI 拥挤 |
| 超时处理 | 无自动超时 | 用户可能需要长时间思考，不强制超时 |
| 取消行为 | 注入"User cancelled"作为 tool result，Loop 继续 | 用户可能不想回答但希望 Agent 自行决策，而非终止整个任务 |

---

## 组件设计

### 1. ask_user 工具定义

**文件:** `backend/amadeus_app/orchestrator/capabilities.py`（修改）

```python
# 新增工具注册
ASK_USER_TOOL = {
    "name": "ask_user",
    "description": "Ask the user a question and wait for their answer. Use when you need "
                   "clarification or a decision from the user before proceeding.",
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user.",
            },
            "context": {
                "type": "string",
                "description": "Context explaining why this question is being asked.",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional predefined choices (max 4). User can also type a custom answer.",
            },
        },
        "required": ["question"],
    },
    "riskLevel": "safe",  # 提问本身不危险，无需确认
}
```

### 2. _ask_user adapter

**文件:** `backend/amadeus_app/orchestrator/capability_adapters.py`（修改）

```python
async def _ask_user(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    question = str(args.get("question") or "").strip()
    if not question:
        return {"ok": False, "summary": "ask_user requires a question.", "data": {}}

    options = args.get("options") or []
    if isinstance(options, list):
        options = [str(o) for o in options[:4]]  # 最多 4 个

    context_str = str(args.get("context") or "").strip()

    # 创建 permission_request（question_type）
    request = await create_permission_request(
        context.storage,
        task_id=context.task_id,
        capability="ask_user",
        action="question",
        reason=question,
        payload={
            "question": question,
            "context": context_str,
            "options": options,
            "questionType": "ask_user",
        },
    )

    # 发送 question 事件供前端渲染
    await _emit_event(
        context,
        kind="question",
        role="assistant",
        name="ask_user",
        status="pending",
        summary=question,
        payload={
            "questionId": request["id"],
            "question": question,
            "context": context_str,
            "options": options,
            "questionType": "ask_user",
        },
    )

    # 抛出异常暂停 Loop
    raise AgentLoopPermissionBlocked(
        request_id=request["id"],
        reason=f"Waiting for user answer: {question[:100]}",
    )
```

### 3. permission_requests 表扩展

**文件:** `backend/amadeus_app/orchestrator/storage.py`（修改）

`orchestrator_permission_requests` 表新增字段：

```sql
ALTER TABLE orchestrator_permission_requests ADD COLUMN question_type TEXT DEFAULT '';
-- 值: "" (普通权限确认) | "ask_user" (用户提问)
```

`create_permission_request` 函数新增 `question_type: str = ""` 参数。

### 4. 回答注入

**文件:** `backend/amadeus_app/routers/orchestrator.py`（修改）

新增回答端点：

```
POST /api/orchestrator/tasks/{task_id}/permissions/{request_id}/answer
Body: { "answer": "用户输入的答案" }
```

- 验证 `question_type == "ask_user"`
- 更新 permission_request 状态为 `approved`，`resolution_payload` 存答案
- 调用 `resume_from_permission(task_id, request_id, approved=True, answer=answer)`

### 5. resume_from_permission 修改

**文件:** `backend/amadeus_app/orchestrator/agent_loop_runner.py`（修改）

现有 `resume_from_permission`（第 211 行）重建 context 后重新进入 `_run_loop`。修改为：

```python
async def resume_from_permission(self, task_id, request_id, *, approved, answer=None):
    # ... 现有 context 重建逻辑 ...

    # 如果是 ask_user 问题，将答案注入为 tool result
    if answer is not None:
        tool_call_id = pending_tool_call_id  # 从事件中恢复
        loop_ctx.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": answer,  # 用户的回答作为工具结果
        })
    elif approved:
        # 普通权限确认，继续执行
        pass
    else:
        # 拒绝/取消
        loop_ctx.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": "Permission denied by user.",
        })

    await self._run_loop(loop_ctx, ...)
```

### 6. 前端渲染

**文件:** `src/components/TaskWorkspace.tsx`（修改）

现有权限卡片渲染逻辑（第 587-638 行）扩展，根据 `questionType` 区分：

```tsx
{permissions.map((perm) => {
    const isQuestion = perm.payload?.questionType === "ask_user";
    if (isQuestion) {
        return (
            <QuestionCard
                key={perm.id}
                question={perm.payload.question}
                context={perm.payload.context}
                options={perm.payload.options || []}
                onSubmit={(answer) => answerPermission(perm.id, answer)}
                onCancel={() => rejectPermission(perm.id)}
            />
        );
    }
    return <PermissionCard ... />;  // 现有权限卡片
})}
```

**QuestionCard 组件行为：**
- 显示问题和上下文
- 如果有选项：渲染为可点击的按钮列表 + 一个"自定义输入"文本框
- 如果无选项：只显示文本输入框
- 提交后调用 `answerPermission(requestId, answer)`
- 取消后调用 `rejectPermission(requestId)`

### 7. API 层

**文件:** `src/api.ts`（修改）

```typescript
export async function answerPermission(
    taskId: string,
    requestId: string,
    answer: string
): Promise<void> {
    await fetch(`/api/orchestrator/tasks/${taskId}/permissions/${requestId}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer }),
    });
}
```

**文件:** `src/types.ts`（修改）

`OrchestratorPermissionRequest` 接口新增：

```typescript
interface OrchestratorPermissionRequest {
    // ... 现有字段 ...
    payload?: {
        questionType?: string;
        question?: string;
        context?: string;
        options?: string[];
    };
}
```

---

## 数据流

```
Agent Loop 第 N 轮
  │
  ▼
模型返回 tool_calls: [{ name: "ask_user", args: { question: "...", options: [...] } }]
  │
  ▼
ToolExecutor.execute_batch → _ask_user adapter
  ├─ 创建 permission_request (question_type="ask_user")
  ├─ 发送 question 事件
  └─ raise AgentLoopPermissionBlocked → Loop 暂停
  │
  ▼
任务状态 → "paused"
  │
  ▼
前端收到 question 事件 → 刷新 permissions → 渲染 QuestionCard
  │
  ▼
用户选择选项 / 输入自定义答案 → POST /answer
  │
  ▼
resume_from_permission(answer=用户答案)
  ├─ 将答案注入 messages: { role: "tool", content: "用户答案" }
  └─ 重新进入 _run_loop → 模型看到答案继续推理
```

## 错误处理

| 场景 | 处理 |
|---|---|
| 用户取消问题 | 注入 `"User cancelled the question."` 作为 tool result，Loop 继续（模型可自行决定下一步） |
| 多个 ask_user 同时触发 | ToolExecutor 串行处理，第一个触发暂停后后续不会执行 |
| 网络断开恢复 | permission_request 持久化在 SQLite，重连后前端可重新拉取 pending permissions |
| 答案为空字符串 | 允许提交，注入空字符串（模型可自行处理） |

## 测试策略

1. **单元测试** — `_ask_user` adapter 创建 permission_request、发送事件、抛出异常
2. **集成测试** — Agent Loop 中 ask_user 触发暂停 → answer → resume → Loop 继续
3. **前端测试** — QuestionCard 渲染、选项点击、自定义输入、提交/取消
4. **边界测试** — 无选项、空 context、长问题、特殊字符答案
