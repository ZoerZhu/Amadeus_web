"""Tests for the Codex-style agent loop upgrade."""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from backend.amadeus_app.orchestrator.capabilities import (
    CapabilityGateway,
    capability_catalog,
)
from backend.amadeus_app.orchestrator.domain import (
    OrchestratorEventKind,
    OrchestratorSettings,
    OrchestratorTaskCreateRequest,
)


# ---------------------------------------------------------------------------
# Task 1: Domain model tests
# ---------------------------------------------------------------------------


def test_orchestrator_settings_has_agent_loop_enabled():
    settings = OrchestratorSettings()
    assert settings.agent_loop_enabled is True


def test_orchestrator_settings_has_task_model():
    settings = OrchestratorSettings()
    assert settings.task_model == {}


def test_orchestrator_event_kind_includes_new_types():
    kinds = set(OrchestratorEventKind.__args__)  # type: ignore[attr-defined]
    assert "agent_thought_summary" in kinds
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert "command" in kinds
    assert "working_set" in kinds


def test_task_create_request_has_task_model_override():
    req = OrchestratorTaskCreateRequest(prompt="test")
    assert req.task_model_override == {}


# ---------------------------------------------------------------------------
# Task 2: Capability catalog tests
# ---------------------------------------------------------------------------


def test_capability_catalog_includes_shell_exec():
    names = {item.name for item in capability_catalog()}
    assert "shell_exec" in names


def test_capability_catalog_includes_desktop_screenshot():
    names = {item.name for item in capability_catalog()}
    assert "desktop_screenshot" in names


def test_gateway_classifies_shell_exec_as_confirm():
    gateway = CapabilityGateway(trust_mode=False)
    risk = gateway.classify("shell_exec")
    assert risk == "confirm"


def test_gateway_classifies_desktop_screenshot_as_safe():
    gateway = CapabilityGateway(trust_mode=False)
    risk = gateway.classify("desktop_screenshot")
    assert risk == "safe"


# ---------------------------------------------------------------------------
# Task 3: shell_exec policy tests
# ---------------------------------------------------------------------------


from backend.amadeus_app.orchestrator.shell_exec_policy import (
    classify_shell_command,
    ShellCommandRisk,
)


def test_shell_exec_low_risk_npm_test():
    result = classify_shell_command("npm test")
    assert result.risk == "safe"
    assert result.auto_approved is True


def test_shell_exec_low_risk_python_pytest():
    result = classify_shell_command("python -m pytest")
    assert result.risk == "safe"
    assert result.auto_approved is True


def test_shell_exec_low_risk_git_status():
    result = classify_shell_command("git status")
    assert result.risk == "safe"
    assert result.auto_approved is True


def test_shell_exec_low_risk_rg():
    result = classify_shell_command("rg 'import' src/")
    assert result.risk == "safe"
    assert result.auto_approved is True


def test_shell_exec_high_risk_npm_install():
    result = classify_shell_command("npm install")
    assert result.risk == "confirm"
    assert result.auto_approved is False


def test_shell_exec_high_risk_git_push():
    result = classify_shell_command("git push origin main")
    assert result.risk == "dangerous"
    assert result.auto_approved is False


def test_shell_exec_high_risk_rm():
    result = classify_shell_command("rm -rf /")
    assert result.risk == "dangerous"
    assert result.auto_approved is False


def test_shell_exec_high_risk_unknown_script():
    result = classify_shell_command("node suspicious_script.js")
    assert result.risk == "confirm"
    assert result.auto_approved is False


# ---------------------------------------------------------------------------
# Task 4-5: shell_exec and desktop_screenshot handler tests
# ---------------------------------------------------------------------------

from backend.amadeus_app.orchestrator.capability_adapters import (
    CapabilityExecutionContext,
    CapabilityRegistry,
    build_default_capability_registry,
)
from backend.amadeus_app.orchestrator.domain import OrchestratorSettings


def _make_context(workspace: str) -> CapabilityExecutionContext:
    return CapabilityExecutionContext(
        task_id="test-task",
        prompt="test",
        workspace_path=workspace,
        settings=OrchestratorSettings(),
        metadata={},
        storage=None,
        emit_event=None,
    )


def test_shell_exec_echo():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _make_context(tmp)
        registry = build_default_capability_registry()
        result = asyncio.run(registry.execute("shell_exec", {"command": "echo hello"}, ctx))
        assert result["ok"] is True
        assert "hello" in str(result.get("data", {}).get("stdout", ""))


def test_shell_exec_rejects_out_of_workspace_cwd():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _make_context(tmp)
        registry = build_default_capability_registry()
        result = asyncio.run(registry.execute(
            "shell_exec", {"command": "echo hi", "cwd": "/etc"}, ctx
        ))
        assert result["ok"] is False
        assert "workspace" in result["summary"].lower()


def test_shell_exec_returns_exit_code():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _make_context(tmp)
        registry = build_default_capability_registry()
        result = asyncio.run(registry.execute(
            "shell_exec", {"command": "python -c \"import sys; sys.exit(1)\""}, ctx
        ))
        assert result["data"]["exitCode"] == 1


def test_desktop_screenshot_returns_error_when_not_electron():
    with tempfile.TemporaryDirectory() as tmp:
        ctx = _make_context(tmp)
        registry = build_default_capability_registry()
        result = asyncio.run(registry.execute("desktop_screenshot", {}, ctx))
        assert result["ok"] is False
        assert "not available" in result["summary"].lower() or "electron" in result["summary"].lower()


# ---------------------------------------------------------------------------
# Task 6: Agent loop context builder tests
# ---------------------------------------------------------------------------

from backend.amadeus_app.orchestrator.agent_loop_context import (
    build_agent_loop_system_prompt,
    build_tool_schemas,
    AgentLoopContext,
)


def test_system_prompt_contains_role_and_instructions():
    prompt = build_agent_loop_system_prompt(workspace_path="/tmp/test")
    assert "agent" in prompt.lower()
    assert "tool" in prompt.lower()
    assert "/tmp/test" in prompt


def test_tool_schemas_include_core_capabilities():
    schemas = build_tool_schemas(enabled_capabilities=None)
    names = {s["function"]["name"] for s in schemas}
    assert "web_search" in names
    assert "file_read" in names
    assert "shell_exec" in names
    assert "desktop_screenshot" in names


def test_tool_schemas_include_finish():
    schemas = build_tool_schemas(enabled_capabilities=None)
    names = {s["function"]["name"] for s in schemas}
    assert "finish" in names


def test_tool_schemas_respect_enabled_capabilities():
    schemas = build_tool_schemas(enabled_capabilities={"web_search", "file_read"})
    names = {s["function"]["name"] for s in schemas}
    assert "web_search" in names
    assert "file_read" in names
    assert "shell_exec" not in names


# ---------------------------------------------------------------------------
# Task 7-8: Agent loop runner and wire-up tests
# ---------------------------------------------------------------------------

from backend.amadeus_app.orchestrator.agent_loop_runner import AgentLoopRunner
from backend.amadeus_app.orchestrator.runner import OrchestratorRunner


def test_agent_loop_runner_exists():
    runner = AgentLoopRunner()
    assert runner is not None


def test_agent_loop_runner_has_run_method():
    runner = AgentLoopRunner()
    assert hasattr(runner, "run")


def test_agent_loop_runner_has_resume_method():
    runner = AgentLoopRunner()
    assert hasattr(runner, "resume_from_permission")


def test_orchestrator_runner_has_agent_loop_runner():
    runner = OrchestratorRunner()
    assert hasattr(runner, "agent_loop_runner")


def test_orchestrator_runner_dispatches_to_agent_loop_when_enabled():
    runner = OrchestratorRunner()
    assert hasattr(runner, "_should_use_agent_loop")
    settings = OrchestratorSettings(agent_loop_enabled=True)
    assert runner._should_use_agent_loop(settings) is True


def test_orchestrator_runner_falls_back_to_planner_when_disabled():
    runner = OrchestratorRunner()
    settings = OrchestratorSettings(agent_loop_enabled=False)
    assert runner._should_use_agent_loop(settings) is False
