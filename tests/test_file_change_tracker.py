from __future__ import annotations
import pytest
from uuid import uuid4

from backend.amadeus_app.storage import SQLiteStorage
from backend.amadeus_app.orchestrator import storage as orch_storage


class TestFileChangesStorage:
    @pytest.mark.asyncio
    async def test_create_file_change(self, tmp_path):
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

        change = await orch_storage.create_file_change(
            storage,
            task_id=task_id,
            seq=1,
            relative_path="src/app.py",
            change_type="modified",
            diff_text="--- a/src/app.py\n+++ b/src/app.py\n@@ -1,3 +1,3 @@\n-old\n+new",
            old_snapshot="old content",
            source="file_write",
        )
        assert change["id"]
        assert change["relativePath"] == "src/app.py"
        assert change["changeType"] == "modified"

    @pytest.mark.asyncio
    async def test_list_file_changes(self, tmp_path):
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

        await orch_storage.create_file_change(
            storage, task_id=task_id, seq=1, relative_path="a.py",
            change_type="created", diff_text="", old_snapshot="", source="file_write"
        )
        await orch_storage.create_file_change(
            storage, task_id=task_id, seq=2, relative_path="b.py",
            change_type="modified", diff_text="diff", old_snapshot="old", source="file_edit"
        )

        changes = await orch_storage.list_file_changes(storage, task_id)
        assert len(changes) == 2
        assert changes[0]["seq"] == 1
        assert changes[1]["seq"] == 2

    @pytest.mark.asyncio
    async def test_list_file_changes_empty(self, tmp_path):
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

        changes = await orch_storage.list_file_changes(storage, task_id)
        assert changes == []
