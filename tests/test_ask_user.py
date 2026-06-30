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
