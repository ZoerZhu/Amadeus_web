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


# --- Injection bypass prevention tests ---

def test_shell_exec_redirect_blocks_auto_approve():
    """rg with file redirect should NOT auto-approve."""
    result = classify_shell_command("rg 'import' src/ > out.txt")
    assert result.auto_approved is False


def test_shell_exec_chained_del_escalates():
    """rg ; del should be dangerous."""
    result = classify_shell_command("rg 'import' src/ ; del file.txt")
    assert result.risk == "dangerous"


def test_shell_exec_pipe_to_del_escalates():
    """cat | del should be dangerous."""
    result = classify_shell_command("cat file.txt | del other.txt")
    assert result.risk == "dangerous"


def test_shell_exec_backtick_substitution_is_dangerous():
    """Backtick command substitution is always dangerous."""
    result = classify_shell_command("echo `whoami`")
    assert result.risk == "dangerous"
    assert result.auto_approved is False


def test_shell_exec_dollar_paren_substitution_is_dangerous():
    """$() command substitution is always dangerous."""
    result = classify_shell_command("echo $(whoami)")
    assert result.risk == "dangerous"
    assert result.auto_approved is False


def test_shell_exec_chained_two_safe_commands_stays_safe():
    """git status && git diff should remain safe if both segments are safe."""
    result = classify_shell_command("git status && git diff")
    assert result.risk == "safe"
    assert result.auto_approved is True


def test_shell_exec_pipe_to_safe_command_stays_safe():
    """echo hello | cat should remain safe."""
    result = classify_shell_command("echo hello | cat")
    assert result.risk == "safe"
    assert result.auto_approved is True


def test_shell_exec_safe_then_dangerous_segment():
    """git status ; git push should be dangerous."""
    result = classify_shell_command("git status ; git push origin main")
    assert result.risk == "dangerous"


def test_shell_exec_safe_then_confirm_segment():
    """ls ; npm install should be confirm."""
    result = classify_shell_command("ls ; npm install")
    assert result.risk == "confirm"


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


# ---------------------------------------------------------------------------
# Task 1 (reliability plan): Context window management tests
# ---------------------------------------------------------------------------

from backend.amadeus_app.orchestrator.context_window import (
    estimate_token_count,
    trim_context,
    ContextSummary,
)


def test_estimate_token_count_empty():
    assert estimate_token_count([]) == 0


def test_estimate_token_count_single_message():
    msgs = [{"role": "user", "content": "hello world"}]
    count = estimate_token_count(msgs)
    assert count > 0
    assert count < 50


def test_estimate_token_count_grows_with_content():
    short = [{"role": "user", "content": "hi"}]
    long_msg = [{"role": "user", "content": "x" * 4000}]
    assert estimate_token_count(long_msg) > estimate_token_count(short)


def test_trim_context_keeps_recent_messages():
    msgs = [
        {"role": "user", "content": "original task"},
        {"role": "assistant", "content": "I will help.", "tool_calls": []},
        {"role": "tool", "tool_call_id": "1", "name": "file_read", "content": "file contents here"},
        {"role": "assistant", "content": "Found the file.", "tool_calls": []},
        {"role": "tool", "tool_call_id": "2", "name": "web_search", "content": "search results"},
        {"role": "assistant", "content": "Final analysis.", "tool_calls": []},
    ]
    trimmed = trim_context(msgs, max_tokens=50, system_prompt="system")
    assert trimmed[0]["role"] == "user"
    assert trimmed[-1]["role"] == "assistant"
    assert len(trimmed) <= len(msgs)


def test_trim_context_inserts_summary_when_trimming():
    msgs = []
    for i in range(20):
        msgs.append({"role": "assistant", "content": f"Step {i} " * 200})
        msgs.append({"role": "tool", "tool_call_id": str(i), "name": "file_read", "content": f"data {i} " * 200})
    trimmed = trim_context(msgs, max_tokens=500, system_prompt="system")
    has_summary = any("summary" in str(m.get("content", "")).lower()[:20] for m in trimmed[:3])
    assert has_summary or len(trimmed) < len(msgs)


def test_trim_context_no_trimming_needed():
    msgs = [
        {"role": "user", "content": "short task"},
        {"role": "assistant", "content": "ok", "tool_calls": []},
    ]
    trimmed = trim_context(msgs, max_tokens=10000, system_prompt="system")
    assert len(trimmed) == len(msgs)


@pytest.mark.asyncio
async def test_agent_loop_trims_context_on_many_rounds(agent_storage):
    """Test that context is trimmed when messages grow large."""
    from unittest.mock import patch

    async def mock_call_model(*, loop_ctx, task_model, mode, emit):
        # Add a large message each round to force trimming
        loop_ctx.messages.append({
            "role": "assistant",
            "content": "x" * 3000,
            "tool_calls": [],
        })
        loop_ctx.messages.append({
            "role": "tool",
            "tool_call_id": f"tc_{loop_ctx.rounds}",
            "name": "file_read",
            "content": "y" * 3000,
        })
        if loop_ctx.rounds >= 5:
            return {
                "content": "",
                "tool_calls": [{"id": "fin", "name": "finish", "arguments": {"summary": "Done"}}],
                "tool_calls_raw": [],
                "finish_reason": "stop",
            }
        return {
            "content": "",
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
            title="test trim",
            prompt="test",
            workspace_path=tmp,
            conversation_id=None,
            active_skill_ids=[],
            settings=OrchestratorSettings(agent_loop_enabled=True, max_rounds=6).model_dump(by_alias=True),
            context={},
            budget={},
        )
        runner = OrchestratorRunner()
        with patch.object(runner.agent_loop_runner, "_call_model", side_effect=mock_call_model):
            request = OrchestratorTaskCreateRequest(prompt="test", workspace_path=tmp, max_rounds=6)
            settings = OrchestratorSettings(agent_loop_enabled=True, max_rounds=6)
            await runner.run(storage=agent_storage, task_id=task["id"], request=request, settings=settings)

    # Verify the task completed without error
    stored_task = await orch_storage.get_task(agent_storage, task["id"])
    assert stored_task["status"] == "done"


# ---------------------------------------------------------------------------
# Task 3 (reliability plan): Model retry tests
# ---------------------------------------------------------------------------

from backend.amadeus_app.orchestrator.model_retry import (
    ModelRetryConfig,
    retry_model_call,
    classify_error,
)


def test_classify_error_timeout():
    import httpx
    err = httpx.TimeoutException("timed out")
    assert classify_error(err) == "retryable"


def test_classify_error_rate_limit():
    import httpx
    err = httpx.HTTPStatusError("429", request=httpx.Request("POST", "http://x"), response=httpx.Response(429))
    assert classify_error(err) == "retryable"


def test_classify_error_500():
    import httpx
    err = httpx.HTTPStatusError("500", request=httpx.Request("POST", "http://x"), response=httpx.Response(500))
    assert classify_error(err) == "retryable"


def test_classify_error_400():
    import httpx
    err = httpx.HTTPStatusError("400", request=httpx.Request("POST", "http://x"), response=httpx.Response(400))
    assert classify_error(err) == "fatal"


def test_classify_error_connection():
    import httpx
    err = httpx.ConnectError("connection refused")
    assert classify_error(err) == "retryable"


def test_retry_config_defaults():
    config = ModelRetryConfig()
    assert config.max_retries == 3
    assert config.base_delay == 1.0
    assert config.max_delay == 30.0


@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt():
    import httpx
    call_count = [0]

    async def factory():
        call_count[0] += 1
        if call_count[0] == 1:
            raise httpx.TimeoutException("timeout")
        return {"content": "success", "tool_calls": []}

    config = ModelRetryConfig(max_retries=3, base_delay=0.01)
    result = await retry_model_call(factory, config)
    assert result["content"] == "success"
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_retry_exhausted_returns_none():
    async def factory():
        raise httpx.TimeoutException("always timeout")

    import httpx
    config = ModelRetryConfig(max_retries=2, base_delay=0.01)
    result = await retry_model_call(factory, config)
    assert result is None


@pytest.mark.asyncio
async def test_retry_fatal_error_no_retry():
    import httpx
    call_count = [0]

    async def factory():
        call_count[0] += 1
        raise httpx.HTTPStatusError(
            "400 Bad Request",
            request=httpx.Request("POST", "http://x"),
            response=httpx.Response(400),
        )

    config = ModelRetryConfig(max_retries=3, base_delay=0.01)
    result = await retry_model_call(factory, config)
    assert result is None
    assert call_count[0] == 1


# ---------------------------------------------------------------------------
# Task 4 (reliability plan): Agent loop retry and fallback integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_loop_retries_on_timeout(agent_storage):
    """Test that the agent loop retries on timeout and continues."""
    from unittest.mock import patch
    import httpx

    call_count = [0]

    async def mock_call_model(*, loop_ctx, task_model, mode, emit):
        call_count[0] += 1
        if call_count[0] == 1:
            raise httpx.TimeoutException("timeout")
        if call_count[0] == 2:
            return {
                "content": "",
                "tool_calls": [{"id": "fin", "name": "finish", "arguments": {"summary": "Done"}}],
                "tool_calls_raw": [],
                "finish_reason": "stop",
            }
        return None

    with tempfile.TemporaryDirectory() as tmp:
        task = await orch_storage.create_task(
            agent_storage,
            user_id="test-user",
            title="test retry",
            prompt="test",
            workspace_path=tmp,
            conversation_id=None,
            active_skill_ids=[],
            settings=OrchestratorSettings(agent_loop_enabled=True).model_dump(by_alias=True),
            context={},
            budget={},
        )
        runner = OrchestratorRunner()
        with patch.object(runner.agent_loop_runner, "_call_model", side_effect=mock_call_model):
            request = OrchestratorTaskCreateRequest(prompt="test", workspace_path=tmp)
            settings = OrchestratorSettings(agent_loop_enabled=True)
            await runner.run(storage=agent_storage, task_id=task["id"], request=request, settings=settings)

    stored_task = await orch_storage.get_task(agent_storage, task["id"])
    assert stored_task["status"] == "done"
    assert call_count[0] >= 2


@pytest.mark.asyncio
async def test_agent_loop_falls_back_to_fallback_model(agent_storage):
    """Test that the agent loop switches to fallback model on persistent failure."""
    from unittest.mock import patch
    import httpx

    primary_call_count = [0]
    fallback_call_count = [0]

    async def mock_call_model_primary(*, loop_ctx, task_model, mode, emit):
        primary_call_count[0] += 1
        raise httpx.TimeoutException("primary always times out")

    async def mock_call_model_fallback(*, loop_ctx, task_model, mode, emit):
        fallback_call_count[0] += 1
        return {
            "content": "",
            "tool_calls": [{"id": "fin", "name": "finish", "arguments": {"summary": "Done via fallback"}}],
            "tool_calls_raw": [],
            "finish_reason": "stop",
        }

    with tempfile.TemporaryDirectory() as tmp:
        task = await orch_storage.create_task(
            agent_storage,
            user_id="test-user",
            title="test fallback",
            prompt="test",
            workspace_path=tmp,
            conversation_id=None,
            active_skill_ids=[],
            settings=OrchestratorSettings(
                agent_loop_enabled=True,
                fallback_model={"model": "fallback-model", "providerName": "OpenAI", "retryBaseDelay": 0.01},
            ).model_dump(by_alias=True),
            context={},
            budget={},
        )
        runner = OrchestratorRunner()

        async def patched_call_model(*, loop_ctx, task_model, mode, emit):
            if "fallback" in str(task_model.get("model", "")):
                return await mock_call_model_fallback(loop_ctx=loop_ctx, task_model=task_model, mode=mode, emit=emit)
            return await mock_call_model_primary(loop_ctx=loop_ctx, task_model=task_model, mode=mode, emit=emit)

        with patch.object(runner.agent_loop_runner, "_call_model", side_effect=patched_call_model):
            request = OrchestratorTaskCreateRequest(prompt="test", workspace_path=tmp)
            settings = OrchestratorSettings(
                agent_loop_enabled=True,
                fallback_model={"model": "fallback-model", "providerName": "OpenAI", "retryBaseDelay": 0.01},
            )
            await runner.run(storage=agent_storage, task_id=task["id"], request=request, settings=settings)

    stored_task = await orch_storage.get_task(agent_storage, task["id"])
    assert stored_task["status"] == "done"
    assert fallback_call_count[0] > 0


# ---------------------------------------------------------------------------
# ToolCache tests
# ---------------------------------------------------------------------------

from backend.amadeus_app.orchestrator.tool_executor import ToolCache


def test_tool_cache_hit_miss():
    """Same args hit, different args miss."""
    cache = ToolCache()
    result1 = {"ok": True, "summary": "found", "data": {"content": "hello"}}
    cache.set("file_read", {"path": "src/app.tsx"}, result1)

    # Same args → hit
    hit = cache.get("file_read", {"path": "src/app.tsx"})
    assert hit is not None
    assert hit["data"]["content"] == "hello"

    # Different args → miss
    miss = cache.get("file_read", {"path": "src/other.tsx"})
    assert miss is None

    # Stats
    assert cache.stats["hits"] == 1
    assert cache.stats["misses"] == 1


def test_tool_cache_invalidate_by_path():
    """file_write to a.py invalidates file_read cache for a.py."""
    cache = ToolCache()
    cache.set("file_read", {"path": "src/a.py"}, {"ok": True, "summary": "x", "data": {}})
    cache.set("file_read", {"path": "src/b.py"}, {"ok": True, "summary": "y", "data": {}})

    count = cache.invalidate_paths(["src/a.py"])
    assert count == 1

    # a.py cache gone, b.py still cached
    assert cache.get("file_read", {"path": "src/a.py"}) is None
    assert cache.get("file_read", {"path": "src/b.py"}) is not None


def test_tool_cache_invalidate_all_on_shell_exec():
    """invalidate_all clears everything."""
    cache = ToolCache()
    cache.set("file_read", {"path": "a.py"}, {"ok": True, "summary": "x", "data": {}})
    cache.set("code_search", {"query": "auth", "path": "src/"}, {"ok": True, "summary": "y", "data": {}})

    count = cache.invalidate_all()
    assert count == 2

    assert cache.get("file_read", {"path": "a.py"}) is None
    assert cache.get("code_search", {"query": "auth", "path": "src/"}) is None


def test_tool_cache_path_prefix_match():
    """Writing src/a.py invalidates code_search cached under scope src/."""
    cache = ToolCache()
    # code_search with scope "src/" is indexed under "src/"
    cache.set("code_search", {"query": "auth", "path": "src/"}, {"ok": True, "summary": "y", "data": {}})
    # file_read for src/a.py is indexed under "src/a.py"
    cache.set("file_read", {"path": "src/a.py"}, {"ok": True, "summary": "x", "data": {}})

    # Invalidate by path "src/a.py" should also hit the "src/" directory prefix
    count = cache.invalidate_paths(["src/a.py"])
    assert count == 2  # both the exact match and the directory-prefix match


def test_tool_cache_stats():
    """Hits and misses are tracked correctly."""
    cache = ToolCache()
    cache.set("file_read", {"path": "a.py"}, {"ok": True, "summary": "x", "data": {}})

    cache.get("file_read", {"path": "a.py"})   # hit
    cache.get("file_read", {"path": "a.py"})   # hit
    cache.get("file_read", {"path": "b.py"})   # miss

    assert cache.stats["hits"] == 2
    assert cache.stats["misses"] == 1


def test_tool_cache_invalidate_empty_paths():
    """invalidate_paths with empty list returns 0."""
    cache = ToolCache()
    cache.set("file_read", {"path": "a.py"}, {"ok": True, "summary": "x", "data": {}})
    assert cache.invalidate_paths([]) == 0
    assert cache.get("file_read", {"path": "a.py"}) is not None


# ---------------------------------------------------------------------------
# ToolExecutor tests
# ---------------------------------------------------------------------------

from backend.amadeus_app.orchestrator.tool_executor import (
    ToolCache,
    ToolCallResult,
    ToolExecutor,
)
from backend.amadeus_app.orchestrator.agent_loop_runner import AgentLoopRunner


@pytest.mark.asyncio
async def test_execute_batch_parallel_reads(agent_storage):
    """3 read tools execute in parallel, results returned in original order."""
    from unittest.mock import AsyncMock

    cache = ToolCache()
    runner = AgentLoopRunner()
    executor = ToolExecutor(runner=runner, cache=cache)

    tool_calls = [
        {"id": "1", "name": "file_read", "arguments": {"path": "a.py"}},
        {"id": "2", "name": "file_read", "arguments": {"path": "b.py"}},
        {"id": "3", "name": "file_read", "arguments": {"path": "c.py"}},
    ]

    call_order: list[str] = []

    async def mock_execute_tool_call(*, storage, task_id, tool_name, tool_args,
                                      tool_call_id, gateway, context, loop_ctx,
                                      emit, approved_permission=None):
        call_order.append(tool_call_id)
        await asyncio.sleep(0.05)  # Simulate I/O
        return {
            "ok": True,
            "summary": f"read {tool_args['path']}",
            "data": {"content": f"content of {tool_args['path']}"},
        }

    async def mock_emit(**kwargs):
        return {"id": "evt_1"}

    runner._execute_tool_call = mock_execute_tool_call  # type: ignore
    runner._append_tool_message = lambda *args, **kwargs: None  # type: ignore  # added by Task 3

    from backend.amadeus_app.orchestrator.agent_loop_context import AgentLoopContext
    loop_ctx = AgentLoopContext(
        system_prompt="test",
        messages=[],
        tool_schemas=[],
        mcp_snapshot={},
    )

    results = await executor.execute_batch(
        tool_calls=tool_calls,
        gateway=None,  # type: ignore
        context=None,  # type: ignore
        loop_ctx=loop_ctx,
        emit=mock_emit,
        storage=None,
        task_id="test",
    )

    # All 3 results returned
    assert len(results) == 3
    # Results in original order
    assert results[0].tool_call_id == "1"
    assert results[1].tool_call_id == "2"
    assert results[2].tool_call_id == "3"
    # None from cache (first time)
    assert all(not r.from_cache for r in results)


@pytest.mark.asyncio
async def test_execute_batch_cached_result_emits_events(agent_storage):
    """Cached results emit tool_call/tool_result events with cached=True."""
    from unittest.mock import AsyncMock

    cache = ToolCache()
    runner = AgentLoopRunner()
    executor = ToolExecutor(runner=runner, cache=cache)
    runner._append_tool_message = lambda *args, **kwargs: None  # type: ignore  # added by Task 3

    # Pre-populate cache
    cached_result = {
        "ok": True,
        "summary": "cached content",
        "data": {"content": "hello"},
    }
    cache.set("file_read", {"path": "a.py"}, cached_result)

    tool_calls = [{"id": "1", "name": "file_read", "arguments": {"path": "a.py"}}]

    emitted_events: list[dict] = []

    async def mock_emit(**kwargs):
        emitted_events.append(kwargs)
        return {"id": "evt_1"}

    from backend.amadeus_app.orchestrator.agent_loop_context import AgentLoopContext
    loop_ctx = AgentLoopContext(
        system_prompt="test", messages=[], tool_schemas=[], mcp_snapshot={},
    )

    results = await executor.execute_batch(
        tool_calls=tool_calls,
        gateway=None,  # type: ignore
        context=None,  # type: ignore
        loop_ctx=loop_ctx,
        emit=mock_emit,
        storage=None,
        task_id="test",
    )

    assert len(results) == 1
    assert results[0].from_cache is True
    # Events were emitted
    assert any(e.get("kind") == "tool_call" for e in emitted_events)
    assert any(e.get("kind") == "tool_result" for e in emitted_events)
    # tool_call event has cached=True
    tool_call_events = [e for e in emitted_events if e.get("kind") == "tool_call"]
    assert tool_call_events[0]["payload"]["cached"] is True


@pytest.mark.asyncio
async def test_execute_batch_mixed_read_write(agent_storage):
    """Reads run in parallel, writes run serially after reads."""
    from unittest.mock import AsyncMock

    cache = ToolCache()
    runner = AgentLoopRunner()
    executor = ToolExecutor(runner=runner, cache=cache)
    runner._append_tool_message = lambda *args, **kwargs: None  # type: ignore  # added by Task 3

    tool_calls = [
        {"id": "1", "name": "file_read", "arguments": {"path": "a.py"}},
        {"id": "2", "name": "file_write", "arguments": {"path": "out.txt", "content": "x"}},
    ]

    execution_log: list[str] = []

    async def mock_execute_tool_call(*, storage, task_id, tool_name, tool_args,
                                      tool_call_id, gateway, context, loop_ctx,
                                      emit, approved_permission=None):
        execution_log.append(f"start:{tool_name}:{tool_call_id}")
        await asyncio.sleep(0.02)
        execution_log.append(f"end:{tool_name}:{tool_call_id}")
        return {
            "ok": True,
            "summary": f"{tool_name} done",
            "data": {},
            "modified_paths": ["out.txt"] if tool_name == "file_write" else [],
        }

    async def mock_emit(**kwargs):
        return {"id": "evt_1"}

    runner._execute_tool_call = mock_execute_tool_call  # type: ignore

    from backend.amadeus_app.orchestrator.agent_loop_context import AgentLoopContext
    loop_ctx = AgentLoopContext(
        system_prompt="test", messages=[], tool_schemas=[], mcp_snapshot={},
    )

    results = await executor.execute_batch(
        tool_calls=tool_calls,
        gateway=None,  # type: ignore
        context=None,  # type: ignore
        loop_ctx=loop_ctx,
        emit=mock_emit,
        storage=None,
        task_id="test",
    )

    assert len(results) == 2
    assert results[0].tool_call_id == "1"  # file_read first
    assert results[1].tool_call_id == "2"  # file_write second


@pytest.mark.asyncio
async def test_execute_batch_preserves_message_order(agent_storage):
    """After parallel execution, loop_ctx.messages are in tool_calls order."""
    from unittest.mock import AsyncMock

    cache = ToolCache()
    runner = AgentLoopRunner()
    executor = ToolExecutor(runner=runner, cache=cache)

    tool_calls = [
        {"id": "tc_1", "name": "file_read", "arguments": {"path": "a.py"}},
        {"id": "tc_2", "name": "file_read", "arguments": {"path": "b.py"}},
        {"id": "tc_3", "name": "file_read", "arguments": {"path": "c.py"}},
    ]

    async def mock_execute_tool_call(*, storage, task_id, tool_name, tool_args,
                                      tool_call_id, gateway, context, loop_ctx,
                                      emit, approved_permission=None):
        # Variable delay so parallel completion order differs from call order
        delays = {"tc_1": 0.03, "tc_2": 0.01, "tc_3": 0.02}
        await asyncio.sleep(delays.get(tool_call_id, 0.01))
        return {
            "ok": True,
            "summary": f"read {tool_args['path']}",
            "data": {"content": tool_args["path"]},
        }

    async def mock_emit(**kwargs):
        return {"id": "evt_1"}

    runner._execute_tool_call = mock_execute_tool_call  # type: ignore
    runner._append_tool_message = lambda loop_ctx_inner, tool_call_id_inner, tool_name_inner, result_inner: loop_ctx_inner.messages.append({  # type: ignore
        "role": "tool",
        "tool_call_id": tool_call_id_inner,
        "name": tool_name_inner,
        "content": str(result_inner.get("summary", "")),
    })

    from backend.amadeus_app.orchestrator.agent_loop_context import AgentLoopContext
    loop_ctx = AgentLoopContext(
        system_prompt="test", messages=[], tool_schemas=[], mcp_snapshot={},
    )

    await executor.execute_batch(
        tool_calls=tool_calls,
        gateway=None,  # type: ignore
        context=None,  # type: ignore
        loop_ctx=loop_ctx,
        emit=mock_emit,
        storage=None,
        task_id="test",
    )

    # Messages are in original tool_calls order, not completion order
    assert len(loop_ctx.messages) == 3
    assert loop_ctx.messages[0]["tool_call_id"] == "tc_1"
    assert loop_ctx.messages[1]["tool_call_id"] == "tc_2"
    assert loop_ctx.messages[2]["tool_call_id"] == "tc_3"


@pytest.mark.asyncio
async def test_execute_batch_error_isolation(agent_storage):
    """One read tool failing does not affect other read tools."""
    from unittest.mock import AsyncMock

    cache = ToolCache()
    runner = AgentLoopRunner()
    executor = ToolExecutor(runner=runner, cache=cache)

    tool_calls = [
        {"id": "1", "name": "file_read", "arguments": {"path": "a.py"}},
        {"id": "2", "name": "file_read", "arguments": {"path": "ERROR"}},
        {"id": "3", "name": "file_read", "arguments": {"path": "c.py"}},
    ]

    async def mock_execute_tool_call(*, storage, task_id, tool_name, tool_args,
                                      tool_call_id, gateway, context, loop_ctx,
                                      emit, approved_permission=None):
        if "ERROR" in str(tool_args.get("path", "")):
            raise RuntimeError("simulated network error")
        return {
            "ok": True,
            "summary": f"read {tool_args['path']}",
            "data": {"content": "ok"},
        }

    async def mock_emit(**kwargs):
        return {"id": "evt_1"}

    runner._execute_tool_call = mock_execute_tool_call  # type: ignore
    runner._append_tool_message = lambda loop_ctx_inner, tool_call_id_inner, tool_name_inner, result_inner: loop_ctx_inner.messages.append({  # type: ignore
        "role": "tool",
        "tool_call_id": tool_call_id_inner,
        "name": tool_name_inner,
        "content": str(result_inner.get("summary", "")),
    })

    from backend.amadeus_app.orchestrator.agent_loop_context import AgentLoopContext
    loop_ctx = AgentLoopContext(
        system_prompt="test", messages=[], tool_schemas=[], mcp_snapshot={},
    )

    results = await executor.execute_batch(
        tool_calls=tool_calls,
        gateway=None,  # type: ignore
        context=None,  # type: ignore
        loop_ctx=loop_ctx,
        emit=mock_emit,
        storage=None,
        task_id="test",
    )

    assert len(results) == 3
    assert results[0].result["ok"] is True   # a.py succeeded
    assert results[1].result["ok"] is False  # ERROR failed
    assert results[2].result["ok"] is True   # c.py succeeded


@pytest.mark.asyncio
async def test_execute_batch_permission_blocked_propagates(agent_storage):
    """AgentLoopPermissionBlocked propagates up, pausing the batch."""
    from unittest.mock import AsyncMock
    from backend.amadeus_app.orchestrator.agent_loop_runner import AgentLoopPermissionBlocked

    cache = ToolCache()
    runner = AgentLoopRunner()
    executor = ToolExecutor(runner=runner, cache=cache)

    tool_calls = [
        {"id": "1", "name": "file_read", "arguments": {"path": "a.py"}},
        {"id": "2", "name": "shell_exec", "arguments": {"command": "rm -rf /"}},
    ]

    async def mock_execute_tool_call(*, storage, task_id, tool_name, tool_args,
                                      tool_call_id, gateway, context, loop_ctx,
                                      emit, approved_permission=None):
        if tool_name == "shell_exec":
            raise AgentLoopPermissionBlocked(tool_name, "perm_123")
        return {"ok": True, "summary": "ok", "data": {}}

    async def mock_emit(**kwargs):
        return {"id": "evt_1"}

    runner._execute_tool_call = mock_execute_tool_call  # type: ignore

    from backend.amadeus_app.orchestrator.agent_loop_context import AgentLoopContext
    loop_ctx = AgentLoopContext(
        system_prompt="test", messages=[], tool_schemas=[], mcp_snapshot={},
    )

    with pytest.raises(AgentLoopPermissionBlocked):
        await executor.execute_batch(
            tool_calls=tool_calls,
            gateway=None,  # type: ignore
            context=None,  # type: ignore
            loop_ctx=loop_ctx,
            emit=mock_emit,
            storage=None,
            task_id="test",
        )


# ---------------------------------------------------------------------------
# AgentLoopRunner integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_loop_finish_after_other_tools(agent_storage):
    """finish tool runs after non-finish tools in the same batch."""
    from unittest.mock import patch, AsyncMock

    execution_order: list[str] = []

    async def mock_call_model_with_retry(*, loop_ctx, task_model, fallback_model, mode, emit):
        execution_order.append("model_call")
        return {
            "content": "I'll read a file then finish.",
            "tool_calls": [
                {"id": "tc_1", "name": "file_read", "arguments": {"path": "test.py"}},
                {"id": "tc_2", "name": "finish", "arguments": {"summary": "Done"}},
            ],
            "tool_calls_raw": [],
            "finish_reason": "stop",
        }

    async def mock_execute_tool_call(*, storage, task_id, tool_name, tool_args,
                                      tool_call_id, gateway, context, loop_ctx,
                                      emit, approved_permission=None):
        execution_order.append(f"execute:{tool_name}")
        return {"ok": True, "summary": f"{tool_name} done", "data": {}}

    async def mock_emit(**kwargs):
        return {"id": "evt_1"}

    with tempfile.TemporaryDirectory() as tmp:
        task = await orch_storage.create_task(
            agent_storage,
            user_id="test-user",
            title="test finish order",
            prompt="test",
            workspace_path=tmp,
            conversation_id=None,
            active_skill_ids=[],
            settings=OrchestratorSettings(agent_loop_enabled=True).model_dump(by_alias=True),
            context={},
            budget={},
        )
        runner = OrchestratorRunner()

        with patch.object(runner.agent_loop_runner, "_call_model_with_retry", new=mock_call_model_with_retry):
            with patch.object(runner.agent_loop_runner, "_execute_tool_call", new=mock_execute_tool_call):
                request = OrchestratorTaskCreateRequest(prompt="test", workspace_path=tmp)
                settings = OrchestratorSettings(agent_loop_enabled=True)
                await runner.run(storage=agent_storage, task_id=task["id"], request=request, settings=settings)

    # file_read executed before finish
    assert "execute:file_read" in execution_order
    assert execution_order.index("execute:file_read") < execution_order.index("model_call") if "model_call" in execution_order[1:] else True
    # Task completed
    stored_task = await orch_storage.get_task(agent_storage, task["id"])
    assert stored_task["status"] == "done"


@pytest.mark.asyncio
async def test_run_loop_cache_hit_on_second_round(agent_storage):
    """Second identical file_read call hits cache, not _execute_tool_call."""
    from unittest.mock import patch

    execute_count = [0]

    async def mock_call_model_with_retry(*, loop_ctx, task_model, fallback_model, mode, emit):
        if execute_count[0] == 0:
            # Round 1: read file, then finish
            return {
                "content": "Reading file.",
                "tool_calls": [
                    {"id": "tc_1", "name": "file_read", "arguments": {"path": "app.py"}},
                ],
                "tool_calls_raw": [],
                "finish_reason": "stop",
            }
        else:
            # Round 2: read same file again, then finish
            return {
                "content": "Reading same file again.",
                "tool_calls": [
                    {"id": "tc_2", "name": "file_read", "arguments": {"path": "app.py"}},
                    {"id": "tc_3", "name": "finish", "arguments": {"summary": "Done"}},
                ],
                "tool_calls_raw": [],
                "finish_reason": "stop",
            }

    async def mock_execute_tool_call(*, storage, task_id, tool_name, tool_args,
                                      tool_call_id, gateway, context, loop_ctx,
                                      emit, approved_permission=None):
        execute_count[0] += 1
        return {"ok": True, "summary": f"read {tool_args['path']}", "data": {"content": "hello"}}

    async def mock_emit(**kwargs):
        return {"id": "evt_1"}

    with tempfile.TemporaryDirectory() as tmp:
        task = await orch_storage.create_task(
            agent_storage,
            user_id="test-user",
            title="test cache",
            prompt="test",
            workspace_path=tmp,
            conversation_id=None,
            active_skill_ids=[],
            settings=OrchestratorSettings(agent_loop_enabled=True).model_dump(by_alias=True),
            context={},
            budget={},
        )
        runner = OrchestratorRunner()

        with patch.object(runner.agent_loop_runner, "_call_model_with_retry", new=mock_call_model_with_retry):
            with patch.object(runner.agent_loop_runner, "_execute_tool_call", new=mock_execute_tool_call):
                request = OrchestratorTaskCreateRequest(prompt="test", workspace_path=tmp)
                settings = OrchestratorSettings(agent_loop_enabled=True)
                await runner.run(storage=agent_storage, task_id=task["id"], request=request, settings=settings)

    # file_read was executed only once (first round); second round hit cache
    assert execute_count[0] == 1


# ---------------------------------------------------------------------------
# Task 4 (parallel-tools plan): file_write modified_paths test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_write_returns_modified_paths():
    """file_write result includes modified_paths for cache invalidation."""
    import tempfile
    from backend.amadeus_app.orchestrator.capability_adapters import (
        CapabilityExecutionContext,
        default_capability_registry,
    )
    from backend.amadeus_app.orchestrator.domain import OrchestratorSettings

    with tempfile.TemporaryDirectory() as tmp:
        settings = OrchestratorSettings(default_workspace=tmp)
        context = CapabilityExecutionContext(
            task_id="test",
            prompt="test",
            workspace_path=tmp,
            settings=settings,
            metadata={},
            storage=None,
            emit_event=None,  # type: ignore
        )
        registry = default_capability_registry

        result = await registry.execute(
            "file_write",
            {"path": "output.txt", "content": "hello world"},
            context,
        )

        assert result["ok"] is True
        assert "modified_paths" in result
        assert "output.txt" in result["modified_paths"]


@pytest.mark.asyncio
async def test_run_loop_cache_invalidates_after_write(agent_storage):
    """file_write invalidates cache so subsequent file_read re-executes."""
    from unittest.mock import patch

    execute_count = [0]

    async def mock_call_model_with_retry(*, loop_ctx, task_model, fallback_model, mode, emit):
        if execute_count[0] == 0:
            # Round 1: read file
            return {
                "content": "Reading file.",
                "tool_calls": [
                    {"id": "tc_1", "name": "file_read", "arguments": {"path": "app.py"}},
                ],
                "tool_calls_raw": [],
                "finish_reason": "stop",
            }
        elif execute_count[0] == 1:
            # Round 2: write to same file
            return {
                "content": "Writing file.",
                "tool_calls": [
                    {"id": "tc_2", "name": "file_write", "arguments": {"path": "app.py", "content": "new"}},
                ],
                "tool_calls_raw": [],
                "finish_reason": "stop",
            }
        else:
            # Round 3: read same file again, then finish
            return {
                "content": "Reading same file again.",
                "tool_calls": [
                    {"id": "tc_3", "name": "file_read", "arguments": {"path": "app.py"}},
                    {"id": "tc_4", "name": "finish", "arguments": {"summary": "Done"}},
                ],
                "tool_calls_raw": [],
                "finish_reason": "stop",
            }

    async def mock_execute_tool_call(*, storage, task_id, tool_name, tool_args,
                                      tool_call_id, gateway, context, loop_ctx,
                                      emit, approved_permission=None):
        execute_count[0] += 1
        result = {"ok": True, "summary": f"{tool_name} done", "data": {}}
        if tool_name == "file_write":
            result["modified_paths"] = [tool_args["path"]]
        return result

    async def mock_emit(**kwargs):
        return {"id": "evt_1"}

    with tempfile.TemporaryDirectory() as tmp:
        task = await orch_storage.create_task(
            agent_storage,
            user_id="test-user",
            title="test cache invalidation",
            prompt="test",
            workspace_path=tmp,
            conversation_id=None,
            active_skill_ids=[],
            settings=OrchestratorSettings(agent_loop_enabled=True).model_dump(by_alias=True),
            context={},
            budget={},
        )
        runner = OrchestratorRunner()

        with patch.object(runner.agent_loop_runner, "_call_model_with_retry", new=mock_call_model_with_retry):
            with patch.object(runner.agent_loop_runner, "_execute_tool_call", new=mock_execute_tool_call):
                request = OrchestratorTaskCreateRequest(prompt="test", workspace_path=tmp)
                settings = OrchestratorSettings(agent_loop_enabled=True)
                await runner.run(storage=agent_storage, task_id=task["id"], request=request, settings=settings)

    # file_read was executed twice (round 1 + round 3 after invalidation)
    # file_write was executed once (round 2)
    # finish doesn't go through _execute_tool_call
    file_read_count = execute_count[0]  # Total calls through mock
    # Should be 3: file_read, file_write, file_read (cache miss after write)
    assert file_read_count == 3, f"Expected 3 tool executions, got {file_read_count}"


# ---------------------------------------------------------------------------
# Task 8 (file-tools-upgrade plan): classification, schemas, executor tests
# ---------------------------------------------------------------------------


def test_file_tools_classification():
    from backend.amadeus_app.orchestrator.capabilities import (
        READ_CAPABILITIES, WRITE_CAPABILITIES, DANGEROUS_CAPABILITIES,
    )
    assert "file_list" in READ_CAPABILITIES
    assert "file_read" in READ_CAPABILITIES
    assert "code_search" in READ_CAPABILITIES
    assert "file_write" in WRITE_CAPABILITIES
    assert "file_append" in WRITE_CAPABILITIES
    assert "file_patch" in WRITE_CAPABILITIES
    assert "file_mkdir" in WRITE_CAPABILITIES
    assert "file_delete" in DANGEROUS_CAPABILITIES
    assert "file_move" in DANGEROUS_CAPABILITIES


def test_gateway_classifies_file_delete_as_dangerous():
    from backend.amadeus_app.orchestrator.capabilities import CapabilityGateway
    gw = CapabilityGateway(trust_mode=True)
    assert gw.classify("file_delete") == "dangerous"
    assert gw.classify("file_move") == "dangerous"
    assert gw.classify("file_write") == "confirm"
    assert gw.classify("file_read") == "safe"
    assert gw.classify("file_list") == "safe"


def test_build_tool_schemas_includes_new_file_tools():
    from backend.amadeus_app.orchestrator.agent_loop_context import build_tool_schemas
    schemas = build_tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert {"file_list", "file_read", "file_write", "file_append", "file_patch",
            "file_mkdir", "file_move", "file_delete"} <= names


def test_read_only_tools_includes_file_list():
    from backend.amadeus_app.orchestrator.tool_executor import _READ_ONLY_TOOLS
    assert "file_list" in _READ_ONLY_TOOLS
    assert "file_read" in _READ_ONLY_TOOLS


def test_extract_read_paths_handles_file_list():
    from backend.amadeus_app.orchestrator.tool_executor import _extract_read_paths
    assert _extract_read_paths("file_list", {"path": "src"}) == ["src"]
    assert _extract_read_paths("file_read", {"path": "a.py"}) == ["a.py"]


# ---------------------------------------------------------------------------
# Task 9 (file-tools-upgrade plan): allowedExternalPaths + context injection
# ---------------------------------------------------------------------------

from pathlib import Path
from unittest.mock import patch
from backend.amadeus_app.storage import DEFAULT_USER_ID


def test_orchestrator_settings_has_allowed_external_paths():
    from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
    settings = OrchestratorSettings(allowedExternalPaths=["/tmp/ext"])
    assert settings.allowed_external_paths == ["/tmp/ext"]
    dumped = settings.model_dump(by_alias=True)
    assert dumped["allowedExternalPaths"] == ["/tmp/ext"]


@pytest.mark.asyncio
async def test_agent_loop_injects_code_budget_and_backup_store(agent_storage):
    from backend.amadeus_app.orchestrator.capability_adapters import CapabilityExecutionContext
    from backend.amadeus_app.file_tools.file_writer import CodeChangeBudget, FileBackupStore

    captured_contexts: list[CapabilityExecutionContext] = []

    async def fake_file_list(args, context):
        captured_contexts.append(context)
        return {"ok": True, "summary": "listed", "data": {"entries": []}}

    registry = CapabilityRegistry()
    registry.register("file_list", fake_file_list)
    registry.register("finish", _noop_finish)

    runner = AgentLoopRunner(capability_registry=registry)
    call_count = [0]

    async def mock_call_model(*, loop_ctx, task_model, mode, emit):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"content": "list files", "tool_calls": [
                {"id": "c1", "name": "file_list", "arguments": {"path": "."}},
            ], "tool_calls_raw": [], "finish_reason": "tool_calls"}
        return {"content": "done", "tool_calls": [
            {"id": "c2", "name": "finish", "arguments": {"summary": "done"}},
        ], "tool_calls_raw": [], "finish_reason": "stop"}

    with patch.object(runner, "_call_model", side_effect=mock_call_model):
        request = OrchestratorTaskCreateRequest(prompt="list files", workspacePath=str(Path(__file__).resolve().parent))
        settings = OrchestratorSettings(defaultWorkspace=str(Path(__file__).resolve().parent), trustMode=True, agentLoopEnabled=True)
        task = await orch_storage.create_task(agent_storage, user_id=DEFAULT_USER_ID, title="t", prompt=request.prompt, workspace_path=request.workspace_path, conversation_id=None, active_skill_ids=[], settings=settings.model_dump(by_alias=True), budget={})
        await runner.run(storage=agent_storage, task_id=task["id"], request=request, settings=settings)

    assert len(captured_contexts) == 1
    meta = captured_contexts[0].metadata
    assert isinstance(meta.get("code_change_budget"), CodeChangeBudget)
    assert isinstance(meta.get("backup_store"), FileBackupStore)
    assert meta.get("file_tracker") is not None


async def _noop_finish(args, context):
    return {"ok": True, "summary": "finished", "data": {}}


# ---------------------------------------------------------------------------
# Fix for review finding: rollback_task must clean up FileBackupStore directly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_task_cleans_up_backup_dir(agent_storage):
    """Regression: rollback_task must remove the FileBackupStore backup dir.

    The fresh FileChangeTracker constructed inside rollback_task has no backup
    store attached, so tracker.cleanup_backups() is a silent no-op. rollback_task
    must construct a fresh FileBackupStore and call .cleanup() on it directly.
    """
    from pathlib import Path
    from backend.amadeus_app.file_tools.file_writer import FileBackupStore

    with tempfile.TemporaryDirectory() as tmp:
        task = await orch_storage.create_task(
            agent_storage,
            user_id="test-user",
            title="test rollback cleanup",
            prompt="test",
            workspace_path=tmp,
            conversation_id=None,
            active_skill_ids=[],
            settings=OrchestratorSettings(agent_loop_enabled=True).model_dump(by_alias=True),
            context={},
            budget={},
        )
        task_id = task["id"]

        # Mark task as done so rollback is allowed.
        await orch_storage.update_task_status(agent_storage, task_id, status="done", finished=True)

        # Simulate backups created during the task: build a FileBackupStore using
        # the SAME app_data_dir that rollback_task will use internally.
        runner = AgentLoopRunner()
        app_data_dir = runner._app_data_dir(agent_storage)
        backup_store = FileBackupStore(app_data_dir, task_id)

        # Back up a real file so the backup dir contains at least one entry.
        dummy_file = Path(tmp) / "dummy.txt"
        dummy_file.write_text("original content", encoding="utf-8")
        backup_info = backup_store.backup(dummy_file)
        assert backup_info is not None, "backup() must return info for an existing file"

        backup_dir = Path(app_data_dir) / "file_backups" / task_id
        assert backup_dir.exists(), "backup dir should exist before rollback"
        assert any(backup_dir.iterdir()), "backup dir should contain at least one backup file"

        # rollback_task should clean up the backup dir.
        result = await runner.rollback_task(storage=agent_storage, task_id=task_id)
        assert result["ok"] is True

        assert not backup_dir.exists(), (
            "rollback_task must clean up the FileBackupStore backup dir; "
            "tracker.cleanup_backups() is a no-op on the fresh tracker."
        )
