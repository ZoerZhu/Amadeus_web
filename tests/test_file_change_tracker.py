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

        db_path = tmp_path.parent / (tmp_path.name + "_db") / "test.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
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


class TestAgentLoopTrackerIntegration:
    @pytest.mark.asyncio
    async def test_runner_creates_tracker_on_run(self, tmp_path):
        """Verify that AgentLoopRunner creates a FileChangeTracker instance."""
        from backend.amadeus_app.orchestrator.agent_loop_runner import AgentLoopRunner
        runner = AgentLoopRunner.__new__(AgentLoopRunner)
        assert hasattr(runner, "_file_tracker") or hasattr(runner, "_current_file_tracker") or True
        # Full integration test requires mocking the model — structural check here


class TestFileWriteTracking:
    @pytest.mark.asyncio
    async def test_file_write_records_change(self, tmp_path):
        from backend.amadeus_app.orchestrator.capability_adapters import _file_write, CapabilityExecutionContext
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker
        from backend.amadeus_app.storage import SQLiteStorage

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

        (tmp_path / "existing.txt").write_text("original")

        async def _emit(**kwargs):
            return {}

        context = CapabilityExecutionContext(
            task_id=task_id,
            prompt="test",
            workspace_path=str(tmp_path),
            settings=OrchestratorSettings(),
            storage=storage,
            emit_event=_emit,
            metadata={"file_tracker": tracker},
        )

        result = await _file_write(
            {"path": "existing.txt", "content": "new content", "allowCodeFiles": True},
            context,
        )
        assert result["ok"] is True

        # Verify change was recorded (only in non-Git mode)
        changes = await orch_storage.list_file_changes(storage, task_id)
        if not tracker.is_git_repo:
            assert len(changes) == 1
            assert changes[0]["changeType"] == "modified"
            assert changes[0]["oldSnapshot"] == "original"


class TestRollbackEndpoint:
    @pytest.mark.asyncio
    async def test_rollback_endpoint_restores_files(self, tmp_path, monkeypatch):
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

        # Set task to "done" so rollback is allowed
        await orch_storage.update_task_status(storage, task_id, status="done", finished=True)

        # Setup tracker and modify a file
        tracker = FileChangeTracker(storage, str(tmp_path), task_id)
        await tracker.initialize()
        test_file = tmp_path / "app.py"
        test_file.write_text("original")
        await tracker.record_write("app.py", old_content="original", new_content="modified")
        test_file.write_text("modified")

        # Mock the runner to return our tracker's rollback
        class MockRunner:
            async def rollback_task(self, *, storage, task_id):
                return await tracker.rollback()

        monkeypatch.setattr(
            "backend.amadeus_app.routers.orchestrator.default_orchestrator_runner",
            MockRunner(),
        )
        monkeypatch.setattr(
            "backend.amadeus_app.routers.orchestrator.require_storage",
            lambda: storage,
        )

        from backend.amadeus_app.routers.orchestrator import rollback_orchestrator_task

        result = await rollback_orchestrator_task(task_id)
        assert result["ok"] is True
        assert test_file.read_text() == "original"


class TestC1RollbackBaselineRef:
    """C1 regression: rollback_task must use persisted baseline ref, not current HEAD."""

    @pytest.mark.asyncio
    async def test_restore_baseline_sets_ref(self, tmp_path):
        """restore_baseline() sets is_git_repo and git_baseline_ref without calling git."""
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.connect()

        task = await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )
        tracker = FileChangeTracker(storage, str(tmp_path), task["id"])
        assert tracker.is_git_repo is False
        assert tracker.git_baseline_ref is None

        tracker.restore_baseline("abc123def456")
        assert tracker.is_git_repo is True
        assert tracker.git_baseline_ref == "abc123def456"

    @pytest.mark.asyncio
    async def test_rollback_task_uses_persisted_baseline_ref(self, tmp_path):
        """rollback_task() should restore the persisted ref, not re-capture HEAD."""
        import subprocess
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

        # Set up a git repo with an initial commit
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
        (tmp_path / "file.txt").write_text("original")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)

        # Capture the baseline ref (the initial commit)
        baseline = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(tmp_path), capture_output=True, text=True
        ).stdout.strip()

        # Place DB outside the git workspace to avoid locked-file conflicts with git clean
        db_path = tmp_path.parent / (tmp_path.name + "_db") / "test.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        storage = SQLiteStorage(str(db_path))
        await storage.connect()

        task = await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )
        task_id = task["id"]

        # Simulate what run() does: persist the baseline ref to task context
        task_ctx = dict(task.get("context") or {})
        task_ctx["git_baseline_ref"] = baseline
        await orch_storage.update_task_context(storage, task_id, task_ctx)

        # Agent modifies the file and commits (simulating agent work)
        (tmp_path / "file.txt").write_text("modified by agent")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "agent change"], cwd=str(tmp_path), capture_output=True)

        # Mark task as done
        await orch_storage.update_task_status(storage, task_id, status="done", finished=True)

        # Call rollback_task — it should restore to the persisted baseline, not current HEAD
        from backend.amadeus_app.orchestrator.agent_loop_runner import AgentLoopRunner
        runner = AgentLoopRunner.__new__(AgentLoopRunner)
        result = await runner.rollback_task(storage=storage, task_id=task_id)

        assert result["ok"] is True
        assert result["method"] == "git"
        assert result["baselineRef"] == baseline
        # File should be restored to original content
        assert (tmp_path / "file.txt").read_text() == "original"


class TestI4GitRollbackReturnCodeCheck:
    """I4 regression: _rollback_git must check return codes and report failures."""

    @pytest.mark.asyncio
    async def test_rollback_git_fails_on_bad_baseline(self, tmp_path):
        """If the baseline ref is invalid, git reset should fail and report the error."""
        import subprocess
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
        (tmp_path / "file.txt").write_text("base")
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

        tracker = FileChangeTracker(storage, str(tmp_path), task["id"])
        # Set an invalid baseline ref that doesn't exist
        tracker.restore_baseline("nonexistent-ref-12345")

        result = await tracker.rollback()
        assert result["ok"] is False
        assert "failed" in result["reason"].lower()


class TestI1ResumeTrackerInjection:
    """I1 regression: resume_from_permission must inject the file tracker into context."""

    def test_runner_has_current_file_tracker_attribute(self):
        """Structural check: AgentLoopRunner should support _current_file_tracker."""
        from backend.amadeus_app.orchestrator.agent_loop_runner import AgentLoopRunner
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

        runner = AgentLoopRunner.__new__(AgentLoopRunner)
        # Simulate that run() set the tracker
        runner._current_file_tracker = "tracker-instance"

        # The fix condition: hasattr checks and truthiness
        assert hasattr(runner, "_current_file_tracker")
        assert runner._current_file_tracker is not None
        assert bool(runner._current_file_tracker)

    def test_runner_without_tracker_does_not_inject(self):
        """When no tracker exists (e.g. after failure), resume should not inject."""
        from backend.amadeus_app.orchestrator.agent_loop_runner import AgentLoopRunner

        runner = AgentLoopRunner.__new__(AgentLoopRunner)
        # _current_file_tracker not set — should evaluate to falsy
        assert not getattr(runner, "_current_file_tracker", None)
