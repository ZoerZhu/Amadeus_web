"""Codex-style single main loop agent runner."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..model_adapter import build_chat_payload
from ..providers import get_provider
from ..domain import ModelSettings
from .agent_loop_context import (
    AgentLoopContext,
    build_agent_loop_system_prompt,
    build_mcp_snapshot,
    build_tool_schemas,
)
from .capability_adapters import (
    CapabilityExecutionContext,
    CapabilityRegistry,
    default_capability_registry,
)
from .capabilities import CapabilityGateway
from .domain import OrchestratorSettings, OrchestratorTaskCreateRequest
from . import storage as orchestrator_storage
from .context_window import estimate_token_count, trim_context
from .model_retry import ModelRetryConfig, retry_model_call
from .shell_exec_policy import classify_shell_command
from .tool_executor import ToolCache, ToolExecutor
from .file_change_tracker import FileChangeTracker
from ..orchestrator_integrations.mcp_client import mcp_manager


class AgentLoopPermissionBlocked(RuntimeError):
    def __init__(self, capability: str, permission_id: str) -> None:
        self.capability = capability
        self.permission_id = permission_id
        super().__init__(f"{capability} requires permission")


@dataclass
class AgentLoopBudget:
    max_rounds: int = 20
    max_tool_calls: int = 80
    max_runtime_seconds: int = 1800
    rounds: int = 0
    tool_calls: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def elapsed(self) -> int:
        return int(time.monotonic() - self.started_at)

    def is_exhausted(self) -> bool:
        return (
            self.rounds >= self.max_rounds
            or self.tool_calls >= self.max_tool_calls
            or self.elapsed() >= self.max_runtime_seconds
        )

    def remaining_reason(self) -> str:
        if self.rounds >= self.max_rounds:
            return f"Reached max rounds ({self.max_rounds})."
        if self.tool_calls >= self.max_tool_calls:
            return f"Reached max tool calls ({self.max_tool_calls})."
        if self.elapsed() >= self.max_runtime_seconds:
            return f"Reached max runtime ({self.max_runtime_seconds}s)."
        return ""


class AgentLoopRunner:
    """Codex-style agent loop: model -> tool -> observe -> continue/finish."""

    def __init__(
        self,
        capability_registry: CapabilityRegistry | None = None,
    ) -> None:
        self.capability_registry = capability_registry or default_capability_registry
        self.tool_executor = ToolExecutor(runner=self, cache=ToolCache())

    async def run(
        self,
        *,
        storage,
        task_id: str,
        request: OrchestratorTaskCreateRequest,
        settings: OrchestratorSettings,
    ) -> None:
        started = time.monotonic()
        workspace_path = request.workspace_path or settings.default_workspace or "."
        trust_mode = settings.trust_mode if request.trust_mode is None else bool(request.trust_mode)
        gateway = CapabilityGateway(trust_mode=trust_mode, raw_mcp_allowed=False)

        task_model = self._resolve_task_model(request, settings)
        mode = request.mode or "fast"

        budget = AgentLoopBudget(
            max_rounds=request.max_rounds or settings.max_rounds,
            max_tool_calls=request.max_tool_calls or settings.max_tool_calls,
            max_runtime_seconds=request.max_runtime_seconds or settings.max_runtime_seconds,
        )

        async def _emit(**event: Any) -> dict[str, Any]:
            return await orchestrator_storage.append_event(storage, task_id=task_id, **event)

        context = CapabilityExecutionContext(
            task_id=task_id,
            prompt=request.prompt,
            workspace_path=workspace_path,
            settings=settings,
            metadata={"mode": mode, "model": task_model, "agentLoop": True},
            storage=storage,
            emit_event=_emit,
        )

        # Initialize file change tracker
        file_tracker = FileChangeTracker(storage, workspace_path, task_id)
        await file_tracker.initialize()
        context.metadata["file_tracker"] = file_tracker
        self._current_file_tracker = file_tracker

        try:
            # Reset the task-level tool result cache so entries never leak across tasks.
            self.tool_executor.cache = ToolCache()
            await orchestrator_storage.update_task_status(storage, task_id, status="running")
            await self._append_user_message(storage=storage, task_id=task_id, request=request)

            await _emit(
                kind="status",
                role="coordinator",
                name="agent_loop",
                status="running",
                summary="Agent loop started.",
                payload={"runtimeVersion": "agent_loop_v1", "taskModel": task_model.get("model", "")},
            )

            loop_ctx = AgentLoopContext(
                system_prompt=build_agent_loop_system_prompt(
                    workspace_path=workspace_path,
                    trust_mode=trust_mode,
                ),
                messages=[{
                    "role": "user",
                    "content": self._build_user_content(request),
                }],
                tool_schemas=build_tool_schemas(
                    enabled_capabilities=set(settings.enabled_capabilities) if settings.enabled_capabilities else None,
                    include_raw_mcp=True,
                ),
                mcp_snapshot=build_mcp_snapshot(),
            )
            loop_ctx.working_set = {
                "workspacePath": workspace_path,
                "toolCount": len(loop_ctx.tool_schemas),
                "mcpSnapshot": loop_ctx.mcp_snapshot,
                "rawMcpExposed": True,
            }

            await _emit(
                kind="working_set",
                role="coordinator",
                name="working_set",
                status="active",
                summary="Initial working set established.",
                payload={
                    "workspacePath": workspace_path,
                    "attachments": [a.model_dump(by_alias=True) for a in request.attachments],
                    "mcpSnapshot": loop_ctx.mcp_snapshot,
                    "toolCount": len(loop_ctx.tool_schemas),
                },
            )

            completed = await self._run_loop(
                storage=storage,
                task_id=task_id,
                loop_ctx=loop_ctx,
                budget=budget,
                gateway=gateway,
                context=context,
                task_model=task_model,
                mode=mode,
                emit=_emit,
            )

            if completed:
                await self._finish_task(
                    storage=storage,
                    task_id=task_id,
                    started=started,
                    budget=budget,
                    emit=_emit,
                )

        except AgentLoopPermissionBlocked as blocked:
            await _emit(
                kind="status",
                role="coordinator",
                name="agent_loop",
                status="paused",
                summary=f"任务已暂停，等待批准 {blocked.capability}。",
                payload={"permissionId": blocked.permission_id, "capability": blocked.capability},
            )
            await orchestrator_storage.update_task_status(storage, task_id, status="paused")
        except Exception as error:  # noqa: BLE001
            self._current_file_tracker = None
            await _emit(
                kind="error",
                role="coordinator",
                name="agent_loop",
                status="failed",
                summary=str(error) or error.__class__.__name__,
                payload={},
            )
            await orchestrator_storage.update_task_status(
                storage, task_id, status="failed", error=str(error), finished=True
            )

    async def resume_from_permission(self, *, storage, permission_id: str) -> None:
        """Resume the agent loop after a permission is approved."""
        permission = await orchestrator_storage.get_permission_request(storage, permission_id)
        if permission is None:
            raise RuntimeError(f"permission request not found: {permission_id}")
        if permission.get("status") != "approved":
            raise RuntimeError("permission is not approved")

        task = await orchestrator_storage.get_task(storage, permission["taskId"])
        if task is None:
            raise RuntimeError(f"task not found: {permission['taskId']}")
        if task.get("status") in {"done", "failed", "cancelled", "rolled_back"}:
            return

        started = time.monotonic()
        settings = OrchestratorSettings.model_validate(task.get("settings") or {})
        workspace_path = str(task.get("workspacePath") or settings.default_workspace or ".")
        gateway = CapabilityGateway(trust_mode=settings.trust_mode)

        task_model = self._resolve_task_model_from_task(task, settings)
        mode = str((task.get("context") or {}).get("mode") or "fast")
        budget = AgentLoopBudget(
            max_rounds=settings.max_rounds,
            max_tool_calls=settings.max_tool_calls,
            max_runtime_seconds=settings.max_runtime_seconds,
        )

        async def _emit(**event: Any) -> dict[str, Any]:
            return await orchestrator_storage.append_event(storage, task_id=permission["taskId"], **event)

        context = CapabilityExecutionContext(
            task_id=permission["taskId"],
            prompt=str(task.get("prompt") or ""),
            workspace_path=workspace_path,
            settings=settings,
            metadata=task.get("context") if isinstance(task.get("context"), dict) else {},
            storage=storage,
            emit_event=_emit,
        )

        events = await orchestrator_storage.list_events(storage, permission["taskId"])
        loop_ctx = self._rebuild_context_from_events(
            events=events,
            settings=settings,
            workspace_path=workspace_path,
        )

        # If this is an ask_user question with an answer, inject it as a tool result
        perm_payload = permission.get("payload") or {}
        if perm_payload.get("questionType") == "ask_user" and "answer" in perm_payload:
            # Find the last tool_call event for ask_user to get its tool_call_id
            tool_call_id = ""
            tool_args: dict[str, Any] = {}
            for event in reversed(events):
                if event.get("kind") == "tool_call" and event.get("name") == "ask_user":
                    payload = event.get("payload") or {}
                    tool_call_id = str(payload.get("toolCallId") or "")
                    tool_args = payload.get("arguments") or {}
                    break
            if tool_call_id:
                # Inject the assistant message with the tool_call
                loop_ctx.messages.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": "ask_user",
                            "arguments": json.dumps(tool_args, ensure_ascii=False),
                        },
                    }],
                })
                # Inject the tool result with the user's answer
                loop_ctx.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": str(perm_payload["answer"]),
                })

        try:
            await orchestrator_storage.update_task_status(storage, permission["taskId"], status="running")
            await _emit(
                kind="status",
                role="coordinator",
                name="agent_loop",
                status="running",
                summary=f"Resuming task after approving {permission['toolName']}.",
                payload={"permissionId": permission_id},
            )
            completed = await self._run_loop(
                storage=storage,
                task_id=permission["taskId"],
                loop_ctx=loop_ctx,
                budget=budget,
                gateway=gateway,
                context=context,
                task_model=task_model,
                mode=mode,
                emit=_emit,
                approved_permission=permission,
            )
            if completed:
                await self._finish_task(
                    storage=storage,
                    task_id=permission["taskId"],
                    started=started,
                    budget=budget,
                    emit=_emit,
                )
        except AgentLoopPermissionBlocked as blocked:
            await _emit(
                kind="status",
                role="coordinator",
                name="agent_loop",
                status="paused",
                summary=f"任务已再次暂停，等待批准 {blocked.capability}。",
                payload={"permissionId": blocked.permission_id, "capability": blocked.capability},
            )
            await orchestrator_storage.update_task_status(storage, permission["taskId"], status="paused")
        except Exception as error:  # noqa: BLE001
            await _emit(
                kind="error",
                role="coordinator",
                name="agent_loop",
                status="failed",
                summary=str(error) or error.__class__.__name__,
                payload={"permissionId": permission_id},
            )
            await orchestrator_storage.update_task_status(
                storage, permission["taskId"], status="failed", error=str(error), finished=True
            )

    async def rollback_task(self, *, storage, task_id: str) -> dict[str, Any]:
        """Rollback a task's file changes to pre-task state."""
        task = await orchestrator_storage.get_task(storage, task_id)
        if task is None:
            return {"ok": False, "reason": "task not found"}
        if task.get("status") not in ("done", "failed"):
            return {"ok": False, "reason": "can only rollback completed or failed tasks"}

        settings = OrchestratorSettings.model_validate(task.get("settings") or {})
        workspace_path = str(task.get("workspacePath") or settings.default_workspace or ".")
        tracker = FileChangeTracker(storage, workspace_path, task_id)
        await tracker.initialize()

        result = await tracker.rollback()

        await orchestrator_storage.update_task_status(
            storage, task_id, status="rolled_back", finished=True
        )
        await orchestrator_storage.append_event(
            storage,
            task_id=task_id,
            kind="status",
            role="coordinator",
            name="rollback",
            status="rolled_back",
            summary=f"Task rolled back: {result.get('method', 'unknown')}",
            payload=result,
        )
        return result

    async def _run_loop(
        self,
        *,
        storage,
        task_id: str,
        loop_ctx: AgentLoopContext,
        budget: AgentLoopBudget,
        gateway: CapabilityGateway,
        context: CapabilityExecutionContext,
        task_model: dict[str, Any],
        mode: str,
        emit,
        approved_permission: dict[str, Any] | None = None,
    ) -> bool:
        """The main agent loop."""
        while not budget.is_exhausted():
            task = await orchestrator_storage.get_task(storage, task_id)
            if task is None or task.get("status") in {"paused", "cancelled", "rolled_back"}:
                return False

            budget.rounds += 1

            # Trim context to fit within token budget before calling the model
            max_context_tokens = int(task_model.get("maxContextTokens") or 120000)
            loop_ctx.messages = trim_context(
                loop_ctx.messages,
                max_tokens=max_context_tokens,
                system_prompt=loop_ctx.system_prompt,
            )

            turn = await self._call_model_with_retry(
                loop_ctx=loop_ctx,
                task_model=task_model,
                fallback_model=context.settings.fallback_model if hasattr(context, 'settings') and context.settings else None,
                mode=mode,
                emit=emit,
            )

            # Re-check cancellation after model call (model call may take time)
            task = await orchestrator_storage.get_task(storage, task_id)
            if task is None or task.get("status") in {"paused", "cancelled", "rolled_back"}:
                return False

            if turn is None:
                await emit(
                    kind="error",
                    role="coordinator",
                    name="model_call",
                    status="error",
                    summary="Model call returned no result.",
                    payload={"round": budget.rounds},
                )
                break

            if turn.get("content"):
                await emit(
                    kind="agent_thought_summary",
                    role="coordinator",
                    name="plan",
                    status="thinking",
                    summary=str(turn["content"])[:500],
                    payload={"round": budget.rounds, "preview": str(turn["content"])[:1000]},
                )
                loop_ctx.messages.append({
                    "role": "assistant",
                    "content": turn["content"],
                    "tool_calls": turn.get("tool_calls_raw", []),
                })

            tool_calls = turn.get("tool_calls", [])
            if not tool_calls:
                break

            # Separate finish from other tools (finish runs after others)
            finish_tc = next((tc for tc in tool_calls if tc["name"] == "finish"), None)
            non_finish_calls = [tc for tc in tool_calls if tc["name"] != "finish"]

            budget.tool_calls += len(tool_calls)
            if budget.is_exhausted():
                break

            # Batch-execute non-finish tools (reads parallel, writes serial, cache integrated)
            if non_finish_calls:
                await self.tool_executor.execute_batch(
                    tool_calls=non_finish_calls,
                    gateway=gateway,
                    context=context,
                    loop_ctx=loop_ctx,
                    emit=emit,
                    approved_permission=approved_permission,
                    storage=storage,
                    task_id=task_id,
                )

            # finish runs after other tools
            if finish_tc:
                tool_args = finish_tc.get("arguments", {})
                await emit(
                    kind="message",
                    role="assistant",
                    name="final_answer",
                    status="done",
                    summary=str(tool_args.get("summary") or "Task complete.")[:200],
                    payload=tool_args,
                )
                return True

        reason = budget.remaining_reason()
        await emit(
            kind="status",
            role="coordinator",
            name="agent_loop",
            status="done",
            summary=f"Agent loop ended: {reason}",
            payload={
                "rounds": budget.rounds,
                "toolCalls": budget.tool_calls,
                "elapsedSeconds": budget.elapsed(),
                "reason": reason,
            },
        )
        return True

    async def _call_model(
        self,
        *,
        loop_ctx: AgentLoopContext,
        task_model: dict[str, Any],
        mode: str,
        emit,
    ) -> dict[str, Any] | None:
        """Call the task model with streaming and return the turn result."""
        provider_name = task_model.get("providerName") or "OpenAI"
        provider = get_provider(provider_name)
        model_settings = ModelSettings(
            providerName=provider_name,
            baseUrl=task_model.get("baseUrl") or provider.base_url,
            model=task_model.get("model") or provider.default_model,
            apiKey=task_model.get("apiKey") or "",
            useRemote=bool(task_model.get("useRemote", True)),
        )

        endpoint = f"{model_settings.base_url.rstrip('/')}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if model_settings.api_key:
            headers["Authorization"] = f"Bearer {model_settings.api_key}"

        messages = [{"role": "system", "content": loop_ctx.system_prompt}] + loop_ctx.messages

        payload = build_chat_payload(
            model_settings,
            provider,
            mode,
            messages,
            tools=loop_ctx.tool_schemas if loop_ctx.tool_schemas else None,
            stream=True,
        )

        content_parts: list[str] = []
        tool_calls_map: dict[int, dict[str, Any]] = {}
        finish_reason = ""
        last_emit_time = 0.0
        emit_interval = 0.5  # Emit at most every 500ms

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                async with client.stream("POST", endpoint, headers=headers, json=payload) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise httpx.HTTPStatusError(
                            f"HTTP {response.status_code}",
                            request=response.request,
                            response=response,
                        )

                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        choice = (chunk.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}

                        # Accumulate content
                        if delta.get("content"):
                            content_parts.append(delta["content"])
                            now = time.monotonic()
                            if now - last_emit_time >= emit_interval:
                                last_emit_time = now
                                partial = "".join(content_parts)
                                if emit is not None:
                                    try:
                                        await emit(
                                            kind="agent_thought_summary",
                                            role="coordinator",
                                            name="plan",
                                            status="streaming",
                                            summary=partial[:500],
                                            payload={
                                                "round": loop_ctx.rounds + 1,
                                                "chunk": delta["content"],
                                                "streaming": True,
                                            },
                                        )
                                    except Exception:  # noqa: BLE001
                                        pass

                        # Accumulate tool calls
                        if delta.get("tool_calls"):
                            for tc in delta["tool_calls"]:
                                idx = int(tc.get("index") or 0)
                                if idx not in tool_calls_map:
                                    tool_calls_map[idx] = {
                                        "id": str(tc.get("id") or ""),
                                        "function": {"name": "", "arguments": ""},
                                    }
                                func = tc.get("function") or {}
                                if func.get("name"):
                                    tool_calls_map[idx]["function"]["name"] += func["name"]
                                if func.get("arguments"):
                                    tool_calls_map[idx]["function"]["arguments"] += func["arguments"]

                        if choice.get("finish_reason"):
                            finish_reason = str(choice["finish_reason"])

        except httpx.HTTPStatusError:
            raise  # Let retry_model_call classify and retry
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as net_err:
            raise net_err  # Retryable — let retry_model_call handle backoff
        except Exception:  # noqa: BLE001
            # Non-retryable unexpected errors (e.g. JSON parse of response body)
            # Return None to signal failure without raising, so retry_model_call
            # treats it as a non-retryable None result
            return None

        content = "".join(content_parts)
        raw_tool_calls = []
        for idx in sorted(tool_calls_map.keys()):
            tc = tool_calls_map[idx]
            raw_tool_calls.append({
                "id": tc["id"],
                "type": "function",
                "function": tc["function"],
            })

        tool_calls: list[dict[str, Any]] = []
        for tc in raw_tool_calls:
            func = tc.get("function") or {}
            name = str(func.get("name") or "")
            raw_args = str(func.get("arguments") or "{}")
            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                args = {"_raw": raw_args}
            tool_calls.append({
                "id": str(tc.get("id") or ""),
                "name": name,
                "arguments": args,
            })

        return {
            "content": content,
            "tool_calls": tool_calls,
            "tool_calls_raw": raw_tool_calls,
            "finish_reason": finish_reason,
        }

    async def _call_model_with_retry(
        self,
        *,
        loop_ctx: AgentLoopContext,
        task_model: dict[str, Any],
        fallback_model: dict[str, Any] | None,
        mode: str,
        emit,
    ) -> dict[str, Any] | None:
        """Call the model with retry and fallback support."""
        retry_config = ModelRetryConfig(
            max_retries=int(task_model.get("maxRetries") or 3),
            base_delay=float(task_model.get("retryBaseDelay") or 1.0),
            max_delay=float(task_model.get("retryMaxDelay") or 30.0),
        )

        async def primary_factory():
            return await self._call_model(
                loop_ctx=loop_ctx, task_model=task_model, mode=mode, emit=emit,
            )

        result = await retry_model_call(primary_factory, retry_config)
        if result is not None:
            return result

        # Primary model failed after retries — try fallback if configured
        if fallback_model and fallback_model.get("model"):
            await emit(
                kind="status",
                role="coordinator",
                name="model_fallback",
                status="running",
                summary=f"Primary model failed, switching to fallback: {fallback_model.get('model')}",
                payload={"primaryModel": task_model.get("model", ""), "fallbackModel": fallback_model.get("model", "")},
            )
            fallback_config = ModelRetryConfig(
                max_retries=int(fallback_model.get("maxRetries") or 2),
                base_delay=float(fallback_model.get("retryBaseDelay") or 1.0),
                max_delay=float(fallback_model.get("retryMaxDelay") or 30.0),
            )

            async def fallback_factory():
                return await self._call_model(
                    loop_ctx=loop_ctx, task_model=fallback_model, mode=mode, emit=emit,
                )

            result = await retry_model_call(fallback_factory, fallback_config)
            if result is not None:
                return result

        return None

    async def _execute_tool_call(
        self,
        *,
        storage,
        task_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_call_id: str,
        gateway: CapabilityGateway,
        context: CapabilityExecutionContext,
        loop_ctx: AgentLoopContext,
        emit,
        approved_permission: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute a single tool call within the loop."""
        await emit(
            kind="tool_call",
            role="worker",
            name=tool_name,
            status="started",
            summary=f"Calling {tool_name}.",
            payload={
                "toolCallId": tool_call_id,
                "arguments": tool_args,
                "round": loop_ctx.rounds + 1,
            },
        )

        if tool_name.startswith("mcp__"):
            result = await self._execute_raw_mcp(
                storage=storage,
                task_id=task_id,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_call_id=tool_call_id,
                gateway=gateway,
                context=context,
                loop_ctx=loop_ctx,
                emit=emit,
                approved_permission=approved_permission,
            )
            return result

        if tool_name == "shell_exec":
            command = str(tool_args.get("command") or "")
            risk = classify_shell_command(command)
            trust_mode = bool(context.settings.trust_mode)
            if risk.auto_approved and trust_mode and risk.risk == "safe":
                result = await self.capability_registry.execute("shell_exec", tool_args, context)
                await self._emit_tool_result(
                    emit=emit, tool_name=tool_name, tool_call_id=tool_call_id,
                    result=result, loop_ctx=loop_ctx,
                )
                return result

        decision = gateway.decide(tool_name)
        is_approved = (
            approved_permission is not None
            and approved_permission.get("toolName") == tool_name
        )

        if not decision.allowed and not is_approved:
            arguments_preview = _json_preview(tool_args)
            permission = await orchestrator_storage.create_permission_request(
                storage,
                task_id=task_id,
                tool_name=tool_name,
                arguments_preview=arguments_preview,
                risk_level=decision.risk,
                payload={
                    "toolName": tool_name,
                    "toolArguments": tool_args,
                    "toolArgumentsPreview": arguments_preview,
                    "riskLevel": decision.risk,
                    "autoApproved": False,
                    "reason": decision.reason,
                    "commandPreview": str(tool_args.get("command") or "")[:200] if tool_name == "shell_exec" else "",
                    "workspacePath": context.workspace_path,
                    "round": loop_ctx.rounds + 1,
                },
            )
            await emit(
                kind="question",
                role="coordinator",
                name="tool_permission",
                status="blocked",
                summary=f"{tool_name} requires permission before execution.",
                payload={
                    "permissionId": permission["id"],
                    "toolName": tool_name,
                    "risk": decision.risk,
                    "reason": decision.reason,
                    "argumentsPreview": arguments_preview,
                    "commandPreview": str(tool_args.get("command") or "")[:200] if tool_name == "shell_exec" else "",
                    "workspacePath": context.workspace_path,
                },
            )
            raise AgentLoopPermissionBlocked(tool_name, permission["id"])

        result = await self.capability_registry.execute(tool_name, tool_args, context)
        await self._emit_tool_result(
            emit=emit, tool_name=tool_name, tool_call_id=tool_call_id,
            result=result, loop_ctx=loop_ctx,
        )
        return result

    async def _execute_raw_mcp(
        self,
        *,
        storage,
        task_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
        tool_call_id: str,
        gateway: CapabilityGateway,
        context: CapabilityExecutionContext,
        loop_ctx: AgentLoopContext,
        emit,
        approved_permission: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Execute a raw MCP tool call (always requires confirmation)."""
        is_approved = (
            approved_permission is not None
            and approved_permission.get("toolName") == tool_name
        )
        if not is_approved:
            arguments_preview = _json_preview(tool_args)
            permission = await orchestrator_storage.create_permission_request(
                storage,
                task_id=task_id,
                tool_name=tool_name,
                arguments_preview=arguments_preview,
                risk_level="confirm",
                payload={
                    "toolName": tool_name,
                    "toolArguments": tool_args,
                    "toolArgumentsPreview": arguments_preview,
                    "riskLevel": "confirm",
                    "autoApproved": False,
                    "reason": "Raw MCP tool requires confirmation.",
                    "commandPreview": "",
                    "workspacePath": context.workspace_path,
                    "round": loop_ctx.rounds + 1,
                },
            )
            await emit(
                kind="question",
                role="coordinator",
                name="mcp_permission",
                status="blocked",
                summary=f"MCP tool {tool_name} requires confirmation.",
                payload={
                    "permissionId": permission["id"],
                    "toolName": tool_name,
                    "argumentsPreview": arguments_preview,
                    "workspacePath": context.workspace_path,
                },
            )
            raise AgentLoopPermissionBlocked(tool_name, permission["id"])

        parts = tool_name.split("__", 2)
        if len(parts) != 3:
            result = {"ok": False, "summary": f"Invalid MCP tool name: {tool_name}", "data": {}}
        else:
            server_id, mcp_tool_name = parts[1], parts[2]
            try:
                client = None
                for c in mcp_manager.list_clients():
                    sid = str(
                        getattr(c, "server_id", "")
                        or getattr(getattr(c, "config", None), "id", "")
                        or ""
                    )
                    if sid == server_id:
                        client = c
                        break
                if client is None:
                    result = {"ok": False, "summary": f"MCP server not found: {server_id}", "data": {}}
                else:
                    raw_result = await client.call_tool(mcp_tool_name, tool_args)
                    result = {
                        "ok": True,
                        "summary": f"MCP {server_id}/{mcp_tool_name} executed.",
                        "data": {"result": raw_result, "serverId": server_id, "toolName": mcp_tool_name},
                    }
            except Exception as error:  # noqa: BLE001
                result = {"ok": False, "summary": str(error), "data": {"serverId": server_id, "toolName": mcp_tool_name}}

        await self._emit_tool_result(
            emit=emit, tool_name=tool_name, tool_call_id=tool_call_id,
            result=result, loop_ctx=loop_ctx,
        )
        return result

    async def _emit_tool_result(
        self,
        *,
        emit,
        tool_name: str,
        tool_call_id: str,
        result: dict[str, Any],
        loop_ctx: AgentLoopContext,
    ) -> None:
        """Emit tool_result event. Message append is handled by ToolExecutor."""
        ok = bool(result.get("ok", True))
        summary = str(result.get("summary") or "")
        data = result.get("data", {})

        await emit(
            kind="tool_result",
            role="worker",
            name=tool_name,
            status="done" if ok else "error",
            summary=summary[:500],
            payload={
                "toolCallId": tool_call_id,
                "ok": ok,
                "data": _truncate_payload(data),
                "artifactIds": _string_list(result.get("artifactIds")),
                "fromCache": bool(result.get("from_cache", False)),
            },
            artifact_ids=_string_list(result.get("artifactIds")),
        )

    def _append_tool_message(
        self,
        loop_ctx: AgentLoopContext,
        tool_call_id: str,
        tool_name: str,
        result: dict[str, Any],
    ) -> None:
        """Append tool observation to loop_ctx.messages in order."""
        ok = bool(result.get("ok", True))
        summary = str(result.get("summary") or "")
        data = result.get("data", {})
        observation = self._compress_observation(tool_name, summary, data, ok)
        loop_ctx.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": observation,
        })

    def _compress_observation(
        self, tool_name: str, summary: str, data: dict[str, Any], ok: bool
    ) -> str:
        """Compress tool result into a concise observation for the model."""
        if not ok:
            return f"Tool {tool_name} failed: {summary}"
        parts = [f"Tool {tool_name}: {summary}"]
        for key in ("stdout", "content", "markdown", "results", "query"):
            value = data.get(key)
            if value is None:
                continue
            text = str(value)
            if len(text) > 3000:
                text = text[:3000] + "...[truncated]"
            parts.append(f"{key}: {text}")
        return "\n".join(parts)[:6000]

    def _resolve_task_model(
        self, request: OrchestratorTaskCreateRequest, settings: OrchestratorSettings
    ) -> dict[str, Any]:
        if request.task_model_override:
            return request.task_model_override
        if settings.task_model:
            return settings.task_model
        return request.model or {}

    def _resolve_task_model_from_task(
        self, task: dict[str, Any], settings: OrchestratorSettings
    ) -> dict[str, Any]:
        ctx = task.get("context") if isinstance(task.get("context"), dict) else {}
        return ctx.get("model") or settings.task_model or {}

    def _build_user_content(self, request: OrchestratorTaskCreateRequest) -> str:
        lines = [request.prompt]
        for attachment in request.attachments:
            name = attachment.file_name or attachment.file_type or "attachment"
            lines.append(f"\n[Attachment: {name}]")
        return "\n".join(lines)

    def _rebuild_context_from_events(
        self,
        *,
        events: list[dict[str, Any]],
        settings: OrchestratorSettings,
        workspace_path: str,
    ) -> AgentLoopContext:
        loop_ctx = AgentLoopContext(
            system_prompt=build_agent_loop_system_prompt(
                workspace_path=workspace_path,
                trust_mode=settings.trust_mode,
            ),
            tool_schemas=build_tool_schemas(
                enabled_capabilities=set(settings.enabled_capabilities) if settings.enabled_capabilities else None,
                include_raw_mcp=True,
            ),
            mcp_snapshot=build_mcp_snapshot(),
        )
        for event in events:
            kind = event.get("kind", "")
            if kind == "message" and event.get("role") == "user":
                payload = event.get("payload") or {}
                loop_ctx.messages.append({
                    "role": "user",
                    "content": str(payload.get("text") or event.get("summary") or ""),
                })
            elif kind == "agent_thought_summary":
                payload = event.get("payload") or {}
                loop_ctx.messages.append({
                    "role": "assistant",
                    "content": str(payload.get("preview") or event.get("summary") or ""),
                })
            elif kind == "tool_result":
                payload = event.get("payload") or {}
                loop_ctx.messages.append({
                    "role": "tool",
                    "tool_call_id": str(payload.get("toolCallId") or ""),
                    "name": event.get("name", ""),
                    "content": str(event.get("summary") or ""),
                })
        return loop_ctx

    async def _append_user_message(
        self, *, storage, task_id: str, request: OrchestratorTaskCreateRequest
    ) -> None:
        attachments = [item.model_dump(by_alias=True) for item in request.attachments]
        await orchestrator_storage.append_event(
            storage,
            task_id=task_id,
            kind="message",
            role="user",
            name="user_request",
            status="done",
            summary=request.prompt[:160],
            payload={"text": request.prompt, "attachments": attachments, "mode": request.mode},
        )

    async def _finish_task(
        self, *, storage, task_id: str, started: float, budget: AgentLoopBudget, emit
    ) -> None:
        elapsed = int(time.monotonic() - started)
        final_answer = await self._build_final_answer(
            storage=storage, task_id=task_id, elapsed=elapsed, budget=budget
        )
        await emit(
            kind="message",
            role="assistant",
            name="final_answer",
            status="done",
            summary=final_answer["summary"],
            payload=final_answer,
        )
        # Collect git diff if workspace is a git repo
        if hasattr(self, "_current_file_tracker") and self._current_file_tracker:
            file_tracker = self._current_file_tracker
            if file_tracker.is_git_repo:
                try:
                    changes = await file_tracker.collect_git_diff()
                    if changes:
                        await emit(
                            kind="artifact",
                            role="coder",
                            name="file_changes",
                            status="done",
                            summary=f"Tracked {len(changes)} file change(s).",
                            payload={"changeCount": len(changes)},
                        )
                except Exception:  # noqa: BLE001
                    pass
            self._current_file_tracker = None
        await emit(
            kind="done",
            role="summarizer",
            name="summary",
            status="done",
            summary="任务已由 agent loop 完成编排和能力执行。",
            payload={
                "elapsedSeconds": elapsed,
                "rounds": budget.rounds,
                "toolCalls": budget.tool_calls,
                "cacheStats": self.tool_executor.cache.stats if self.tool_executor else {"hits": 0, "misses": 0},
            },
        )
        await orchestrator_storage.update_task_status(
            storage, task_id, status="done", finished=True,
            budget={
                "elapsedSeconds": elapsed,
                "rounds": budget.rounds,
                "toolCalls": budget.tool_calls,
            },
        )

    async def _build_final_answer(
        self, *, storage, task_id: str, elapsed: int, budget: AgentLoopBudget
    ) -> dict[str, Any]:
        events = await orchestrator_storage.list_events(storage, task_id)
        artifacts = await orchestrator_storage.list_artifacts(storage, task_id)

        finish_event = None
        for event in reversed(events):
            if event.get("name") == "final_answer" and event.get("kind") == "message":
                finish_event = event
                break

        if finish_event and isinstance(finish_event.get("payload"), dict):
            payload = finish_event["payload"]
            return {
                "text": str(payload.get("summary") or payload.get("text") or "任务已完成。"),
                "summary": str(payload.get("summary") or "任务已完成。"),
                "completedItems": payload.get("completedItems") or [],
                "keyFindings": payload.get("keyFindings") or [],
                "artifacts": payload.get("artifacts") or [],
                "verification": payload.get("verification") or [],
                "remainingRisks": payload.get("remainingRisks") or [],
                "elapsedSeconds": elapsed,
            }

        completed_tools = [
            e for e in events
            if e.get("kind") in {"tool_result", "tool", "command"} and e.get("status") in {"done", "completed"}
        ]
        errors = [e for e in events if e.get("kind") == "error" or e.get("status") == "error"]
        completed_items = [
            str(e.get("summary") or e.get("name") or "完成一项能力调用").strip()
            for e in completed_tools[-8:]
            if str(e.get("summary") or e.get("name") or "").strip()
        ]
        artifact_items = [
            {"id": a.get("id", ""), "name": a.get("name", ""), "kind": a.get("kind", "")}
            for a in artifacts[-6:]
        ]
        risk_items = [
            str(e.get("summary") or e.get("name") or "存在未处理风险").strip()
            for e in errors[-4:]
            if str(e.get("summary") or e.get("name") or "").strip()
        ]
        if not completed_items:
            completed_items = ["已完成任务编排和可用能力执行。"]
        summary = "任务已完成。" if not risk_items else "任务执行结束，但存在需要关注的问题。"
        text = "\n".join([
            summary, "",
            "完成事项：", *[f"- {item}" for item in completed_items], "",
            "产物：", *[f"- {a['name'] or a['id']}" for a in artifact_items]] +
            (["- 本轮没有生成可下载产物。"] if not artifact_items else []) +
            ["", "验证：", f"- Agent loop 运行 {budget.rounds} 轮，{budget.tool_calls} 次工具调用，耗时约 {elapsed}s。", "",
             "剩余风险："] + [f"- {item}" for item in risk_items] or ["- 暂无明显剩余风险。"]
        )
        return {
            "text": text,
            "summary": summary,
            "completedItems": completed_items,
            "artifacts": artifact_items,
            "verification": [f"Agent loop 运行 {budget.rounds} 轮，{budget.tool_calls} 次工具调用，耗时约 {elapsed}s。"],
            "remainingRisks": risk_items,
            "elapsedSeconds": elapsed,
        }


def _truncate_payload(value: Any, limit: int = 4000) -> Any:
    text = repr(value)
    if len(text) <= limit:
        return value
    return {"truncated": True, "preview": text[:limit]}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _json_preview(value: Any, limit: int = 2000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = repr(value)
    return text[:limit]


default_agent_loop_runner = AgentLoopRunner()
