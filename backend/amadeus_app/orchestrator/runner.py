from __future__ import annotations

import json
import time
from typing import Any

from ..storage import SQLiteStorage
from . import storage as orchestrator_storage
from .capability_adapters import (
    CapabilityExecutionContext,
    CapabilityRegistry,
    default_capability_registry,
)
from .capabilities import CapabilityGateway
from .domain import OrchestratorSettings, OrchestratorTaskCreateRequest
from .planner import OrchestratorPlanner, default_orchestrator_planner


class PermissionBlocked(RuntimeError):
    def __init__(self, capability: str, permission_id: str) -> None:
        self.capability = capability
        self.permission_id = permission_id
        super().__init__(f"{capability} requires permission")


class OrchestratorRunner:
    """Deterministic v2 coordinator scaffold.

    This runner establishes the new task ledger, worker planning, and
    capability execution boundary. High-risk capabilities remain governed by
    the gateway and can be blocked before adapters run.
    """

    def __init__(
        self,
        capability_registry: CapabilityRegistry | None = None,
        planner: OrchestratorPlanner | None = None,
    ) -> None:
        self.capability_registry = capability_registry or default_capability_registry
        self.planner = planner or default_orchestrator_planner

    async def run(
        self,
        *,
        storage: SQLiteStorage,
        task_id: str,
        request: OrchestratorTaskCreateRequest,
        settings: OrchestratorSettings,
    ) -> None:
        started = time.monotonic()
        gateway = CapabilityGateway(trust_mode=settings.trust_mode if request.trust_mode is None else bool(request.trust_mode))
        async def _emit_capability_event(**event: Any) -> dict[str, Any]:
            return await orchestrator_storage.append_event(storage, task_id=task_id, **event)

        context = self._build_context(
            task_id=task_id,
            prompt=request.prompt,
            workspace_path=request.workspace_path or settings.default_workspace or ".",
            settings=settings,
            metadata={"mode": request.mode, "model": request.model},
            storage=storage,
            emit_event=_emit_capability_event,
        )
        try:
            await orchestrator_storage.update_task_status(storage, task_id, status="running")
            await self._append_user_message(storage=storage, task_id=task_id, request=request)
            await orchestrator_storage.append_event(
                storage,
                task_id=task_id,
                kind="status",
                role="coordinator",
                name="orchestrator",
                status="running",
                summary="Orchestrator v2 task started.",
                payload={"runtimeVersion": "orchestrator_v2"},
            )
            plan_result = self.planner.build_plan(request.prompt, settings=settings)
            plan = plan_result.steps
            await orchestrator_storage.append_event(
                storage,
                task_id=task_id,
                kind="plan",
                role="coordinator",
                name="planner",
                status="done",
                summary="Created orchestrator execution plan.",
                payload={"steps": plan, "planner": plan_result.context},
            )
            completed = await self._run_steps(
                storage=storage,
                task_id=task_id,
                plan=plan,
                start_index=0,
                gateway=gateway,
                context=context,
            )
            if completed:
                await self._finish_task(storage=storage, task_id=task_id, started=started, rounds=len(plan))
        except PermissionBlocked as blocked:
            await orchestrator_storage.append_event(
                storage,
                task_id=task_id,
                kind="status",
                role="coordinator",
                name="orchestrator",
                status="paused",
                summary=f"任务已暂停，等待批准 {blocked.capability}。",
                payload={"permissionId": blocked.permission_id, "capability": blocked.capability},
            )
            await orchestrator_storage.update_task_status(storage, task_id, status="paused")
        except Exception as error:  # noqa: BLE001
            await orchestrator_storage.append_event(
                storage,
                task_id=task_id,
                kind="error",
                role="coordinator",
                name="orchestrator",
                status="failed",
                summary=str(error) or error.__class__.__name__,
                payload={},
            )
            await orchestrator_storage.update_task_status(storage, task_id, status="failed", error=str(error), finished=True)

    async def resume_from_permission(self, *, storage: SQLiteStorage, permission_id: str) -> None:
        permission = await orchestrator_storage.get_permission_request(storage, permission_id)
        if permission is None:
            raise RuntimeError(f"permission request not found: {permission_id}")
        if permission.get("status") != "approved":
            raise RuntimeError(f"permission is not approved: {permission.get('status')}")
        task = await orchestrator_storage.get_task(storage, permission["taskId"])
        if task is None:
            raise RuntimeError(f"task not found: {permission['taskId']}")
        if task.get("status") in {"done", "failed", "cancelled", "rolled_back"}:
            return

        started = time.monotonic()
        payload = permission.get("payload") if isinstance(permission.get("payload"), dict) else {}
        plan = payload.get("plan")
        if not isinstance(plan, list):
            plan = self.planner.build_plan(
                str(task.get("prompt") or ""),
                settings=OrchestratorSettings.model_validate(task.get("settings") or {}),
            ).steps
        try:
            start_index = int(payload.get("stepIndex", 0) or 0)
        except (TypeError, ValueError):
            start_index = 0
        settings = OrchestratorSettings.model_validate(task.get("settings") or {})
        gateway = CapabilityGateway(trust_mode=settings.trust_mode)

        async def _emit_capability_event(**event: Any) -> dict[str, Any]:
            return await orchestrator_storage.append_event(storage, task_id=permission["taskId"], **event)

        context = self._build_context(
            task_id=permission["taskId"],
            prompt=str(task.get("prompt") or ""),
            workspace_path=str(task.get("workspacePath") or settings.default_workspace or "."),
            settings=settings,
            metadata=task.get("context") if isinstance(task.get("context"), dict) else {},
            storage=storage,
            emit_event=_emit_capability_event,
        )
        try:
            await orchestrator_storage.update_task_status(storage, permission["taskId"], status="running")
            await orchestrator_storage.append_event(
                storage,
                task_id=permission["taskId"],
                kind="status",
                role="coordinator",
                name="orchestrator",
                status="running",
                summary=f"Resuming task after approving {permission['toolName']}.",
                payload={"permissionId": permission_id, "resumeStepIndex": start_index},
            )
            completed = await self._run_steps(
                storage=storage,
                task_id=permission["taskId"],
                plan=plan,
                start_index=start_index,
                gateway=gateway,
                context=context,
                approved_permission=permission,
            )
            if completed:
                await self._finish_task(
                    storage=storage,
                    task_id=permission["taskId"],
                    started=started,
                    rounds=max(0, len(plan) - start_index),
                )
        except PermissionBlocked as blocked:
            await orchestrator_storage.append_event(
                storage,
                task_id=permission["taskId"],
                kind="status",
                role="coordinator",
                name="orchestrator",
                status="paused",
                summary=f"任务已再次暂停，等待批准 {blocked.capability}。",
                payload={"permissionId": blocked.permission_id, "capability": blocked.capability},
            )
            await orchestrator_storage.update_task_status(storage, permission["taskId"], status="paused")
        except Exception as error:  # noqa: BLE001
            await orchestrator_storage.append_event(
                storage,
                task_id=permission["taskId"],
                kind="error",
                role="coordinator",
                name="orchestrator",
                status="failed",
                summary=str(error) or error.__class__.__name__,
                payload={"permissionId": permission_id},
            )
            await orchestrator_storage.update_task_status(
                storage,
                permission["taskId"],
                status="failed",
                error=str(error),
                finished=True,
            )

    def _build_context(
        self,
        *,
        task_id: str,
        prompt: str,
        workspace_path: str,
        settings: OrchestratorSettings,
        metadata: dict[str, Any],
        storage: SQLiteStorage,
        emit_event,
    ) -> CapabilityExecutionContext:
        return CapabilityExecutionContext(
            task_id=task_id,
            prompt=prompt,
            workspace_path=workspace_path,
            settings=settings,
            metadata=metadata,
            storage=storage,
            emit_event=emit_event,
        )

    async def _run_steps(
        self,
        *,
        storage: SQLiteStorage,
        task_id: str,
        plan: list[dict[str, Any]],
        start_index: int,
        gateway: CapabilityGateway,
        context: CapabilityExecutionContext,
        approved_permission: dict[str, Any] | None = None,
    ) -> bool:
        for step_index, step in enumerate(plan[start_index:], start=start_index):
            task = await orchestrator_storage.get_task(storage, task_id)
            if task is None:
                return False
            if task.get("status") in {"paused", "cancelled", "rolled_back"}:
                return False
            await self._run_step(
                storage=storage,
                task_id=task_id,
                step=step,
                step_index=step_index,
                plan=plan,
                gateway=gateway,
                context=context,
                approved_permission=approved_permission if step_index == start_index else None,
            )
        task = await orchestrator_storage.get_task(storage, task_id)
        return bool(task and task.get("status") not in {"paused", "cancelled", "rolled_back"})

    async def _finish_task(self, *, storage: SQLiteStorage, task_id: str, started: float, rounds: int) -> None:
        elapsed = int(time.monotonic() - started)
        final_answer = await self._build_final_answer(storage=storage, task_id=task_id, elapsed=elapsed)
        await orchestrator_storage.append_event(
            storage,
            task_id=task_id,
            kind="message",
            role="assistant",
            name="final_answer",
            status="done",
            summary=final_answer["summary"],
            payload=final_answer,
        )
        await orchestrator_storage.append_event(
            storage,
            task_id=task_id,
            kind="done",
            role="summarizer",
            name="summary",
            status="done",
            summary="任务已由 orchestrator v2 完成编排和能力执行。",
            payload={"elapsedSeconds": elapsed},
        )
        await orchestrator_storage.update_task_status(
            storage,
            task_id,
            status="done",
            finished=True,
            budget={"elapsedSeconds": elapsed, "rounds": rounds, "toolCalls": 0},
        )

    async def _append_user_message(
        self,
        *,
        storage: SQLiteStorage,
        task_id: str,
        request: OrchestratorTaskCreateRequest,
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
            payload={
                "text": request.prompt,
                "attachments": attachments,
                "mode": request.mode,
            },
        )

    async def _build_final_answer(
        self,
        *,
        storage: SQLiteStorage,
        task_id: str,
        elapsed: int,
    ) -> dict[str, Any]:
        events = await orchestrator_storage.list_events(storage, task_id)
        artifacts = await orchestrator_storage.list_artifacts(storage, task_id)
        completed_tools = [
            event for event in events
            if event.get("kind") in {"tool", "mcp", "browser"} and event.get("status") in {"done", "completed"}
        ]
        errors = [event for event in events if event.get("kind") == "error" or event.get("status") == "error"]
        completed_items = [
            str(event.get("summary") or event.get("name") or "完成一项能力调用").strip()
            for event in completed_tools[-6:]
            if str(event.get("summary") or event.get("name") or "").strip()
        ]
        artifact_items = [
            {
                "id": artifact.get("id", ""),
                "name": artifact.get("name", ""),
                "kind": artifact.get("kind", ""),
                "description": artifact.get("description", ""),
            }
            for artifact in artifacts[-6:]
        ]
        risk_items = [
            str(event.get("summary") or event.get("name") or "存在未处理风险").strip()
            for event in errors[-4:]
            if str(event.get("summary") or event.get("name") or "").strip()
        ]
        if not completed_items:
            completed_items = ["已完成任务编排和可用能力执行。"]
        summary = "任务已完成。" if not risk_items else "任务执行结束，但存在需要关注的问题。"
        flat_lines = [
            summary,
            "",
            "完成事项：",
            *[f"- {item}" for item in completed_items],
            "",
            "产物：",
            *[f"- {item['name'] or item['id']}" for item in artifact_items],
        ]
        if not artifact_items:
            flat_lines.append("- 本轮没有生成可下载产物。")
        flat_lines.extend(["", "验证：", f"- Orchestrator 运行耗时约 {elapsed}s。"])
        flat_lines.extend(["", "剩余风险："])
        flat_lines.extend([f"- {item}" for item in risk_items] or ["- 暂无明显剩余风险。"])
        text = "\n".join(flat_lines).strip()
        return {
            "text": text,
            "summary": summary,
            "completedItems": completed_items,
            "artifacts": artifact_items,
            "verification": [f"Orchestrator 运行耗时约 {elapsed}s。"],
            "risks": risk_items,
            "elapsedSeconds": elapsed,
        }

    async def _run_step(
        self,
        *,
        storage: SQLiteStorage,
        task_id: str,
        step: dict[str, Any],
        step_index: int,
        plan: list[dict[str, Any]],
        gateway: CapabilityGateway,
        context: CapabilityExecutionContext,
        approved_permission: dict[str, Any] | None = None,
    ) -> None:
        capability = str(step.get("capability") or "")
        if capability:
            approved_for_step = (
                approved_permission is not None
                and approved_permission.get("toolName") == capability
                and _payload_step_index(approved_permission.get("payload")) == step_index
            )
            decision = gateway.decide(capability)
            await orchestrator_storage.append_event(
                storage,
                task_id=task_id,
                kind="tool",
                role=str(step.get("worker") or "worker"),
                name=capability,
                status="allowed" if decision.allowed or approved_for_step else "blocked",
                summary=(
                    f"{capability} approved by permission {approved_permission['id']}."
                    if approved_for_step
                    else decision.reason
                ),
                payload={
                    "risk": decision.risk,
                    "allowed": decision.allowed or approved_for_step,
                    "permissionId": approved_permission["id"] if approved_for_step else "",
                },
            )
            if not decision.allowed and not approved_for_step:
                arguments_preview = _json_preview(
                    {
                        "capability": capability,
                        "worker": step.get("worker") or "worker",
                        "args": step.get("args") or {},
                    }
                )
                permission = await orchestrator_storage.create_permission_request(
                    storage,
                    task_id=task_id,
                    tool_name=capability,
                    arguments_preview=arguments_preview,
                    risk_level=decision.risk,
                    payload={
                        "plan": plan,
                        "step": step,
                        "stepIndex": step_index,
                        "capability": capability,
                    },
                )
                await orchestrator_storage.append_event(
                    storage,
                    task_id=task_id,
                    kind="question",
                    role="coordinator",
                    name="tool_permission",
                    status="blocked",
                    summary=f"{capability} requires permission before execution.",
                    payload={
                        "permissionId": permission["id"],
                        "toolName": capability,
                        "risk": decision.risk,
                        "reason": decision.reason,
                        "argumentsPreview": arguments_preview,
                    },
                )
                raise PermissionBlocked(capability, permission["id"])
            result = await self.capability_registry.execute(
                capability,
                dict(step.get("args") or {}),
                context,
            )
            await orchestrator_storage.append_event(
                storage,
                task_id=task_id,
                kind="tool",
                role=str(step.get("worker") or "worker"),
                name=capability,
                status="done" if result.get("ok", True) else "error",
                summary=str(result.get("summary") or ""),
                payload={
                    "ok": bool(result.get("ok", True)),
                    "data": _truncate_payload(result.get("data", {})),
                },
                artifact_ids=_string_list(result.get("artifactIds")),
            )
            if not result.get("ok", True) and capability == "code_agent":
                raise RuntimeError(str(result.get("summary") or "code_agent failed"))
        await orchestrator_storage.append_event(
            storage,
            task_id=task_id,
            kind="step",
            role=str(step.get("worker") or "worker"),
            name=str(step.get("id") or "step"),
            status="done",
            summary=str(step.get("goal") or ""),
            payload=step,
        )

default_orchestrator_runner = OrchestratorRunner()


def _truncate_payload(value: Any, limit: int = 4000) -> Any:
    text = repr(value)
    if len(text) <= limit:
        return value
    return {"truncated": True, "preview": text[:limit]}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _payload_step_index(value: Any) -> int:
    if not isinstance(value, dict):
        return -1
    try:
        return int(value.get("stepIndex", -1))
    except (TypeError, ValueError):
        return -1


def _json_preview(value: Any, limit: int = 2000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = repr(value)
    return text[:limit]
