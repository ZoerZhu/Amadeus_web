"""Tests for the complex agent / MCP / skills subsystem.

Covers:
- Agent task state machine (created -> running -> done/failed/cancelled/rolled_back)
- File snapshot creation and rollback
- Skills ZIP import + manifest validation + path traversal rejection
- MCP server config persistence
- Chat routing detection
- Unified tool registry filtering
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio

from backend.amadeus_app.complex_agent import agent_storage
from backend.amadeus_app.complex_agent.chat_router import detect_complex_task_intent
from backend.amadeus_app.complex_agent.domain import AgentSettings, McpServerConfig, SkillPackageInfo
from backend.amadeus_app.complex_agent.skills import (
    import_skill_from_zip,
    is_safe_relative_path,
    validate_skill_id,
)
from backend.amadeus_app.complex_agent.task_hub import TaskBudget, agent_task_hub
from backend.amadeus_app.complex_agent.tool_registry import UnifiedTool, UnifiedToolRegistry
from backend.amadeus_app.storage import SQLiteStorage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def storage():
    """Return a connected in-memory SQLiteStorage."""
    s = SQLiteStorage(":memory:")
    await s.connect()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# Unified tool registry
# ---------------------------------------------------------------------------


class TestUnifiedToolRegistry:
    def test_register_and_get(self):
        reg = UnifiedToolRegistry()

        async def handler(args):
            return {"ok": True, "data": args}

        reg.register(UnifiedTool(name="test_tool", description="d", parameters={}, handler=handler))
        tool = reg.get("test_tool")
        assert tool is not None
        assert tool.name == "test_tool"

    def test_openai_schema_with_allowlist(self):
        reg = UnifiedToolRegistry()

        async def h(args):
            return {}

        reg.register(UnifiedTool(name="a", description="da", parameters={}, handler=h))
        reg.register(UnifiedTool(name="b", description="db", parameters={}, handler=h))
        schema = reg.openai_schema(allowlist=["a"])
        assert len(schema) == 1
        assert schema[0]["function"]["name"] == "a"

    def test_openai_schema_with_denylist(self):
        reg = UnifiedToolRegistry()

        async def h(args):
            return {}

        reg.register(UnifiedTool(name="a", description="da", parameters={}, handler=h))
        reg.register(UnifiedTool(name="b", description="db", parameters={}, handler=h))
        schema = reg.openai_schema(denylist=["a"])
        assert len(schema) == 1
        assert schema[0]["function"]["name"] == "b"

    @pytest.mark.asyncio
    async def test_call_unknown_tool(self):
        reg = UnifiedToolRegistry()
        result = await reg.call("missing", {})
        assert result["ok"] is False
        assert "Unknown tool" in result["error"]

    @pytest.mark.asyncio
    async def test_call_handler_exception(self):
        reg = UnifiedToolRegistry()

        async def bad_handler(args):
            raise ValueError("boom")

        reg.register(UnifiedTool(name="bad", description="d", parameters={}, handler=bad_handler))
        result = await reg.call("bad", {})
        assert result["ok"] is False
        assert "boom" in result["error"]


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


class TestSkillIdValidation:
    def test_valid_ids(self):
        assert validate_skill_id("my-skill")
        assert validate_skill_id("skill_123")
        assert validate_skill_id("a")

    def test_invalid_ids(self):
        assert not validate_skill_id("")
        assert not validate_skill_id("My-Skill")  # uppercase
        assert not validate_skill_id("../etc")
        assert not validate_skill_id("a" * 65)  # too long
        assert not validate_skill_id(".hidden")


class TestPathSafety:
    def test_safe_relative(self, tmp_path: Path):
        assert is_safe_relative_path("sub/file.txt", tmp_path)

    def test_traversal_rejected(self, tmp_path: Path):
        assert not is_safe_relative_path("../../etc/passwd", tmp_path)
        assert not is_safe_relative_path("sub/../../../etc", tmp_path)


class TestSkillZipImport:
    def test_import_valid_zip(self, tmp_path: Path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "manifest.json",
                json.dumps({
                    "id": "test-skill",
                    "name": "Test Skill",
                    "version": "1.0.0",
                    "description": "A test skill",
                    "triggers": ["test", "测试"],
                }),
            )
            zf.writestr("SKILL.md", "# Test Skill\n\nThis is a test.")
            zf.writestr("references/api.md", "# API\n\nDocs.")
        buf.seek(0)
        zip_path = tmp_path / "test.zip"
        zip_path.write_bytes(buf.getvalue())
        skills_dir = tmp_path / "skills"
        info = import_skill_from_zip(zip_path, skills_dir)
        assert info.id == "test-skill"
        assert info.name == "Test Skill"
        assert "test" in info.triggers
        assert (skills_dir / "test-skill" / "manifest.json").exists()
        assert (skills_dir / "test-skill" / "SKILL.md").exists()
        assert (skills_dir / "test-skill" / "references" / "api.md").exists()

    def test_import_rejects_traversal(self, tmp_path: Path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"id": "bad-skill", "name": "Bad"}))
            zf.writestr("../../etc/evil.txt", "pwned")
        buf.seek(0)
        zip_path = tmp_path / "bad.zip"
        zip_path.write_bytes(buf.getvalue())
        with pytest.raises(ValueError, match="unsafe zip entry"):
            import_skill_from_zip(zip_path, tmp_path / "skills")

    def test_import_rejects_missing_manifest(self, tmp_path: Path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", "no manifest here")
        buf.seek(0)
        zip_path = tmp_path / "no-manifest.zip"
        zip_path.write_bytes(buf.getvalue())
        with pytest.raises(ValueError, match="manifest.json"):
            import_skill_from_zip(zip_path, tmp_path / "skills")

    def test_import_rejects_invalid_skill_id(self, tmp_path: Path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"id": "../Bad", "name": "Bad"}))
        buf.seek(0)
        zip_path = tmp_path / "bad-id.zip"
        zip_path.write_bytes(buf.getvalue())
        with pytest.raises(ValueError, match="invalid skill id"):
            import_skill_from_zip(zip_path, tmp_path / "skills")


# ---------------------------------------------------------------------------
# Agent storage (DB)
# ---------------------------------------------------------------------------


class TestAgentStorage:
    @pytest.mark.asyncio
    async def test_create_and_get_task(self, storage: SQLiteStorage):
        task = await agent_storage.create_agent_task(
            storage,
            user_id="user1",
            title="Test Task",
            prompt="do something",
            workspace_path="/tmp",
            conversation_id=None,
            active_skill_ids=[],
            settings={"trustMode": True},
            budget={"rounds": 0},
        )
        assert task["id"]
        assert task["status"] == "created"
        fetched = await agent_storage.get_agent_task(storage, task["id"])
        assert fetched is not None
        assert fetched["title"] == "Test Task"

    @pytest.mark.asyncio
    async def test_update_task_status(self, storage: SQLiteStorage):
        task = await agent_storage.create_agent_task(
            storage,
            user_id="user1",
            title="T",
            prompt="p",
            workspace_path=".",
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget={},
        )
        await agent_storage.update_agent_task_status(storage, task["id"], status="running")
        fetched = await agent_storage.get_agent_task(storage, task["id"])
        assert fetched["status"] == "running"
        await agent_storage.update_agent_task_status(storage, task["id"], status="done", finished=True)
        fetched = await agent_storage.get_agent_task(storage, task["id"])
        assert fetched["status"] == "done"
        assert fetched["finishedAt"] is not None

    @pytest.mark.asyncio
    async def test_append_and_list_events(self, storage: SQLiteStorage):
        task = await agent_storage.create_agent_task(
            storage,
            user_id="u",
            title="t",
            prompt="p",
            workspace_path=".",
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget={},
        )
        for i in range(5):
            await agent_storage.append_agent_task_event(
                storage,
                task_id=task["id"],
                seq=i,
                kind="status",
                name="step",
                summary=f"step {i}",
            )
        events = await agent_storage.list_agent_task_events(storage, task["id"])
        assert len(events) == 5
        assert events[0]["seq"] == 0
        assert events[4]["seq"] == 4

    @pytest.mark.asyncio
    async def test_create_artifact(self, storage: SQLiteStorage):
        task = await agent_storage.create_agent_task(
            storage,
            user_id="u",
            title="t",
            prompt="p",
            workspace_path=".",
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget={},
        )
        artifact = await agent_storage.create_artifact(
            storage,
            task_id=task["id"],
            kind="file",
            name="output.md",
            path="/tmp/output.md",
            mime_type="text/markdown",
            size_bytes=100,
            description="test output",
        )
        assert artifact["id"]
        fetched = await agent_storage.get_artifact(storage, artifact["id"])
        assert fetched is not None
        assert fetched["name"] == "output.md"
        artifacts = await agent_storage.list_artifacts(storage, task["id"])
        assert len(artifacts) == 1

    @pytest.mark.asyncio
    async def test_mcp_server_crud(self, storage: SQLiteStorage):
        server = await agent_storage.upsert_mcp_server(
            storage,
            user_id="u",
            server={
                "id": "srv1",
                "name": "Test Server",
                "transport": "stdio",
                "command": "echo",
                "enabled": True,
            },
        )
        assert server["id"] == "srv1"
        servers = await agent_storage.list_mcp_servers(storage, "u")
        assert len(servers) == 1
        deleted = await agent_storage.delete_mcp_server(storage, "srv1")
        assert deleted is True
        servers = await agent_storage.list_mcp_servers(storage, "u")
        assert len(servers) == 0

    @pytest.mark.asyncio
    async def test_skill_package_crud(self, storage: SQLiteStorage):
        skill = await agent_storage.upsert_skill_package(
            storage,
            user_id="u",
            skill={
                "id": "my-skill",
                "name": "My Skill",
                "version": "1.0.0",
                "enabled": True,
                "source": "local",
                "path": "/tmp/skills/my-skill",
                "triggers": ["hello"],
            },
        )
        assert skill["id"] == "my-skill"
        fetched = await agent_storage.get_skill_package(storage, "my-skill")
        assert fetched is not None
        assert "hello" in fetched["triggers"]
        deleted = await agent_storage.delete_skill_package(storage, "my-skill")
        assert deleted is True


# ---------------------------------------------------------------------------
# File snapshots + rollback
# ---------------------------------------------------------------------------


class TestFileSnapshots:
    @pytest.mark.asyncio
    async def test_snapshot_and_rollback_existing_file(self, storage: SQLiteStorage, tmp_path: Path):
        task = await agent_storage.create_agent_task(
            storage,
            user_id="u",
            title="t",
            prompt="p",
            workspace_path=str(tmp_path),
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget={},
        )
        target = tmp_path / "data.txt"
        target.write_text("original content", encoding="utf-8")
        backup_dir = tmp_path / "backups"
        snap = await agent_storage.create_file_snapshot(
            storage,
            task_id=task["id"],
            path=str(target),
            backup_dir=backup_dir,
        )
        assert snap["sizeBytes"] > 0
        # Modify the file
        target.write_text("modified content", encoding="utf-8")
        assert target.read_text() == "modified content"
        # Rollback
        result = await agent_storage.rollback_file_snapshots(storage, task["id"])
        assert result["restored"] == 1
        assert target.read_text() == "original content"
        fetched = await agent_storage.get_agent_task(storage, task["id"])
        assert fetched["status"] == "rolled_back"

    @pytest.mark.asyncio
    async def test_snapshot_sentinel_deletes_new_file(self, storage: SQLiteStorage, tmp_path: Path):
        task = await agent_storage.create_agent_task(
            storage,
            user_id="u",
            title="t",
            prompt="p",
            workspace_path=str(tmp_path),
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget={},
        )
        target = tmp_path / "new_file.txt"
        # File does not exist yet — snapshot is a sentinel
        snap = await agent_storage.create_file_snapshot(
            storage,
            task_id=task["id"],
            path=str(target),
            backup_dir=tmp_path / "backups",
        )
        assert snap["sizeBytes"] == 0
        assert snap["backupPath"] == ""
        # Now create the file (simulating the agent writing it)
        target.write_text("created by agent", encoding="utf-8")
        assert target.exists()
        # Rollback should delete it
        result = await agent_storage.rollback_file_snapshots(storage, task["id"])
        assert result["deleted"] == 1
        assert not target.exists()


# ---------------------------------------------------------------------------
# Chat routing
# ---------------------------------------------------------------------------


class TestChatRouter:
    def test_explicit_phrase_triggers_routing(self):
        settings = AgentSettings(enabled=True)
        result = detect_complex_task_intent("交给复杂 agent 执行这个任务", enabled_skills=[], agent_settings=settings)
        assert result is not None
        assert "explicit phrase" in result["reason"]

    def test_skill_trigger_match(self):
        settings = AgentSettings(enabled=True)
        skills = [
            {"id": "doc-skill", "name": "Doc", "enabled": True, "triggers": ["写文档", "生成文档"]},
        ]
        result = detect_complex_task_intent("帮我写文档", enabled_skills=skills, agent_settings=settings)
        assert result is not None
        assert "doc-skill" in result["skillIds"]

    def test_long_multistep_prompt(self):
        settings = AgentSettings(enabled=True)
        long_prompt = "请帮我完成以下任务：" + "详细描述 " * 50 + "然后保存到文件，接着跑测试，最后生成报告。"
        result = detect_complex_task_intent(long_prompt, enabled_skills=[], agent_settings=settings)
        assert result is not None
        assert "multi-step" in result["reason"] or "long" in result["reason"]

    def test_simple_prompt_not_routed(self):
        settings = AgentSettings(enabled=True)
        result = detect_complex_task_intent("你好", enabled_skills=[], agent_settings=settings)
        assert result is None

    def test_disabled_agent_not_routed(self):
        settings = AgentSettings(enabled=False)
        result = detect_complex_task_intent("交给复杂 agent 执行", enabled_skills=[], agent_settings=settings)
        assert result is None

    def test_skill_id_extraction_from_phrase(self):
        settings = AgentSettings(enabled=True)
        result = detect_complex_task_intent("用 skill my-skill 执行这个", enabled_skills=[], agent_settings=settings)
        assert result is not None
        assert "my-skill" in result["skillIds"]


# ---------------------------------------------------------------------------
# Task budget
# ---------------------------------------------------------------------------


class TestTaskBudget:
    def test_budget_not_exhausted_initially(self):
        budget = TaskBudget(max_rounds=10, max_tool_calls=50, max_runtime_seconds=600)
        exhausted, _ = budget.exhausted()
        assert not exhausted

    def test_budget_exhausted_by_rounds(self):
        budget = TaskBudget(max_rounds=2, max_tool_calls=50, max_runtime_seconds=600)
        budget.rounds = 2
        exhausted, reason = budget.exhausted()
        assert exhausted
        assert "max_rounds" in reason

    def test_budget_exhausted_by_tool_calls(self):
        budget = TaskBudget(max_rounds=10, max_tool_calls=5, max_runtime_seconds=600)
        budget.tool_calls = 5
        exhausted, reason = budget.exhausted()
        assert exhausted
        assert "max_tool_calls" in reason

    def test_budget_to_dict(self):
        budget = TaskBudget(max_rounds=10, max_tool_calls=50, max_runtime_seconds=600, max_sampling_depth=3)
        d = budget.to_dict()
        assert d["maxRounds"] == 10
        assert d["remainingRounds"] == 10
        assert d["remainingToolCalls"] == 50


# ---------------------------------------------------------------------------
# API routes (integration via TestClient)
# ---------------------------------------------------------------------------


class TestAgentRoutes:
    def test_list_tasks_empty(self, client):
        response = client.get("/api/agent/tasks")
        assert response.status_code == 200
        data = response.json()
        assert "tasks" in data
        assert isinstance(data["tasks"], list)

    def test_list_mcp_servers_empty(self, client):
        response = client.get("/api/mcp/servers")
        assert response.status_code == 200
        data = response.json()
        assert "servers" in data

    def test_upsert_and_delete_mcp_server(self, client):
        response = client.put("/api/mcp/servers", json={
            "server": {
                "id": "test-srv",
                "name": "Test",
                "transport": "stdio",
                "command": "echo",
                "enabled": True,
            }
        })
        assert response.status_code == 200
        assert response.json()["server"]["id"] == "test-srv"
        # List
        response = client.get("/api/mcp/servers")
        assert any(s["id"] == "test-srv" for s in response.json()["servers"])
        # Delete
        response = client.delete("/api/mcp/servers/test-srv")
        assert response.status_code == 200

    def test_list_skills_empty(self, client):
        response = client.get("/api/skills")
        assert response.status_code == 200
        assert "skills" in response.json()

    def test_artifact_not_found(self, client):
        response = client.get("/api/artifacts/nonexistent")
        assert response.status_code == 404

    def test_task_not_found(self, client):
        response = client.get("/api/agent/tasks/nonexistent")
        assert response.status_code == 404

    def test_control_nonexistent_task(self, client):
        response = client.post("/api/agent/tasks/nonexistent/control", json={"action": "pause"})
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------


class TestSettingsPersistence:
    def test_settings_include_agent_field(self, client):
        # First save settings to create a row
        save_resp = client.put("/api/settings", json={
            "model": {"providerName": "OpenAI", "baseUrl": "", "model": "", "apiKey": "", "useRemote": True},
            "vision": {"enabled": False, "screenCaptureEnabled": False, "providerName": "", "baseUrl": "", "model": "", "apiKey": "", "useRemote": True},
            "speechInput": {"enabled": False, "providerName": "", "baseUrl": "", "model": "", "apiKey": "", "language": "auto", "useRemote": True},
            "voice": {"ttsBackend": "local", "siliconFlowApiKey": "", "clonedVoiceUri": "", "autoPlay": False, "syncTextOutput": True, "speed": 1.0, "gain": 0.0},
            "desktopAssistant": {"subtitleEnabled": True, "voiceOutputEnabled": False, "autoVoiceInputEnabled": True, "autoScreenshotEnabled": False, "screenshotIntervalSeconds": 15, "cameraEnabled": False},
            "mode": "fast",
        })
        assert save_resp.status_code == 200
        # Now verify the returned settings include agent/mcpServers fields
        response = client.get("/api/settings")
        assert response.status_code == 200
        settings = response.json()["settings"]
        assert settings is not None
        assert "agent" in settings
        assert "mcpServers" in settings

    def test_save_agent_settings(self, client):
        response = client.put("/api/settings", json={
            "model": {"providerName": "OpenAI", "baseUrl": "", "model": "", "apiKey": "", "useRemote": True},
            "vision": {"enabled": False, "screenCaptureEnabled": False, "providerName": "", "baseUrl": "", "model": "", "apiKey": "", "useRemote": True},
            "speechInput": {"enabled": False, "providerName": "", "baseUrl": "", "model": "", "apiKey": "", "language": "auto", "useRemote": True},
            "voice": {"ttsBackend": "local", "siliconFlowApiKey": "", "clonedVoiceUri": "", "autoPlay": False, "syncTextOutput": True, "speed": 1.0, "gain": 0.0},
            "desktopAssistant": {"subtitleEnabled": True, "voiceOutputEnabled": False, "autoVoiceInputEnabled": True, "autoScreenshotEnabled": False, "screenshotIntervalSeconds": 15, "cameraEnabled": False},
            "mode": "fast",
            "agent": {
                "enabled": True,
                "trustMode": False,
                "defaultWorkspace": "/tmp",
                "maxRounds": 5,
            },
        })
        assert response.status_code == 200
        # Verify it persisted
        response = client.get("/api/settings")
        settings = response.json()["settings"]
        assert settings["agent"]["enabled"] is True
        assert settings["agent"]["trustMode"] is False
        assert settings["agent"]["maxRounds"] == 5


# ---------------------------------------------------------------------------
# Review fixes
# ---------------------------------------------------------------------------


async def _noop_event(kind, payload):
    pass


class TestMcpToolNaming:
    """P1-4: MCP tool names must be OpenAI-compatible (no dots)."""

    def test_mcp_tool_name_uses_double_underscore(self):
        from backend.amadeus_app.complex_agent.tool_registry import mcp_tool_name

        name = mcp_tool_name("my-server", "search_docs")
        assert name == "mcp__my-server__search_docs"
        # Must only contain [a-zA-Z0-9_-]
        import re

        assert re.match(r"^[a-zA-Z0-9_-]+$", name), f"invalid characters in {name}"

    def test_mcp_tool_name_sanitizes_unsafe_chars(self):
        from backend.amadeus_app.complex_agent.tool_registry import mcp_tool_name

        # Dots and spaces are replaced with underscores
        name = mcp_tool_name("my.server", "tool.name")
        assert "." not in name
        assert name == "mcp__my_server__tool_name"

    def test_register_mcp_tool_uses_safe_name(self):
        from backend.amadeus_app.complex_agent.tool_registry import (
            register_mcp_tool,
            unified_tool_registry,
            unregister_mcp_server_tools,
        )

        async def handler(args):
            return {"ok": True}

        unified_name = register_mcp_tool(
            server_id="test.srv",
            tool_name="my.tool",
            description="test",
            input_schema={},
            handler=handler,
        )
        assert "." not in unified_name
        tool = unified_tool_registry.get(unified_name)
        assert tool is not None
        # Original names preserved in meta
        assert tool.meta["serverId"] == "test.srv"
        assert tool.meta["toolName"] == "my.tool"
        # Cleanup
        unregister_mcp_server_tools("test.srv")
        assert unified_tool_registry.get(unified_name) is None


class TestSseTermination:
    """P0-3: SSE subscribe() must terminate on done/error/terminal status."""

    @pytest.mark.asyncio
    async def test_subscribe_breaks_on_done(self, storage: SQLiteStorage):
        from backend.amadeus_app.complex_agent.task_hub import AgentTaskHub

        hub = AgentTaskHub()
        task = await hub.create_task(
            storage=storage,
            user_id="u",
            title="t",
            prompt="p",
            workspace_path=".",
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget=TaskBudget(max_rounds=1, max_tool_calls=1, max_runtime_seconds=60),
        )

        async def _emit_later():
            await asyncio.sleep(0.05)  # let subscriber register
            await task.emit(storage=storage, kind="status", name="start", status="running", summary="started")
            await task.emit(storage=storage, kind="done", name="finish", status="done", summary="finished")

        emitter = asyncio.create_task(_emit_later())
        events = []
        async for record in hub.subscribe(task.task_id, replay=False):
            events.append(record)
        await emitter
        assert len(events) == 2
        assert events[0]["kind"] == "status"
        assert events[1]["kind"] == "done"

    @pytest.mark.asyncio
    async def test_subscribe_breaks_on_error(self, storage: SQLiteStorage):
        from backend.amadeus_app.complex_agent.task_hub import AgentTaskHub

        hub = AgentTaskHub()
        task = await hub.create_task(
            storage=storage,
            user_id="u",
            title="t",
            prompt="p",
            workspace_path=".",
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget=TaskBudget(max_rounds=1, max_tool_calls=1, max_runtime_seconds=60),
        )

        async def _emit_later():
            await asyncio.sleep(0.05)
            await task.emit(storage=storage, kind="error", name="boom", status="failed", summary="error")

        emitter = asyncio.create_task(_emit_later())
        events = []
        async for record in hub.subscribe(task.task_id, replay=False):
            events.append(record)
        await emitter
        assert len(events) == 1
        assert events[0]["kind"] == "error"

    @pytest.mark.asyncio
    async def test_subscribe_breaks_on_cancelled_status(self, storage: SQLiteStorage):
        from backend.amadeus_app.complex_agent.task_hub import AgentTaskHub

        hub = AgentTaskHub()
        task = await hub.create_task(
            storage=storage,
            user_id="u",
            title="t",
            prompt="p",
            workspace_path=".",
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget=TaskBudget(max_rounds=1, max_tool_calls=1, max_runtime_seconds=60),
        )

        async def _emit_later():
            await asyncio.sleep(0.05)
            await task.emit(storage=storage, kind="status", name="cancel", status="cancelled", summary="cancelled")

        emitter = asyncio.create_task(_emit_later())
        events = []
        async for record in hub.subscribe(task.task_id, replay=False):
            events.append(record)
        await emitter
        assert len(events) == 1
        assert events[0]["status"] == "cancelled"


class TestOpencodeDelegateTrustMode:
    """P1-6: opencode_delegate autoApprove must be controlled by trust_mode, not model args."""

    def test_auto_approve_not_in_parameters(self):
        from backend.amadeus_app.complex_agent.opencode_delegate import make_opencode_delegate_tool

        tool = make_opencode_delegate_tool(task_id="t1", workspace=".", on_event=_noop_event)
        # autoApprove must NOT be in the tool parameters (model can't set it)
        props = tool.parameters.get("properties", {})
        assert "autoApprove" not in props

    @pytest.mark.asyncio
    async def test_trust_mode_true_enables_auto_approve(self):
        from backend.amadeus_app.complex_agent.opencode_delegate import make_opencode_delegate_tool
        import backend.amadeus_app.complex_agent.opencode_delegate as delegate_mod

        captured: dict = {}
        original = delegate_mod.stream_opencode_task

        async def fake_stream(request):
            captured["auto_approve"] = request.auto_approve
            if False:
                yield  # make it an async generator

        delegate_mod.stream_opencode_task = fake_stream
        try:
            tool = make_opencode_delegate_tool(
                task_id="t1", workspace=".", on_event=_noop_event, trust_mode=True
            )
            await tool.handler({"prompt": "test"})
            assert captured["auto_approve"] is True
        finally:
            delegate_mod.stream_opencode_task = original

    @pytest.mark.asyncio
    async def test_trust_mode_false_disables_auto_approve(self):
        from backend.amadeus_app.complex_agent.opencode_delegate import make_opencode_delegate_tool
        import backend.amadeus_app.complex_agent.opencode_delegate as delegate_mod

        captured: dict = {}
        original = delegate_mod.stream_opencode_task

        async def fake_stream(request):
            captured["auto_approve"] = request.auto_approve
            if False:
                yield

        delegate_mod.stream_opencode_task = fake_stream
        try:
            tool = make_opencode_delegate_tool(
                task_id="t1", workspace=".", on_event=_noop_event, trust_mode=False
            )
            # Even though model passed autoApprove=True, trust_mode=False wins
            await tool.handler({"prompt": "test", "autoApprove": True})
            assert captured["auto_approve"] is False
        finally:
            delegate_mod.stream_opencode_task = original


class TestArtifactDownloadBoundary:
    """P1-7: artifact download must validate path is within allowed roots."""

    def test_artifact_download_returns_404_for_nonexistent(self, client):
        # First save settings so agent config exists
        client.put("/api/settings", json={
            "model": {"providerName": "OpenAI", "baseUrl": "", "model": "", "apiKey": "", "useRemote": True},
            "vision": {"enabled": False, "screenCaptureEnabled": False, "providerName": "", "baseUrl": "", "model": "", "apiKey": "", "useRemote": True},
            "speechInput": {"enabled": False, "providerName": "", "baseUrl": "", "model": "", "apiKey": "", "language": "auto", "useRemote": True},
            "voice": {"ttsBackend": "local", "siliconFlowApiKey": "", "clonedVoiceUri": "", "autoPlay": False, "syncTextOutput": True, "speed": 1.0, "gain": 0.0},
            "desktopAssistant": {"subtitleEnabled": True, "voiceOutputEnabled": False, "autoVoiceInputEnabled": True, "autoScreenshotEnabled": False, "screenshotIntervalSeconds": 15, "cameraEnabled": False},
            "mode": "fast",
        })
        # This test verifies the endpoint returns 404 for nonexistent artifacts
        response = client.get("/api/artifacts/nonexistent/download")
        assert response.status_code == 404


class TestCallLlmProviderResolution:
    """P0-1: call_llm must resolve provider correctly (not pass request as first arg)."""

    def test_call_llm_resolves_provider_correctly(self):
        """Verify call_llm uses get_provider + effective_model_settings(base, provider)."""
        from backend.amadeus_app.complex_agent.agent import call_llm
        import inspect

        source = inspect.getsource(call_llm)
        assert "get_provider(base.provider_name)" in source
        assert "effective_model_settings(base, provider)" in source
        # Must NOT pass a request object as first arg
        assert "effective_model_settings(request)" not in source


class TestTaskScopedTools:
    """P0-2: executor must use task._tools, not global registry, for schema and calls."""

    def test_executor_uses_task_tools_for_schema(self):
        from backend.amadeus_app.complex_agent.agent import executor_node
        import inspect

        source = inspect.getsource(executor_node)
        # Must build schema from task._tools, not global registry
        assert "t.to_openai_schema() for t in tools" in source
        # Must NOT use unified_tool_registry.openai_schema() directly
        assert "unified_tool_registry.openai_schema()" not in source
        # Must call via _call_task_tool, not unified_tool_registry.call
        assert "_call_task_tool" in source

    @pytest.mark.asyncio
    async def test_call_task_tool_finds_task_scoped_tool(self):
        from backend.amadeus_app.complex_agent.agent import _call_task_tool
        from backend.amadeus_app.complex_agent.tool_registry import UnifiedTool

        called: dict = {}

        async def handler(args):
            called["args"] = args
            return {"ok": True, "data": {"result": "done"}}

        tool = UnifiedTool(name="my_scoped_tool", description="d", parameters={}, handler=handler)
        result = await _call_task_tool({"my_scoped_tool": tool}, "my_scoped_tool", {"x": 1})
        assert result["ok"] is True
        assert called["args"] == {"x": 1}

    @pytest.mark.asyncio
    async def test_call_task_tool_falls_back_to_global(self):
        from backend.amadeus_app.complex_agent.agent import _call_task_tool

        # Calling a tool not in task scope should fall back to global registry
        result = await _call_task_tool({}, "nonexistent_tool_xyz", {})
        assert result["ok"] is False
        assert "Unknown tool" in result["error"]


# ---------------------------------------------------------------------------
# Tool permission queue
# ---------------------------------------------------------------------------


class TestToolPermissionClassification:
    """Test classify_tool_permission for builtin and MCP tools."""

    def test_safe_builtin_tools_auto_execute(self):
        from backend.amadeus_app.complex_agent.tool_registry import classify_tool_permission

        assert classify_tool_permission("get_current_time") == "safe"
        assert classify_tool_permission("file_reader") == "safe"
        assert classify_tool_permission("web_search") == "safe"
        assert classify_tool_permission("calculate") == "safe"

    def test_confirm_builtin_tools_need_confirmation(self):
        from backend.amadeus_app.complex_agent.tool_registry import classify_tool_permission

        assert classify_tool_permission("file_writer") == "confirm"
        assert classify_tool_permission("opencode_delegate") == "confirm"
        assert classify_tool_permission("doc_writer") == "confirm"
        assert classify_tool_permission("save_memory") == "confirm"

    def test_dangerous_browser_patterns(self):
        from backend.amadeus_app.complex_agent.tool_registry import classify_tool_permission

        assert classify_tool_permission("browser_click") == "dangerous"
        assert classify_tool_permission("browser_type") == "dangerous"
        assert classify_tool_permission("browser_screenshot") == "dangerous"

    def test_unknown_builtin_defaults_to_confirm(self):
        from backend.amadeus_app.complex_agent.tool_registry import classify_tool_permission

        assert classify_tool_permission("some_unknown_tool") == "confirm"

    def test_mcp_trusted_in_allowlist_is_safe(self):
        from backend.amadeus_app.complex_agent.tool_registry import classify_tool_permission

        result = classify_tool_permission(
            "mcp__myserver__search",
            source="mcp",
            mcp_trusted=True,
            mcp_allowed_tools={"mcp__myserver__search"},
        )
        assert result == "safe"

    def test_mcp_trusted_not_in_allowlist_is_confirm(self):
        from backend.amadeus_app.complex_agent.tool_registry import classify_tool_permission

        result = classify_tool_permission(
            "mcp__myserver__dangerous_tool",
            source="mcp",
            mcp_trusted=True,
            mcp_allowed_tools={"mcp__myserver__safe_tool"},
        )
        assert result == "confirm"

    def test_mcp_untrusted_is_dangerous(self):
        from backend.amadeus_app.complex_agent.tool_registry import classify_tool_permission

        result = classify_tool_permission(
            "mcp__myserver__any_tool",
            source="mcp",
            mcp_trusted=False,
        )
        assert result == "dangerous"

    def test_browser_action_open_is_safe(self):
        from backend.amadeus_app.complex_agent.tool_registry import classify_tool_permission

        assert classify_tool_permission("browser", arguments={"action": "open"}) == "safe"
        assert classify_tool_permission("browser", arguments={"action": "extract_text"}) == "safe"
        assert classify_tool_permission("browser", arguments={"action": "close"}) == "safe"

    def test_browser_action_screenshot_is_confirm(self):
        from backend.amadeus_app.complex_agent.tool_registry import classify_tool_permission

        assert classify_tool_permission("browser", arguments={"action": "screenshot"}) == "confirm"

    def test_browser_action_click_type_is_dangerous(self):
        from backend.amadeus_app.complex_agent.tool_registry import classify_tool_permission

        assert classify_tool_permission("browser", arguments={"action": "click"}) == "dangerous"
        assert classify_tool_permission("browser", arguments={"action": "type"}) == "dangerous"

    def test_browser_action_unknown_defaults_to_confirm(self):
        from backend.amadeus_app.complex_agent.tool_registry import classify_tool_permission

        assert classify_tool_permission("browser", arguments={"action": "unknown"}) == "confirm"

    def test_browser_without_arguments_falls_back_to_confirm(self):
        from backend.amadeus_app.complex_agent.tool_registry import classify_tool_permission

        # No arguments → can't determine action → confirm (in CONFIRM_TOOLS)
        assert classify_tool_permission("browser") == "confirm"


class TestPermissionStorage:
    """Test permission request CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_and_get_permission(self, storage: SQLiteStorage):
        from backend.amadeus_app.complex_agent import agent_storage
        from backend.amadeus_app.complex_agent.task_hub import AgentTaskHub, TaskBudget

        hub = AgentTaskHub()
        task = await hub.create_task(
            storage=storage,
            user_id="u",
            title="t",
            prompt="p",
            workspace_path=".",
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget=TaskBudget(max_rounds=1, max_tool_calls=1, max_runtime_seconds=60),
        )
        perm = await agent_storage.create_permission_request(
            storage,
            task_id=task.task_id,
            tool_name="file_writer",
            arguments_preview='{"path": "test.txt"}',
            risk_level="confirm",
        )
        assert perm["status"] == "pending"
        assert perm["toolName"] == "file_writer"
        assert perm["riskLevel"] == "confirm"

        fetched = await agent_storage.get_permission_request(storage, perm["id"])
        assert fetched is not None
        assert fetched["id"] == perm["id"]

    @pytest.mark.asyncio
    async def test_list_pending_permissions(self, storage: SQLiteStorage):
        from backend.amadeus_app.complex_agent import agent_storage
        from backend.amadeus_app.complex_agent.task_hub import AgentTaskHub, TaskBudget

        hub = AgentTaskHub()
        task = await hub.create_task(
            storage=storage,
            user_id="u",
            title="t",
            prompt="p",
            workspace_path=".",
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget=TaskBudget(max_rounds=1, max_tool_calls=1, max_runtime_seconds=60),
        )
        await agent_storage.create_permission_request(
            storage, task_id=task.task_id, tool_name="file_writer", arguments_preview="{}", risk_level="confirm"
        )
        await agent_storage.create_permission_request(
            storage, task_id=task.task_id, tool_name="browser_click", arguments_preview="{}", risk_level="dangerous"
        )
        perms = await agent_storage.list_pending_permission_requests(storage, task.task_id)
        assert len(perms) == 2
        assert all(p["status"] == "pending" for p in perms)

    @pytest.mark.asyncio
    async def test_update_permission_status(self, storage: SQLiteStorage):
        from backend.amadeus_app.complex_agent import agent_storage
        from backend.amadeus_app.complex_agent.task_hub import AgentTaskHub, TaskBudget

        hub = AgentTaskHub()
        task = await hub.create_task(
            storage=storage,
            user_id="u",
            title="t",
            prompt="p",
            workspace_path=".",
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget=TaskBudget(max_rounds=1, max_tool_calls=1, max_runtime_seconds=60),
        )
        perm = await agent_storage.create_permission_request(
            storage, task_id=task.task_id, tool_name="file_writer", arguments_preview="{}", risk_level="confirm"
        )
        updated = await agent_storage.update_permission_request_status(
            storage, perm["id"], status="approved", reason="ok"
        )
        assert updated["status"] == "approved"
        assert updated["reason"] == "ok"
        assert updated["resolvedAt"] is not None

        # Already resolved → update should not change status
        updated_again = await agent_storage.update_permission_request_status(
            storage, perm["id"], status="rejected"
        )
        assert updated_again["status"] == "approved"


class TestPermissionResolution:
    """Test in-memory permission wait/resolve mechanism."""

    @pytest.mark.asyncio
    async def test_resolve_permission_approves(self, storage: SQLiteStorage):
        from backend.amadeus_app.complex_agent.task_hub import AgentTaskHub, TaskBudget

        hub = AgentTaskHub()
        task = await hub.create_task(
            storage=storage,
            user_id="u",
            title="t",
            prompt="p",
            workspace_path=".",
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget=TaskBudget(max_rounds=1, max_tool_calls=1, max_runtime_seconds=60),
        )
        perm_id = "test-perm-1"
        task.register_permission_request(perm_id)

        async def _resolve_later():
            await asyncio.sleep(0.05)
            task.resolve_permission(perm_id, "approved")

        resolver = asyncio.create_task(_resolve_later())
        decision = await task.wait_for_permission(perm_id, timeout=5.0)
        await resolver
        assert decision == "approved"

    @pytest.mark.asyncio
    async def test_resolve_permission_rejects(self, storage: SQLiteStorage):
        from backend.amadeus_app.complex_agent.task_hub import AgentTaskHub, TaskBudget

        hub = AgentTaskHub()
        task = await hub.create_task(
            storage=storage,
            user_id="u",
            title="t",
            prompt="p",
            workspace_path=".",
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget=TaskBudget(max_rounds=1, max_tool_calls=1, max_runtime_seconds=60),
        )
        perm_id = "test-perm-2"
        task.register_permission_request(perm_id)

        async def _resolve_later():
            await asyncio.sleep(0.05)
            task.resolve_permission(perm_id, "rejected")

        resolver = asyncio.create_task(_resolve_later())
        decision = await task.wait_for_permission(perm_id, timeout=5.0)
        await resolver
        assert decision == "rejected"

    @pytest.mark.asyncio
    async def test_permission_timeout(self, storage: SQLiteStorage):
        from backend.amadeus_app.complex_agent.task_hub import AgentTaskHub, TaskBudget

        hub = AgentTaskHub()
        task = await hub.create_task(
            storage=storage,
            user_id="u",
            title="t",
            prompt="p",
            workspace_path=".",
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget=TaskBudget(max_rounds=1, max_tool_calls=1, max_runtime_seconds=60),
        )
        perm_id = "test-perm-timeout"
        task.register_permission_request(perm_id)
        # No one resolves → should timeout
        decision = await task.wait_for_permission(perm_id, timeout=0.1)
        assert decision == "timeout"

    @pytest.mark.asyncio
    async def test_cleanup_rejects_pending_permissions(self, storage: SQLiteStorage):
        from backend.amadeus_app.complex_agent.task_hub import AgentTaskHub, TaskBudget

        hub = AgentTaskHub()
        task = await hub.create_task(
            storage=storage,
            user_id="u",
            title="t",
            prompt="p",
            workspace_path=".",
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget=TaskBudget(max_rounds=1, max_tool_calls=1, max_runtime_seconds=60),
        )
        perm_id = "test-perm-cleanup"
        task.register_permission_request(perm_id)
        await task.cleanup()
        # After cleanup, the permission should be rejected
        decision = await task.wait_for_permission(perm_id, timeout=0.1)
        assert decision == "rejected"


class TestPermissionRoutes:
    """Test permission API endpoints."""

    def test_list_permissions_empty(self, client):
        # First create a task, then list permissions
        response = client.get("/api/agent/tasks/nonexistent/permissions")
        assert response.status_code == 200
        assert response.json() == {"permissions": []}

    def test_approve_nonexistent_permission_returns_404(self, client):
        response = client.post(
            "/api/agent/permissions/nonexistent/approve",
            json={"reason": ""},
        )
        assert response.status_code == 404

    def test_reject_nonexistent_permission_returns_404(self, client):
        response = client.post(
            "/api/agent/permissions/nonexistent/reject",
            json={"reason": ""},
        )
        assert response.status_code == 404
