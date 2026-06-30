# tests/test_sandbox_guard.py
from __future__ import annotations
import os
import pytest
from pathlib import Path

from backend.amadeus_app.orchestrator.sandbox_guard import SandboxGuard, SandboxResult


class TestSandboxGuardCheckCommand:
    def test_off_mode_allows_everything(self):
        guard = SandboxGuard(workspace_path=".", mode="off")
        result = guard.check_command("del /f C:\\Windows\\System32")
        assert result.allowed is True
        assert result.action == "pass"

    def test_guard_mode_blocks_del(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("del /f C:\\important.txt")
        assert result.allowed is False
        assert result.action == "block"
        assert "matched pattern" in result.reason

    def test_guard_mode_blocks_rmdir_s(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("rmdir /s /q C:\\stuff")
        assert result.allowed is False
        assert result.action == "block"

    def test_guard_mode_blocks_format(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("format D:")
        assert result.allowed is False
        assert result.action == "block"

    def test_guard_mode_blocks_reg_delete(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("reg delete HKLM\\Software\\MyApp")
        assert result.allowed is False
        assert result.action == "block"

    def test_guard_mode_blocks_path_traversal(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("type ..\\..\\..\\Windows\\system32\\config\\SAM")
        assert result.allowed is False
        assert result.action == "block"

    def test_guard_mode_allows_safe_command(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("npm install")
        assert result.allowed is True
        assert result.action == "pass"

    def test_guard_mode_allows_git_command(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("git add -A && git commit -m 'fix'")
        assert result.allowed is True
        assert result.action == "pass"

    def test_strict_mode_dry_runs_dangerous(self):
        guard = SandboxGuard(workspace_path=".", mode="strict")
        result = guard.check_command("npm install", risk_level="dangerous")
        assert result.allowed is False
        assert result.action == "dry_run"
        assert result.preview == "npm install"

    def test_strict_mode_allows_safe_risk(self):
        guard = SandboxGuard(workspace_path=".", mode="strict")
        result = guard.check_command("echo hello", risk_level="safe")
        assert result.allowed is True
        assert result.action == "pass"

    def test_case_insensitive_blocking(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("FORMAT C:")
        assert result.allowed is False
        assert result.action == "block"


class TestSandboxGuardCheckPath:
    def test_path_inside_workspace_allowed(self, tmp_path):
        guard = SandboxGuard(workspace_path=str(tmp_path), mode="guard")
        assert guard.check_path("subdir/file.txt") is True

    def test_path_outside_workspace_blocked(self, tmp_path):
        guard = SandboxGuard(workspace_path=str(tmp_path), mode="guard")
        assert guard.check_path(str(tmp_path.parent / "outside.txt")) is False

    def test_absolute_path_inside_workspace(self, tmp_path):
        guard = SandboxGuard(workspace_path=str(tmp_path), mode="guard")
        assert guard.check_path(str(tmp_path / "file.txt")) is True

    def test_dotdot_traversal_blocked(self, tmp_path):
        guard = SandboxGuard(workspace_path=str(tmp_path), mode="guard")
        assert guard.check_path("../../../etc/passwd") is False


class TestSandboxGuardSanitizeEnv:
    def test_removes_sensitive_keys(self):
        guard = SandboxGuard(workspace_path="/tmp/ws", mode="guard")
        env = {
            "PATH": "C:\\Python;C:\\Windows\\System32",
            "APPDATA": "C:\\Users\\test\\AppData",
            "WINDIR": "C:\\Windows",
            "USERPROFILE": "C:\\Users\\test",
            "HOME": "/home/test",
        }
        clean = guard.sanitize_env(env)
        assert "APPDATA" not in clean
        assert "WINDIR" not in clean
        assert clean["USERPROFILE"] == str(Path("/tmp/ws").resolve())

    def test_path_filters_system_dirs(self):
        guard = SandboxGuard(workspace_path="/tmp/ws", mode="guard")
        env = {
            "PATH": "C:\\Python311;C:\\Windows\\System32;C:\\Program Files\\nodejs",
        }
        clean = guard.sanitize_env(env)
        path_parts = clean["PATH"].split(";")
        assert "C:\\Python311" in path_parts
        assert not any("windows" in p.lower() for p in path_parts)
        assert not any("program files" in p.lower() for p in path_parts)

    def test_preserves_non_sensitive_keys(self):
        guard = SandboxGuard(workspace_path="/tmp/ws", mode="guard")
        env = {"PATH": "C:\\Python", "HOME": "/home/test", "CUSTOM_VAR": "value"}
        clean = guard.sanitize_env(env)
        assert clean["CUSTOM_VAR"] == "value"
        assert clean["HOME"] == "/home/test"
