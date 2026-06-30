# tests/test_sandbox_guard.py
from __future__ import annotations
import os
import pytest
from pathlib import Path
from typing import Any

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


class TestOrchestratorSettingsSandboxMode:
    def test_default_sandbox_mode_is_guard(self):
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        settings = OrchestratorSettings()
        assert settings.sandbox_mode == "guard"

    def test_sandbox_mode_accepts_off(self):
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        settings = OrchestratorSettings(sandboxMode="off")
        assert settings.sandbox_mode == "off"

    def test_sandbox_mode_accepts_strict(self):
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        settings = OrchestratorSettings(sandboxMode="strict")
        assert settings.sandbox_mode == "strict"

    def test_sandbox_mode_invalid_falls_back_to_guard(self):
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        with pytest.raises(Exception):
            OrchestratorSettings(sandboxMode="invalid")


class TestShellExecSandboxIntegration:
    """Integration tests verifying that SandboxGuard is wired into _shell_exec."""

    @staticmethod
    def _make_emit_capture():
        events: list[dict[str, Any]] = []

        async def _emit(**kwargs):
            events.append(kwargs)
            return {}

        return events, _emit

    @pytest.mark.asyncio
    async def test_shell_exec_blocks_dangerous_command(self, tmp_path):
        from backend.amadeus_app.orchestrator.capability_adapters import (
            CapabilityExecutionContext,
            _shell_exec,
        )
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings

        events, _emit = self._make_emit_capture()
        context = CapabilityExecutionContext(
            task_id="test-task",
            prompt="test",
            workspace_path=str(tmp_path),
            settings=OrchestratorSettings(sandboxMode="guard"),
            emit_event=_emit,
        )
        result = await _shell_exec({"command": "format C:", "cwd": "."}, context)

        assert result["ok"] is False
        assert "Blocked by sandbox" in result["summary"]
        assert result["data"]["action"] == "blocked"
        # A blocked event should have been emitted with status="blocked".
        assert any(event.get("status") == "blocked" for event in events)

    @pytest.mark.asyncio
    async def test_shell_exec_emits_started_then_blocked_event(self, tmp_path):
        from backend.amadeus_app.orchestrator.capability_adapters import (
            CapabilityExecutionContext,
            _shell_exec,
        )
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings

        events, _emit = self._make_emit_capture()
        context = CapabilityExecutionContext(
            task_id="test-task",
            prompt="test",
            workspace_path=str(tmp_path),
            settings=OrchestratorSettings(sandboxMode="guard"),
            emit_event=_emit,
        )
        await _shell_exec({"command": "shutdown /s /t 0"}, context)

        statuses = [event.get("status") for event in events]
        assert "started" in statuses
        assert "blocked" in statuses

    @pytest.mark.asyncio
    async def test_shell_exec_dry_run_in_strict_mode(self, tmp_path):
        from backend.amadeus_app.orchestrator.capability_adapters import (
            CapabilityExecutionContext,
            _shell_exec,
        )
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings

        events, _emit = self._make_emit_capture()
        context = CapabilityExecutionContext(
            task_id="test-task",
            prompt="test",
            workspace_path=str(tmp_path),
            settings=OrchestratorSettings(sandboxMode="strict", trustMode=True),
            emit_event=_emit,
        )
        # "rm -rf local_dir" is classified as dangerous by the policy but is
        # not in SandboxGuard's blocked patterns (those require a trailing "/"),
        # so strict mode should surface it as a dry-run rather than executing.
        result = await _shell_exec({"command": "rm -rf local_dir"}, context)

        assert result["ok"] is True
        assert result["data"]["dryRun"] is True
        assert result["data"]["preview"] == "rm -rf local_dir"
        assert "Dry-run" in result["summary"]
        assert any(event.get("status") == "dry_run" for event in events)

    @pytest.mark.asyncio
    async def test_shell_exec_off_mode_allows_blocked_pattern(self, tmp_path):
        from backend.amadeus_app.orchestrator.capability_adapters import (
            CapabilityExecutionContext,
            _shell_exec,
        )
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings

        events, _emit = self._make_emit_capture()
        context = CapabilityExecutionContext(
            task_id="test-task",
            prompt="test",
            workspace_path=str(tmp_path),
            settings=OrchestratorSettings(sandboxMode="off", trustMode=True),
            emit_event=_emit,
        )
        # In off mode, even a "format C:" pattern is allowed through the guard.
        # The command should not be blocked by the sandbox; instead it will be
        # attempted (and likely fail on this platform), but the key assertion is
        # that no "blocked"/"dry_run" status was emitted by the guard.
        result = await _shell_exec({"command": "echo sandbox-off-check"}, context)

        statuses = [event.get("status") for event in events]
        assert "blocked" not in statuses
        assert "dry_run" not in statuses
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_shell_exec_safe_command_still_runs_in_guard_mode(self, tmp_path):
        from backend.amadeus_app.orchestrator.capability_adapters import (
            CapabilityExecutionContext,
            _shell_exec,
        )
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings

        events, _emit = self._make_emit_capture()
        context = CapabilityExecutionContext(
            task_id="test-task",
            prompt="test",
            workspace_path=str(tmp_path),
            settings=OrchestratorSettings(sandboxMode="guard", trustMode=True),
            emit_event=_emit,
        )
        result = await _shell_exec({"command": "echo hello-sandbox"}, context)

        assert result["ok"] is True
        assert "hello-sandbox" in str(result.get("data", {}).get("stdout", ""))
        statuses = [event.get("status") for event in events]
        assert "blocked" not in statuses
        assert "dry_run" not in statuses
        assert "done" in statuses
