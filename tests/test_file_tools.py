from __future__ import annotations
import hashlib
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

from backend.amadeus_app.file_tools.path_guard import PathGuard, compute_sha256
from backend.amadeus_app.storage import SQLiteStorage


def test_compute_sha256_matches_hashlib():
    text = "hello world\n"
    assert compute_sha256(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_path_guard_resolves_relative_inside_workspace(tmp_path: Path):
    guard = PathGuard(str(tmp_path))
    resolved = guard.resolve_read("src/app.py")
    assert resolved == (tmp_path / "src" / "app.py").resolve()
    assert guard.is_within_workspace(resolved)


def test_path_guard_resolves_absolute_inside_workspace(tmp_path: Path):
    guard = PathGuard(str(tmp_path))
    resolved = guard.resolve_read(str(tmp_path / "app.py"))
    assert guard.is_within_workspace(resolved)


def test_path_guard_rejects_path_traversal(tmp_path: Path):
    guard = PathGuard(str(tmp_path))
    with pytest.raises(ValueError, match="outside the workspace"):
        guard.resolve_read("../../etc/passwd")


def test_path_guard_allows_external_when_authorized(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    target = external / "data.txt"
    guard = PathGuard(str(workspace), allowed_external_paths=[str(external)])
    resolved = guard.resolve_read(str(target))
    assert guard.is_external_allowed(resolved)
    assert not guard.is_within_workspace(resolved)


def test_path_guard_rejects_external_when_not_authorized(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    guard = PathGuard(str(workspace))
    with pytest.raises(ValueError, match="outside the workspace"):
        guard.resolve_read(str(external / "data.txt"))


def test_path_guard_ensures_not_denied_env(tmp_path: Path):
    guard = PathGuard(str(tmp_path))
    env_file = tmp_path / ".env"
    with pytest.raises(ValueError, match="denied"):
        guard.ensure_not_denied(env_file)


def test_path_guard_ensures_not_denied_env_example_allowed(tmp_path: Path):
    guard = PathGuard(str(tmp_path))
    env_example = tmp_path / ".env.example"
    guard.ensure_not_denied(env_example)  # should not raise


def test_path_guard_ensures_not_denied_pem_suffix(tmp_path: Path):
    guard = PathGuard(str(tmp_path))
    with pytest.raises(ValueError, match="denied"):
        guard.ensure_not_denied(tmp_path / "secret.pem")


def test_path_guard_ensures_writable_suffix_rejects_pyc(tmp_path: Path):
    guard = PathGuard(str(tmp_path))
    with pytest.raises(ValueError, match="denied"):
        guard.ensure_writable_suffix(tmp_path / "cache.pyc")


def test_path_guard_write_resolver_rejects_external(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    guard = PathGuard(str(workspace))
    with pytest.raises(ValueError, match="outside the workspace"):
        guard.resolve_write(str(external / "out.txt"))


from backend.amadeus_app.file_tools.file_reader import (
    run_file_read, run_file_list, run_file_reader,
)


@pytest.mark.asyncio
async def test_run_file_read_returns_sha256_and_content(tmp_path: Path):
    f = tmp_path / "app.py"
    f.write_text("print('hi')\n", encoding="utf-8")
    result = await run_file_read(
        path=str(f), workspace_root=str(tmp_path), include_line_numbers=True,
    )
    assert result["content"].startswith("1→print('hi')")
    assert result["sha256"] == compute_sha256("print('hi')\n")
    assert result["sizeBytes"] == len("print('hi')\n".encode("utf-8"))
    assert result["encoding"] == "utf-8"
    assert result["truncated"] is False
    assert "mtime" in result


@pytest.mark.asyncio
async def test_run_file_read_respects_line_range(tmp_path: Path):
    f = tmp_path / "multi.txt"
    f.write_text("a\nb\nc\nd\n", encoding="utf-8")
    result = await run_file_read(
        path=str(f), workspace_root=str(tmp_path), start_line=2, end_line=3,
    )
    assert result["content"] == "b\nc\n"
    assert result["startLine"] == 2
    assert result["endLine"] == 3


@pytest.mark.asyncio
async def test_run_file_read_truncates_on_max_bytes(tmp_path: Path):
    f = tmp_path / "big.txt"
    f.write_text("x" * 500, encoding="utf-8")
    result = await run_file_read(
        path=str(f), workspace_root=str(tmp_path), max_bytes=100,
    )
    assert result["truncated"] is True
    assert len(result["content"].encode("utf-8")) <= 100


@pytest.mark.asyncio
async def test_run_file_list_returns_entries(tmp_path: Path):
    (tmp_path / "a.py").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    result = await run_file_list(path=".", workspace_root=str(tmp_path))
    names = {e["name"] for e in result["entries"]}
    assert {"a.py", "b.txt"} <= names
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_run_file_list_recursive_and_hidden(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("c", encoding="utf-8")
    (tmp_path / ".hidden").write_text("h", encoding="utf-8")
    result = await run_file_list(
        path=".", workspace_root=str(tmp_path), recursive=True, include_hidden=True,
    )
    names = {e["name"] for e in result["entries"]}
    assert "c.py" in names
    assert ".hidden" in names


@pytest.mark.asyncio
async def test_run_file_read_external_path_authorized(tmp_path: Path):
    external = tmp_path / "ext"
    external.mkdir()
    f = external / "data.txt"
    f.write_text("ext-data", encoding="utf-8")
    result = await run_file_read(
        path=str(f), workspace_root=str(tmp_path),
        allowed_external_paths=[str(external)],
    )
    assert result["content"] == "ext-data"


@pytest.mark.asyncio
async def test_old_run_file_reader_returns_migration_error():
    result = await run_file_reader({"action": "list", "path": "."})
    assert result["ok"] is False
    assert "migration" in result["summary"].lower() or "migrate" in result["summary"].lower()


@pytest.mark.asyncio
async def test_run_file_read_pdf_extraction(tmp_path: Path):
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    pdf_path = tmp_path / "doc.pdf"
    with open(pdf_path, "wb") as fh:
        writer.write(fh)
    result = await run_file_read(path=str(pdf_path), workspace_root=str(tmp_path))
    assert result["encoding"] == "pdf-text"
    assert "truncated" in result


from backend.amadeus_app.file_tools.code_search import run_code_search


@pytest.mark.asyncio
async def test_code_search_builtin_finds_match(tmp_path: Path):
    (tmp_path / "a.py").write_text("def hello():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("no match here\n", encoding="utf-8")
    result = await run_code_search(
        query="hello", workspace_root=str(tmp_path), path=".",
    )
    assert result["provider"] in ("ripgrep", "builtin")
    assert len(result["results"]) == 1
    assert result["results"][0]["path"].endswith("a.py")
    assert result["results"][0]["line"] == 1
    assert "hello" in result["results"][0]["preview"]


@pytest.mark.asyncio
async def test_code_search_builtin_case_insensitive(tmp_path: Path):
    (tmp_path / "a.py").write_text("Hello World\n", encoding="utf-8")
    result = await run_code_search(
        query="hello", workspace_root=str(tmp_path), path=".", case_sensitive=False,
    )
    assert len(result["results"]) == 1


@pytest.mark.asyncio
async def test_code_search_case_sensitive_no_match(tmp_path: Path):
    (tmp_path / "a.py").write_text("Hello\n", encoding="utf-8")
    result = await run_code_search(
        query="hello", workspace_root=str(tmp_path), path=".", case_sensitive=True,
    )
    assert len(result["results"]) == 0


@pytest.mark.asyncio
async def test_code_search_results_truncated_at_max(tmp_path: Path):
    for i in range(10):
        (tmp_path / f"f{i}.py").write_text("needle\n", encoding="utf-8")
    result = await run_code_search(
        query="needle", workspace_root=str(tmp_path), path=".", max_results=3,
    )
    assert len(result["results"]) == 3


@pytest.mark.asyncio
async def test_code_search_provider_field_present(tmp_path: Path):
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
    result = await run_code_search(query="x", workspace_root=str(tmp_path))
    assert "provider" in result
    assert result["provider"] in ("ripgrep", "builtin")


@pytest.mark.asyncio
async def test_code_search_falls_back_to_python_when_rg_unavailable(tmp_path: Path, monkeypatch):
    # Force ripgrep to be considered unavailable so the pure-Python fallback runs.
    from backend.amadeus_app.file_tools import code_search
    monkeypatch.setattr(code_search, "_ripgrep_available", lambda: False)
    (tmp_path / "a.py").write_text("def hello():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("no match here\n", encoding="utf-8")
    result = await run_code_search(
        query="hello", workspace_root=str(tmp_path), path=".",
    )
    assert result["provider"] == "builtin"
    assert len(result["results"]) == 1
    assert result["results"][0]["path"].endswith("a.py")
    assert result["results"][0]["line"] == 1
    assert "hello" in result["results"][0]["preview"]


from backend.amadeus_app.file_tools.file_writer import (
    run_file_write, run_file_append, run_file_mkdir,
    CodeChangeBudget, FileBackupStore,
)


@pytest.mark.asyncio
async def test_file_write_creates_new_file_returns_hash(tmp_path: Path):
    result = await run_file_write(
        path="out.txt", content="hello", workspace_root=str(tmp_path),
    )
    assert result["ok"] is True
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hello"
    assert result["data"]["newHash"] == compute_sha256("hello")
    assert "out.txt" in result["modified_paths"]


@pytest.mark.asyncio
async def test_file_write_overwrite_requires_hash(tmp_path: Path):
    f = tmp_path / "out.txt"
    f.write_text("old", encoding="utf-8")
    result = await run_file_write(
        path="out.txt", content="new", workspace_root=str(tmp_path),
    )
    assert result["ok"] is False
    assert "expectedHash" in result["summary"] or "hash" in result["summary"].lower()


@pytest.mark.asyncio
async def test_file_write_overwrite_hash_mismatch_rejected(tmp_path: Path):
    f = tmp_path / "out.txt"
    f.write_text("old", encoding="utf-8")
    result = await run_file_write(
        path="out.txt", content="new", workspace_root=str(tmp_path),
        expected_hash="0000000000000000000000000000000000000000000000000000000000000000",
    )
    assert result["ok"] is False
    assert "hash" in result["summary"].lower() or "mismatch" in result["summary"].lower()
    assert f.read_text(encoding="utf-8") == "old"  # unchanged


@pytest.mark.asyncio
async def test_file_write_overwrite_hash_match_succeeds(tmp_path: Path):
    f = tmp_path / "out.txt"
    f.write_text("old", encoding="utf-8")
    result = await run_file_write(
        path="out.txt", content="new", workspace_root=str(tmp_path),
        expected_hash=compute_sha256("old"),
    )
    assert result["ok"] is True
    assert f.read_text(encoding="utf-8") == "new"
    assert result["data"]["newHash"] == compute_sha256("new")
    assert result["data"]["diffSummary"]


@pytest.mark.asyncio
async def test_file_append_creates_and_appends(tmp_path: Path):
    await run_file_append(path="log.txt", content="line1\n", workspace_root=str(tmp_path))
    result = await run_file_append(
        path="log.txt", content="line2\n", workspace_root=str(tmp_path),
        expected_hash=compute_sha256("line1\n"),
    )
    assert result["ok"] is True
    assert (tmp_path / "log.txt").read_text(encoding="utf-8") == "line1\nline2\n"


@pytest.mark.asyncio
async def test_file_mkdir_creates_nested(tmp_path: Path):
    result = await run_file_mkdir(path="a/b/c", workspace_root=str(tmp_path))
    assert result["ok"] is True
    assert (tmp_path / "a" / "b" / "c").is_dir()


@pytest.mark.asyncio
async def test_code_change_budget_rejects_over_limit():
    budget = CodeChangeBudget(max_files=2, max_lines=5)
    assert budget.record("a.py", 3) is True
    assert budget.record("b.py", 2) is True  # 2 files, 5 lines
    assert budget.record("c.py", 1) is False  # exceeds file count


@pytest.mark.asyncio
async def test_code_change_budget_rejects_line_over_limit():
    budget = CodeChangeBudget(max_files=10, max_lines=5)
    assert budget.record("a.py", 6) is False


@pytest.mark.asyncio
async def test_file_backup_store_backs_up_and_cleans(tmp_path: Path):
    store_dir = tmp_path / "backups"
    store = FileBackupStore(str(store_dir), task_id="t1")
    f = tmp_path / "orig.txt"
    f.write_text("original", encoding="utf-8")
    backup = store.backup(f)
    assert backup is not None
    assert backup["originalPath"] == str(f)
    assert Path(backup["backupPath"]).read_text(encoding="utf-8") == "original"
    store.cleanup()
    assert not Path(backup["backupPath"]).exists()


from backend.amadeus_app.file_tools.file_writer import (
    run_file_patch, run_file_move, run_file_delete,
)


@pytest.mark.asyncio
async def test_file_patch_replaces_exact_text(tmp_path: Path):
    f = tmp_path / "app.py"
    f.write_text("def foo():\n    return 1\n", encoding="utf-8")
    result = await run_file_patch(
        path="app.py", old_text="return 1", new_text="return 2",
        workspace_root=str(tmp_path),
        expected_hash=compute_sha256("def foo():\n    return 1\n"),
    )
    assert result["ok"] is True
    assert f.read_text(encoding="utf-8") == "def foo():\n    return 2\n"
    assert result["data"]["newHash"] == compute_sha256("def foo():\n    return 2\n")


@pytest.mark.asyncio
async def test_file_patch_hash_mismatch_rejected(tmp_path: Path):
    f = tmp_path / "app.py"
    f.write_text("old content\n", encoding="utf-8")
    result = await run_file_patch(
        path="app.py", old_text="old", new_text="new",
        workspace_root=str(tmp_path),
        expected_hash="0" * 64,
    )
    assert result["ok"] is False
    assert "hash" in result["summary"].lower()


@pytest.mark.asyncio
async def test_file_patch_old_text_not_found(tmp_path: Path):
    f = tmp_path / "app.py"
    f.write_text("content\n", encoding="utf-8")
    result = await run_file_patch(
        path="app.py", old_text="missing", new_text="new",
        workspace_root=str(tmp_path),
        expected_hash=compute_sha256("content\n"),
    )
    assert result["ok"] is False
    assert "oldText" in result["summary"] or "not found" in result["summary"].lower()


@pytest.mark.asyncio
async def test_file_patch_multiple_matches_rejected(tmp_path: Path):
    f = tmp_path / "app.py"
    f.write_text("dup\ndup\n", encoding="utf-8")
    result = await run_file_patch(
        path="app.py", old_text="dup", new_text="x",
        workspace_root=str(tmp_path),
        expected_hash=compute_sha256("dup\ndup\n"),
    )
    assert result["ok"] is False
    assert "multiple" in result["summary"].lower() or "matches" in result["summary"].lower()


@pytest.mark.asyncio
async def test_file_move_relocates_file_with_backup(tmp_path: Path):
    backup_dir = tmp_path / "backups"
    store = FileBackupStore(str(backup_dir), task_id="t1")
    f = tmp_path / "src.txt"
    f.write_text("data", encoding="utf-8")
    result = await run_file_move(
        src="src.txt", dst="dst.txt", workspace_root=str(tmp_path),
        backup_store=store,
    )
    assert result["ok"] is True
    assert not (tmp_path / "src.txt").exists()
    assert (tmp_path / "dst.txt").read_text(encoding="utf-8") == "data"


@pytest.mark.asyncio
async def test_file_delete_removes_file_with_backup(tmp_path: Path):
    backup_dir = tmp_path / "backups"
    store = FileBackupStore(str(backup_dir), task_id="t1")
    f = tmp_path / "trash.txt"
    f.write_text("bye", encoding="utf-8")
    result = await run_file_delete(
        path="trash.txt", workspace_root=str(tmp_path), backup_store=store,
    )
    assert result["ok"] is True
    assert not f.exists()


@pytest.mark.asyncio
async def test_file_delete_empty_directory(tmp_path: Path):
    (tmp_path / "emptydir").mkdir()
    result = await run_file_delete(path="emptydir", workspace_root=str(tmp_path))
    assert result["ok"] is True
    assert not (tmp_path / "emptydir").exists()


@pytest.mark.asyncio
async def test_file_delete_non_empty_directory_rejected(tmp_path: Path):
    d = tmp_path / "nonempty"
    d.mkdir()
    (d / "child.txt").write_text("x", encoding="utf-8")
    result = await run_file_delete(path="nonempty", workspace_root=str(tmp_path))
    assert result["ok"] is False
    assert "non-empty" in result["summary"].lower() or "not empty" in result["summary"].lower()
    assert d.exists()  # unchanged


# --- Task 7: capability adapter wiring tests ---
from typing import Any

from backend.amadeus_app.orchestrator.capability_adapters import (
    CapabilityExecutionContext, default_capability_registry,
)
from backend.amadeus_app.orchestrator.domain import OrchestratorSettings


def _make_ctx(workspace: str, **meta) -> CapabilityExecutionContext:
    return CapabilityExecutionContext(
        task_id="test-task", prompt="test", workspace_path=workspace,
        settings=OrchestratorSettings(defaultWorkspace=workspace, trustMode=True),
        metadata=meta, storage=None, emit_event=None,
    )


@pytest.mark.asyncio
async def test_adapter_file_list_uses_workspace(tmp_path: Path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    ctx = _make_ctx(str(tmp_path))
    result = await default_capability_registry.execute("file_list", {"path": "."}, ctx)
    assert result["ok"] is True
    names = {e["name"] for e in result["data"]["entries"]}
    assert "a.py" in names


@pytest.mark.asyncio
async def test_adapter_file_read_returns_hash(tmp_path: Path):
    (tmp_path / "a.py").write_text("hi\n", encoding="utf-8")
    ctx = _make_ctx(str(tmp_path))
    result = await default_capability_registry.execute(
        "file_read", {"path": "a.py", "includeLineNumbers": True}, ctx,
    )
    assert result["ok"] is True
    assert result["data"]["sha256"] == compute_sha256("hi\n")


@pytest.mark.asyncio
async def test_adapter_file_read_external_logs_event(tmp_path: Path):
    import shutil
    import tempfile
    external = Path(tempfile.mkdtemp(prefix="external_"))
    try:
        f = external / "data.txt"
        f.write_text("ext", encoding="utf-8")
        events: list[dict[str, Any]] = []

        async def _emit(**kwargs):
            events.append(kwargs)
            return {}

        settings = OrchestratorSettings(defaultWorkspace=str(tmp_path), trustMode=True)
        settings.allowed_external_paths = [str(external)]
        ctx = CapabilityExecutionContext(
            task_id="t", prompt="p", workspace_path=str(tmp_path),
            settings=settings, metadata={}, storage=None, emit_event=_emit,
        )
        result = await default_capability_registry.execute(
            "file_read", {"path": str(f)}, ctx,
        )
        assert result["ok"] is True
        assert any(e.get("name") == "read_external" for e in events)
    finally:
        shutil.rmtree(external, ignore_errors=True)


@pytest.mark.asyncio
async def test_adapter_file_write_creates_file(tmp_path: Path):
    ctx = _make_ctx(str(tmp_path))
    result = await default_capability_registry.execute(
        "file_write", {"path": "out.txt", "content": "hello"}, ctx,
    )
    assert result["ok"] is True
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_adapter_file_patch_requires_hash(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("old\n", encoding="utf-8")
    ctx = _make_ctx(str(tmp_path))
    result = await default_capability_registry.execute(
        "file_patch", {"path": "a.py", "oldText": "old", "newText": "new"}, ctx,
    )
    assert result["ok"] is False
    assert "expectedHash" in result["summary"] or "hash" in result["summary"].lower()


@pytest.mark.asyncio
async def test_adapter_old_file_read_action_returns_migration_error(tmp_path: Path):
    ctx = _make_ctx(str(tmp_path))
    result = await default_capability_registry.execute(
        "file_read", {"action": "list", "path": "."}, ctx,
    )
    assert result["ok"] is False
    assert "migrate" in result["summary"].lower() or "migration" in result["summary"].lower()


@pytest.mark.asyncio
async def test_adapter_file_delete_nonempty_rejected(tmp_path: Path):
    d = tmp_path / "ne"
    d.mkdir()
    (d / "c.txt").write_text("x", encoding="utf-8")
    ctx = _make_ctx(str(tmp_path))
    result = await default_capability_registry.execute(
        "file_delete", {"path": "ne"}, ctx,
    )
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# Task 12: Integration test — read → hash → patch → rollback end-to-end
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def agent_storage():
    with tempfile.TemporaryDirectory() as tmp:
        store = SQLiteStorage(os.path.join(tmp, "test_agent.db"))
        await store.connect()
        await store.init_schema()
        yield store
        await store.close()


@pytest.mark.asyncio
async def test_integration_read_patch_rollback_flow(agent_storage, tmp_path):
    from backend.amadeus_app.orchestrator.capability_adapters import CapabilityRegistry
    from backend.amadeus_app.orchestrator.agent_loop_runner import AgentLoopRunner
    from backend.amadeus_app.orchestrator.domain import OrchestratorSettings, OrchestratorTaskCreateRequest
    from backend.amadeus_app.orchestrator import storage as orch_storage
    from backend.amadeus_app.storage import DEFAULT_USER_ID

    f = tmp_path / "app.py"
    f.write_text("def foo():\n    return 1\n", encoding="utf-8")
    original = f.read_text(encoding="utf-8")

    runner = AgentLoopRunner()
    step = [0]

    async def mock_model(*, loop_ctx, task_model, mode, emit):
        step[0] += 1
        if step[0] == 1:
            return {"content": "read file", "tool_calls": [
                {"id": "c1", "name": "file_read", "arguments": {"path": "app.py"}},
            ], "tool_calls_raw": [], "finish_reason": "tool_calls"}
        if step[0] == 2:
            return {"content": "patch file", "tool_calls": [
                {"id": "c2", "name": "file_patch", "arguments": {
                    "path": "app.py", "oldText": "return 1", "newText": "return 2",
                    "expectedHash": compute_sha256(original),
                }},
            ], "tool_calls_raw": [], "finish_reason": "tool_calls"}
        return {"content": "done", "tool_calls": [
            {"id": "c3", "name": "finish", "arguments": {"summary": "patched"}},
        ], "tool_calls_raw": [], "finish_reason": "stop"}

    request = OrchestratorTaskCreateRequest(prompt="patch app.py", workspacePath=str(tmp_path), trustMode=True)
    settings = OrchestratorSettings(defaultWorkspace=str(tmp_path), trustMode=True, agentLoopEnabled=True)
    task = await orch_storage.create_task(agent_storage, user_id=DEFAULT_USER_ID, title="t", prompt=request.prompt, workspace_path=str(tmp_path), conversation_id=None, active_skill_ids=[], settings=settings.model_dump(by_alias=True), budget={})

    with patch.object(runner, "_call_model", side_effect=mock_model):
        await runner.run(storage=agent_storage, task_id=task["id"], request=request, settings=settings)

    assert f.read_text(encoding="utf-8") == "def foo():\n    return 2\n"
    detail = await orch_storage.get_detail(agent_storage, task["id"])
    assert detail["task"]["status"] == "done"
    # Rollback restores original
    await runner.rollback_task(storage=agent_storage, task_id=task["id"])
    assert f.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Final-review fix: file_delete/file_move must record changes in FileChangeTracker
# so non-git rollback can restore deleted files and moved sources.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_delete_records_tracker_change(tmp_path: Path):
    """run_file_delete records a 'deleted' change with oldSnapshot for rollback."""
    from backend.amadeus_app.orchestrator import storage as orch_storage
    from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

    storage = SQLiteStorage(str(tmp_path / "test.db"))
    await storage.connect()
    task = await orch_storage.create_task(
        storage, user_id="u", title="t", prompt="p",
        workspace_path=str(tmp_path), conversation_id=None,
        active_skill_ids=[], settings={},
    )
    tracker = FileChangeTracker(storage, str(tmp_path), task["id"])
    await tracker.initialize()

    f = tmp_path / "trash.txt"
    f.write_text("bye-content", encoding="utf-8")
    result = await run_file_delete(
        path="trash.txt", workspace_root=str(tmp_path), file_tracker=tracker,
    )
    assert result["ok"] is True
    assert not f.exists()

    changes = await orch_storage.list_file_changes(storage, task["id"])
    assert len(changes) == 1
    assert changes[0]["changeType"] == "deleted"
    assert changes[0]["oldSnapshot"] == "bye-content"
    await storage.close()


@pytest.mark.asyncio
async def test_file_move_records_tracker_changes(tmp_path: Path):
    """run_file_move records 'deleted' for src and 'created' for dst."""
    from backend.amadeus_app.orchestrator import storage as orch_storage
    from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

    storage = SQLiteStorage(str(tmp_path / "test.db"))
    await storage.connect()
    task = await orch_storage.create_task(
        storage, user_id="u", title="t", prompt="p",
        workspace_path=str(tmp_path), conversation_id=None,
        active_skill_ids=[], settings={},
    )
    tracker = FileChangeTracker(storage, str(tmp_path), task["id"])
    await tracker.initialize()

    (tmp_path / "src.txt").write_text("moved-content", encoding="utf-8")
    result = await run_file_move(
        src="src.txt", dst="dst.txt", workspace_root=str(tmp_path),
        file_tracker=tracker,
    )
    assert result["ok"] is True

    changes = await orch_storage.list_file_changes(storage, task["id"])
    types_by_path = {c["relativePath"]: c["changeType"] for c in changes}
    assert types_by_path.get("src.txt") == "deleted"
    assert types_by_path.get("dst.txt") == "created"
    src_change = next(c for c in changes if c["relativePath"] == "src.txt")
    assert src_change["oldSnapshot"] == "moved-content"
    await storage.close()


@pytest.mark.asyncio
async def test_rollback_after_delete_restores_file(tmp_path: Path):
    """Rollback after run_file_delete restores the deleted file."""
    from backend.amadeus_app.orchestrator import storage as orch_storage
    from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

    storage = SQLiteStorage(str(tmp_path / "test.db"))
    await storage.connect()
    task = await orch_storage.create_task(
        storage, user_id="u", title="t", prompt="p",
        workspace_path=str(tmp_path), conversation_id=None,
        active_skill_ids=[], settings={},
    )
    tracker = FileChangeTracker(storage, str(tmp_path), task["id"])
    await tracker.initialize()

    f = tmp_path / "trash.txt"
    f.write_text("restore-me", encoding="utf-8")
    await run_file_delete(
        path="trash.txt", workspace_root=str(tmp_path), file_tracker=tracker,
    )
    assert not f.exists()

    result = await tracker.rollback()
    assert result["ok"] is True
    assert f.read_text(encoding="utf-8") == "restore-me"
    await storage.close()


@pytest.mark.asyncio
async def test_rollback_after_move_restores_source(tmp_path: Path):
    """Rollback after run_file_move restores the source file and removes destination."""
    from backend.amadeus_app.orchestrator import storage as orch_storage
    from backend.amadeus_app.orchestrator.file_change_tracker import FileChangeTracker

    storage = SQLiteStorage(str(tmp_path / "test.db"))
    await storage.connect()
    task = await orch_storage.create_task(
        storage, user_id="u", title="t", prompt="p",
        workspace_path=str(tmp_path), conversation_id=None,
        active_skill_ids=[], settings={},
    )
    tracker = FileChangeTracker(storage, str(tmp_path), task["id"])
    await tracker.initialize()

    (tmp_path / "src.txt").write_text("move-me-back", encoding="utf-8")
    await run_file_move(
        src="src.txt", dst="dst.txt", workspace_root=str(tmp_path),
        file_tracker=tracker,
    )
    assert not (tmp_path / "src.txt").exists()
    assert (tmp_path / "dst.txt").exists()

    result = await tracker.rollback()
    assert result["ok"] is True
    assert (tmp_path / "src.txt").read_text(encoding="utf-8") == "move-me-back"
    assert not (tmp_path / "dst.txt").exists()
    await storage.close()
