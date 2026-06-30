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


class TestFileChangeTracker:
    @pytest.mark.asyncio
    async def test_initialize_non_git_workspace(self, tmp_path):
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.connect()

        task = await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )
        task_id = task["id"]

        tracker = FileChangeTracker(storage, str(tmp_path), task_id)
        await tracker.initialize()
        assert tracker.is_git_repo is False

    @pytest.mark.asyncio
    async def test_initialize_git_workspace(self, tmp_path):
        import subprocess
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
        (tmp_path / "initial.txt").write_text("initial")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.connect()

        task = await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )
        task_id = task["id"]

        tracker = FileChangeTracker(storage, str(tmp_path), task_id)
        await tracker.initialize()
        assert tracker.is_git_repo is True
        assert tracker.git_baseline_ref is not None

    @pytest.mark.asyncio
    async def test_record_write_non_git(self, tmp_path):
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.connect()

        task = await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )
        task_id = task["id"]

        tracker = FileChangeTracker(storage, str(tmp_path), task_id)
        await tracker.initialize()

        test_file = tmp_path / "app.py"
        test_file.write_text("old content")

        await tracker.record_write("app.py", old_content="old content", new_content="new content")

        changes = await orch_storage.list_file_changes(storage, task_id)
        assert len(changes) == 1
        assert changes[0]["changeType"] == "modified"
        assert changes[0]["oldSnapshot"] == "old content"

    @pytest.mark.asyncio
    async def test_record_write_created_file(self, tmp_path):
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.connect()

        task = await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )
        task_id = task["id"]

        tracker = FileChangeTracker(storage, str(tmp_path), task_id)
        await tracker.initialize()

        await tracker.record_write("new_file.py", old_content=None, new_content="new file content")

        changes = await orch_storage.list_file_changes(storage, task_id)
        assert len(changes) == 1
        assert changes[0]["changeType"] == "created"

    @pytest.mark.asyncio
    async def test_rollback_non_git(self, tmp_path):
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.connect()

        task = await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )
        task_id = task["id"]

        tracker = FileChangeTracker(storage, str(tmp_path), task_id)
        await tracker.initialize()

        test_file = tmp_path / "app.py"
        test_file.write_text("original")
        await tracker.record_write("app.py", old_content="original", new_content="modified")
        test_file.write_text("modified")

        new_file = tmp_path / "new.py"
        new_file.write_text("new")
        await tracker.record_write("new.py", old_content=None, new_content="new")

        result = await tracker.rollback()
        assert result["ok"] is True

        assert test_file.read_text() == "original"
        assert not new_file.exists()

    @pytest.mark.asyncio
    async def test_rollback_git(self, tmp_path):
        import subprocess
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
        (tmp_path / "base.txt").write_text("base")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.connect()

        task = await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )
        task_id = task["id"]

        tracker = FileChangeTracker(storage, str(tmp_path), task_id)
        await tracker.initialize()

        (tmp_path / "base.txt").write_text("modified by agent")
        (tmp_path / "new.txt").write_text("new file by agent")

        changes = await tracker.collect_git_diff()
        assert len(changes) >= 1

        result = await tracker.rollback()
        assert result["ok"] is True

        assert (tmp_path / "base.txt").read_text() == "base"
        assert not (tmp_path / "new.txt").exists()
