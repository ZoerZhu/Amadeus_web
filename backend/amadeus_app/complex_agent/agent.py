"""Complex agent orchestration built on LangGraph.

The graph implements a configurable multi-role pipeline:

    planner -> executor <-> researcher/browser_operator/document_writer/code_delegate
           -> critic -> summarizer

The graph structure is system-controlled; skills may declare role prompts
and tool allowlists but cannot define arbitrary nodes or edges.

Trust mode (default): the executor auto-runs enabled tools without asking
the user for confirmation. All calls are still audit-logged and subject
to budgets / pause / cancel / rollback.
"""

from __future__ import annotations

import json
import time
from typing import Any, Literal

import httpx

try:
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict
    _HAS_LANGGRAPH = True
except ImportError:  # pragma: no cover
    _HAS_LANGGRAPH = False
    StateGraph = None  # type: ignore[assignment]
    END = "END"
    START = "START"

from ..agent_registry import tool_registry as builtin_tool_registry
from ..logging_config import get_logger
from ..model_adapter import build_chat_payload
from ..personas import build_system_prompt, get_persona
from ..providers import get_provider
from ..runtime_config import effective_model_settings
from . import agent_storage
from .browser import close_browser_session, make_browser_tool
from .domain import AgentSettings, AgentTaskCreateRequest
from .file_writer import make_file_writer_tool
from .opencode_delegate import make_opencode_delegate_tool
from .skills import LoadedSkillContext, load_skill_context
from .task_hub import ManagedAgentTask, TaskBudget, agent_task_hub
from .tool_registry import UnifiedTool, classify_tool_permission, unified_tool_registry

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------


if _HAS_LANGGRAPH:

    class AgentState(TypedDict, total=False):
        prompt: str
        plan: list[dict[str, Any]]
        steps: list[dict[str, Any]]
        tool_results: list[dict[str, Any]]
        artifacts: list[str]
        messages: list[dict[str, Any]]
        round: int
        done: bool
        summary: str
        error: str
        # Task ledger for structured flow
        current_step_index: int
        completed_steps: list[int]
        open_questions: list[str]
        risks: list[str]
        rejected_tools: list[dict[str, Any]]

else:  # pragma: no cover

    class AgentState(dict):  # type: ignore[no-redef]
        pass


# ---------------------------------------------------------------------------
# Role prompts
# ---------------------------------------------------------------------------


ROLE_PROMPTS: dict[str, str] = {
    "planner": (
        "你是复杂任务的规划者。根据用户目标，拆解为 3-8 个可执行的步骤。"
        "每个步骤需说明：id、goal（目标）、tools（所需工具列表）、expected（预期产物）。"
        "避免危险操作（删除系统文件、修改 .env/.git 等）。"
        "考虑步骤间的依赖关系和风险。"
        '只输出 JSON：{"steps": [{"id": 1, "goal": "...", "tools": [...], "expected": "...", "risk": "low|medium|high"}]}。'
    ),
    "executor": (
        "你是任务执行者。根据当前步骤和可用工具，选择最合适的工具调用。"
        "信任模式下可直接调用 safe 权限工具，无需用户确认。"
        "每次只调用一个工具，并基于结果决定下一步。"
        "专注于当前步骤的目标，不要偏航到其他步骤。"
        "如果当前步骤已完成，输出 {\"step_done\": true, \"summary\": \"...\"}。"
        "如果整个任务已完成，输出 {\"task_done\": true, \"summary\": \"...\"}。"
        "如果遇到阻塞或需要用户输入，输出 {\"blocked\": true, \"question\": \"...\"}。"
    ),
    "researcher": (
        "你是研究员。使用 web_search、code_search、file_reader 和 local_git 工具收集信息，"
        "输出结构化摘要和来源引用。"
    ),
    "browser_operator": (
        "你是浏览器操作员。使用 browser 工具打开页面、点击、输入、截图、提取文本。"
        "每次操作后简述结果。click 和 type 操作需要用户确认。"
    ),
    "document_writer": (
        "你是文档撰写者。使用 doc_writer 或 file_writer 工具生成文档，"
        "保存为 md/txt/docx/csv/xlsx，并记录产物路径。"
    ),
    "code_delegate": (
        "你是代码任务委托者。当代码修改或命令执行需要时，使用 opencode_delegate 工具。"
        "不要直接执行 shell 命令。"
    ),
    "critic": (
        "你是评审者。检查执行结果是否满足原始目标，指出遗漏或错误。"
        "评估当前步骤是否完成，是否需要继续工具调用，是否有遗漏的步骤。"
        '输出 JSON：{"satisfied": bool, "current_step_done": bool, "missing_steps": [...], "issues": [...], "suggestions": [...], "needs_more_tools": bool}。'
        "satisfied=true 表示整个任务目标已达成；current_step_done=true 表示当前步骤可以进入下一个。"
    ),
    "summarizer": (
        "你是总结者。基于全部步骤和工具结果，生成结构化中文任务总结。"
        "输出 JSON，包含以下字段："
        '{"completed": "完成的内容摘要", "artifacts": ["产物路径列表"], '
        '"incomplete": ["未完成项"], "rejected_tools": ["被拒绝/失败的工具调用"], '
        '"next_steps": "下一步建议"}。'
        "如果无法输出 JSON，则输出纯文本总结，不超过 500 字。"
    ),
}


DEFAULT_ROLES = list(ROLE_PROMPTS.keys())


# ---------------------------------------------------------------------------
# Tool assembly
# ---------------------------------------------------------------------------


async def assemble_task_tools(
    *,
    storage,
    task: ManagedAgentTask,
    settings: AgentSettings,
    skill_contexts: list[LoadedSkillContext],
) -> list[UnifiedTool]:
    """Build the task-scoped tool set.

    Combines builtin tools (filtered by skill allowlists), MCP tools
    (filtered by skill allowlists), and task-scoped tools (file_writer,
    browser, opencode_delegate).
    """
    # Compute the intersection of skill tool allowlists (if any skill declares one).
    allowlists: list[set[str]] = []
    for ctx in skill_contexts:
        if ctx.manifest.tool_allowlist:
            allowlists.append(set(ctx.manifest.tool_allowlist))
    if allowlists:
        # Intersection: a tool is allowed only if every declaring skill allows it.
        allowed = set.intersection(*allowlists)
    else:
        allowed = set()  # empty means "no restriction"

    # Load MCP server configs for permission classification.
    # Stored as a dict: server_id → {trusted, allowedTools} for the executor.
    mcp_server_info: dict[str, dict[str, Any]] = {}
    try:
        mcp_rows = await agent_storage.list_mcp_servers(storage)
    except Exception:  # noqa: BLE001
        mcp_rows = []
    for row in mcp_rows:
        server_id = row.get("id", "")
        mcp_server_info[server_id] = {
            "trusted": bool(row.get("trusted", False)),
            "allowedTools": set(row.get("allowedTools", []) or []),
        }
    task.settings["_mcp_server_info"] = mcp_server_info

    # Start from the global unified registry (builtin + MCP)
    tools: list[UnifiedTool] = []
    for tool in unified_tool_registry.list_tools():
        if allowed and tool.name not in allowed:
            continue
        tools.append(tool)

    # Add task-scoped tools
    file_writer = make_file_writer_tool(
        storage=storage,
        task_id=task.task_id,
        workspace=task.workspace_path or settings.default_workspace,
        artifact_root=settings.artifact_root,
    )
    tools.append(file_writer)

    if settings.browser_enabled:
        browser_tool = make_browser_tool(
            storage=storage,
            task_id=task.task_id,
            artifact_root=settings.artifact_root,
        )
        tools.append(browser_tool)
        task.add_cleanup_hook(lambda: close_browser_session(browser_tool))

    if settings.opencode_enabled:
        async def _on_event(kind: str, payload: dict[str, Any]) -> None:
            await task.emit(
                storage=storage,
                kind=kind,  # type: ignore[arg-type]
                name=payload.get("name", ""),
                status=payload.get("status", ""),
                summary=payload.get("summary", ""),
                payload=payload,
            )

        opencode_tool = make_opencode_delegate_tool(
            task_id=task.task_id,
            workspace=task.workspace_path or settings.default_workspace,
            on_event=_on_event,
            trust_mode=settings.trust_mode,
        )
        tools.append(opencode_tool)

    # Add local safe tools (code_search, local_git, markitdown_convert)
    from .local_tools import (
        make_code_search_tool,
        make_local_git_tool,
        make_markitdown_convert_tool,
    )

    workspace = task.workspace_path or settings.default_workspace
    tools.append(make_code_search_tool(workspace))
    tools.append(make_local_git_tool(workspace))
    tools.append(make_markitdown_convert_tool(workspace))

    # Add MCP resource/prompt tools (expose MCP resources/prompts to agent)
    from .mcp_resource_tools import (
        make_mcp_list_prompts_tool,
        make_mcp_list_resources_tool,
        make_mcp_prompt_get_tool,
        make_mcp_resource_read_tool,
    )

    tools.append(make_mcp_resource_read_tool())
    tools.append(make_mcp_prompt_get_tool())
    tools.append(make_mcp_list_resources_tool())
    tools.append(make_mcp_list_prompts_tool())

    task._tools = tools
    return tools


# ---------------------------------------------------------------------------
# LLM call helper
# ---------------------------------------------------------------------------


async def call_llm(
    *,
    messages: list[dict[str, Any]],
    model_settings: dict[str, Any] | None,
    mode: str,
    tools_schema: list[dict[str, Any]] | None = None,
    timeout: float = 90.0,
) -> dict[str, Any]:
    """Call the configured chat model and return the raw response."""
    from ..domain import ModelSettings

    base = ModelSettings.model_validate(model_settings or {})
    # Resolve provider + effective settings the same way chat_service does:
    #   provider = get_provider(base.provider_name)
    #   settings = effective_model_settings(base, provider)
    #   provider = get_provider(settings.provider_name)  # re-resolve
    provider = get_provider(base.provider_name)
    settings = effective_model_settings(base, provider)
    provider = get_provider(settings.provider_name)
    if not settings.use_remote:
        raise RuntimeError("remote model is disabled")
    if not provider.compatible:
        raise RuntimeError(f"provider {provider.name} is not compatible")
    if not settings.api_key and provider.name.lower() != "ollama":
        raise RuntimeError("model api key is not configured")

    payload = build_chat_payload(settings, provider, mode, messages, stream=False)
    if tools_schema:
        payload["tools"] = tools_schema
    endpoint = f"{settings.base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
    if response.status_code < 200 or response.status_code >= 300:
        raise RuntimeError(f"LLM HTTP {response.status_code}: {response.text[:500]}")
    return response.json()


def extract_message(data: dict[str, Any]) -> dict[str, Any]:
    choices = data.get("choices") or []
    if not choices:
        return {"content": "", "tool_calls": []}
    message = choices[0].get("message", {}) or {}
    return {
        "content": str(message.get("content") or ""),
        "tool_calls": message.get("tool_calls") or [],
        "finish_reason": choices[0].get("finish_reason", ""),
    }


# ---------------------------------------------------------------------------
# Graph nodes
# ---------------------------------------------------------------------------


async def planner_node(state: AgentState, *, task: ManagedAgentTask, storage, config) -> AgentState:
    await task.wait_if_paused()
    task.budget.rounds += 1
    exhausted, reason = task.budget.exhausted()
    if exhausted:
        state["error"] = f"budget exhausted: {reason}"
        state["done"] = True
        return state
    await task.emit(
        storage=storage,
        kind="plan",
        role="planner",
        name="plan",
        status="started",
        summary=f"planning round {task.budget.rounds}",
    )
    skill_context = _build_skill_context_block(config.skill_contexts)
    messages = [
        {"role": "system", "content": ROLE_PROMPTS["planner"]},
        {"role": "system", "content": skill_context},
        {"role": "user", "content": state.get("prompt", task.prompt)},
    ]
    try:
        data = await call_llm(messages=messages, model_settings=config.model_settings, mode=config.mode)
    except Exception as error:  # noqa: BLE001
        state["error"] = f"planner LLM call failed: {error}"
        state["done"] = True
        await task.emit(storage=storage, kind="error", name="planner", summary=str(error), payload={"error": str(error)})
        return state
    message = extract_message(data)
    plan = _parse_plan(message["content"])
    state["plan"] = plan
    state["round"] = task.budget.rounds
    # Initialize task ledger
    state["current_step_index"] = 0
    state["completed_steps"] = []
    state["open_questions"] = []
    state["risks"] = [s.get("risk", "low") for s in plan if s.get("risk") in ("high", "medium")]
    state["rejected_tools"] = []
    await task.emit(
        storage=storage,
        kind="plan",
        role="planner",
        name="plan",
        status="completed",
        summary=f"plan with {len(plan)} steps",
        payload={"plan": plan, "ledger": {
            "currentStepIndex": 0,
            "completedSteps": [],
            "risks": state["risks"],
        }},
    )
    return state


async def executor_node(state: AgentState, *, task: ManagedAgentTask, storage, config) -> AgentState:
    await task.wait_if_paused()
    task.check_cancelled()
    tools = task._tools
    # Build schema and call map directly from task-scoped tools so that
    # file_writer / browser / opencode_delegate are actually available.
    tool_by_name = {t.name: t for t in tools}
    schema = [t.to_openai_schema() for t in tools]
    skill_context = _build_skill_context_block(config.skill_contexts)
    recent_results = state.get("tool_results", [])[-5:]
    # Build current step context from ledger
    plan = state.get("plan", [])
    step_idx = state.get("current_step_index", 0)
    completed = state.get("completed_steps", [])
    current_step = plan[step_idx] if step_idx < len(plan) else None
    step_context = (
        f"当前步骤 (index={step_idx}): {json.dumps(current_step, ensure_ascii=False)}\n"
        f"已完成步骤: {completed}\n"
        f"待完成步骤: {[s.get('id') for s in plan[step_idx:]]}"
        if current_step else "所有步骤已完成"
    )
    messages = [
        {"role": "system", "content": ROLE_PROMPTS["executor"]},
        {"role": "system", "content": skill_context},
        {"role": "system", "content": f"当前计划：{json.dumps(plan, ensure_ascii=False)}"},
        {"role": "system", "content": step_context},
        {"role": "system", "content": f"最近工具结果：{json.dumps(recent_results, ensure_ascii=False)[:4000]}"},
        {"role": "user", "content": state.get("prompt", task.prompt)},
    ]
    try:
        data = await call_llm(messages=messages, model_settings=config.model_settings, mode=config.mode, tools_schema=schema)
    except Exception as error:  # noqa: BLE001
        state["error"] = f"executor LLM call failed: {error}"
        state["done"] = True
        await task.emit(storage=storage, kind="error", name="executor", summary=str(error))
        return state
    message = extract_message(data)
    content = message["content"]
    tool_calls = message.get("tool_calls") or []
    # Check for task_done / step_done signals in content
    if content:
        lowered = content.lower()
        if '"task_done"' in lowered or "task_done: true" in lowered:
            state["done"] = True
            state["summary"] = _extract_summary(content)
        elif '"step_done"' in lowered or "step_done: true" in lowered:
            state["steps"].append({"done": True, "summary": _extract_summary(content)})
    if tool_calls:
        for call in tool_calls:
            await task.wait_if_paused()
            task.check_cancelled()
            task.budget.tool_calls += 1
            tool_name = call.get("function", {}).get("name", "")
            args_text = call.get("function", {}).get("arguments", "{}")
            try:
                args = json.loads(args_text) if isinstance(args_text, str) else args_text
            except json.JSONDecodeError:
                args = {"_raw": args_text}
            await task.emit(
                storage=storage,
                kind="tool",
                role="executor",
                name=tool_name,
                status="started",
                summary=f"call {tool_name}",
                payload={"arguments": args},
            )

            # --- Permission check ---
            tool = tool_by_name.get(tool_name)
            source = tool.source if tool else "builtin"
            # Look up MCP server info for permission classification
            mcp_trusted: bool | None = None
            mcp_allowed_tools: set[str] | None = None
            if source == "mcp" and tool is not None:
                server_id = tool.meta.get("serverId", "")
                server_info = task.settings.get("_mcp_server_info", {}).get(server_id, {})
                mcp_trusted = server_info.get("trusted")
                mcp_allowed_tools = server_info.get("allowedTools")
            risk = classify_tool_permission(
                tool_name,
                source=source,
                mcp_trusted=mcp_trusted,
                mcp_allowed_tools=mcp_allowed_tools,
                arguments=args,
            )
            trust_mode = bool(task.settings.get("trustMode", True))
            needs_confirmation = False
            if risk == "dangerous":
                needs_confirmation = True
            elif risk == "confirm" and not trust_mode:
                needs_confirmation = True

            if needs_confirmation:
                args_preview = json.dumps(args, ensure_ascii=False)[:500]
                perm = await agent_storage.create_permission_request(
                    storage,
                    task_id=task.task_id,
                    tool_name=tool_name,
                    arguments_preview=args_preview,
                    risk_level=risk,
                )
                perm_id = perm["id"]
                task.register_permission_request(perm_id)
                await task.emit(
                    storage=storage,
                    kind="question",
                    role="executor",
                    name="tool_permission",
                    status="pending",
                    summary=f"awaiting approval for {tool_name} ({risk})",
                    payload={
                        "permissionId": perm_id,
                        "toolName": tool_name,
                        "riskLevel": risk,
                        "argumentsPreview": args_preview,
                    },
                )
                decision = await task.wait_for_permission(perm_id)
                await agent_storage.update_permission_request_status(
                    storage, perm_id, status=decision
                )
                if decision != "approved":
                    result = {
                        "ok": False,
                        "error": f"tool {tool_name} was {decision}",
                        "summary": f"工具 {tool_name} 被{('拒绝' if decision == 'rejected' else '超时')}",
                    }
                    state["tool_results"].append({"tool": tool_name, "arguments": args, "result": result})
                    await task.emit(
                        storage=storage,
                        kind="tool",
                        role="executor",
                        name=tool_name,
                        status="skipped",
                        summary=result["summary"],
                        payload={"arguments": args, "result": result},
                    )
                    continue

            result = await _call_task_tool(tool_by_name, tool_name, args)
            state["tool_results"].append({"tool": tool_name, "arguments": args, "result": result})
            # If the tool produced an artifact, record its id
            artifact_id = ""
            if isinstance(result, dict):
                data_block = result.get("data")
                if isinstance(data_block, dict):
                    artifact_id = str(data_block.get("artifactId") or "")
            await task.emit(
                storage=storage,
                kind="tool",
                role="executor",
                name=tool_name,
                status="completed" if result.get("ok", True) else "failed",
                summary=str(result.get("summary") or result.get("error") or "tool call done"),
                payload={"arguments": args, "result": _truncate(result)},
                artifact_ids=[artifact_id] if artifact_id else [],
            )
            exhausted, reason = task.budget.exhausted()
            if exhausted:
                state["error"] = f"budget exhausted: {reason}"
                state["done"] = True
                return state
    else:
        # No tool call and no done signal -> advance to critic
        pass
    return state


async def _call_task_tool(tool_by_name: dict[str, UnifiedTool], name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call a task-scoped tool by name, falling back to the global registry."""
    tool = tool_by_name.get(name)
    if tool is None:
        # Fallback to global registry for builtin/MCP tools that weren't
        # duplicated into the task scope (they should already be there, but
        # this is a safety net).
        return await unified_tool_registry.call(name, args)
    try:
        result = await tool.handler(args)
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": str(error) or error.__class__.__name__}
    return result if isinstance(result, dict) else {"ok": True, "data": result}


async def critic_node(state: AgentState, *, task: ManagedAgentTask, storage, config) -> AgentState:
    await task.wait_if_paused()
    task.check_cancelled()
    if state.get("done"):
        return state
    skill_context = _build_skill_context_block(config.skill_contexts)
    plan = state.get("plan", [])
    step_idx = state.get("current_step_index", 0)
    current_step = plan[step_idx] if step_idx < len(plan) else None
    messages = [
        {"role": "system", "content": ROLE_PROMPTS["critic"]},
        {"role": "system", "content": skill_context},
        {"role": "user", "content": f"目标：{state.get('prompt', task.prompt)}"},
        {"role": "user", "content": f"计划：{json.dumps(plan, ensure_ascii=False)}"},
        {"role": "user", "content": f"当前步骤：{json.dumps(current_step, ensure_ascii=False) if current_step else '无'}"},
        {"role": "user", "content": f"已完成步骤：{state.get('completed_steps', [])}"},
        {"role": "user", "content": f"工具结果：{json.dumps(state.get('tool_results', [])[-8:], ensure_ascii=False)[:6000]}"},
    ]
    try:
        data = await call_llm(messages=messages, model_settings=config.model_settings, mode=config.mode)
    except Exception as error:  # noqa: BLE001
        await task.emit(storage=storage, kind="error", name="critic", summary=str(error))
        return state
    message = extract_message(data)
    critique = _parse_critique(message["content"])
    # Advance step if critic says current step is done
    if critique.get("current_step_done") and current_step:
        completed = state.get("completed_steps", [])
        step_id = current_step.get("id", step_idx)
        if step_id not in completed:
            completed.append(step_id)
        state["completed_steps"] = completed
        state["current_step_index"] = step_idx + 1
    # Track missing steps
    if critique.get("missing_steps"):
        state["open_questions"] = critique["missing_steps"]
    await task.emit(
        storage=storage,
        kind="step",
        role="critic",
        name="critic",
        status="completed",
        summary=f"satisfied={critique.get('satisfied')}, step_done={critique.get('current_step_done')}",
        payload={
            **critique,
            "ledger": {
                "currentStepIndex": state.get("current_step_index", 0),
                "completedSteps": state.get("completed_steps", []),
                "openQuestions": state.get("open_questions", []),
            },
        },
    )
    if critique.get("satisfied"):
        state["done"] = True
    elif state.get("current_step_index", 0) >= len(plan) and not critique.get("needs_more_tools"):
        state["done"] = True
    elif task.budget.rounds >= task.budget.max_rounds:
        state["done"] = True
        state["error"] = "max rounds reached"
    return state


async def summarizer_node(state: AgentState, *, task: ManagedAgentTask, storage, config) -> AgentState:
    await task.wait_if_paused()
    task.check_cancelled()
    skill_context = _build_skill_context_block(config.skill_contexts)
    # Include ledger info in summarizer context
    ledger_info = (
        f"已完成步骤：{state.get('completed_steps', [])}\n"
        f"当前步骤索引：{state.get('current_step_index', 0)}\n"
        f"未解决问题：{state.get('open_questions', [])}\n"
        f"被拒绝工具：{state.get('rejected_tools', [])}"
    )
    messages = [
        {"role": "system", "content": ROLE_PROMPTS["summarizer"]},
        {"role": "system", "content": skill_context},
        {"role": "user", "content": f"目标：{state.get('prompt', task.prompt)}"},
        {"role": "user", "content": f"计划：{json.dumps(state.get('plan', []), ensure_ascii=False)}"},
        {"role": "user", "content": f"任务账本：{ledger_info}"},
        {"role": "user", "content": f"工具结果：{json.dumps(state.get('tool_results', []), ensure_ascii=False)[:8000]}"},
        {"role": "user", "content": f"错误：{state.get('error', '')}"},
    ]
    try:
        data = await call_llm(messages=messages, model_settings=config.model_settings, mode=config.mode)
    except Exception as error:  # noqa: BLE001
        state["summary"] = f"总结生成失败：{error}"
        await task.emit(storage=storage, kind="error", name="summarizer", summary=str(error))
        return state
    message = extract_message(data)
    raw_summary = message["content"]
    # Try to parse structured JSON summary
    structured = _parse_structured_summary(raw_summary)
    if structured:
        state["summary"] = structured.get("completed", raw_summary)
        await task.emit(
            storage=storage,
            kind="done",
            role="summarizer",
            name="summary",
            status="completed",
            summary=state["summary"],
            payload=structured,
        )
    else:
        state["summary"] = raw_summary
        await task.emit(
            storage=storage,
            kind="done",
            role="summarizer",
            name="summary",
            status="completed",
            summary=state["summary"],
            payload={"summary": state["summary"]},
        )
    return state


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def route_after_executor(state: AgentState) -> Literal["critic", "summarizer", "executor"]:
    if state.get("done"):
        return "summarizer"
    return "critic"


def route_after_critic(state: AgentState) -> Literal["executor", "summarizer"]:
    if state.get("done"):
        return "summarizer"
    return "executor"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_agent_graph(*, task: ManagedAgentTask, storage, config) -> Any:
    """Build the LangGraph state graph for the task."""
    if not _HAS_LANGGRAPH:
        raise RuntimeError("langgraph is not installed; cannot build agent graph")
    graph = StateGraph(AgentState)
    graph.add_node("planner", lambda s: planner_node(s, task=task, storage=storage, config=config))
    graph.add_node("executor", lambda s: executor_node(s, task=task, storage=storage, config=config))
    graph.add_node("critic", lambda s: critic_node(s, task=task, storage=storage, config=config))
    graph.add_node("summarizer", lambda s: summarizer_node(s, task=task, storage=storage, config=config))
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "executor")
    graph.add_conditional_edges("executor", route_after_executor)
    graph.add_conditional_edges("critic", route_after_critic)
    graph.add_edge("summarizer", END)
    return graph.compile()


# ---------------------------------------------------------------------------
# Task config
# ---------------------------------------------------------------------------


class TaskConfig:
    """Bundles runtime configuration passed to graph nodes."""

    def __init__(
        self,
        *,
        model_settings: dict[str, Any],
        mode: str,
        skill_contexts: list[LoadedSkillContext],
        agent_settings: AgentSettings,
    ) -> None:
        self.model_settings = model_settings
        self.mode = mode
        self.skill_contexts = skill_contexts
        self.agent_settings = agent_settings


# ---------------------------------------------------------------------------
# Run entrypoint
# ---------------------------------------------------------------------------


async def run_agent_task(
    *,
    storage,
    task: ManagedAgentTask,
    request: AgentTaskCreateRequest,
    agent_settings: AgentSettings,
    skill_contexts: list[LoadedSkillContext],
) -> None:
    """Execute the agent task graph and persist final state."""
    from ..storage import DEFAULT_USER_ID

    # Sync builtin tools into the unified registry (idempotent)
    from .tool_registry import sync_builtin_tools
    sync_builtin_tools()

    # Assemble task-scoped tools
    await assemble_task_tools(
        storage=storage,
        task=task,
        settings=agent_settings,
        skill_contexts=skill_contexts,
    )

    config = TaskConfig(
        model_settings=request.model or {},
        mode=request.mode,
        skill_contexts=skill_contexts,
        agent_settings=agent_settings,
    )

    # Mark running
    task.status = "running"
    await agent_storage.update_agent_task_status(storage, task.task_id, status="running")
    await task.emit(
        storage=storage,
        kind="status",
        name="start",
        status="running",
        summary=f"agent task started with {len(task._tools)} tools",
        payload={"tools": [t.name for t in task._tools]},
    )

    initial_state: AgentState = {
        "prompt": task.prompt,
        "plan": [],
        "steps": [],
        "tool_results": [],
        "artifacts": [],
        "messages": [],
        "round": 0,
        "done": False,
        "summary": "",
        "error": "",
    }

    final_state: AgentState = initial_state
    try:
        if _HAS_LANGGRAPH:
            graph = build_agent_graph(task=task, storage=storage, config=config)
            final_state = await graph.ainvoke(initial_state)
        else:
            # Fallback: single-pass executor without LangGraph
            final_state = await executor_node(initial_state, task=task, storage=storage, config=config)
            if not final_state.get("done"):
                final_state = await critic_node(final_state, task=task, storage=storage, config=config)
            final_state = await summarizer_node(final_state, task=task, storage=storage, config=config)
    except asyncio.CancelledError:
        await task.emit(storage=storage, kind="status", name="cancelled", status="cancelled", summary="task cancelled")
        await agent_storage.update_agent_task_status(storage, task.task_id, status="cancelled", error="cancelled", finished=True)
        await task.cleanup()
        raise
    except Exception as error:  # noqa: BLE001
        final_state["error"] = str(error)
        await task.emit(storage=storage, kind="error", name="run", summary=str(error), payload={"error": str(error)})

    # Persist final state
    error = final_state.get("error", "")
    summary = final_state.get("summary", "")
    if error:
        task.status = "failed"
        await agent_storage.update_agent_task_status(storage, task.task_id, status="failed", error=error, finished=True, budget=task.budget.to_dict())
    else:
        task.status = "done"
        await agent_storage.update_agent_task_status(storage, task.task_id, status="done", finished=True, budget=task.budget.to_dict())
    await task.emit(
        storage=storage,
        kind="done",
        name="finish",
        status=task.status,
        summary=summary or error or "task finished",
        payload={"summary": summary, "error": error, "budget": task.budget.to_dict()},
    )
    await task.cleanup()

    # If the task has a conversation, write the summary as an assistant message
    if task.conversation_id and (summary or error):
        try:
            content = summary or f"任务失败：{error}"
            artifacts = await agent_storage.list_artifacts(storage, task.task_id)
            if artifacts:
                artifact_lines = "\n\n**产物：**\n" + "\n".join(
                    f"- [{a['name']}] (artifact:{a['id']}) — {a['description']}"
                    for a in artifacts
                )
                content += artifact_lines
            await storage.add_message(
                user_id=DEFAULT_USER_ID,
                conversation_id=task.conversation_id,  # type: ignore[arg-type]
                role="assistant",
                content=content,
            )
        except Exception as error2:  # noqa: BLE001
            _log.warning("failed to write summary to conversation: %s", error2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_skill_context_block(skill_contexts: list[LoadedSkillContext]) -> str:
    if not skill_contexts:
        return ""
    parts: list[str] = []
    for ctx in skill_contexts:
        parts.append(f"## Skill: {ctx.name}\n{ctx.description}\n\n{ctx.skill_md}")
        for ref_name, ref_content in ctx.references.items():
            parts.append(f"### Reference: {ref_name}\n{ref_content[:4000]}")
    return "\n\n---\n\n".join(parts)


def _parse_plan(content: str) -> list[dict[str, Any]]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        try:
            data = json.loads(text[start : end + 1])
            return data.get("steps") or []
        except json.JSONDecodeError:
            pass
    return [{"id": 1, "goal": content[:500], "tools": [], "expected": ""}]


def _parse_critique(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {"satisfied": False, "issues": [content[:500]], "suggestions": []}


def _parse_structured_summary(content: str) -> dict[str, Any] | None:
    """Try to parse a structured JSON summary from the summarizer output.

    Returns None if the content is not valid JSON with expected fields.
    """
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict) and ("completed" in data or "summary" in data):
                return data
        except json.JSONDecodeError:
            pass
    return None


def _extract_summary(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        try:
            data = json.loads(text[start : end + 1])
            return str(data.get("summary") or "")
        except json.JSONDecodeError:
            pass
    return text[:500]


def _truncate(value: Any, limit: int = 4000) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "..."
    if isinstance(value, dict):
        return {k: _truncate(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(v, limit) for v in value]
    return value
