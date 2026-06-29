from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.amadeus_app import chat_service
from backend.amadeus_app.domain import ChatStreamRequest, OrchestratorInvokeRequest
from backend.amadeus_app.chat_service import ModelToolCall
from backend.amadeus_app.event_stream import sse
from backend.amadeus_app.orchestrator_integrations import storage as integration_storage
from backend.amadeus_app.complex_agent import agent_storage as legacy_agent_storage
from backend.amadeus_app.orchestrator import planner as orchestrator_planner
from backend.amadeus_app.orchestrator import capability_adapters
from backend.amadeus_app.orchestrator import chat_routing
from backend.amadeus_app.orchestrator import invoke_service
from backend.amadeus_app.orchestrator import storage as orchestrator_storage
from backend.amadeus_app.orchestrator.capability_adapters import CapabilityExecutionContext, CapabilityRegistry
from backend.amadeus_app.orchestrator.capabilities import CapabilityGateway, prompt_requires_code_agent
from backend.amadeus_app.orchestrator.domain import (
    OrchestratorSettings,
    OrchestratorTaskCreateRequest,
    OrchestratorTaskMessageRequest,
)
from backend.amadeus_app.orchestrator.planner import OrchestratorPlanner
from backend.amadeus_app.orchestrator.runner import OrchestratorRunner, default_orchestrator_runner
from backend.amadeus_app.personas import build_system_prompt, get_persona
from backend.amadeus_app.routers import orchestrator_integrations, complex_agent, legacy_agent, orchestrator, settings
from backend.amadeus_app.storage import DEFAULT_USER_ID, SQLiteStorage


@pytest_asyncio.fixture
async def memory_storage():
    storage = SQLiteStorage(":memory:")
    await storage.connect()
    try:
        yield storage
    finally:
        await storage.close()


def create_memory_storage_sync() -> SQLiteStorage:
    storage = SQLiteStorage(":memory:")
    asyncio.run(storage.connect())
    return storage


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_frontend_protocol_does_not_export_legacy_agent_aliases():
    root = repo_root()
    forbidden_by_file = {
        "src/types.ts": [
            'event: "complex_task"',
            "export type AgentAction",
            "export type AgentTargetType",
            "export type AgentInvokeRequest",
            "export type AgentInvokeResponse",
            "export type AgentSettings",
            "export type AgentTaskStatus",
            "export type AgentTaskControlAction",
            "export type AgentEventKind",
            "export type AgentTaskBudget",
            "export type AgentTaskSummary",
            "export type AgentArtifactKind",
            "export type AgentArtifact",
            "export type AgentTaskEvent",
            "export type AgentTaskDetail",
            "export type AgentTaskCreateRequest",
        ],
        "src/api.ts": [
            "export const fetchAgentCapabilities",
            "export const invokeAgent",
            "export const uploadAgentFile",
            "export const fetchAgentTasks",
            "export const fetchAgentTaskDetail",
            "export const controlAgentTask",
            "export const streamAgentTaskEvents",
            "export const createAgentTask",
            "export const streamAgentTask",
        ],
        "src/agentDefaults.ts": [
            "DEFAULT_AGENT_SETTINGS",
            "normalizeAgentSettings",
        ],
        "src/App.tsx": [
            'event.event === "complex_task"',
        ],
    }

    for relative_path, forbidden_values in forbidden_by_file.items():
        content = (root / relative_path).read_text(encoding="utf-8")
        for value in forbidden_values:
            assert value not in content, f"{relative_path} still exposes legacy Agent alias: {value}"

    assert "OrchestratorInvokeRequest" in (root / "src/types.ts").read_text(encoding="utf-8")
    assert "fetchOrchestratorTasks" in (root / "src/api.ts").read_text(encoding="utf-8")


def test_backend_domain_does_not_export_legacy_invoke_aliases():
    content = (repo_root() / "backend/amadeus_app/domain.py").read_text(encoding="utf-8")

    assert "AgentAction = OrchestratorInvokeAction" not in content
    assert "AgentTargetType = OrchestratorInvokeTargetType" not in content
    assert "AgentInvokeRequest = OrchestratorInvokeRequest" not in content
    assert "class OrchestratorInvokeRequest" in content


def test_primary_backend_does_not_import_retired_agent_packages():
    root = repo_root()
    backend_root = root / "backend" / "amadeus_app"
    allowed_files = {
        Path("backend/amadeus_app/routers/complex_agent.py"),
        Path("backend/amadeus_app/routers/agent_integrations.py"),
    }
    import_pattern = re.compile(
        r"^\s*(?:from\s+(?:\.+|backend\.amadeus_app\.)?(?:complex_agent|agent_integrations)(?:\.|\s)"
        r"|import\s+(?:backend\.amadeus_app\.)?(?:complex_agent|agent_integrations)(?:\.|\s|$))",
        re.MULTILINE,
    )

    for path in backend_root.rglob("*.py"):
        relative_path = path.relative_to(root)
        if relative_path in allowed_files:
            continue
        if "complex_agent" in relative_path.parts or "agent_integrations" in relative_path.parts:
            continue
        content = path.read_text(encoding="utf-8")
        assert not import_pattern.search(content), f"{relative_path} imports retired Agent package"


def test_kurisu_prompt_advertises_current_orchestrator_capabilities():
    prompt = build_system_prompt(get_persona("amadeus"), "fast")

    assert "Orchestrator 任务编排" in prompt
    assert "层级记忆" in prompt
    assert "MCP/Skills" in prompt
    assert "OpenCode" in prompt
    assert "尚未接入" not in prompt
    assert "会通过后端 registry 扩展" not in prompt


@pytest.mark.asyncio
async def test_orchestrator_runner_writes_v2_ledger(memory_storage: SQLiteStorage):
    async def fake_code_search(args, context):
        return {"ok": True, "summary": "searched code", "data": {"results": []}}

    async def fake_code_agent(args, context):
        return {"ok": True, "summary": "code agent delegated", "data": {"eventCount": 1}}

    registry = CapabilityRegistry()
    registry.register("code_search", fake_code_search)
    registry.register("code_agent", fake_code_agent)
    runner = OrchestratorRunner(capability_registry=registry)

    request = OrchestratorTaskCreateRequest(
        prompt="请修改 backend 代码并运行测试",
        workspacePath="E:/Amadeus/Amadeus_web",
    )
    settings = OrchestratorSettings(defaultWorkspace="E:/Amadeus/Amadeus_web", trustMode=True, agentLoopEnabled=False)
    task = await orchestrator_storage.create_task(
        memory_storage,
        user_id=DEFAULT_USER_ID,
        title="代码任务",
        prompt=request.prompt,
        workspace_path=request.workspace_path or settings.default_workspace,
        conversation_id=None,
        active_skill_ids=[],
        settings=settings.model_dump(by_alias=True),
        budget={"maxRounds": 20, "maxToolCalls": 80, "maxRuntimeSeconds": 1800, "maxSamplingDepth": 3},
    )

    await runner.run(
        storage=memory_storage,
        task_id=task["id"],
        request=request,
        settings=settings,
    )

    detail = await orchestrator_storage.get_detail(memory_storage, task["id"])
    assert detail is not None
    assert detail["task"]["status"] == "done"
    kinds = [event["kind"] for event in detail["events"]]
    assert "plan" in kinds
    assert "tool" in kinds
    assert "done" in kinds
    plan_event = next(event for event in detail["events"] if event["kind"] == "plan")
    assert plan_event["payload"]["planner"]["plannerVersion"] == "orchestrator_planner_v1"
    assert "code_search" in plan_event["payload"]["planner"]["availableCapabilities"]
    assert any(event["name"] == "code_agent" and event["status"] == "done" for event in detail["events"])
    assert any(event["name"] == "code_search" and event["summary"] == "searched code" for event in detail["events"])
    assert any(event["kind"] == "message" and event["role"] == "user" for event in detail["events"])
    assert any(event["kind"] == "message" and event["name"] == "final_answer" for event in detail["events"])


@pytest.mark.asyncio
async def test_orchestrator_runner_stops_when_task_is_cancelled(memory_storage: SQLiteStorage):
    calls: list[str] = []

    async def fake_first(args, context):
        calls.append("first")
        await orchestrator_storage.update_task_status(context.storage, context.task_id, status="cancelled")
        return {"ok": True, "summary": "first step cancelled task", "data": {}}

    async def fake_second(args, context):
        calls.append("second")
        return {"ok": True, "summary": "second step should not run", "data": {}}

    class FakePlanner:
        def build_plan(self, prompt, settings):
            return SimpleNamespace(
                steps=[
                    {"id": "first", "worker": "worker", "capability": "web_search", "args": {}, "goal": "first"},
                    {"id": "second", "worker": "worker", "capability": "todo_task", "args": {}, "goal": "second"},
                ],
                context={"plannerVersion": "test_planner", "availableCapabilities": ["web_search", "todo_task"]},
            )

    registry = CapabilityRegistry()
    registry.register("web_search", fake_first)
    registry.register("todo_task", fake_second)
    runner = OrchestratorRunner(capability_registry=registry, planner=FakePlanner())
    request = OrchestratorTaskCreateRequest(prompt="执行两步任务", workspacePath="E:/Amadeus/Amadeus_web")
    settings = OrchestratorSettings(defaultWorkspace="E:/Amadeus/Amadeus_web", trustMode=True, agentLoopEnabled=False)
    task = await orchestrator_storage.create_task(
        memory_storage,
        user_id=DEFAULT_USER_ID,
        title="取消任务",
        prompt=request.prompt,
        workspace_path=request.workspace_path or settings.default_workspace,
        conversation_id=None,
        active_skill_ids=[],
        settings=settings.model_dump(by_alias=True),
        budget={"maxRounds": 20, "maxToolCalls": 80, "maxRuntimeSeconds": 1800, "maxSamplingDepth": 3},
    )

    await runner.run(storage=memory_storage, task_id=task["id"], request=request, settings=settings)

    detail = await orchestrator_storage.get_detail(memory_storage, task["id"])
    assert detail is not None
    assert detail["task"]["status"] == "cancelled"
    assert calls == ["first"]
    assert not any(event["kind"] == "done" for event in detail["events"])
    assert not any(event["kind"] == "message" and event["role"] == "assistant" for event in detail["events"])


def test_capability_gateway_keeps_raw_mcp_explicit():
    gateway = CapabilityGateway(trust_mode=True)
    assert gateway.classify("web_search") == "safe"
    assert gateway.classify("code_search") == "safe"
    assert gateway.classify("todo_task") == "safe"
    assert gateway.classify("code_agent") == "confirm"
    assert gateway.classify("mcp__firecrawl__scrape") == "dangerous"
    assert gateway.decide("code_agent").allowed is True

    strict_gateway = CapabilityGateway(trust_mode=False)
    assert strict_gateway.decide("code_agent").allowed is False
    assert prompt_requires_code_agent("修复 React 组件里的 bug") is True
    assert prompt_requires_code_agent("解释一下这段代码") is False


def test_orchestrator_settings_preserve_runtime_toggles_and_legacy_alias():
    from backend.amadeus_app.complex_agent.domain import AgentSettings

    settings = OrchestratorSettings.model_validate(
        {
            "rollbackEnabled": False,
            "browserEnabled": True,
            "enabledCapabilities": ["web_search", "code_search"],
        }
    )

    dumped = settings.model_dump(by_alias=True)
    assert dumped["rollbackEnabled"] is False
    assert dumped["browserEnabled"] is True
    assert dumped["enabledCapabilities"] == ["web_search", "code_search"]
    assert AgentSettings is OrchestratorSettings


def test_orchestrator_planner_respects_browser_enabled_toggle():
    planner = OrchestratorPlanner()

    disabled = planner.build_plan("整理当前目录", settings=OrchestratorSettings(browserEnabled=False))
    enabled = planner.build_plan("整理当前目录", settings=OrchestratorSettings(browserEnabled=True))

    assert "browser" not in disabled.context["availableCapabilities"]
    assert "browser" in enabled.context["availableCapabilities"]


def test_orchestrator_chat_routing_and_legacy_wrapper_match():
    from backend.amadeus_app.complex_agent import chat_router as legacy_chat_router

    settings = OrchestratorSettings(enabled=True, agentLoopEnabled=False)
    routing = chat_routing.detect_orchestrator_task_intent(
        "请交给复杂 agent 执行，查代码然后写文档",
        enabled_skills=[],
        orchestrator_settings=settings,
    )
    legacy_routing = chat_routing.detect_orchestrator_task_intent(
        "请交给复杂 agent 执行，查代码然后写文档",
        enabled_skills=[],
        agent_settings=settings,
    )
    orchestrator_routing = chat_routing.detect_orchestrator_task_intent(
        "请交给 Orchestrator 执行，查代码然后写文档",
        enabled_skills=[],
        orchestrator_settings=settings,
    )

    assert routing is not None
    assert routing["reason"].startswith("explicit phrase")
    assert orchestrator_routing is not None
    assert orchestrator_routing["reason"].startswith("explicit phrase")
    assert "Orchestrator 任务路由" in chat_routing.ORCHESTRATOR_TASK_SYSTEM_PROMPT
    assert "创建 Orchestrator 任务" in chat_routing.ORCHESTRATOR_TASK_SYSTEM_PROMPT
    assert legacy_routing == routing
    assert legacy_chat_router.detect_complex_task_intent is chat_routing.detect_orchestrator_task_intent
    assert legacy_chat_router.COMPLEX_TASK_SYSTEM_PROMPT == chat_routing.ORCHESTRATOR_TASK_SYSTEM_PROMPT
    assert chat_routing.scan_for_orchestrator_task_marker(chat_routing.ORCHESTRATOR_TASK_MARKER)
    assert not chat_routing.scan_for_orchestrator_task_marker(legacy_chat_router.COMPLEX_TASK_MARKER)
    assert legacy_chat_router.scan_for_complex_task_marker(legacy_chat_router.COMPLEX_TASK_MARKER)
    assert legacy_chat_router.scan_for_complex_task_marker(chat_routing.ORCHESTRATOR_TASK_MARKER)
    assert not hasattr(chat_routing, "LEGACY_COMPLEX_TASK_MARKER")
    assert not hasattr(chat_routing, "detect_complex_task_intent")


@pytest.mark.asyncio
async def test_chat_stream_emits_orchestrator_task_event(monkeypatch, memory_storage: SQLiteStorage):
    from backend.amadeus_app.orchestrator import runner as orchestrator_runner

    async def noop_run(**kwargs):
        return None

    monkeypatch.setattr(orchestrator_runner.default_orchestrator_runner, "run", noop_run)
    await memory_storage.save_settings(
        user_id=DEFAULT_USER_ID,
        model={},
        vision={},
        speech_input={},
        voice={},
        desktop_assistant={},
        mode="fast",
        orchestrator=OrchestratorSettings(enabled=True, agentLoopEnabled=False, defaultWorkspace=".").model_dump(by_alias=True),
    )

    request = ChatStreamRequest.model_validate(
        {
            "messages": [
                {"role": "user", "content": "请交给复杂 agent 执行，查代码然后写文档"},
            ],
            "model": {"useRemote": False},
        }
    )

    chunks = [chunk async for chunk in chat_service.stream_chat(request, storage=memory_storage)]

    assert len(chunks) == 1
    assert chunks[0].startswith("event: orchestrator_task\n")
    data_line = next(line for line in chunks[0].splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["taskId"]
    assert payload["reason"].startswith("explicit phrase")


@pytest.mark.asyncio
async def test_chat_stream_reads_orchestrator_settings_without_legacy_agent_alias(
    monkeypatch,
    memory_storage: SQLiteStorage,
):
    from backend.amadeus_app.orchestrator import runner as orchestrator_runner

    async def noop_run(**kwargs):
        return None

    async def get_settings_only_orchestrator(user_id: str):
        assert user_id == DEFAULT_USER_ID
        return {
            "orchestrator": OrchestratorSettings(
                enabled=True,
                agentLoopEnabled=False,
                defaultWorkspace=".",
            ).model_dump(by_alias=True)
        }

    monkeypatch.setattr(orchestrator_runner.default_orchestrator_runner, "run", noop_run)
    monkeypatch.setattr(memory_storage, "get_settings", get_settings_only_orchestrator)

    request = ChatStreamRequest.model_validate(
        {
            "messages": [
                {"role": "user", "content": "请交给复杂 agent 执行，查代码然后写文档"},
            ],
            "model": {"useRemote": False},
        }
    )

    chunks = [chunk async for chunk in chat_service.stream_chat(request, storage=memory_storage)]

    assert len(chunks) == 1
    assert chunks[0].startswith("event: orchestrator_task\n")
    data_line = next(line for line in chunks[0].splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["taskId"]
    assert payload["reason"].startswith("explicit phrase")


def test_shared_sse_helper_and_legacy_task_hub_use_same_formatter():
    from backend.amadeus_app.complex_agent.task_hub import sse as legacy_task_sse

    payload = {"text": "你好", "ok": True}
    expected = 'event: content\ndata: {"text": "你好", "ok": true}\n\n'

    assert sse("content", payload) == expected
    assert legacy_task_sse is sse


def test_retired_complex_agent_router_reexports_legacy_router():
    assert complex_agent.router is legacy_agent.router


def test_legacy_agent_service_wraps_orchestrator_invoke_service():
    from backend.amadeus_app import agent_service

    assert agent_service.call_tool is invoke_service.call_tool
    assert not hasattr(invoke_service, "invoke_agent_request")
    assert agent_service.invoke_agent_request is not invoke_service.invoke_orchestrator_request
    result = asyncio.run(agent_service.invoke_agent_request(OrchestratorInvokeRequest(action="query_capabilities")))
    assert result["data"]["runtime"] == "orchestrator_v2"


def test_legacy_agent_registry_reexports_builtin_tool_registry():
    from backend.amadeus_app import agent_registry, builtin_tool_registry

    assert agent_registry.ToolDefinition is builtin_tool_registry.ToolDefinition
    assert agent_registry.tool_registry is builtin_tool_registry.tool_registry


def test_legacy_agent_integrations_reexport_orchestrator_integrations():
    from backend.amadeus_app.agent_integrations import storage as legacy_integration_storage
    from backend.amadeus_app.orchestrator_integrations import storage as canonical_integration_storage
    from backend.amadeus_app.routers import agent_integrations as legacy_integration_router
    from backend.amadeus_app.routers import orchestrator_integrations as canonical_integration_router

    assert legacy_integration_storage.list_mcp_servers is canonical_integration_storage.list_mcp_servers
    assert legacy_integration_router.router is canonical_integration_router.router


@pytest.mark.asyncio
async def test_legacy_complex_agent_runtime_is_retired():
    from backend.amadeus_app.complex_agent.agent import run_agent_task

    with pytest.raises(RuntimeError, match="/api/orchestrator/tasks"):
        await run_agent_task()


@pytest.mark.asyncio
async def test_legacy_agent_storage_delegates_mcp_and_skill_settings(memory_storage: SQLiteStorage):
    with pytest.raises(RuntimeError, match="/api/orchestrator/tasks"):
        await legacy_agent_storage.create_agent_task(
            memory_storage,
            user_id=DEFAULT_USER_ID,
            title="legacy",
            prompt="旧任务",
            workspace_path=".",
            conversation_id=None,
            active_skill_ids=[],
            settings={},
            budget={},
        )

    saved_mcp = await legacy_agent_storage.upsert_mcp_server(
        memory_storage,
        user_id=DEFAULT_USER_ID,
        server={
            "id": "compat-mcp",
            "name": "Compat MCP",
            "enabled": True,
            "transport": "http",
            "url": "https://example.invalid/mcp",
            "authType": "none",
        },
    )
    servers = await integration_storage.list_mcp_servers(memory_storage, DEFAULT_USER_ID)

    assert saved_mcp["id"] == "compat-mcp"
    assert [server["id"] for server in servers] == ["compat-mcp"]

    saved_skill = await legacy_agent_storage.upsert_skill_package(
        memory_storage,
        user_id=DEFAULT_USER_ID,
        skill={
            "id": "compat-skill",
            "name": "Compat Skill",
            "enabled": True,
            "path": "skills/compat",
            "triggers": ["compat"],
        },
    )
    skill = await integration_storage.get_skill_package(memory_storage, "compat-skill")

    assert saved_skill["id"] == "compat-skill"
    assert skill is not None
    assert skill["triggers"] == ["compat"]


def test_orchestrator_planner_routes_document_convert_and_mcp_snapshot(monkeypatch):
    class FakeMcpManager:
        def list_capability_cache(self):
            return {
                "markitdown": {
                    "tools": [{"name": "convert_to_markdown", "description": "Convert Office and PDF files"}],
                    "resources": [],
                    "prompts": [],
                    "lastConnectedAt": "2026-06-25T00:00:00Z",
                    "lastError": None,
                }
            }

    monkeypatch.setattr(orchestrator_planner, "mcp_manager", FakeMcpManager())
    plan = OrchestratorPlanner().build_plan(
        "请将 docs/spec.pdf 转换为 markdown",
        settings=OrchestratorSettings(),
    )

    assert plan.steps[0]["capability"] == "document_convert"
    assert plan.steps[0]["args"] == {"path": "docs/spec.pdf"}
    assert plan.steps[-1]["id"] == "summarize"
    assert plan.context["mcpCapabilities"]["servers"]["markitdown"]["tools"] == [
        {"name": "convert_to_markdown", "description": "Convert Office and PDF files"}
    ]
    assert "document_convert_intent" in plan.context["reasons"]


def test_orchestrator_planner_routes_code_tasks_through_search_then_code_agent():
    plan = OrchestratorPlanner().build_plan(
        "请修复 `target_func` 的 Python 代码",
        settings=OrchestratorSettings(),
        mcp_snapshot={"available": False, "servers": {}},
    )

    assert [step["capability"] for step in plan.steps] == ["code_search", "code_agent", ""]
    assert plan.steps[0]["args"]["query"] == "target_func"
    assert "code_agent_intent" in plan.context["reasons"]
    assert plan.context["opencodeRouting"]["allowed"] is True
    assert "代码变更启发式" in plan.context["opencodeRouting"]["matchedRules"]


def test_orchestrator_planner_respects_opencode_force_deny():
    plan = OrchestratorPlanner().build_plan(
        "不要调用 coding agent，请修复 React 代码",
        settings=OrchestratorSettings(
            opencodeRouting={
                "forceDenyKeywords": ["不要调用 coding"],
                "rules": [],
            }
        ),
        mcp_snapshot={"available": False, "servers": {}},
    )

    capabilities = [step["capability"] for step in plan.steps]
    assert "code_agent" not in capabilities
    assert "code_agent_denied" in plan.context["reasons"]
    assert plan.context["opencodeRouting"]["allowed"] is False
    assert plan.context["opencodeRouting"]["method"] == "force_deny"


def test_orchestrator_planner_respects_opencode_disabled():
    plan = OrchestratorPlanner().build_plan(
        "请修复 React 代码",
        settings=OrchestratorSettings(opencodeEnabled=False),
        mcp_snapshot={"available": False, "servers": {}},
    )

    capabilities = [step["capability"] for step in plan.steps]
    assert "code_agent" not in capabilities
    assert "code_agent_denied" in plan.context["reasons"]
    assert plan.context["opencodeRouting"]["allowed"] is False
    assert plan.context["opencodeRouting"]["reason"] == "opencodeEnabled is false"


@pytest.mark.asyncio
async def test_web_search_capability_executes_through_adapter(monkeypatch, memory_storage: SQLiteStorage):
    async def fake_web_search(args):
        return {
            "summary": "mock web result",
            "data": {"query": args["query"], "results": [{"title": "A"}]},
        }

    monkeypatch.setattr(capability_adapters, "run_web_search_agent", fake_web_search)
    request = OrchestratorTaskCreateRequest(prompt="上网搜索 Amadeus 项目资料", workspacePath="E:/Amadeus/Amadeus_web")
    settings = OrchestratorSettings(defaultWorkspace="E:/Amadeus/Amadeus_web", trustMode=True, agentLoopEnabled=False)
    task = await orchestrator_storage.create_task(
        memory_storage,
        user_id=DEFAULT_USER_ID,
        title="搜索任务",
        prompt=request.prompt,
        workspace_path=request.workspace_path,
        conversation_id=None,
        active_skill_ids=[],
        settings=settings.model_dump(by_alias=True),
        budget={},
    )

    await default_orchestrator_runner.run(storage=memory_storage, task_id=task["id"], request=request, settings=settings)

    detail = await orchestrator_storage.get_detail(memory_storage, task["id"])
    assert detail is not None
    assert detail["task"]["status"] == "done"
    assert any(event["name"] == "web_search" and event["summary"] == "mock web result" for event in detail["events"])


@pytest.mark.asyncio
async def test_web_search_prefers_connected_mcp_tool(monkeypatch, memory_storage: SQLiteStorage):
    class FakeTool:
        name = "firecrawl_search"
        description = "Search the web with Firecrawl"
        input_schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "scrapeOptions": {"type": "object"},
            },
            "required": ["query"],
        }

    class FakeClient:
        connected = True
        server_id = "firecrawl"
        tools = [FakeTool()]

        def __init__(self):
            self.calls: list[dict] = []

        async def call_tool(self, name, arguments):
            self.calls.append({"name": name, "arguments": arguments})
            return {"content": [{"type": "text", "text": "mcp search result"}]}

    class FakeMcpManager:
        def __init__(self, client):
            self.client = client

        def list_clients(self):
            return [self.client]

    async def unexpected_builtin_search(args):
        raise AssertionError("builtin web_search should not run when a stronger MCP search tool is connected")

    fake_client = FakeClient()
    monkeypatch.setattr(capability_adapters, "mcp_manager", FakeMcpManager(fake_client))
    monkeypatch.setattr(capability_adapters, "run_web_search_agent", unexpected_builtin_search)
    request = OrchestratorTaskCreateRequest(prompt="上网搜索 Amadeus 项目资料", workspacePath="E:/Amadeus/Amadeus_web")
    settings = OrchestratorSettings(defaultWorkspace="E:/Amadeus/Amadeus_web", trustMode=True, agentLoopEnabled=False)
    task = await orchestrator_storage.create_task(
        memory_storage,
        user_id=DEFAULT_USER_ID,
        title="MCP 搜索任务",
        prompt=request.prompt,
        workspace_path=request.workspace_path,
        conversation_id=None,
        active_skill_ids=[],
        settings=settings.model_dump(by_alias=True),
        budget={},
    )

    await default_orchestrator_runner.run(storage=memory_storage, task_id=task["id"], request=request, settings=settings)

    detail = await orchestrator_storage.get_detail(memory_storage, task["id"])
    assert detail is not None
    assert detail["task"]["status"] == "done"
    assert fake_client.calls == [
        {
            "name": "firecrawl_search",
            "arguments": {
                "query": request.prompt,
                "limit": 5,
                "scrapeOptions": {"formats": ["markdown"], "onlyMainContent": True},
            },
        }
    ]
    assert any(event["kind"] == "mcp" and event["name"] == "firecrawl.firecrawl_search" for event in detail["events"])
    web_events = [event for event in detail["events"] if event["kind"] == "tool" and event["name"] == "web_search"]
    assert web_events
    assert web_events[-1]["payload"]["data"]["provider"] == "mcp"
    assert web_events[-1]["payload"]["data"]["serverId"] == "firecrawl"


@pytest.mark.asyncio
async def test_code_search_prefers_connected_mcp_tool(tmp_path: Path, monkeypatch, memory_storage: SQLiteStorage):
    task = await orchestrator_storage.create_task(
        memory_storage,
        user_id=DEFAULT_USER_ID,
        title="MCP 代码搜索",
        prompt="查找 `target_func` 的定义",
        workspace_path=str(tmp_path),
        conversation_id=None,
        active_skill_ids=[],
        settings=OrchestratorSettings(defaultWorkspace=str(tmp_path), agentLoopEnabled=False).model_dump(by_alias=True),
        budget={},
    )

    class FakeTool:
        name = "search_code"
        description = "Search code in a repository workspace"
        input_schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        }

    class FakeClient:
        connected = True
        server_id = "repo-tools"
        tools = [FakeTool()]

        def __init__(self):
            self.calls: list[dict] = []

        async def call_tool(self, name, arguments):
            self.calls.append({"name": name, "arguments": arguments})
            return {"matches": [{"path": "app.py", "line": 1, "preview": "def target_func():"}]}

    class FakeMcpManager:
        def __init__(self, client):
            self.client = client

        def list_clients(self):
            return [self.client]

    fake_client = FakeClient()
    monkeypatch.setattr(capability_adapters, "mcp_manager", FakeMcpManager(fake_client))

    async def emit_event(**event):
        return await orchestrator_storage.append_event(memory_storage, task_id=task["id"], **event)

    context = CapabilityExecutionContext(
        task_id=task["id"],
        prompt="查找 `target_func` 的定义",
        workspace_path=str(tmp_path),
        settings=OrchestratorSettings(defaultWorkspace=str(tmp_path), trustMode=True, agentLoopEnabled=False),
        storage=memory_storage,
        emit_event=emit_event,
    )
    result = await capability_adapters.default_capability_registry.execute(
        "code_search",
        {"query": "target_func", "path": ".", "maxResults": 10},
        context,
    )

    assert result["ok"] is True
    assert result["data"]["provider"] == "mcp"
    assert fake_client.calls == [
        {"name": "search_code", "arguments": {"query": "target_func", "path": str(tmp_path), "limit": 10}}
    ]
    events = await orchestrator_storage.list_events(memory_storage, task["id"])
    assert any(event["kind"] == "mcp" and event["name"] == "repo-tools.search_code" for event in events)


@pytest.mark.asyncio
async def test_code_search_falls_back_to_local_workspace(tmp_path: Path, monkeypatch, memory_storage: SQLiteStorage):
    source = tmp_path / "app.py"
    source.write_text("def target_func():\n    return 42\n", encoding="utf-8")
    task = await orchestrator_storage.create_task(
        memory_storage,
        user_id=DEFAULT_USER_ID,
        title="本地代码搜索",
        prompt="查找 target_func",
        workspace_path=str(tmp_path),
        conversation_id=None,
        active_skill_ids=[],
        settings=OrchestratorSettings(defaultWorkspace=str(tmp_path), agentLoopEnabled=False).model_dump(by_alias=True),
        budget={},
    )

    class EmptyMcpManager:
        def list_clients(self):
            return []

    monkeypatch.setattr(capability_adapters, "mcp_manager", EmptyMcpManager())
    context = CapabilityExecutionContext(
        task_id=task["id"],
        prompt="查找 target_func",
        workspace_path=str(tmp_path),
        settings=OrchestratorSettings(defaultWorkspace=str(tmp_path), trustMode=True, agentLoopEnabled=False),
        storage=memory_storage,
    )
    result = await capability_adapters.default_capability_registry.execute(
        "code_search",
        {"query": "target_func", "path": "."},
        context,
    )

    assert result["ok"] is True
    assert result["data"]["provider"] == "builtin"
    assert result["data"]["results"] == [{"path": str(source), "line": 1, "preview": "def target_func():"}]


@pytest.mark.asyncio
async def test_opencode_disabled_prevents_code_agent_planning(memory_storage: SQLiteStorage):
    request = OrchestratorTaskCreateRequest(prompt="请修改 backend 代码", workspacePath="E:/Amadeus/Amadeus_web")
    settings = OrchestratorSettings(defaultWorkspace="E:/Amadeus/Amadeus_web", trustMode=True, opencodeEnabled=False, agentLoopEnabled=False)
    task = await orchestrator_storage.create_task(
        memory_storage,
        user_id=DEFAULT_USER_ID,
        title="禁用 OpenCode",
        prompt=request.prompt,
        workspace_path=request.workspace_path,
        conversation_id=None,
        active_skill_ids=[],
        settings=settings.model_dump(by_alias=True),
        budget={},
    )

    await default_orchestrator_runner.run(storage=memory_storage, task_id=task["id"], request=request, settings=settings)

    detail = await orchestrator_storage.get_detail(memory_storage, task["id"])
    assert detail is not None
    assert detail["task"]["status"] == "done"
    plan_event = next(event for event in detail["events"] if event["kind"] == "plan")
    planner_payload = plan_event["payload"]["planner"]
    capabilities = [step["capability"] for step in plan_event["payload"]["steps"]]
    assert "code_agent" not in capabilities
    assert "code_agent_denied" in planner_payload["reasons"]
    assert planner_payload["opencodeRouting"]["allowed"] is False
    assert planner_payload["opencodeRouting"]["reason"] == "opencodeEnabled is false"
    assert not any(event["name"] == "code_agent" for event in detail["events"])


@pytest.mark.asyncio
async def test_orchestrator_permission_queue_blocks_and_approves(monkeypatch, memory_storage: SQLiteStorage):
    async def fake_code_search(args, context):
        return {"ok": True, "summary": "searched code", "data": {"results": []}}

    code_agent_calls = 0

    async def fake_code_agent(args, context):
        nonlocal code_agent_calls
        code_agent_calls += 1
        return {"ok": True, "summary": "code agent resumed", "data": {"args": args}}

    registry = CapabilityRegistry()
    registry.register("code_search", fake_code_search)
    registry.register("code_agent", fake_code_agent)
    runner = OrchestratorRunner(capability_registry=registry)
    request = OrchestratorTaskCreateRequest(
        prompt="请修改 backend 代码",
        workspacePath="E:/Amadeus/Amadeus_web",
        trustMode=False,
    )
    settings = OrchestratorSettings(defaultWorkspace="E:/Amadeus/Amadeus_web", trustMode=False, agentLoopEnabled=False)
    task = await orchestrator_storage.create_task(
        memory_storage,
        user_id=DEFAULT_USER_ID,
        title="需要权限",
        prompt=request.prompt,
        workspace_path=request.workspace_path,
        conversation_id=None,
        active_skill_ids=[],
        settings=settings.model_dump(by_alias=True),
        budget={},
    )

    await runner.run(storage=memory_storage, task_id=task["id"], request=request, settings=settings)

    detail = await orchestrator_storage.get_detail(memory_storage, task["id"])
    assert detail is not None
    assert detail["task"]["status"] == "paused"
    permissions = await orchestrator_storage.list_pending_permission_requests(memory_storage, task["id"])
    assert len(permissions) == 1
    assert permissions[0]["toolName"] == "code_agent"
    assert permissions[0]["riskLevel"] == "confirm"
    assert permissions[0]["payload"]["stepIndex"] == 1
    assert code_agent_calls == 0
    assert any(event["kind"] == "question" and event["name"] == "tool_permission" for event in detail["events"])

    monkeypatch.setattr(orchestrator, "require_storage", lambda: memory_storage)
    monkeypatch.setattr(orchestrator, "default_orchestrator_runner", runner)
    app = FastAPI()
    app.include_router(orchestrator.router)
    client = TestClient(app)

    listed = client.get(f"/api/orchestrator/tasks/{task['id']}/permissions")
    assert listed.status_code == 200
    assert listed.json()["permissions"][0]["id"] == permissions[0]["id"]
    approved = client.post(
        f"/api/orchestrator/permissions/{permissions[0]['id']}/approve",
        json={"reason": "允许本次代码 agent"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert code_agent_calls == 1
    listed_after = client.get(f"/api/orchestrator/tasks/{task['id']}/permissions")
    assert listed_after.status_code == 200
    assert listed_after.json()["permissions"] == []
    resumed_detail = await orchestrator_storage.get_detail(memory_storage, task["id"])
    assert resumed_detail is not None
    assert resumed_detail["task"]["status"] == "done"
    assert any(event["name"] == "code_agent" and event["summary"] == "code agent resumed" for event in resumed_detail["events"])
    duplicate = client.post(f"/api/orchestrator/permissions/{permissions[0]['id']}/approve", json={})
    assert duplicate.status_code == 409


def test_orchestrator_create_task_persists_attachments_in_context(monkeypatch, tmp_path: Path):
    memory_storage = create_memory_storage_sync()
    asyncio.run(
        memory_storage.save_settings(
            user_id=DEFAULT_USER_ID,
            model={},
            vision={},
            speech_input={},
            voice={},
            desktop_assistant={},
            mode="fast",
            orchestrator={"enabled": True, "defaultWorkspace": str(tmp_path), "trustMode": True},
        )
    )
    monkeypatch.setattr(orchestrator, "require_storage", lambda: memory_storage)

    scheduled: list[str] = []

    def fake_create_task(coro, name=None):
        scheduled.append(str(name or ""))
        coro.close()
        return SimpleNamespace(cancel=lambda: None)

    monkeypatch.setattr(orchestrator.asyncio, "create_task", fake_create_task)
    app = FastAPI()
    app.include_router(orchestrator.router)
    client = TestClient(app)

    response = client.post(
        "/api/orchestrator/tasks",
        json={
            "prompt": "分析附件并写总结",
            "attachments": [
                {
                    "path": "agent_uploads/host/2026-06-29/document/report.md",
                    "originalFilename": "report.md",
                    "filename": "report.md",
                    "mediaType": "document",
                    "contentType": "text/markdown",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert scheduled
    task = asyncio.run(orchestrator_storage.get_task(memory_storage, response.json()["taskId"]))
    assert task is not None
    assert task["context"]["attachments"][0]["path"].endswith("report.md")
    assert task["context"]["threadHistory"][0]["role"] == "user"
    assert task["context"]["threadHistory"][0]["attachments"][0]["mediaType"] == "document"
    asyncio.run(memory_storage.close())


@pytest.mark.asyncio
async def test_orchestrator_task_message_endpoint_appends_user_message(monkeypatch, memory_storage: SQLiteStorage, tmp_path: Path):
    monkeypatch.setattr(orchestrator, "require_storage", lambda: memory_storage)

    async def fake_run_task(task_id: str, request: OrchestratorTaskCreateRequest, settings: OrchestratorSettings):
        await default_orchestrator_runner._append_user_message(storage=memory_storage, task_id=task_id, request=request)
        await orchestrator_storage.append_event(
            memory_storage,
            task_id=task_id,
            kind="message",
            role="assistant",
            name="final_answer",
            status="done",
            summary="续聊完成",
            payload={"text": "续聊完成", "completedItems": ["done"], "artifacts": [], "verification": [], "risks": []},
        )
        await orchestrator_storage.update_task_status(memory_storage, task_id, status="done", finished=True)

    monkeypatch.setattr(orchestrator, "_run_task", fake_run_task)
    settings_payload = OrchestratorSettings(defaultWorkspace=str(tmp_path), trustMode=True, agentLoopEnabled=False).model_dump(by_alias=True)
    task = await orchestrator_storage.create_task(
        memory_storage,
        user_id=DEFAULT_USER_ID,
        title="任务续聊",
        prompt="初始任务",
        workspace_path=str(tmp_path),
        conversation_id=None,
        active_skill_ids=[],
        settings=settings_payload,
        context={
            "mode": "fast",
            "model": {"providerName": "demo"},
            "attachments": [],
            "threadHistory": [{"role": "user", "text": "初始任务", "attachments": []}],
        },
        budget={"maxRounds": 20, "maxToolCalls": 80, "maxRuntimeSeconds": 1800, "maxSamplingDepth": 3},
    )
    await orchestrator_storage.update_task_status(memory_storage, task["id"], status="done", finished=True)

    response = await orchestrator.append_orchestrator_task_message(
        task["id"],
        OrchestratorTaskMessageRequest(
            prompt="继续补充测试验证",
            attachments=[
                {
                    "path": "agent_uploads/host/2026-06-29/document/checklist.md",
                    "originalFilename": "checklist.md",
                    "filename": "checklist.md",
                    "mediaType": "document",
                    "contentType": "text/markdown",
                }
            ],
            trustMode=True,
        ),
    )
    await asyncio.sleep(0)

    assert response["taskId"] == task["id"]
    updated = await orchestrator_storage.get_task(memory_storage, task["id"])
    assert updated is not None
    assert updated["context"]["threadHistory"][-1]["text"] == "继续补充测试验证"
    assert updated["context"]["attachments"][-1]["filename"] == "checklist.md"
    events = await orchestrator_storage.list_events(memory_storage, task["id"])
    assert any(event["kind"] == "message" and event["role"] == "user" and event["summary"] == "继续补充测试验证" for event in events)
    assert any(event["kind"] == "message" and event["role"] == "assistant" and event["name"] == "final_answer" for event in events)


def test_orchestrator_api_and_deprecated_agent_api(monkeypatch):
    memory_storage = create_memory_storage_sync()
    monkeypatch.setattr(orchestrator, "require_storage", lambda: memory_storage)
    monkeypatch.setattr(orchestrator_integrations, "require_storage", lambda: memory_storage)
    monkeypatch.setattr(legacy_agent, "require_storage", lambda: memory_storage)
    monkeypatch.setattr(settings, "require_storage", lambda: memory_storage)
    asyncio.run(memory_storage.save_settings(
        user_id=DEFAULT_USER_ID,
        model={},
        vision={},
        speech_input={},
        voice={},
        desktop_assistant={},
        mode="fast",
        orchestrator=OrchestratorSettings(enabled=True, agentLoopEnabled=False, defaultWorkspace="E:/Amadeus/Amadeus_web").model_dump(by_alias=True),
    ))

    app = FastAPI()
    app.include_router(orchestrator.router)
    app.include_router(orchestrator_integrations.router)
    app.include_router(legacy_agent.router)
    app.include_router(settings.router)
    client = TestClient(app)

    response = client.post(
        "/api/orchestrator/tasks/stream",
        json={"prompt": "整理当前项目结构", "workspacePath": "E:/Amadeus/Amadeus_web"},
    )
    assert response.status_code == 200
    task_id = response.headers.get("X-Task-Id")
    assert task_id
    assert "event: plan" in response.text
    assert "event: done" in response.text

    detail = client.get(f"/api/orchestrator/tasks/{task_id}")
    assert detail.status_code == 200
    payload = detail.json()["task"]
    assert payload["task"]["id"] == task_id
    assert payload["task"]["runtimeVersion"] == "orchestrator_v2"
    assert len(payload["events"]) >= 3

    capabilities = client.get("/api/orchestrator/capabilities")
    assert capabilities.status_code == 200
    capabilities_payload = capabilities.json()
    assert capabilities_payload["ok"] is True
    assert capabilities_payload["data"]["runtime"] == "orchestrator_v2"
    assert capabilities_payload["data"]["defaultEndpoint"] == "/api/orchestrator/tasks"
    capability_names = {item["name"] for item in capabilities_payload["data"]["capabilities"]}
    assert {"web_search", "code_agent", "todo_task"}.issubset(capability_names)
    assert "browser" not in capability_names
    legacy_capabilities = client.get("/api/agent/capabilities")
    assert legacy_capabilities.status_code == 200
    assert legacy_capabilities.headers["Deprecation"] == "true"
    assert legacy_capabilities.headers["X-Amadeus-Replacement"] == "/api/orchestrator/capabilities"
    legacy_capability_names = {item["name"] for item in legacy_capabilities.json()["data"]["capabilities"]}
    assert "browser" not in legacy_capability_names
    assert legacy_capabilities.json()["data"]["runtime"] == "orchestrator_v2"

    asyncio.run(
        memory_storage.save_settings(
            user_id=DEFAULT_USER_ID,
            model={},
            vision={},
            speech_input={},
            voice={},
            desktop_assistant={},
            mode="fast",
            orchestrator={"browserEnabled": True},
        )
    )
    browser_capabilities = client.get("/api/orchestrator/capabilities")
    assert browser_capabilities.status_code == 200
    browser_capability_names = {item["name"] for item in browser_capabilities.json()["data"]["capabilities"]}
    assert "browser" in browser_capability_names
    legacy_browser_capabilities = client.get("/api/agent/capabilities")
    assert legacy_browser_capabilities.status_code == 200
    legacy_browser_capability_names = {
        item["name"] for item in legacy_browser_capabilities.json()["data"]["capabilities"]
    }
    assert "browser" in legacy_browser_capability_names

    canonical_invoke = client.post(
        "/api/orchestrator/invoke",
        json={"action": "call_tool", "targetType": "tool", "target": "calculate", "payload": {"expression": "2 + 3"}},
    )
    assert canonical_invoke.status_code == 200
    assert canonical_invoke.json()["data"]["result"]["value"] == 5
    legacy_invoke = client.post(
        "/api/agent/invoke",
        json={"action": "query_capabilities", "targetType": "auto"},
    )
    assert legacy_invoke.status_code == 200
    assert legacy_invoke.headers["Deprecation"] == "true"
    assert legacy_invoke.headers["X-Amadeus-Replacement"] == "/api/orchestrator/invoke"
    legacy_invoke_payload = legacy_invoke.json()
    assert legacy_invoke_payload["data"]["invokeEndpoint"] == "/api/orchestrator/invoke"
    assert legacy_invoke_payload["data"]["compatInvokeEndpoint"] == "/api/agent/invoke"

    settings_save = client.put(
        "/api/settings",
        json={
            "model": {},
            "vision": {},
            "speechInput": {},
            "voice": {},
            "desktopAssistant": {},
            "mode": "fast",
            "orchestrator": {"enabled": True, "defaultWorkspace": "E:/Amadeus/Amadeus_web"},
        },
    )
    assert settings_save.status_code == 200
    settings_get = client.get("/api/settings")
    assert settings_get.status_code == 200
    settings_payload = settings_get.json()["settings"]
    assert settings_payload["orchestrator"]["defaultWorkspace"] == "E:/Amadeus/Amadeus_web"
    assert settings_payload["agent"]["defaultWorkspace"] == "E:/Amadeus/Amadeus_web"

    old_create = client.post("/api/agent/tasks", json={"prompt": "旧入口"})
    assert old_create.status_code == 410
    assert old_create.headers["Deprecation"] == "true"
    assert old_create.headers["X-Amadeus-Replacement"] == "/api/orchestrator/tasks"
    old_stream = client.post("/api/agent/tasks/stream", json={"prompt": "旧流式入口"})
    assert old_stream.status_code == 410
    old_list = client.get("/api/agent/tasks")
    assert old_list.status_code == 410
    old_permissions = client.get("/api/agent/tasks/legacy-task/permissions")
    assert old_permissions.status_code == 410
    old_approve = client.post("/api/agent/permissions/legacy-permission/approve", json={})
    assert old_approve.status_code == 410
    old_reject = client.post("/api/agent/permissions/legacy-permission/reject", json={})
    assert old_reject.status_code == 410

    presets = client.get("/api/mcp/presets")
    assert presets.status_code == 200
    assert isinstance(presets.json()["presets"], list)
    skills = client.get("/api/skills")
    assert skills.status_code == 200
    assert skills.json()["skills"] == []
    asyncio.run(memory_storage.close())


def test_orchestrator_artifact_metadata_and_download(monkeypatch, tmp_path: Path):
    memory_storage = create_memory_storage_sync()
    monkeypatch.setattr(orchestrator, "require_storage", lambda: memory_storage)
    monkeypatch.setattr(legacy_agent, "require_storage", lambda: memory_storage)

    artifact_path = tmp_path / "report.md"
    artifact_path.write_text("# Report\n\ncontent", encoding="utf-8")
    task = asyncio.run(
        orchestrator_storage.create_task(
            memory_storage,
            user_id=DEFAULT_USER_ID,
            title="artifact task",
            prompt="生成文档",
            workspace_path=str(tmp_path),
            conversation_id=None,
            active_skill_ids=[],
            settings=OrchestratorSettings(defaultWorkspace=str(tmp_path), agentLoopEnabled=False).model_dump(by_alias=True),
            budget={},
        )
    )
    artifact = asyncio.run(
        orchestrator_storage.create_artifact(
            memory_storage,
            task_id=task["id"],
            kind="document",
            name=artifact_path.name,
            path=str(artifact_path),
            mime_type="text/markdown",
            size_bytes=artifact_path.stat().st_size,
            description="test artifact",
        )
    )

    legacy_task_id = "legacy-artifact-task"
    legacy_artifact_id = "legacy-artifact-id"
    legacy_path = tmp_path / "legacy.md"
    legacy_path.write_text("# Legacy\n\ncontent", encoding="utf-8")
    now = "2026-06-25T00:00:00+00:00"

    def _insert_legacy(conn):
        conn.execute(
            """
            INSERT INTO agent_tasks
            (id, user_id, title, prompt, workspace_path, conversation_id, status,
             active_skill_ids, settings_json, budget_json, error, created_at, updated_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                legacy_task_id,
                DEFAULT_USER_ID,
                "legacy artifact task",
                "旧任务",
                str(tmp_path),
                None,
                "done",
                "[]",
                "{}",
                "{}",
                "",
                now,
                now,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO agent_task_events
            (task_id, seq, kind, role, name, status, summary, payload, artifact_ids, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                legacy_task_id,
                1,
                "done",
                "assistant",
                "finish",
                "done",
                "legacy done",
                json.dumps({"ok": True}),
                json.dumps([legacy_artifact_id]),
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO agent_artifacts
            (id, task_id, kind, name, path, mime_type, size_bytes, description, meta_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                legacy_artifact_id,
                legacy_task_id,
                "document",
                legacy_path.name,
                str(legacy_path),
                "text/markdown",
                legacy_path.stat().st_size,
                "legacy artifact",
                "{}",
                now,
            ),
        )

    asyncio.run(memory_storage.run_in_thread(_insert_legacy))

    app = FastAPI()
    app.include_router(orchestrator.router)
    app.include_router(legacy_agent.router)
    client = TestClient(app)

    metadata = client.get(f"/api/orchestrator/artifacts/{artifact['id']}")
    assert metadata.status_code == 200
    assert metadata.json()["artifact"]["id"] == artifact["id"]
    download = client.get(f"/api/orchestrator/artifacts/{artifact['id']}/download")
    assert download.status_code == 200
    assert download.content == artifact_path.read_bytes()

    legacy_detail = client.get(f"/api/orchestrator/tasks/{legacy_task_id}")
    assert legacy_detail.status_code == 200
    legacy_payload = legacy_detail.json()["task"]
    assert legacy_payload["task"]["runtimeVersion"] == "legacy_complex_agent"
    assert legacy_payload["task"]["eventCount"] == 1
    assert legacy_payload["task"]["artifactCount"] == 1
    assert legacy_payload["events"][0]["payload"] == {"ok": True}
    assert legacy_payload["artifacts"][0]["id"] == legacy_artifact_id

    legacy_metadata = client.get(f"/api/artifacts/{legacy_artifact_id}")
    assert legacy_metadata.status_code == 200
    assert legacy_metadata.json()["artifact"]["id"] == legacy_artifact_id
    legacy_download = client.get(f"/api/artifacts/{legacy_artifact_id}/download")
    assert legacy_download.status_code == 200
    assert legacy_download.content == legacy_path.read_bytes()
    asyncio.run(memory_storage.close())


@pytest.mark.asyncio
async def test_code_agent_maps_opencode_events_to_v2_ledger(monkeypatch, memory_storage: SQLiteStorage):
    async def fake_code_search(args, context):
        return {"ok": True, "summary": "searched code", "data": {"results": []}}

    async def fake_stream_opencode_task(request):
        yield 'event: status\ndata: {"message": "starting"}\n\n'
        yield 'event: tool\ndata: {"command": "pytest", "status": "done"}\n\n'
        yield 'event: done\ndata: {"summary": "patched"}\n\n'

    monkeypatch.setattr(capability_adapters, "stream_opencode_task", fake_stream_opencode_task)
    registry = CapabilityRegistry()
    registry.register("code_search", fake_code_search)
    registry.register("code_agent", capability_adapters._code_agent)
    runner = OrchestratorRunner(capability_registry=registry)
    request = OrchestratorTaskCreateRequest(prompt="请修改 backend 代码", workspacePath="E:/Amadeus/Amadeus_web")
    settings = OrchestratorSettings(defaultWorkspace="E:/Amadeus/Amadeus_web", trustMode=True, opencodeEnabled=True, agentLoopEnabled=False)
    task = await orchestrator_storage.create_task(
        memory_storage,
        user_id=DEFAULT_USER_ID,
        title="OpenCode ledger",
        prompt=request.prompt,
        workspace_path=request.workspace_path,
        conversation_id=None,
        active_skill_ids=[],
        settings=settings.model_dump(by_alias=True),
        budget={},
    )

    await runner.run(storage=memory_storage, task_id=task["id"], request=request, settings=settings)

    detail = await orchestrator_storage.get_detail(memory_storage, task["id"])
    assert detail is not None
    assert detail["task"]["status"] == "done"
    event_names = [event["name"] for event in detail["events"]]
    assert "code_agent.status" in event_names
    assert "code_agent.tool" in event_names
    assert "code_agent.done" in event_names
    assert any(event["name"] == "code_agent" and event["summary"] == "patched" for event in detail["events"])


@pytest.mark.asyncio
async def test_file_write_adapter_records_v2_artifact(tmp_path: Path, memory_storage: SQLiteStorage):
    task = await orchestrator_storage.create_task(
        memory_storage,
        user_id=DEFAULT_USER_ID,
        title="file write",
        prompt="写一份说明",
        workspace_path=str(tmp_path),
        conversation_id=None,
        active_skill_ids=[],
        settings=OrchestratorSettings(defaultWorkspace=str(tmp_path), agentLoopEnabled=False).model_dump(by_alias=True),
        budget={},
    )

    async def emit_event(**event):
        return await orchestrator_storage.append_event(memory_storage, task_id=task["id"], **event)

    context = CapabilityExecutionContext(
        task_id=task["id"],
        prompt="写一份说明",
        workspace_path=str(tmp_path),
        settings=OrchestratorSettings(defaultWorkspace=str(tmp_path), trustMode=True, agentLoopEnabled=False),
        storage=memory_storage,
        emit_event=emit_event,
    )
    result = await capability_adapters.default_capability_registry.execute(
        "file_write",
        {"path": "notes/result.md", "content": "hello"},
        context,
    )

    assert result["ok"] is True
    assert (tmp_path / "notes" / "result.md").read_text(encoding="utf-8") == "hello"
    artifacts = await orchestrator_storage.list_artifacts(memory_storage, task["id"])
    assert len(artifacts) == 1
    assert artifacts[0]["name"] == "result.md"
    events = await orchestrator_storage.list_events(memory_storage, task["id"])
    assert any(event["kind"] == "artifact" and event["artifactIds"] == [artifacts[0]["id"]] for event in events)


@pytest.mark.asyncio
async def test_document_convert_prefers_markitdown_mcp(tmp_path: Path, monkeypatch, memory_storage: SQLiteStorage):
    source = tmp_path / "brief.pdf"
    source.write_bytes(b"%PDF fake")
    task = await orchestrator_storage.create_task(
        memory_storage,
        user_id=DEFAULT_USER_ID,
        title="document convert",
        prompt="转换 brief.pdf",
        workspace_path=str(tmp_path),
        conversation_id=None,
        active_skill_ids=[],
        settings=OrchestratorSettings(defaultWorkspace=str(tmp_path), agentLoopEnabled=False).model_dump(by_alias=True),
        budget={},
    )

    class FakeTool:
        name = "convert_to_markdown"
        description = "MarkItDown document conversion"
        input_schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "format": {"type": "string"},
            },
            "required": ["path"],
        }

    class FakeClient:
        connected = True
        server_id = "markitdown"
        tools = [FakeTool()]

        def __init__(self):
            self.calls: list[dict] = []

        async def call_tool(self, name, arguments):
            self.calls.append({"name": name, "arguments": arguments})
            return {"content": [{"type": "text", "text": "# Converted\n\nvia MCP"}]}

    class FakeMcpManager:
        def __init__(self, client):
            self.client = client

        def list_clients(self):
            return [self.client]

    fake_client = FakeClient()
    monkeypatch.setattr(capability_adapters, "mcp_manager", FakeMcpManager(fake_client))

    async def emit_event(**event):
        return await orchestrator_storage.append_event(memory_storage, task_id=task["id"], **event)

    context = CapabilityExecutionContext(
        task_id=task["id"],
        prompt="转换 brief.pdf",
        workspace_path=str(tmp_path),
        settings=OrchestratorSettings(defaultWorkspace=str(tmp_path), trustMode=True, agentLoopEnabled=False),
        storage=memory_storage,
        emit_event=emit_event,
    )
    result = await capability_adapters.default_capability_registry.execute(
        "document_convert",
        {"path": "brief.pdf"},
        context,
    )

    assert result["ok"] is True
    assert result["data"]["provider"] == "mcp"
    assert result["data"]["markdown"] == "# Converted\n\nvia MCP"
    assert fake_client.calls == [{"name": "convert_to_markdown", "arguments": {"path": str(source), "format": "markdown"}}]
    artifacts = await orchestrator_storage.list_artifacts(memory_storage, task["id"])
    assert len(artifacts) == 1
    assert artifacts[0]["name"] == "brief.converted.md"
    assert Path(artifacts[0]["path"]).read_text(encoding="utf-8") == "# Converted\n\nvia MCP"
    events = await orchestrator_storage.list_events(memory_storage, task["id"])
    assert any(event["kind"] == "mcp" and event["name"] == "markitdown.convert_to_markdown" for event in events)


@pytest.mark.asyncio
async def test_document_convert_falls_back_to_builtin_reader(tmp_path: Path, monkeypatch, memory_storage: SQLiteStorage):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nlocal fallback", encoding="utf-8")
    task = await orchestrator_storage.create_task(
        memory_storage,
        user_id=DEFAULT_USER_ID,
        title="document convert fallback",
        prompt="转换 notes.md",
        workspace_path=str(tmp_path),
        conversation_id=None,
        active_skill_ids=[],
        settings=OrchestratorSettings(defaultWorkspace=str(tmp_path), agentLoopEnabled=False).model_dump(by_alias=True),
        budget={},
    )

    class EmptyMcpManager:
        def list_clients(self):
            return []

    monkeypatch.setattr(capability_adapters, "mcp_manager", EmptyMcpManager())

    async def emit_event(**event):
        return await orchestrator_storage.append_event(memory_storage, task_id=task["id"], **event)

    context = CapabilityExecutionContext(
        task_id=task["id"],
        prompt="转换 notes.md",
        workspace_path=str(tmp_path),
        settings=OrchestratorSettings(defaultWorkspace=str(tmp_path), trustMode=True, agentLoopEnabled=False),
        storage=memory_storage,
        emit_event=emit_event,
    )
    result = await capability_adapters.default_capability_registry.execute(
        "document_convert",
        {"path": "notes.md"},
        context,
    )

    assert result["ok"] is True
    assert result["data"]["provider"] == "builtin"
    assert result["data"]["markdown"] == "# Notes\n\nlocal fallback"
    artifacts = await orchestrator_storage.list_artifacts(memory_storage, task["id"])
    assert len(artifacts) == 1
    assert Path(artifacts[0]["path"]).read_text(encoding="utf-8") == "# Notes\n\nlocal fallback"


@pytest.mark.asyncio
async def test_simple_chat_safe_tool_still_executes():
    request = OrchestratorInvokeRequest(
        action="call_tool",
        targetType="tool",
        target="calculate",
        payload={"expression": "2 + 2"},
    )
    result = await invoke_service.call_tool(request)
    assert result["ok"] is True
    assert result["data"]["result"]["value"] == 4


@pytest.mark.asyncio
async def test_legacy_agent_tool_routes_through_orchestrator_capability(monkeypatch):
    calls: list[dict] = []

    class FakeCapabilityRegistry:
        async def execute(self, name, args, context):
            calls.append(
                {
                    "name": name,
                    "args": args,
                    "prompt": context.prompt,
                    "workspace": context.workspace_path,
                    "trustMode": context.settings.trust_mode,
                    "metadata": context.metadata,
                }
            )
            return {"ok": True, "summary": "routed through capability", "data": {"provider": "fake"}}

    monkeypatch.setattr(invoke_service, "default_capability_registry", FakeCapabilityRegistry())
    request = OrchestratorInvokeRequest(
        action="call_tool",
        targetType="tool",
        target="web_search",
        intent="搜索 Amadeus Agent 架构",
        payload={"workspacePath": "E:/Amadeus/Amadeus_web"},
    )
    result = await invoke_service.call_tool(request)

    assert result["ok"] is True
    assert result["summary"] == "routed through capability"
    assert result["data"]["capability"] == "web_search"
    assert calls == [
        {
            "name": "web_search",
            "args": {
                "workspacePath": "E:/Amadeus/Amadeus_web",
                "intent": "搜索 Amadeus Agent 架构",
                "query": "搜索 Amadeus Agent 架构",
            },
            "prompt": "搜索 Amadeus Agent 架构",
            "workspace": "E:/Amadeus/Amadeus_web",
            "trustMode": False,
            "metadata": {"source": "orchestrator_invoke_compat", "tool": "web_search"},
        }
    ]


@pytest.mark.asyncio
async def test_legacy_agent_endpoint_blocks_confirm_capabilities():
    request = OrchestratorInvokeRequest(
        action="call_tool",
        targetType="tool",
        target="doc_writer",
        intent="写一份项目总结文档",
        payload={"instruction": "写一份项目总结文档"},
    )
    result = await invoke_service.call_tool(request)

    assert result["ok"] is False
    assert result["data"]["capability"] == "doc_writer"
    assert result["data"]["risk"] == "confirm"
    assert "/api/orchestrator/tasks" in result["summary"]


@pytest.mark.asyncio
async def test_chat_complex_tool_routes_through_orchestrator_capability(monkeypatch):
    calls: list[dict] = []

    class FakeCapabilityRegistry:
        async def execute(self, name, args, context):
            calls.append(
                {
                    "name": name,
                    "args": args,
                    "prompt": context.prompt,
                    "workspace": context.workspace_path,
                    "trustMode": context.settings.trust_mode,
                }
            )
            return {"ok": True, "summary": "chat routed through capability", "data": {"provider": "fake"}}

    monkeypatch.setattr(chat_service, "default_capability_registry", FakeCapabilityRegistry())
    call = ModelToolCall(
        id="tool_1",
        name="web_search",
        arguments='{"query":"搜索 Amadeus Agent 架构","workspacePath":"E:/Amadeus/Amadeus_web"}',
    )

    result = await chat_service.execute_model_tool_call(call)

    assert result.ok is True
    assert result.summary == "chat routed through capability"
    assert result.result_payload is not None
    assert result.result_payload["data"]["capability"] == "web_search"
    assert calls == [
        {
            "name": "web_search",
            "args": {
                "query": "搜索 Amadeus Agent 架构",
                "workspacePath": "E:/Amadeus/Amadeus_web",
            },
            "prompt": "搜索 Amadeus Agent 架构",
            "workspace": "E:/Amadeus/Amadeus_web",
            "trustMode": False,
        }
    ]


@pytest.mark.asyncio
async def test_chat_tool_call_blocks_confirm_capabilities():
    call = ModelToolCall(
        id="tool_1",
        name="doc_writer",
        arguments='{"instruction":"写一份项目总结文档"}',
    )

    result = await chat_service.execute_model_tool_call(call)

    assert result.ok is False
    assert result.result_payload is not None
    assert result.result_payload["capability"] == "doc_writer"
    assert result.result_payload["risk"] == "confirm"
    assert "/api/orchestrator/tasks" in result.summary

