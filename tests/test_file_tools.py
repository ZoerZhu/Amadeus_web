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
