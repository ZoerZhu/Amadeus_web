from __future__ import annotations

import pytest

from backend.amadeus_app.orchestrator import storage as orch_storage
from backend.amadeus_app.storage import SQLiteStorage


class TestPermissionRequestQuestionType:
    @pytest.mark.asyncio
    async def test_create_permission_with_question_type(self, tmp_path):
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.connect()

        task = await orch_storage.create_task(
            storage,
            user_id="test-user",
            title="test",
            prompt="test",
            workspace_path=str(tmp_path),
            conversation_id=None,
            active_skill_ids=[],
            settings={},
        )
        task_id = task["id"]

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
        await storage.connect()

        task = await orch_storage.create_task(
            storage,
            user_id="test-user",
            title="test",
            prompt="test",
            workspace_path=str(tmp_path),
            conversation_id=None,
            active_skill_ids=[],
            settings={},
        )
        task_id = task["id"]

        perm = await orch_storage.create_permission_request(
            storage,
            task_id=task_id,
            tool_name="file_write",
            arguments_preview="write a.py",
            risk_level="confirm",
        )
        assert perm["questionType"] == ""


class TestUpdatePermissionAnswer:
    @pytest.mark.asyncio
    async def test_update_answer_stores_in_payload(self, tmp_path):
        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.connect()

        task = await orch_storage.create_task(
            storage,
            user_id="test-user",
            title="test",
            prompt="test",
            workspace_path=str(tmp_path),
            conversation_id=None,
            active_skill_ids=[],
            settings={},
        )
        task_id = task["id"]

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


class TestAskUserAdapter:
    @pytest.mark.asyncio
    async def test_ask_user_creates_permission_and_raises(self, tmp_path):
        from backend.amadeus_app.orchestrator.capability_adapters import _ask_user, CapabilityExecutionContext
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        from backend.amadeus_app.storage import SQLiteStorage
        from backend.amadeus_app.orchestrator.agent_loop_runner import AgentLoopPermissionBlocked

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.connect()

        task = await orch_storage.create_task(
            storage, user_id="test-user", title="test", prompt="test",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )
        task_id = task["id"]

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

        question_events = [e for e in events if e.get("kind") == "question"]
        assert len(question_events) == 1
        assert question_events[0]["payload"]["question"] == "Which framework?"

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
        await storage.connect()

        task = await orch_storage.create_task(
            storage, user_id="test-user", title="test", prompt="test",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )
        task_id = task["id"]

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
