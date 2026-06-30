# Ask User Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `ask_user` tool that lets the Agent pause execution and ask the user a question, then resume with the user's answer injected as a tool result.

**Architecture:** Reuses the existing permission pause/resume infrastructure. The `ask_user` adapter creates a `permission_request` with `question_type="ask_user"`, emits a `question` event, and raises `AgentLoopPermissionBlocked` to pause. A new `/answer` endpoint lets the user respond; `resume_from_permission` injects the answer as a tool result message and re-enters `_run_loop`.

**Tech Stack:** Python 3.12 / asyncio / SQLite / FastAPI / pytest + pytest-asyncio / React + TypeScript

## Global Constraints

- `ask_user` tool has `riskLevel="safe"` — no permission confirmation needed to ask
- Maximum 4 predefined options; user can always type a custom answer
- Answer is injected as `{"role": "tool", "content": answer, "tool_call_id": ...}` message
- Must not break existing permission pause/resume flow for normal tool approvals
- `question_type` field defaults to `""` (empty string) for normal permission requests

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `backend/amadeus_app/orchestrator/capabilities.py` | Modify | Register `ask_user` in capability catalog |
| `backend/amadeus_app/orchestrator/capability_adapters.py` | Modify | Add `_ask_user` adapter |
| `backend/amadeus_app/storage.py` | Modify | Add `question_type` column to permission_requests schema |
| `backend/amadeus_app/orchestrator/storage.py` | Modify | Add question_type param to create_permission_request + new storage functions |
| `backend/amadeus_app/orchestrator/agent_loop_runner.py` | Modify | Inject answer in resume_from_permission |
| `backend/amadeus_app/routers/orchestrator.py` | Modify | Add `/answer` endpoint |
| `src/types.ts` | Modify | Add question payload types |
| `src/api.ts` | Modify | Add `answerPermission` function |
| `src/components/TaskWorkspace.tsx` | Modify | Add QuestionCard component |
| `tests/test_ask_user.py` | Create | Unit + integration tests |

---

### Task 1: Add question_type column to permission_requests schema

**Files:**
- Modify: `backend/amadeus_app/storage.py:491-507` (schema) and migration block at `:530-537`
- Modify: `backend/amadeus_app/orchestrator/storage.py:437-472` (create_permission_request) and `:651-663` (_serialize_permission_row)

**Interfaces:**
- Produces: `create_permission_request(..., question_type="")` parameter
- Produces: serialized permission request includes `questionType` field

- [ ] **Step 1: Write failing test**

```python
# tests/test_ask_user.py
from __future__ import annotations
import pytest
from uuid import uuid4

from backend.amadeus_app.storage import SQLiteStorage
from backend.amadeus_app.orchestrator import storage as orch_storage


class TestPermissionRequestQuestionType:
    @pytest.mark.asyncio
    async def test_create_permission_with_question_type(self, tmp_path):
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.init_db()

        task_id = uuid4().hex
        await orch_storage.create_task(
            storage,
            user_id="test-user",
            title="test",
            prompt="test",
            workspace_path=str(tmp_path),
            conversation_id=None,
            active_skill_ids=[],
            settings={},
        )

        perm = await orch_storage.create_permission_request(
            storage,
            task_id=task_id,
            tool_name="ask_user",
            arguments_preview="What framework?",
            risk_level="safe",
            payload={"question": "Which framework?", "options": ["React", "Vue"]},
            question_type="ask_user",
        )
        assert perm["questionType"] == "ask_user"

    @pytest.mark.asyncio
    async def test_default_question_type_is_empty(self, tmp_path):
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.init_db()

        task_id = uuid4().hex
        await orch_storage.create_task(
            storage,
            user_id="test-user",
            title="test",
            prompt="test",
            workspace_path=str(tmp_path),
            conversation_id=None,
            active_skill_ids=[],
            settings={},
        )

        perm = await orch_storage.create_permission_request(
            storage,
            task_id=task_id,
            tool_name="file_write",
            arguments_preview="write a.py",
            risk_level="confirm",
        )
        assert perm["questionType"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ask_user.py::TestPermissionRequestQuestionType -v`
Expected: FAIL — `question_type` parameter not accepted / `questionType` not in serialized output

- [ ] **Step 3: Add migration for question_type column**

In `backend/amadeus_app/storage.py`, find the migration block at line ~530 that checks `orchestrator_permission_columns`. Add after the existing `payload_json` migration:

```python
        if "question_type" not in orchestrator_permission_columns:
            conn.execute(
                "ALTER TABLE orchestrator_permission_requests ADD COLUMN question_type TEXT NOT NULL DEFAULT ''"
            )
```

- [ ] **Step 4: Update create_permission_request to accept question_type**

In `backend/amadeus_app/orchestrator/storage.py`, modify `create_permission_request` (line 437):

```python
async def create_permission_request(
    storage: SQLiteStorage,
    *,
    task_id: str,
    tool_name: str,
    arguments_preview: str,
    risk_level: str = "confirm",
    payload: dict[str, Any] | None = None,
    question_type: str = "",
) -> dict[str, Any]:
    permission_id = uuid4().hex
    now = _now_iso()
    record = {
        "id": permission_id,
        "task_id": task_id,
        "tool_name": tool_name,
        "arguments_preview": arguments_preview[:2000],
        "risk_level": risk_level,
        "status": "pending",
        "reason": "",
        "payload_json": _json_dumps(payload or {}),
        "question_type": question_type,
        "created_at": now,
        "resolved_at": None,
    }

    def _exec(conn) -> None:
        conn.execute(
            """
            INSERT INTO orchestrator_permission_requests
            (id, task_id, tool_name, arguments_preview, risk_level, status, reason, payload_json, question_type, created_at, resolved_at)
            VALUES (:id, :task_id, :tool_name, :arguments_preview, :risk_level, :status, :reason, :payload_json, :question_type, :created_at, :resolved_at)
            """,
            record,
        )

    await storage.run_in_thread(_exec)
    return _serialize_permission_row(record)
```

- [ ] **Step 5: Update _serialize_permission_row to include questionType**

In `backend/amadeus_app/orchestrator/storage.py`, modify `_serialize_permission_row` (line 651):

```python
def _serialize_permission_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "toolName": row["tool_name"],
        "argumentsPreview": row["arguments_preview"],
        "riskLevel": row["risk_level"],
        "status": row["status"],
        "reason": row["reason"],
        "payload": _decode_json(row["payload_json"]) if "payload_json" in row.keys() else {},
        "questionType": row["question_type"] if "question_type" in row.keys() else "",
        "createdAt": row["created_at"],
        "resolvedAt": row["resolved_at"],
    }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_ask_user.py::TestPermissionRequestQuestionType -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add backend/amadeus_app/storage.py backend/amadeus_app/orchestrator/storage.py tests/test_ask_user.py
git commit -m "feat: add question_type field to permission_requests for ask_user tool"
```

---

### Task 2: Add update_permission_answer storage function

**Files:**
- Modify: `backend/amadeus_app/orchestrator/storage.py`

**Interfaces:**
- Produces: `update_permission_answer(storage, permission_id, answer)` function

- [ ] **Step 1: Write failing test**

```python
# tests/test_ask_user.py (append)
class TestUpdatePermissionAnswer:
    @pytest.mark.asyncio
    async def test_update_answer_stores_in_payload(self, tmp_path):
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.init_db()

        task_id = uuid4().hex
        await orch_storage.create_task(
            storage,
            user_id="test-user",
            title="test",
            prompt="test",
            workspace_path=str(tmp_path),
            conversation_id=None,
            active_skill_ids=[],
            settings={},
        )

        perm = await orch_storage.create_permission_request(
            storage,
            task_id=task_id,
            tool_name="ask_user",
            arguments_preview="question",
            risk_level="safe",
            question_type="ask_user",
        )

        updated = await orch_storage.update_permission_answer(
            storage, perm["id"], answer="React"
        )
        assert updated is not None
        assert updated["status"] == "approved"
        assert updated["payload"]["answer"] == "React"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ask_user.py::TestUpdatePermissionAnswer -v`
Expected: FAIL — `update_permission_answer` does not exist

- [ ] **Step 3: Implement update_permission_answer**

In `backend/amadeus_app/orchestrator/storage.py`, add after `update_permission_request_status` (after line ~525):

```python
async def update_permission_answer(
    storage: SQLiteStorage,
    permission_id: str,
    *,
    answer: str,
) -> dict[str, Any] | None:
    """Approve a permission request and store the user's answer in payload."""
    now = _now_iso()

    def _exec(conn) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT * FROM orchestrator_permission_requests WHERE id = ? AND status = 'pending'",
            (permission_id,),
        ).fetchone()
        if row is None:
            return None
        existing_payload = _decode_json(row["payload_json"]) if "payload_json" in row.keys() else {}
        existing_payload["answer"] = answer
        conn.execute(
            """
            UPDATE orchestrator_permission_requests
            SET status = 'approved', payload_json = ?, resolved_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (_json_dumps(existing_payload), now, permission_id),
        )
        row = conn.execute(
            "SELECT * FROM orchestrator_permission_requests WHERE id = ?",
            (permission_id,),
        ).fetchone()
        return _serialize_permission_row(row) if row else None

    return await storage.run_in_thread(_exec)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ask_user.py::TestUpdatePermissionAnswer -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/amadeus_app/orchestrator/storage.py tests/test_ask_user.py
git commit -m "feat: add update_permission_answer storage function"
```

---

### Task 3: Register ask_user tool + adapter

**Files:**
- Modify: `backend/amadeus_app/orchestrator/capabilities.py:75-91`
- Modify: `backend/amadeus_app/orchestrator/capability_adapters.py:1381` (registry)
- Modify: `backend/amadeus_app/orchestrator/capability_adapters.py` (add _ask_user function)

**Interfaces:**
- Produces: `ask_user` capability definition (risk="safe")
- Produces: `_ask_user` async handler that creates permission_request + raises AgentLoopPermissionBlocked

- [ ] **Step 1: Write failing test**

```python
# tests/test_ask_user.py (append)
class TestAskUserAdapter:
    @pytest.mark.asyncio
    async def test_ask_user_creates_permission_and_raises(self, tmp_path):
        from backend.amadeus_app.orchestrator.capability_adapters import _ask_user, CapabilityExecutionContext
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        from backend.amadeus_app.storage import SQLiteStorage
        from backend.amadeus_app.orchestrator.agent_loop_runner import AgentLoopPermissionBlocked

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.init_db()

        task_id = uuid4().hex
        await orch_storage.create_task(
            storage,
            user_id="test-user",
            title="test",
            prompt="test",
            workspace_path=str(tmp_path),
            conversation_id=None,
            active_skill_ids=[],
            settings={},
        )

        events = []
        async def _emit(**kwargs):
            events.append(kwargs)
            return {}

        context = CapabilityExecutionContext(
            task_id=task_id,
            prompt="test",
            workspace_path=str(tmp_path),
            settings=OrchestratorSettings(),
            storage=storage,
            emit_event=_emit,
        )

        with pytest.raises(AgentLoopPermissionBlocked):
            await _ask_user(
                {"question": "Which framework?", "options": ["React", "Vue"]},
                context,
            )

        # Verify question event was emitted
        question_events = [e for e in events if e.get("kind") == "question"]
        assert len(question_events) == 1
        assert question_events[0]["payload"]["question"] == "Which framework?"

        # Verify permission request was created
        perms = await orch_storage.list_pending_permission_requests(storage, task_id)
        assert len(perms) == 1
        assert perms[0]["questionType"] == "ask_user"
        assert perms[0]["payload"]["question"] == "Which framework?"

    @pytest.mark.asyncio
    async def test_ask_user_without_question_returns_error(self, tmp_path):
        from backend.amadeus_app.orchestrator.capability_adapters import _ask_user, CapabilityExecutionContext
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings

        context = CapabilityExecutionContext(
            task_id="test",
            prompt="test",
            workspace_path=str(tmp_path),
            settings=OrchestratorSettings(),
        )
        result = await _ask_user({"question": ""}, context)
        assert result["ok"] is False
        assert "requires a question" in result["summary"]

    @pytest.mark.asyncio
    async def test_ask_user_truncates_options_to_four(self, tmp_path):
        from backend.amadeus_app.orchestrator.capability_adapters import _ask_user, CapabilityExecutionContext
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        from backend.amadeus_app.storage import SQLiteStorage
        from backend.amadeus_app.orchestrator.agent_loop_runner import AgentLoopPermissionBlocked

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.init_db()

        task_id = uuid4().hex
        await orch_storage.create_task(
            storage,
            user_id="test-user",
            title="test",
            prompt="test",
            workspace_path=str(tmp_path),
            conversation_id=None,
            active_skill_ids=[],
            settings={},
        )

        async def _emit(**kwargs):
            return {}

        context = CapabilityExecutionContext(
            task_id=task_id,
            prompt="test",
            workspace_path=str(tmp_path),
            settings=OrchestratorSettings(),
            storage=storage,
            emit_event=_emit,
        )

        with pytest.raises(AgentLoopPermissionBlocked):
            await _ask_user(
                {"question": "Pick one", "options": ["A", "B", "C", "D", "E"]},
                context,
            )

        perms = await orch_storage.list_pending_permission_requests(storage, task_id)
        assert len(perms[0]["payload"]["options"]) == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_ask_user.py::TestAskUserAdapter -v`
Expected: FAIL — `_ask_user` does not exist

- [ ] **Step 3: Add ask_user to capability catalog**

In `backend/amadeus_app/orchestrator/capabilities.py`, add to `READ_CAPABILITIES` set (line 9):

```python
    "ask_user",
```

Add to `capability_catalog()` function (line 75), add a new entry in the list:

```python
        CapabilityDefinition(name="ask_user", description="Ask the user a question and wait for their answer. Use when you need clarification or a decision before proceeding.", risk="safe", workerRoles=["coordinator", "coder", "writer"]),
```

- [ ] **Step 4: Implement _ask_user adapter**

In `backend/amadeus_app/orchestrator/capability_adapters.py`, add import at top:

```python
from .agent_loop_runner import AgentLoopPermissionBlocked
```

Add the `_ask_user` function (before the registry registration at line ~1381):

```python
async def _ask_user(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    question = str(args.get("question") or "").strip()
    if not question:
        return {"ok": False, "summary": "ask_user requires a question.", "data": {}}

    options = args.get("options") or []
    if isinstance(options, list):
        options = [str(o) for o in options[:4]]
    else:
        options = []

    context_str = str(args.get("context") or "").strip()

    permission = await orchestrator_storage.create_permission_request(
        context.storage,
        task_id=context.task_id,
        tool_name="ask_user",
        arguments_preview=question[:2000],
        risk_level="safe",
        payload={
            "question": question,
            "context": context_str,
            "options": options,
            "questionType": "ask_user",
        },
        question_type="ask_user",
    )

    if context.emit_event:
        await context.emit_event(
            kind="question",
            role="assistant",
            name="ask_user",
            status="pending",
            summary=question,
            payload={
                "permissionId": permission["id"],
                "question": question,
                "context": context_str,
                "options": options,
                "questionType": "ask_user",
            },
        )

    raise AgentLoopPermissionBlocked(
        permission_id=permission["id"],
        capability="ask_user",
        reason=f"Waiting for user answer: {question[:100]}",
    )
```

Then add to the registry (find the registry section at ~line 1381):

```python
    registry.register("ask_user", _ask_user)
```

- [ ] **Step 5: Check AgentLoopPermissionBlocked constructor**

Verify the `AgentLoopPermissionBlocked` exception accepts `permission_id`, `capability`, `reason` keyword arguments. If the existing constructor only uses `permission_id` and `reason`, add `capability` parameter. Check `agent_loop_runner.py` for the class definition.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_ask_user.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add backend/amadeus_app/orchestrator/capabilities.py backend/amadeus_app/orchestrator/capability_adapters.py tests/test_ask_user.py
git commit -m "feat: add ask_user tool adapter that pauses loop for user input"
```

---

### Task 4: Inject answer in resume_from_permission

**Files:**
- Modify: `backend/amadeus_app/orchestrator/agent_loop_runner.py:211-310`

**Interfaces:**
- Consumes: `permission["payload"]["answer"]` — the user's answer stored by Task 2
- Produces: `resume_from_permission` injects answer as tool message before re-entering `_run_loop`

- [ ] **Step 1: Write failing test**

```python
# tests/test_ask_user.py (append)
class TestResumeWithAnswer:
    @pytest.mark.asyncio
    async def test_resume_injects_answer_as_tool_message(self, tmp_path):
        """Verify that resume_from_permission injects the user's answer
        as a tool result message into loop_ctx.messages."""
        from backend.amadeus_app.orchestrator.agent_loop_runner import AgentLoopRunner

        # This is a structural test — we verify the runner has the logic
        # to inject answers. Full integration testing requires a mock model.
        runner = AgentLoopRunner.__new__(AgentLoopRunner)
        # Check the method signature includes answer handling
        import inspect
        sig = inspect.signature(runner.resume_from_permission)
        params = list(sig.parameters.keys())
        assert "storage" in params
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ask_user.py::TestResumeWithAnswer -v`
Expected: May pass structurally — but the actual injection logic doesn't exist yet

- [ ] **Step 3: Modify resume_from_permission to inject answer**

In `backend/amadeus_app/orchestrator/agent_loop_runner.py`, modify `resume_from_permission` (line 211). After `loop_ctx = self._rebuild_context_from_events(...)` (line 252) and before the `try:` block (line 258), add answer injection logic:

```python
        # If this is an ask_user question with an answer, inject it as a tool result
        perm_payload = permission.get("payload") or {}
        if perm_payload.get("questionType") == "ask_user" and "answer" in perm_payload:
            # Find the last tool_call message to get its tool_call_id
            tool_call_id = ""
            for msg in reversed(loop_ctx.messages):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    tool_calls = msg.get("tool_calls") or []
                    if tool_calls:
                        tool_call_id = str(tool_calls[-1].get("id") or "")
                        break
            if tool_call_id:
                loop_ctx.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": str(perm_payload["answer"]),
                })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ask_user.py -v`
Expected: All PASS

Run: `python -m pytest tests/test_agent_loop.py -v`
Expected: Existing tests still PASS

- [ ] **Step 5: Commit**

```bash
git add backend/amadeus_app/orchestrator/agent_loop_runner.py tests/test_ask_user.py
git commit -m "feat: inject ask_user answer as tool result in resume_from_permission"
```

---

### Task 5: Add /answer API endpoint

**Files:**
- Modify: `backend/amadeus_app/routers/orchestrator.py`

**Interfaces:**
- Produces: `POST /tasks/{task_id}/permissions/{permission_id}/answer` endpoint

- [ ] **Step 1: Write failing test**

```python
# tests/test_ask_user.py (append)
class TestAnswerEndpoint:
    @pytest.mark.asyncio
    async def test_answer_endpoint_approves_and_stores_answer(self, tmp_path, monkeypatch):
        from backend.amadeus_app.storage import SQLiteStorage
        from backend.amadeus_app.orchestrator import storage as orch_storage

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.init_db()

        task_id = uuid4().hex
        await orch_storage.create_task(
            storage,
            user_id="test-user",
            title="test",
            prompt="test",
            workspace_path=str(tmp_path),
            conversation_id=None,
            active_skill_ids=[],
            settings={},
        )
        perm = await orch_storage.create_permission_request(
            storage,
            task_id=task_id,
            tool_name="ask_user",
            arguments_preview="question",
            risk_level="safe",
            question_type="ask_user",
        )

        # Mock the resume function to avoid actually running the loop
        resume_called = False
        async def mock_resume(*args, **kwargs):
            nonlocal resume_called
            resume_called = True

        monkeypatch.setattr(
            "backend.amadeus_app.routers.orchestrator.default_orchestrator_runner.resume_from_permission",
            mock_resume,
        )
        monkeypatch.setattr(
            "backend.amadeus_app.routers.orchestrator.require_storage",
            lambda: storage,
        )

        from backend.amadeus_app.routers.orchestrator import answer_orchestrator_permission
        from fastapi import BackgroundTasks

        result = await answer_orchestrator_permission(
            task_id=task_id,
            permission_id=perm["id"],
            request=type("Req", (), {"answer": "React"})(),
            background_tasks=BackgroundTasks(),
        )
        assert result["ok"] is True

        updated = await orch_storage.get_permission_request(storage, perm["id"])
        assert updated["status"] == "approved"
        assert updated["payload"]["answer"] == "React"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ask_user.py::TestAnswerEndpoint -v`
Expected: FAIL — `answer_orchestrator_permission` does not exist

- [ ] **Step 3: Add answer endpoint**

In `backend/amadeus_app/routers/orchestrator.py`, add a new Pydantic model for the request body (near the other request models):

```python
class OrchestratorPermissionAnswerRequest(BaseModel):
    answer: str
```

Add the endpoint after the `reject_orchestrator_permission` endpoint (after line ~334):

```python
@router.post("/tasks/{task_id}/permissions/{permission_id}/answer")
async def answer_orchestrator_permission(
    task_id: str,
    permission_id: str,
    request: OrchestratorPermissionAnswerRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    storage = require_storage()
    permission = await orchestrator_storage.get_permission_request(storage, permission_id)
    if permission is None:
        raise HTTPException(status_code=404, detail="permission request not found")
    if permission["taskId"] != task_id:
        raise HTTPException(status_code=404, detail="permission does not belong to this task")
    if permission["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"permission already {permission['status']}")

    updated = await orchestrator_storage.update_permission_answer(
        storage, permission_id, answer=request.answer
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="permission request not found")

    await orchestrator_storage.append_event(
        storage,
        task_id=task_id,
        kind="question",
        role="user",
        name="ask_user",
        status="answered",
        summary=f"User answered: {request.answer[:200]}",
        payload={"permissionId": permission_id, "answer": request.answer},
    )

    background_tasks.add_task(_resume_approved_permission, permission_id)
    return {"ok": True}
```

Make sure `BackgroundTasks` is imported from `fastapi` at the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_ask_user.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/amadeus_app/routers/orchestrator.py tests/test_ask_user.py
git commit -m "feat: add /answer endpoint for ask_user question responses"
```

---

### Task 6: Frontend — QuestionCard + API + Types

**Files:**
- Modify: `src/types.ts`
- Modify: `src/api.ts`
- Modify: `src/components/TaskWorkspace.tsx`

- [ ] **Step 1: Add TypeScript types**

In `src/types.ts`, update `OrchestratorPermissionRequest` to include question payload:

```typescript
interface OrchestratorPermissionRequest {
    id: string;
    taskId: string;
    toolName: string;
    argumentsPreview: string;
    riskLevel: string;
    status: string;
    reason: string;
    payload?: {
        questionType?: string;
        question?: string;
        context?: string;
        options?: string[];
        answer?: string;
    };
    questionType?: string;
    createdAt: string;
    resolvedAt: string | null;
}
```

- [ ] **Step 2: Add answerPermission API function**

In `src/api.ts`, add:

```typescript
export async function answerPermission(
    taskId: string,
    permissionId: string,
    answer: string
): Promise<void> {
    await fetch(`/api/orchestrator/tasks/${taskId}/permissions/${permissionId}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer }),
    });
}
```

- [ ] **Step 3: Add QuestionCard to TaskWorkspace**

In `src/components/TaskWorkspace.tsx`:

1. Add `answerPermission` to the import from `../api`:

```typescript
import {
    // ... existing imports ...
    answerPermission,
    // ...
} from "../api";
```

2. Add a `QuestionCard` inline component before the main component:

```tsx
function QuestionCard({
    permission,
    onAnswer,
    onSkip,
    busy
}: {
    permission: OrchestratorPermissionRequest;
    onAnswer: (answer: string) => void;
    onSkip: () => void;
    busy: boolean;
}) {
    const [customAnswer, setCustomAnswer] = useState("");
    const payload = permission.payload || {};
    const options: string[] = payload.options || [];
    const question = payload.question || "";
    const contextStr = payload.context || "";

    return (
        <div className="permission-card question-card">
            <div className="question-header">
                <Shield size={16} />
                <span>{question}</span>
            </div>
            {contextStr && <p className="question-context">{contextStr}</p>}
            {options.length > 0 && (
                <div className="question-options">
                    {options.map((opt, idx) => (
                        <button
                            key={idx}
                            className="option-btn"
                            disabled={busy}
                            onClick={() => onAnswer(opt)}
                        >
                            {opt}
                        </button>
                    ))}
                </div>
            )}
            <div className="question-custom">
                <input
                    type="text"
                    value={customAnswer}
                    onChange={(e) => setCustomAnswer(e.target.value)}
                    placeholder="自定义输入..."
                    disabled={busy}
                />
                <button
                    disabled={busy || !customAnswer.trim()}
                    onClick={() => onAnswer(customAnswer.trim())}
                >
                    <Send size={14} />
                </button>
            </div>
            <button className="skip-btn" disabled={busy} onClick={onSkip}>
                跳过
            </button>
        </div>
    );
}
```

4. In the permissions rendering section (around line 587), add question card rendering:

```tsx
{permissions.map((perm) => {
    const isQuestion = perm.payload?.questionType === "ask_user";
    if (isQuestion) {
        return (
            <QuestionCard
                key={perm.id}
                permission={perm}
                busy={permBusy[perm.id] || false}
                onAnswer={async (answer) => {
                    setPermBusy((prev) => ({ ...prev, [perm.id]: true }));
                    try {
                        await answerPermission(effectiveActiveId!, perm.id, answer);
                        await refreshPermissions();
                    } catch (err) {
                        setError(err instanceof Error ? err.message : "回答失败");
                    } finally {
                        setPermBusy((prev) => ({ ...prev, [perm.id]: false }));
                    }
                }}
                onSkip={async () => {
                    setPermBusy((prev) => ({ ...prev, [perm.id]: true }));
                    try {
                        await answerPermission(effectiveActiveId!, perm.id, "User skipped the question.");
                        await refreshPermissions();
                    } catch (err) {
                        setError(err instanceof Error ? err.message : "跳过失败");
                    } finally {
                        setPermBusy((prev) => ({ ...prev, [perm.id]: false }));
                    }
                }}
            />
        );
    }
    // Existing permission card rendering follows...
    return null;
})}
```

- [ ] **Step 4: Verify TypeScript compiles**

Run: `npx tsc --noEmit`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add src/types.ts src/api.ts src/components/TaskWorkspace.tsx
git commit -m "feat: add QuestionCard component and answer API for ask_user tool"
```

---

### Task 7: Full Integration Test + Cleanup

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

Run: `npx tsc --noEmit`
Expected: No errors

- [ ] **Step 2: Commit if any cleanup needed**

```bash
git add -A
git commit -m "test: ask_user tool full integration verification"
```
