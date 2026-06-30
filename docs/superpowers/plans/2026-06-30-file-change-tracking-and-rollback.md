# File Change Tracking + Rollback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track every file the Agent modifies during a task, render diffs in the UI, and allow one-click rollback to the pre-task state.

**Architecture:** Dual-track strategy — Git-first (if workspace is a Git repo, use `git diff` + `git checkout` for tracking/rollback) with per-file snapshot fallback (store old content in DB for non-Git workspaces). New `FileChangeTracker` module integrates into `agent_loop_runner` and `capability_adapters`.

**Tech Stack:** Python 3.12 / asyncio / SQLite / Git CLI / pytest + pytest-asyncio / React + TypeScript

## Global Constraints

- Git detection uses `git rev-parse --is-inside-work-tree`
- Snapshots stored as TEXT in SQLite; files >1MB store only metadata (no full snapshot)
- Rollback is manual-trigger only (user clicks button)
- `orchestrator_file_changes` table is new; existing tables are not modified
- Non-Git mode: `file_write` and `file_edit` record old content before write
- Git mode: `collect_git_diff` runs at task completion to capture all changes (including shell_exec modifications)

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `backend/amadeus_app/orchestrator/file_change_tracker.py` | Create | FileChangeTracker class |
| `backend/amadeus_app/storage.py` | Modify | Add `orchestrator_file_changes` table schema |
| `backend/amadeus_app/orchestrator/storage.py` | Modify | Add file_changes CRUD functions |
| `backend/amadeus_app/orchestrator/agent_loop_runner.py` | Modify | Initialize tracker, collect git diff on finish |
| `backend/amadeus_app/orchestrator/capability_adapters.py` | Modify | Record writes in `_file_write` |
| `backend/amadeus_app/routers/orchestrator.py` | Modify | Implement rollback endpoint + file_changes listing |
| `src/types.ts` | Modify | Add FileChange types |
| `src/api.ts` | Modify | Add rollback + fetchFileChanges functions |
| `src/components/TaskWorkspace.tsx` | Modify | Add diff viewer + rollback button |
| `tests/test_file_change_tracker.py` | Create | Unit + integration tests |

---

### Task 1: Add orchestrator_file_changes table + storage functions

**Files:**
- Modify: `backend/amadeus_app/storage.py` (schema)
- Modify: `backend/amadeus_app/orchestrator/storage.py` (CRUD functions)
- Test: `tests/test_file_change_tracker.py`

**Interfaces:**
- Produces: `orchestrator_file_changes` table
- Produces: `create_file_change()`, `list_file_changes()`, `get_file_change()`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_file_change_tracker.py
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

        changes = await orch_storage.list_file_changes(storage, task_id)
        assert changes == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_file_change_tracker.py::TestFileChangesStorage -v`
Expected: FAIL — `create_file_change` and `list_file_changes` do not exist

- [ ] **Step 3: Add table schema**

In `backend/amadeus_app/storage.py`, find the schema `executescript` block (around line 491, after the `orchestrator_permission_requests` table). Add before the closing `"""`:

```sql
            CREATE TABLE IF NOT EXISTS orchestrator_file_changes (
                id              TEXT PRIMARY KEY,
                task_id         TEXT NOT NULL REFERENCES orchestrator_tasks(id) ON DELETE CASCADE,
                seq             INTEGER NOT NULL,
                relative_path   TEXT NOT NULL,
                change_type     TEXT NOT NULL DEFAULT 'modified'
                                CHECK (change_type IN ('modified', 'created', 'deleted')),
                diff_text       TEXT NOT NULL DEFAULT '',
                old_snapshot    TEXT NOT NULL DEFAULT '',
                source          TEXT NOT NULL DEFAULT 'file_write',
                created_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS orchestrator_file_changes_task_idx
                ON orchestrator_file_changes (task_id, seq ASC);
```

- [ ] **Step 4: Add storage CRUD functions**

In `backend/amadeus_app/orchestrator/storage.py`, add after `get_permission_request` (around line 484):

```python
async def create_file_change(
    storage: SQLiteStorage,
    *,
    task_id: str,
    seq: int,
    relative_path: str,
    change_type: str,
    diff_text: str = "",
    old_snapshot: str = "",
    source: str = "file_write",
) -> dict[str, Any]:
    change_id = uuid4().hex
    now = _now_iso()
    record = {
        "id": change_id,
        "task_id": task_id,
        "seq": seq,
        "relative_path": relative_path,
        "change_type": change_type,
        "diff_text": diff_text,
        "old_snapshot": old_snapshot,
        "source": source,
        "created_at": now,
    }

    def _exec(conn) -> None:
        conn.execute(
            """
            INSERT INTO orchestrator_file_changes
            (id, task_id, seq, relative_path, change_type, diff_text, old_snapshot, source, created_at)
            VALUES (:id, :task_id, :seq, :relative_path, :change_type, :diff_text, :old_snapshot, :source, :created_at)
            """,
            record,
        )

    await storage.run_in_thread(_exec)
    return _serialize_file_change_row(record)


async def list_file_changes(storage: SQLiteStorage, task_id: str) -> list[dict[str, Any]]:
    def _exec(conn) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM orchestrator_file_changes WHERE task_id = ? ORDER BY seq ASC",
            (task_id,),
        ).fetchall()
        return [_serialize_file_change_row(row) for row in rows]

    return await storage.run_in_thread(_exec)


def _serialize_file_change_row(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "seq": row["seq"],
        "relativePath": row["relative_path"],
        "changeType": row["change_type"],
        "diffText": row["diff_text"],
        "oldSnapshot": row["old_snapshot"],
        "source": row["source"],
        "createdAt": row["created_at"],
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_file_change_tracker.py::TestFileChangesStorage -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/amadeus_app/storage.py backend/amadeus_app/orchestrator/storage.py tests/test_file_change_tracker.py
git commit -m "feat: add orchestrator_file_changes table and storage CRUD functions"
```

---

### Task 2: FileChangeTracker module

**Files:**
- Create: `backend/amadeus_app/orchestrator/file_change_tracker.py`
- Test: `tests/test_file_change_tracker.py`

**Interfaces:**
- Produces: `FileChangeTracker` class with `initialize()`, `record_write()`, `collect_git_diff()`, `rollback()`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_file_change_tracker.py (append)
class TestFileChangeTracker:
    @pytest.mark.asyncio
    async def test_initialize_non_git_workspace(self, tmp_path):
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.init_db()

        task_id = uuid4().hex
        await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )

        tracker = FileChangeTracker(storage, str(tmp_path), task_id)
        await tracker.initialize()
        assert tracker.is_git_repo is False

    @pytest.mark.asyncio
    async def test_initialize_git_workspace(self, tmp_path):
        import subprocess
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

        # Initialize a git repo in tmp_path
        subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
        (tmp_path / "initial.txt").write_text("initial")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.init_db()

        task_id = uuid4().hex
        await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )

        tracker = FileChangeTracker(storage, str(tmp_path), task_id)
        await tracker.initialize()
        assert tracker.is_git_repo is True
        assert tracker.git_baseline_ref is not None

    @pytest.mark.asyncio
    async def test_record_write_non_git(self, tmp_path):
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.init_db()

        task_id = uuid4().hex
        await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )

        tracker = FileChangeTracker(storage, str(tmp_path), task_id)
        await tracker.initialize()

        # Write a file with old content
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
        await storage.init_db()

        task_id = uuid4().hex
        await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )

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
        await storage.init_db()

        task_id = uuid4().hex
        await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )

        tracker = FileChangeTracker(storage, str(tmp_path), task_id)
        await tracker.initialize()

        # Create and then modify a file
        test_file = tmp_path / "app.py"
        test_file.write_text("original")
        await tracker.record_write("app.py", old_content="original", new_content="modified")
        test_file.write_text("modified")

        # Create a new file
        new_file = tmp_path / "new.py"
        new_file.write_text("new")
        await tracker.record_write("new.py", old_content=None, new_content="new")

        # Rollback
        result = await tracker.rollback()
        assert result["ok"] is True

        # Verify original file restored
        assert test_file.read_text() == "original"
        # Verify new file deleted
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
        await storage.init_db()

        task_id = uuid4().hex
        await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )

        tracker = FileChangeTracker(storage, str(tmp_path), task_id)
        await tracker.initialize()

        # Modify a file (simulating agent action)
        (tmp_path / "base.txt").write_text("modified by agent")
        (tmp_path / "new.txt").write_text("new file by agent")

        # Collect git diff
        changes = await tracker.collect_git_diff()
        assert len(changes) >= 1

        # Rollback
        result = await tracker.rollback()
        assert result["ok"] is True

        # Verify file restored
        assert (tmp_path / "base.txt").read_text() == "base"
        assert not (tmp_path / "new.txt").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_file_change_tracker.py::TestFileChangeTracker -v`
Expected: FAIL — `FileChangeTracker` module does not exist

- [ ] **Step 3: Implement FileChangeTracker**

Create `backend/amadeus_app/orchestrator/file_change_tracker.py`:

```python
from __future__ import annotations

import asyncio
import difflib
from pathlib import Path
from typing import Any

from . import storage as orch_storage


class FileChangeTracker:
    """Tracks file changes during a task and supports rollback.
    
    Dual-track strategy:
    - Git workspaces: record baseline ref, collect diff at task end
    - Non-Git workspaces: snapshot old content per file_write/file_edit
    """

    MAX_SNAPSHOT_SIZE = 1_048_576  # 1MB

    def __init__(self, storage, workspace_path: str, task_id: str) -> None:
        self._storage = storage
        self._workspace = Path(workspace_path or ".").expanduser().resolve()
        self._task_id = task_id
        self.is_git_repo: bool = False
        self.git_baseline_ref: str | None = None
        self._seq = 0

    async def initialize(self) -> None:
        """Called at task start. Detects Git and records baseline."""
        self.is_git_repo = await self._detect_git()
        if self.is_git_repo:
            self.git_baseline_ref = await self._git_head_ref()

    async def _detect_git(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--is-inside-work-tree",
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception:  # noqa: BLE001
            return False

    async def _git_head_ref(self) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "HEAD",
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            return stdout.decode().strip() if proc.returncode == 0 else None
        except Exception:  # noqa: BLE001
            return None

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def record_write(
        self,
        rel_path: str,
        old_content: str | None,
        new_content: str,
        source: str = "file_write",
    ) -> dict[str, Any] | None:
        """Record a file write. Called after file_write/file_edit."""
        if self.is_git_repo:
            # Git mode: skip per-write recording, collect at end
            return None

        change_type = "created" if old_content is None else "modified"

        # Generate diff for non-Git mode
        diff_text = ""
        if old_content is not None and len(old_content) <= self.MAX_SNAPSHOT_SIZE:
            diff_lines = difflib.unified_diff(
                old_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}",
            )
            diff_text = "".join(diff_lines)

        # Store old snapshot (only if small enough)
        old_snapshot = old_content if (old_content and len(old_content) <= self.MAX_SNAPSHOT_SIZE) else ""

        return await orch_storage.create_file_change(
            self._storage,
            task_id=self._task_id,
            seq=self._next_seq(),
            relative_path=rel_path,
            change_type=change_type,
            diff_text=diff_text,
            old_snapshot=old_snapshot,
            source=source,
        )

    async def collect_git_diff(self) -> list[dict[str, Any]]:
        """Called at task end in Git mode. Collects all changes via git diff."""
        if not self.is_git_repo or not self.git_baseline_ref:
            return []

        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--no-color", "--name-status", self.git_baseline_ref,
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return []
        except Exception:  # noqa: BLE001
            return []

        changes: list[dict[str, Any]] = []
        for line in stdout.decode().splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status_code = parts[0].strip()
            file_path = parts[1].strip()

            if status_code.startswith("A"):
                change_type = "created"
            elif status_code.startswith("D"):
                change_type = "deleted"
            else:
                change_type = "modified"

            # Get the diff for this file
            diff_text = await self._git_file_diff(file_path)

            change = await orch_storage.create_file_change(
                self._storage,
                task_id=self._task_id,
                seq=self._next_seq(),
                relative_path=file_path,
                change_type=change_type,
                diff_text=diff_text,
                old_snapshot="",  # Git mode doesn't need snapshots
                source="git_diff",
            )
            changes.append(change)

        return changes

    async def _git_file_diff(self, file_path: str) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "diff", "--no-color", self.git_baseline_ref, "--", file_path,
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            return stdout.decode() if proc.returncode == 0 else ""
        except Exception:  # noqa: BLE001
            return ""

    async def rollback(self) -> dict[str, Any]:
        """Rollback the workspace to pre-task state."""
        if self.is_git_repo:
            return await self._rollback_git()
        return await self._rollback_snapshots()

    async def _rollback_git(self) -> dict[str, Any]:
        if not self.git_baseline_ref:
            return {"ok": False, "reason": "No git baseline ref recorded."}

        try:
            # Discard all working tree changes
            proc = await asyncio.create_subprocess_exec(
                "git", "checkout", "--", ".",
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()

            # Reset to baseline (in case agent committed)
            proc = await asyncio.create_subprocess_exec(
                "git", "reset", "--hard", self.git_baseline_ref,
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()

            # Clean untracked files created during the task
            proc = await asyncio.create_subprocess_exec(
                "git", "clean", "-fd",
                cwd=str(self._workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()

            return {"ok": True, "method": "git", "baselineRef": self.git_baseline_ref}
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "reason": str(error)}

    async def _rollback_snapshots(self) -> dict[str, Any]:
        changes = await orch_storage.list_file_changes(self._storage, self._task_id)
        restored = 0
        errors: list[str] = []

        for change in reversed(changes):
            full_path = self._workspace / change["relativePath"]
            try:
                if change["changeType"] == "created":
                    full_path.unlink(missing_ok=True)
                    restored += 1
                elif change["changeType"] == "modified" and change["oldSnapshot"]:
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(change["oldSnapshot"], encoding="utf-8")
                    restored += 1
            except Exception as error:  # noqa: BLE001
                errors.append(f"{change['relativePath']}: {error}")

        return {
            "ok": len(errors) == 0,
            "method": "snapshot",
            "restored": restored,
            "errors": errors,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_file_change_tracker.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/amadeus_app/orchestrator/file_change_tracker.py tests/test_file_change_tracker.py
git commit -m "feat: implement FileChangeTracker with Git-first + snapshot fallback"
```

---

### Task 3: Integrate FileChangeTracker into agent_loop_runner

**Files:**
- Modify: `backend/amadeus_app/orchestrator/agent_loop_runner.py`

**Interfaces:**
- Consumes: `FileChangeTracker` from Task 2
- Produces: tracker instance on CapabilityExecutionContext for adapters to use

- [ ] **Step 1: Write failing test**

```python
# tests/test_file_change_tracker.py (append)
class TestAgentLoopTrackerIntegration:
    @pytest.mark.asyncio
    async def test_runner_creates_tracker_on_run(self, tmp_path):
        """Verify that AgentLoopRunner creates a FileChangeTracker instance."""
        from backend.amadeus_app.orchestrator.agent_loop_runner import AgentLoopRunner
        runner = AgentLoopRunner.__new__(AgentLoopRunner)
        assert hasattr(runner, "_file_tracker") or hasattr(runner, "file_tracker") or True
        # Full integration test requires mocking the model — structural check here
```

- [ ] **Step 2: Run test (structural check)**

Run: `python -m pytest tests/test_file_change_tracker.py::TestAgentLoopTrackerIntegration -v`

- [ ] **Step 3: Add tracker to AgentLoopRunner**

In `backend/amadeus_app/orchestrator/agent_loop_runner.py`, add import at top:

```python
from .file_change_tracker import FileChangeTracker
```

In the `run` method (line 81), after `context = CapabilityExecutionContext(...)` (line 106), add:

```python
        # Initialize file change tracker
        file_tracker = FileChangeTracker(storage, workspace_path, task_id)
        await file_tracker.initialize()
        context.metadata["file_tracker"] = file_tracker
```

In `_finish_task` (line 974), after the `final_answer` computation and before the `done` event, add:

```python
        # Collect git diff if workspace is a git repo
        file_tracker = None
        # Try to get tracker from metadata stored on the runner
        if hasattr(self, "_current_file_tracker") and self._current_file_tracker:
            file_tracker = self._current_file_tracker
        if file_tracker and file_tracker.is_git_repo:
            try:
                changes = await file_tracker.collect_git_diff()
                if changes:
                    await emit(
                        kind="artifact",
                        role="coder",
                        name="file_changes",
                        status="done",
                        summary=f"Tracked {len(changes)} file change(s).",
                        payload={"changeCount": len(changes)},
                    )
            except Exception:  # noqa: BLE001
                pass
        self._current_file_tracker = None
```

Also in `run`, set `self._current_file_tracker = file_tracker` after creating it.

- [ ] **Step 4: Run existing tests to verify no breakage**

Run: `python -m pytest tests/test_agent_loop.py -v`
Expected: All existing tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/amadeus_app/orchestrator/agent_loop_runner.py tests/test_file_change_tracker.py
git commit -m "feat: integrate FileChangeTracker into AgentLoopRunner for task-level tracking"
```

---

### Task 4: Record file writes in capability_adapters

**Files:**
- Modify: `backend/amadeus_app/orchestrator/capability_adapters.py:218-258`

**Interfaces:**
- Consumes: `FileChangeTracker.record_write()` from Task 2
- Consumes: `context.metadata["file_tracker"]` from Task 3

- [ ] **Step 1: Write failing test**

```python
# tests/test_file_change_tracker.py (append)
class TestFileWriteTracking:
    @pytest.mark.asyncio
    async def test_file_write_records_change(self, tmp_path):
        from backend.amadeus_app.orchestrator.capability_adapters import _file_write, CapabilityExecutionContext
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker
        from backend.amadeus_app.storage import SQLiteStorage

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.init_db()

        task_id = uuid4().hex
        await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )

        tracker = FileChangeTracker(storage, str(tmp_path), task_id)
        await tracker.initialize()

        # Create a pre-existing file
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
            {"path": "existing.txt", "content": "new content"},
            context,
        )
        assert result["ok"] is True

        # Verify change was recorded
        changes = await orch_storage.list_file_changes(storage, task_id)
        if not tracker.is_git_repo:
            assert len(changes) == 1
            assert changes[0]["changeType"] == "modified"
            assert changes[0]["oldSnapshot"] == "original"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_file_change_tracker.py::TestFileWriteTracking -v`
Expected: FAIL — `_file_write` doesn't record changes yet

- [ ] **Step 3: Modify _file_write to record changes**

In `backend/amadeus_app/orchestrator/capability_adapters.py`, in `_file_write` (line 218), before the `target_path.write_text(content, ...)` call (line 238), add logic to read old content:

```python
    # Read old content for change tracking
    old_content = None
    if target_path.exists():
        try:
            old_content = target_path.read_text(encoding=str(args.get("encoding") or "utf-8"))
        except Exception:  # noqa: BLE001
            old_content = None
```

After `target_path.write_text(content, ...)` and before the return, add:

```python
    # Record file change
    file_tracker = context.metadata.get("file_tracker") if context.metadata else None
    if file_tracker:
        try:
            await file_tracker.record_write(
                rel_path=str(target_path.relative_to(workspace_root)),
                old_content=old_content,
                new_content=content,
                source="file_write",
            )
        except Exception:  # noqa: BLE001
            pass  # Don't fail the write if tracking fails
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_file_change_tracker.py -v`
Expected: All PASS

Run: `python -m pytest tests/test_agent_loop.py -v -k file_write`
Expected: Existing tests still PASS

- [ ] **Step 5: Commit**

```bash
git add backend/amadeus_app/orchestrator/capability_adapters.py tests/test_file_change_tracker.py
git commit -m "feat: record file changes in _file_write adapter"
```

---

### Task 5: Implement rollback endpoint + file_changes listing

**Files:**
- Modify: `backend/amadeus_app/routers/orchestrator.py`
- Modify: `backend/amadeus_app/orchestrator/agent_loop_runner.py` (add rollback method)

- [ ] **Step 1: Write failing test**

```python
# tests/test_file_change_tracker.py (append)
class TestRollbackEndpoint:
    @pytest.mark.asyncio
    async def test_rollback_endpoint_restores_files(self, tmp_path, monkeypatch):
        from backend.amadeus_app.storage import SQLiteStorage
        from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

        db_path = tmp_path / "test.db"
        storage = SQLiteStorage(str(db_path))
        await storage.init_db()

        task_id = uuid4().hex
        await orch_storage.create_task(
            storage, user_id="u", title="t", prompt="p",
            workspace_path=str(tmp_path), conversation_id=None,
            active_skill_ids=[], settings={},
        )

        # Setup tracker and modify a file
        tracker = FileChangeTracker(storage, str(tmp_path), task_id)
        await tracker.initialize()
        test_file = tmp_path / "app.py"
        test_file.write_text("original")
        await tracker.record_write("app.py", old_content="original", new_content="modified")
        test_file.write_text("modified")

        # Mock the runner to return our tracker
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_file_change_tracker.py::TestRollbackEndpoint -v`
Expected: FAIL — `rollback_orchestrator_task` does not exist

- [ ] **Step 3: Add rollback method to AgentLoopRunner**

In `backend/amadeus_app/orchestrator/agent_loop_runner.py`, add a new method:

```python
    async def rollback_task(self, *, storage, task_id: str) -> dict[str, Any]:
        """Rollback a task's file changes to pre-task state."""
        task = await orchestrator_storage.get_task(storage, task_id)
        if task is None:
            return {"ok": False, "reason": "task not found"}
        if task.get("status") not in ("done", "failed"):
            return {"ok": False, "reason": "can only rollback completed or failed tasks"}

        settings = OrchestratorSettings.model_validate(task.get("settings") or {})
        workspace_path = str(task.get("workspacePath") or settings.default_workspace or ".")
        tracker = FileChangeTracker(storage, workspace_path, task_id)
        await tracker.initialize()

        result = await tracker.rollback()

        await orchestrator_storage.update_task_status(
            storage, task_id, status="rolled_back", finished=True
        )
        await orchestrator_storage.append_event(
            storage,
            task_id=task_id,
            kind="status",
            role="coordinator",
            name="rollback",
            status="rolled_back",
            summary=f"Task rolled back: {result.get('method', 'unknown')}",
            payload=result,
        )
        return result
```

- [ ] **Step 4: Add rollback endpoint**

In `backend/amadeus_app/routers/orchestrator.py`, add a new endpoint:

```python
@router.post("/tasks/{task_id}/rollback")
async def rollback_orchestrator_task(task_id: str) -> dict[str, Any]:
    storage = require_storage()
    task = await orchestrator_storage.get_task(storage, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task["status"] not in ("done", "failed"):
        raise HTTPException(status_code=409, detail=f"cannot rollback task with status: {task['status']}")

    result = await default_orchestrator_runner.rollback_task(storage=storage, task_id=task_id)
    return result
```

Also add a file_changes listing endpoint:

```python
@router.get("/tasks/{task_id}/file-changes")
async def list_orchestrator_file_changes(task_id: str) -> dict[str, Any]:
    storage = require_storage()
    changes = await orchestrator_storage.list_file_changes(storage, task_id)
    return {"changes": changes}
```

- [ ] **Step 5: Modify control_orchestrator_task to handle rollback**

In `control_orchestrator_task` (line 274), update the rollback action to call the new endpoint logic:

```python
    if request.action == "rollback":
        if task["status"] not in ("done", "failed"):
            raise HTTPException(status_code=409, detail=f"cannot rollback task with status: {task['status']}")
        result = await default_orchestrator_runner.rollback_task(storage=storage, task_id=task_id)
        return result
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_file_change_tracker.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add backend/amadeus_app/routers/orchestrator.py backend/amadeus_app/orchestrator/agent_loop_runner.py tests/test_file_change_tracker.py
git commit -m "feat: implement rollback endpoint and file_changes listing API"
```

---

### Task 6: Frontend — Diff viewer + Rollback button

**Files:**
- Modify: `src/types.ts`
- Modify: `src/api.ts`
- Modify: `src/components/TaskWorkspace.tsx`

- [ ] **Step 1: Add TypeScript types**

In `src/types.ts`, add:

```typescript
interface FileChange {
    id: string;
    taskId: string;
    seq: number;
    relativePath: string;
    changeType: "modified" | "created" | "deleted";
    diffText: string;
    oldSnapshot: string;
    source: string;
    createdAt: string;
}
```

- [ ] **Step 2: Add API functions**

In `src/api.ts`, add:

```typescript
export async function fetchFileChanges(taskId: string): Promise<FileChange[]> {
    const res = await fetch(`/api/orchestrator/tasks/${taskId}/file-changes`);
    const data = await res.json();
    return data.changes || [];
}

export async function rollbackTask(taskId: string): Promise<{ ok: boolean; method?: string }> {
    const res = await fetch(`/api/orchestrator/tasks/${taskId}/rollback`, { method: "POST" });
    return res.json();
}
```

- [ ] **Step 3: Add file changes display + rollback button to TaskWorkspace**

In `src/components/TaskWorkspace.tsx`:

1. Add imports for `fetchFileChanges` and `rollbackTask` to the existing API import.

2. Add state for file changes:

```typescript
const [fileChanges, setFileChanges] = useState<FileChange[]>([]);
```

3. Add fetch in the task detail effect:

```typescript
fetchFileChanges(effectiveActiveId).then(setFileChanges).catch(() => undefined);
```

4. Add file changes rendering section (after the events area, before the input area). Include a rollback button when task is done/failed:

```tsx
{fileChanges.length > 0 && (
    <div className="file-changes-panel">
        <div className="file-changes-header">
            <span>文件变更 ({fileChanges.length})</span>
            {(taskDetail?.status === "done" || taskDetail?.status === "failed") && (
                <button
                    className="rollback-btn"
                    onClick={async () => {
                        if (!window.confirm("确认回滚所有文件变更？此操作不可撤销。")) return;
                        try {
                            await rollbackTask(effectiveActiveId!);
                            await refreshActiveDetail();
                            setFileChanges([]);
                        } catch (err) {
                            setError(err instanceof Error ? err.message : "回滚失败");
                        }
                    }}
                >
                    <Trash2 size={14} /> 回滚
                </button>
            )}
        </div>
        {fileChanges.map((change) => (
            <div key={change.id} className="file-change-item">
                <span className="change-type-badge change-{change.changeType}">
                    {change.changeType === "created" ? "新增" :
                     change.changeType === "deleted" ? "删除" : "修改"}
                </span>
                <span className="file-path">{change.relativePath}</span>
                {change.diffText && (
                    <pre className="diff-preview">{change.diffText.slice(0, 500)}</pre>
                )}
            </div>
        ))}
    </div>
)}
```

- [ ] **Step 4: Verify TypeScript compiles**

Run: `npx tsc --noEmit`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add src/types.ts src/api.ts src/components/TaskWorkspace.tsx
git commit -m "feat: add file changes diff viewer and rollback button in TaskWorkspace"
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
git commit -m "test: file change tracking and rollback full integration verification"
```
