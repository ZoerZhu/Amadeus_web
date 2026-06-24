"""Agent task hub: in-memory state, SSE streaming, and control signals.

Each agent task has:
- A persistent record in SQLite (via :mod:`agent_storage`).
- An in-memory :class:`ManagedAgentTask` holding the live asyncio task,
  subscriber queues, pause/resume/cancel signals, and budget counters.
- A monotonically increasing event sequence counter.

SSE events are produced by calling :meth:`ManagedAgentTask.emit` which
persists the event and fans it out to all subscribers.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..logging_config import get_logger
from . import agent_storage
from .domain import AgentEventKind, AgentTaskStatus
from .tool_registry import UnifiedTool, unified_tool_registry

_log = get_logger(__name__)


TERMINAL_STATUSES: set[str] = {"cancelled", "done", "failed", "rolled_back"}


@dataclass
class TaskBudget:
    """Live budget counters for a running task."""

    max_rounds: int = 20
    max_tool_calls: int = 80
    max_runtime_seconds: int = 1800
    max_sampling_depth: int = 3
    rounds: int = 0
    tool_calls: int = 0
    sampling_depth: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict[str, Any]:
        elapsed = time.monotonic() - self.started_at
        return {
            "maxRounds": self.max_rounds,
            "maxToolCalls": self.max_tool_calls,
            "maxRuntimeSeconds": self.max_runtime_seconds,
            "maxSamplingDepth": self.max_sampling_depth,
            "rounds": self.rounds,
            "toolCalls": self.tool_calls,
            "samplingDepth": self.sampling_depth,
            "elapsedSeconds": round(elapsed, 1),
            "remainingRounds": max(0, self.max_rounds - self.rounds),
            "remainingToolCalls": max(0, self.max_tool_calls - self.tool_calls),
            "remainingSeconds": max(0, int(self.max_runtime_seconds - elapsed)),
        }

    def exhausted(self) -> tuple[bool, str]:
        if self.rounds >= self.max_rounds:
            return True, "max_rounds reached"
        if self.tool_calls >= self.max_tool_calls:
            return True, "max_tool_calls reached"
        if time.monotonic() - self.started_at >= self.max_runtime_seconds:
            return True, "max_runtime_seconds reached"
        if self.sampling_depth >= self.max_sampling_depth:
            return True, "max_sampling_depth reached"
        return False, ""


@dataclass
class ManagedAgentTask:
    task_id: str
    user_id: str
    title: str
    prompt: str
    workspace_path: str
    conversation_id: str | None
    active_skill_ids: list[str]
    status: AgentTaskStatus = "created"
    error: str = ""
    budget: TaskBudget = field(default_factory=TaskBudget)
    settings: dict[str, Any] = field(default_factory=dict)
    background_task: asyncio.Task[Any] | None = None
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    _next_seq: int = 0
    _pause_event: asyncio.Event = field(default_factory=asyncio.Event)
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    _tools: list[UnifiedTool] = field(default_factory=list)
    _cleanup_hooks: list = field(default_factory=list)
    # Permission queue: perm_id → (Event, resolved_status)
    _permission_events: dict[str, tuple[asyncio.Event, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._pause_event.set()  # not paused by default

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def paused(self) -> bool:
        return not self._pause_event.is_set()

    async def wait_if_paused(self) -> None:
        """Block while the task is paused. Raises if cancelled."""
        await self._pause_event.wait()
        if self.cancelled:
            raise asyncio.CancelledError("task cancelled")

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError("task cancelled")

    async def emit(
        self,
        *,
        storage,
        kind: AgentEventKind,
        role: str = "",
        name: str = "",
        status: str = "",
        summary: str = "",
        payload: dict[str, Any] | None = None,
        artifact_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        self._next_seq += 1
        seq = self._next_seq
        record = await agent_storage.append_agent_task_event(
            storage,
            task_id=self.task_id,
            seq=seq,
            kind=kind,
            role=role,
            name=name,
            status=status,
            summary=summary,
            payload=payload or {},
            artifact_ids=artifact_ids or [],
        )
        # Fan out to subscribers (non-blocking)
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(record)
            except asyncio.QueueFull:
                # Drop oldest to make room
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(record)
                except asyncio.QueueFull:
                    pass
        return record

    def add_cleanup_hook(self, hook) -> None:
        self._cleanup_hooks.append(hook)

    async def cleanup(self) -> None:
        for hook in self._cleanup_hooks:
            try:
                result = hook()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as error:  # noqa: BLE001
                _log.warning("task %s cleanup hook failed: %s", self.task_id, error)
        self._cleanup_hooks.clear()
        # Reject any pending permission requests
        for perm_id, (event, _) in self._permission_events.items():
            if not event.is_set():
                self._permission_events[perm_id] = (event, "rejected")
                event.set()

    def register_permission_request(self, perm_id: str) -> asyncio.Event:
        """Register a pending permission request and return its wait event."""
        event = asyncio.Event()
        self._permission_events[perm_id] = (event, "pending")
        return event

    def resolve_permission(self, perm_id: str, decision: str) -> bool:
        """Resolve a pending permission request.

        Returns True if the permission was found and resolved, False otherwise.
        ``decision`` should be ``approved`` or ``rejected``.
        """
        entry = self._permission_events.get(perm_id)
        if entry is None:
            return False
        event, _ = entry
        if event.is_set():
            return False  # already resolved
        self._permission_events[perm_id] = (event, decision)
        event.set()
        return True

    async def wait_for_permission(self, perm_id: str, timeout: float = 300.0) -> str:
        """Wait for a permission request to be resolved.

        Returns the decision (``approved`` / ``rejected`` / ``timeout``).
        Also checks for cancellation while waiting.
        """
        entry = self._permission_events.get(perm_id)
        if entry is None:
            return "rejected"
        event, _ = entry
        try:
            await asyncio.wait_for(
                self._wait_permission_or_cancel(event),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            self._permission_events[perm_id] = (event, "timeout")
            event.set()
            return "timeout"
        _, decision = self._permission_events.get(perm_id, (event, "rejected"))
        return decision

    async def _wait_permission_or_cancel(self, event: asyncio.Event) -> None:
        """Wait for either the permission event or task cancellation."""
        cancel_task = asyncio.create_task(self._cancel_event.wait())
        perm_task = asyncio.create_task(event.wait())
        try:
            done, pending = await asyncio.wait(
                {cancel_task, perm_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancel_task in done:
                # Task was cancelled — reject the permission
                event.set()
        finally:
            cancel_task.cancel()
            perm_task.cancel()


class AgentTaskHub:
    """In-memory registry of running/recently-finished agent tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, ManagedAgentTask] = {}
        self._lock = asyncio.Lock()

    async def create_task(
        self,
        *,
        storage,
        user_id: str,
        title: str,
        prompt: str,
        workspace_path: str,
        conversation_id: str | None,
        active_skill_ids: list[str],
        settings: dict[str, Any],
        budget: TaskBudget,
    ) -> ManagedAgentTask:
        task_record = await agent_storage.create_agent_task(
            storage,
            user_id=user_id,
            title=title,
            prompt=prompt,
            workspace_path=workspace_path,
            conversation_id=conversation_id,
            active_skill_ids=active_skill_ids,
            settings=settings,
            budget=budget.to_dict(),
        )
        task_id = task_record["id"]
        managed = ManagedAgentTask(
            task_id=task_id,
            user_id=user_id,
            title=title,
            prompt=prompt,
            workspace_path=workspace_path,
            conversation_id=conversation_id,
            active_skill_ids=active_skill_ids,
            status="created",
            settings=settings,
            budget=budget,
        )
        async with self._lock:
            self._tasks[task_id] = managed
        return managed

    def get(self, task_id: str) -> ManagedAgentTask | None:
        return self._tasks.get(task_id)

    async def attach_background_task(self, task_id: str, background: asyncio.Task[Any]) -> None:
        managed = self._tasks.get(task_id)
        if managed is None:
            background.cancel()
            return
        managed.background_task = background

    async def list_tasks(self, *, storage, user_id: str | None = None, conversation_id: str | None = None) -> list[dict[str, Any]]:
        records = await agent_storage.list_agent_tasks(storage, user_id=user_id, conversation_id=conversation_id)
        # Augment with live status/budget from in-memory tasks
        for record in records:
            managed = self._tasks.get(record["id"])
            if managed is not None:
                record["status"] = managed.status
                record["budget"] = managed.budget.to_dict()
        return records

    async def get_detail(self, *, storage, task_id: str) -> dict[str, Any] | None:
        record = await agent_storage.get_agent_task(storage, task_id)
        if record is None:
            return None
        managed = self._tasks.get(task_id)
        if managed is not None:
            record["status"] = managed.status
            record["budget"] = managed.budget.to_dict()
        events = await agent_storage.list_agent_task_events(storage, task_id, limit=2000)
        record["events"] = events
        artifacts = await agent_storage.list_artifacts(storage, task_id)
        record["artifacts"] = artifacts
        return record

    async def control(
        self,
        *,
        storage,
        task_id: str,
        action: str,
        reason: str = "",
    ) -> dict[str, Any]:
        managed = self._tasks.get(task_id)
        if managed is None:
            return {"ok": False, "error": f"task not found: {task_id}"}
        if action == "pause":
            if managed.status not in {"running", "created"}:
                return {"ok": False, "error": f"cannot pause task in status {managed.status}"}
            managed._pause_event.clear()
            managed.status = "paused"
            await agent_storage.update_agent_task_status(storage, task_id, status="paused")
            await managed.emit(storage, kind="status", name="pause", status="paused", summary=reason or "task paused")
            return {"ok": True, "status": "paused"}
        if action == "resume":
            if managed.status != "paused":
                return {"ok": False, "error": f"cannot resume task in status {managed.status}"}
            managed._pause_event.set()
            managed.status = "running"
            await agent_storage.update_agent_task_status(storage, task_id, status="running")
            await managed.emit(storage, kind="status", name="resume", status="running", summary=reason or "task resumed")
            return {"ok": True, "status": "running"}
        if action == "cancel":
            managed._cancel_event.set()
            managed._pause_event.set()  # unblock any paused wait
            managed.status = "cancelled"
            if managed.background_task is not None and not managed.background_task.done():
                managed.background_task.cancel()
            await agent_storage.update_agent_task_status(storage, task_id, status="cancelled", error=reason, finished=True)
            await managed.emit(storage, kind="status", name="cancel", status="cancelled", summary=reason or "task cancelled")
            await managed.cleanup()
            return {"ok": True, "status": "cancelled"}
        if action == "rollback":
            if not managed.settings.get("rollbackEnabled", True):
                return {"ok": False, "error": "rollback is disabled for this task"}
            result = await agent_storage.rollback_file_snapshots(storage, task_id)
            managed.status = "rolled_back"
            await managed.emit(
                storage,
                kind="status",
                name="rollback",
                status="rolled_back",
                summary=f"rolled back: restored={result['restored']} deleted={result['deleted']} failed={len(result['failed'])}",
                payload=result,
            )
            await managed.cleanup()
            return {"ok": True, "status": "rolled_back", "result": result}
        return {"ok": False, "error": f"unknown control action: {action}"}

    async def subscribe(self, task_id: str, *, replay: bool = True) -> AsyncIterator[dict[str, Any]]:
        managed = self._tasks.get(task_id)
        if managed is None:
            raise KeyError(task_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        replay_events: list[dict[str, Any]] = []
        if replay:
            # Replay is handled by the route layer via list_agent_task_events;
            # here we just register for live events.
            pass
        managed.subscribers.add(queue)
        try:
            while True:
                record = await queue.get()
                yield record
                # Break on terminal events so the SSE stream can close.
                kind = record.get("kind", "")
                status = record.get("status", "")
                if kind in {"done", "error"} or status in TERMINAL_STATUSES:
                    break
        finally:
            managed.subscribers.discard(queue)

    async def cancel_all(self) -> None:
        async with self._lock:
            tasks = list(self._tasks.values())
        for managed in tasks:
            if managed.background_task is not None and not managed.background_task.done():
                managed._cancel_event.set()
                managed._pause_event.set()
                managed.background_task.cancel()
            await managed.cleanup()


agent_task_hub = AgentTaskHub()


def sse(event: str, payload: dict[str, Any]) -> str:
    """Format an SSE chunk."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
