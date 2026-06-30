from __future__ import annotations
import hashlib
from pathlib import Path
import pytest
from backend.amadeus_app.file_tools.path_guard import PathGuard, compute_sha256


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
