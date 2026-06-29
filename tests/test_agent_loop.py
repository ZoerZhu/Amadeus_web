"""Tests for the Codex-style agent loop upgrade."""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
import pytest_asyncio

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


# ---------------------------------------------------------------------------
# Task 12: Agent loop integration tests
# ---------------------------------------------------------------------------

from backend.amadeus_app.orchestrator import storage as orch_storage
from backend.amadeus_app.orchestrator.domain import ChatAttachment
from backend.amadeus_app.storage import SQLiteStorage


@pytest_asyncio.fixture
async def agent_storage():
    with tempfile.TemporaryDirectory() as tmp:
        store = SQLiteStorage(os.path.join(tmp, "test_agent.db"))
        await store.connect()
        await store.init_schema()
        yield store
        await store.close()


@pytest.mark.asyncio
async def test_agent_loop_writes_events_for_multi_round(agent_storage):
    """Test that the agent loop writes plan, tool_call, tool_result, and done events."""
    from unittest.mock import AsyncMock, patch

    call_count = [0]

    async def mock_call_model(*, loop_ctx, task_model, mode, emit):
        call_count[0] += 1
        if call_count[0] == 1:
            return {
                "content": "I'll search for files first.",
                "tool_calls": [{
                    "id": "call_1",
                    "name": "file_read",
                    "arguments": {"action": "list", "path": "."},
                }],
                "tool_calls_raw": [],
                "finish_reason": "tool_calls",
            }
        else:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "call_2",
                    "name": "finish",
                    "arguments": {"summary": "Done.", "completedItems": ["Listed files"]},
                }],
                "tool_calls_raw": [],
                "finish_reason": "stop",
            }

    with tempfile.TemporaryDirectory() as tmp:
        task = await orch_storage.create_task(
            agent_storage,
            user_id="test-user",
            title="list files",
            prompt="list files",
            workspace_path=tmp,
            conversation_id=None,
            active_skill_ids=[],
            settings=OrchestratorSettings(agent_loop_enabled=True, trust_mode=True).model_dump(by_alias=True),
            context={},
            budget={},
        )
        runner = OrchestratorRunner()
        with patch.object(runner.agent_loop_runner, "_call_model", side_effect=mock_call_model):
            request = OrchestratorTaskCreateRequest(
                prompt="list files",
                workspace_path=tmp,
            )
            settings = OrchestratorSettings(agent_loop_enabled=True, trust_mode=True)
            await runner.run(storage=agent_storage, task_id=task["id"], request=request, settings=settings)

    events = await orch_storage.list_events(agent_storage, task["id"])
    kinds = [e["kind"] for e in events]
    assert "agent_thought_summary" in kinds
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert "done" in kinds
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_shell_exec_auto_approves_low_risk_in_trust_mode(agent_storage):
    """Test that low-risk shell commands auto-approve in trust mode."""
    from unittest.mock import patch

    async def mock_call_model(*, loop_ctx, task_model, mode, emit):
        return {
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "name": "shell_exec",
                "arguments": {"command": "echo test"},
            }],
            "tool_calls_raw": [],
            "finish_reason": "tool_calls",
        }

    with tempfile.TemporaryDirectory() as tmp:
        task = await orch_storage.create_task(
            agent_storage,
            user_id="test-user",
            title="run echo",
            prompt="run echo",
            workspace_path=tmp,
            conversation_id=None,
            active_skill_ids=[],
            settings=OrchestratorSettings(agent_loop_enabled=True, trust_mode=True).model_dump(by_alias=True),
            context={},
            budget={},
        )
        runner = OrchestratorRunner()
        call_count = [0]

        async def mock_call_model_count(*, loop_ctx, task_model, mode, emit):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "name": "shell_exec",
                        "arguments": {"command": "echo test"},
                    }],
                    "tool_calls_raw": [],
                    "finish_reason": "tool_calls",
                }
            return {
                "content": "",
                "tool_calls": [{"id": "call_2", "name": "finish", "arguments": {"summary": "Done"}}],
                "tool_calls_raw": [],
                "finish_reason": "stop",
            }

        with patch.object(runner.agent_loop_runner, "_call_model", side_effect=mock_call_model_count):
            request = OrchestratorTaskCreateRequest(prompt="run echo", workspace_path=tmp)
            settings = OrchestratorSettings(agent_loop_enabled=True, trust_mode=True)
            await runner.run(storage=agent_storage, task_id=task["id"], request=request, settings=settings)

    events = await orch_storage.list_events(agent_storage, task["id"])
    kinds = [e["kind"] for e in events]
    assert "command" in kinds
    assert "question" not in kinds


@pytest.mark.asyncio
async def test_shell_exec_high_risk_generates_permission(agent_storage):
    """Test that high-risk shell commands generate a permission request."""
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as tmp:
        task = await orch_storage.create_task(
            agent_storage,
            user_id="test-user",
            title="push code",
            prompt="push code",
            workspace_path=tmp,
            conversation_id=None,
            active_skill_ids=[],
            settings=OrchestratorSettings(agent_loop_enabled=True, trust_mode=False).model_dump(by_alias=True),
            context={},
            budget={},
        )
        runner = OrchestratorRunner()

        async def mock_call_model(*, loop_ctx, task_model, mode, emit):
            return {
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "name": "shell_exec",
                    "arguments": {"command": "git push origin main"},
                }],
                "tool_calls_raw": [],
                "finish_reason": "tool_calls",
            }

        with patch.object(runner.agent_loop_runner, "_call_model", side_effect=mock_call_model):
            request = OrchestratorTaskCreateRequest(prompt="push code", workspace_path=tmp)
            settings = OrchestratorSettings(agent_loop_enabled=True, trust_mode=False)
            await runner.run(storage=agent_storage, task_id=task["id"], request=request, settings=settings)

    events = await orch_storage.list_events(agent_storage, task["id"])
    kinds = [e["kind"] for e in events]
    assert "question" in kinds
    stored_task = await orch_storage.get_task(agent_storage, task["id"])
    assert stored_task["status"] == "paused"


@pytest.mark.asyncio
async def test_agent_loop_cancellation_between_rounds(agent_storage):
    """Test that cancellation stops the loop between rounds."""
    from unittest.mock import patch

    call_count = [0]

    async def mock_call_model(*, loop_ctx, task_model, mode, emit):
        call_count[0] += 1
        if call_count[0] == 1:
            return {
                "content": "Working...",
                "tool_calls": [{
                    "id": "call_1",
                    "name": "file_read",
                    "arguments": {"action": "list", "path": "."},
                }],
                "tool_calls_raw": [],
                "finish_reason": "tool_calls",
            }
        await orch_storage.update_task_status(agent_storage, "test-cancel-task", status="cancelled")
        return {
            "content": "",
            "tool_calls": [{"id": "call_2", "name": "finish", "arguments": {"summary": "Done"}}],
            "tool_calls_raw": [],
            "finish_reason": "stop",
        }

    with tempfile.TemporaryDirectory() as tmp:
        task = await orch_storage.create_task(
            agent_storage,
            user_id="test-user",
            title="test cancel",
            prompt="test",
            workspace_path=tmp,
            conversation_id=None,
            active_skill_ids=[],
            settings=OrchestratorSettings(agent_loop_enabled=True).model_dump(by_alias=True),
            context={},
            budget={},
        )
        # Rename task to match the mock's hardcoded ID
        # Actually, let's use the real task ID
        task_id = task["id"]

        # Override the mock to use the real task_id
        async def mock_call_model_real(*, loop_ctx, task_model, mode, emit):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "content": "Working...",
                    "tool_calls": [{
                        "id": "call_1",
                        "name": "file_read",
                        "arguments": {"action": "list", "path": "."},
                    }],
                    "tool_calls_raw": [],
                    "finish_reason": "tool_calls",
                }
            await orch_storage.update_task_status(agent_storage, task_id, status="cancelled")
            return {
                "content": "",
                "tool_calls": [{"id": "call_2", "name": "finish", "arguments": {"summary": "Done"}}],
                "tool_calls_raw": [],
                "finish_reason": "stop",
            }

        runner = OrchestratorRunner()
        with patch.object(runner.agent_loop_runner, "_call_model", side_effect=mock_call_model_real):
            request = OrchestratorTaskCreateRequest(prompt="test", workspace_path=tmp)
            settings = OrchestratorSettings(agent_loop_enabled=True)
            await runner.run(storage=agent_storage, task_id=task_id, request=request, settings=settings)

    stored_task = await orch_storage.get_task(agent_storage, task_id)
    assert stored_task["status"] == "cancelled"


@pytest.mark.asyncio
async def test_agent_loop_stops_on_max_rounds(agent_storage):
    """Test that the loop stops when max_rounds is reached."""
    from unittest.mock import patch

    async def mock_call_model(*, loop_ctx, task_model, mode, emit):
        return {
            "content": "Continuing...",
            "tool_calls": [{
                "id": f"call_{loop_ctx.rounds}",
                "name": "file_read",
                "arguments": {"action": "list", "path": "."},
            }],
            "tool_calls_raw": [],
            "finish_reason": "tool_calls",
        }

    with tempfile.TemporaryDirectory() as tmp:
        task = await orch_storage.create_task(
            agent_storage,
            user_id="test-user",
            title="test budget",
            prompt="test",
            workspace_path=tmp,
            conversation_id=None,
            active_skill_ids=[],
            settings=OrchestratorSettings(agent_loop_enabled=True, max_rounds=2).model_dump(by_alias=True),
            context={},
            budget={},
        )
        runner = OrchestratorRunner()
        with patch.object(runner.agent_loop_runner, "_call_model", side_effect=mock_call_model):
            request = OrchestratorTaskCreateRequest(prompt="test", workspace_path=tmp, max_rounds=2)
            settings = OrchestratorSettings(agent_loop_enabled=True, max_rounds=2)
            await runner.run(storage=agent_storage, task_id=task["id"], request=request, settings=settings)

    stored_task = await orch_storage.get_task(agent_storage, task["id"])
    assert stored_task["status"] == "done"
    events = await orch_storage.list_events(agent_storage, task["id"])
    status_events = [
        e for e in events
        if e["kind"] == "status" and "ended" in (e.get("summary") or "").lower()
    ]
    assert len(status_events) > 0


@pytest.mark.asyncio
async def test_permission_request_stores_extended_payload(agent_storage):
    """Test that extended payload fields are stored and retrieved."""
    task = await orch_storage.create_task(
        agent_storage,
        user_id="test-user",
        title="test",
        prompt="test",
        workspace_path="/tmp",
        conversation_id=None,
        active_skill_ids=[],
        settings=OrchestratorSettings(agent_loop_enabled=False).model_dump(by_alias=True),
        context={},
        budget={},
    )
    perm = await orch_storage.create_permission_request(
        agent_storage,
        task_id=task["id"],
        tool_name="shell_exec",
        arguments_preview='{"command": "rm -rf /"}',
        risk_level="dangerous",
        payload={
            "toolName": "shell_exec",
            "toolArguments": {"command": "rm -rf /"},
            "toolArgumentsPreview": '{"command": "rm -rf /"}',
            "riskLevel": "dangerous",
            "autoApproved": False,
            "reason": "dangerous pattern matched: rm -rf",
            "commandPreview": "rm -rf /",
            "workspacePath": "/tmp",
            "round": 3,
        },
    )
    assert perm["id"]
    fetched = await orch_storage.get_permission_request(agent_storage, perm["id"])
    payload = fetched.get("payload") if isinstance(fetched.get("payload"), dict) else {}
    assert payload.get("riskLevel") == "dangerous"
    assert payload.get("autoApproved") is False
    assert payload.get("commandPreview") == "rm -rf /"
    assert payload.get("workspacePath") == "/tmp"
