from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_TASK_EVENTS = int(os.getenv("AMADEUS_CODE_TASK_EVENT_HISTORY", "500"))
MAX_TASKS = int(os.getenv("AMADEUS_CODE_TASK_HISTORY", "80"))
DEFAULT_TASK_STORE_PATH = Path(__file__).resolve().parents[3] / ".amadeus" / "code_tasks.json"
TASK_STORE_PATH = Path(os.getenv("AMADEUS_CODE_TASK_STORE", str(DEFAULT_TASK_STORE_PATH))).expanduser()
TERMINAL_TASK_STATUSES = {"idle", "done", "stopped", "error"}
SESSION_ID_RE = re.compile(r"\bses_[A-Za-z0-9_-]+\b")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def string_field(value: Any) -> str:
    return str(value or "").strip()


def extract_session_id_from_text(value: str) -> str:
    match = SESSION_ID_RE.search(value)
    return match.group(0) if match else ""


def extract_session_from_payload(payload: dict[str, Any]) -> tuple[str, str]:
    session_id = string_field(payload.get("sessionId") or payload.get("session_id"))
    server_url = string_field(payload.get("serverUrl") or payload.get("server_url"))
    if not session_id:
        text = " ".join(
            string_field(payload.get(key))
            for key in ("text", "message", "detail", "title", "summary")
        )
        session_id = extract_session_id_from_text(text)
    return session_id, server_url


def merge_session_fields(current: tuple[str, str], next_fields: tuple[str, str]) -> tuple[str, str]:
    session_id, server_url = current
    next_session_id, next_server_url = next_fields
    return session_id or next_session_id, server_url or next_server_url


def extract_snapshot_session(snapshot: dict[str, Any]) -> tuple[str, str]:
    session: tuple[str, str] = extract_session_from_payload(snapshot)
    events = snapshot.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            payload = event.get("payload")
            if isinstance(payload, dict):
                session = merge_session_fields(session, extract_session_from_payload(payload))
            session = merge_session_fields(session, extract_session_from_payload(event))
            if session[0] and session[1]:
                return session

    messages = snapshot.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            session = merge_session_fields(session, extract_session_from_payload(message))
            question = message.get("question")
            if isinstance(question, dict):
                session = merge_session_fields(session, extract_session_from_payload(question))
            payload = message.get("payload")
            if isinstance(payload, dict):
                session = merge_session_fields(session, extract_session_from_payload(payload))
            if session[0] and session[1]:
                return session

    nested_snapshot = snapshot.get("snapshot")
    if isinstance(nested_snapshot, dict):
        session = merge_session_fields(session, extract_snapshot_session(nested_snapshot))
    return session


@dataclass
class CodeTaskEventRecord:
    seq: int
    event: str
    payload: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "event": self.event,
            "payload": self.payload,
            "createdAt": self.created_at,
        }


@dataclass
class ManagedCodeTask:
    id: str
    title: str
    prompt: str
    workspace_path: str
    status: str = "idle"
    session_id: str = ""
    server_url: str = ""
    running: bool = False
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    events: list[CodeTaskEventRecord] = field(default_factory=list)
    pending_questions: dict[str, dict[str, Any]] = field(default_factory=dict)
    subscribers: set[asyncio.Queue[CodeTaskEventRecord]] = field(default_factory=set)
    background_task: asyncio.Task[Any] | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)
    _next_seq: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "workspacePath": self.workspace_path,
            "status": self.status,
            "sessionId": self.session_id or None,
            "serverUrl": self.server_url or None,
            "running": self.running,
            "backgroundRunning": self.background_task is not None and not self.background_task.done(),
            "pendingQuestions": list(self.pending_questions.values()),
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "eventCount": len(self.events),
            "lastEvent": self.events[-1].to_dict() if self.events else None,
        }

    def detail(self) -> dict[str, Any]:
        data = self.summary()
        data["events"] = [event.to_dict() for event in self.events]
        if self.snapshot:
            data["snapshot"] = self.snapshot
        return data


class CodeTaskHub:
    def __init__(self) -> None:
        self._tasks: dict[str, ManagedCodeTask] = {}
        self._lock = asyncio.Lock()
        self._global_subscribers: set[asyncio.Queue[CodeTaskEventRecord]] = set()
        self._load_from_disk()

    async def ensure_task(
        self,
        *,
        task_id: str,
        title: str,
        prompt: str,
        workspace_path: str,
        status: str = "running",
        running: bool = True,
        session_id: str = "",
    ) -> ManagedCodeTask:
        now = utc_now_iso()
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                task = ManagedCodeTask(
                    id=task_id,
                    title=title or "OpenCode 任务",
                    prompt=prompt,
                    workspace_path=workspace_path,
                    status=status,
                    running=running,
                    session_id=session_id,
                    created_at=now,
                    updated_at=now,
                )
                self._tasks[task_id] = task
                self._trim_tasks_locked()
                self._save_locked()
                return task

            task.title = title or task.title
            task.prompt = prompt or task.prompt
            task.workspace_path = workspace_path or task.workspace_path
            task.status = status or task.status
            task.running = running
            task.session_id = session_id or task.session_id
            task.updated_at = now
            self._save_locked()
            return task

    async def sync_snapshots(self, snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
        synced: list[dict[str, Any]] = []
        async with self._lock:
            incoming_ids: set[str] = set()
            for snapshot in snapshots[:MAX_TASKS]:
                task_id = str(snapshot.get("id") or "").strip()
                if not task_id:
                    continue
                incoming_ids.add(task_id)
                now = utc_now_iso()
                snapshot_session_id, snapshot_server_url = extract_snapshot_session(snapshot)
                task = self._tasks.get(task_id)
                if task is None:
                    task = ManagedCodeTask(
                        id=task_id,
                        title=str(snapshot.get("title") or "OpenCode 任务"),
                        prompt=str(snapshot.get("prompt") or ""),
                        workspace_path=str(snapshot.get("workspacePath") or ""),
                        status=str(snapshot.get("status") or "idle"),
                        session_id=snapshot_session_id,
                        server_url=snapshot_server_url,
                        running=False,
                        created_at=str(snapshot.get("createdAt") or now),
                        updated_at=str(snapshot.get("updatedAt") or now),
                    )
                    self._tasks[task_id] = task
                elif not task.running:
                    task.title = str(snapshot.get("title") or task.title)
                    task.prompt = str(snapshot.get("prompt") or task.prompt)
                    task.workspace_path = str(snapshot.get("workspacePath") or task.workspace_path)
                    task.status = str(snapshot.get("status") or task.status)
                    task.session_id = snapshot_session_id or task.session_id
                    task.server_url = snapshot_server_url or task.server_url
                    task.updated_at = str(snapshot.get("updatedAt") or now)
                else:
                    task.session_id = snapshot_session_id or task.session_id
                    task.server_url = snapshot_server_url or task.server_url
                task.snapshot = snapshot
                synced.append(task.summary())
            removed_ids: list[str] = []
            for task_id, task in list(self._tasks.items()):
                if task_id in incoming_ids:
                    continue
                if task.running or (task.background_task is not None and not task.background_task.done()):
                    continue
                self._tasks.pop(task_id, None)
                removed_ids.append(task_id)
            self._trim_tasks_locked()
            if snapshots or removed_ids:
                self._save_locked()
        return synced

    async def publish(self, task_id: str, event: str, payload: dict[str, Any]) -> CodeTaskEventRecord:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                task = ManagedCodeTask(
                    id=task_id,
                    title=str(payload.get("title") or "OpenCode 任务"),
                    prompt=str(payload.get("prompt") or ""),
                    workspace_path=str(payload.get("workspacePath") or ""),
                    status="running",
                    running=True,
                )
                self._tasks[task_id] = task
            task._next_seq += 1
            created_at = utc_now_iso()
            event_payload = dict(payload)
            event_payload.setdefault("taskId", task_id)
            event_payload.setdefault("eventId", task._next_seq)
            event_payload.setdefault("emittedAt", created_at)
            record = CodeTaskEventRecord(
                seq=task._next_seq,
                event=event,
                payload=event_payload,
                created_at=created_at,
            )
            task.events.append(record)
            if len(task.events) > MAX_TASK_EVENTS:
                task.events = task.events[-MAX_TASK_EVENTS:]
            self._apply_event_locked(task, event, event_payload, created_at)
            self._save_locked()
            subscribers = list(task.subscribers)
            global_subscribers = list(self._global_subscribers)

        for queue in subscribers:
            self._offer(queue, record)
        for queue in global_subscribers:
            self._offer(queue, record)
        return record

    async def finish_task(self, task_id: str, status: str = "") -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.running = False
            if status:
                task.status = status
            task.updated_at = utc_now_iso()
            task.background_task = None
            self._save_locked()

    async def attach_background_task(self, task_id: str, background_task: asyncio.Task[Any]) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.background_task = background_task

    async def clear_background_task(self, task_id: str, background_task: asyncio.Task[Any]) -> None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is not None and task.background_task is background_task:
                task.background_task = None

    async def cancel_background_tasks(self) -> None:
        async with self._lock:
            tasks = [
                task.background_task
                for task in self._tasks.values()
                if task.background_task is not None and not task.background_task.done()
            ]
            for managed_task in self._tasks.values():
                if managed_task.background_task is not None and not managed_task.background_task.done():
                    managed_task.status = "stopped"
                    managed_task.running = False
                    managed_task.updated_at = utc_now_iso()
                    managed_task.background_task.cancel()
                managed_task.background_task = None
            self._save_locked()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def get_summary(self, task_id: str) -> dict[str, Any] | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            return task.summary() if task else None

    async def get_detail(self, task_id: str) -> dict[str, Any] | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            return task.detail() if task else None

    async def list_tasks(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [
                task.summary()
                for task in sorted(
                    self._tasks.values(),
                    key=lambda item: item.updated_at,
                    reverse=True,
                )
            ]

    async def subscribe(
        self,
        task_id: str,
        *,
        replay: bool = True,
    ):
        queue: asyncio.Queue[CodeTaskEventRecord] = asyncio.Queue(maxsize=300)
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            replay_events = list(task.events) if replay else []
            task.subscribers.add(queue)

        try:
            for record in replay_events:
                yield record
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                task = self._tasks.get(task_id)
                if task is not None:
                    task.subscribers.discard(queue)

    async def subscribe_all(self, *, replay: bool = False):
        queue: asyncio.Queue[CodeTaskEventRecord] = asyncio.Queue(maxsize=500)
        async with self._lock:
            replay_events: list[CodeTaskEventRecord] = []
            if replay:
                for task in self._tasks.values():
                    replay_events.extend(task.events)
                replay_events.sort(key=lambda item: item.created_at)
            self._global_subscribers.add(queue)

        try:
            for record in replay_events:
                yield record
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                self._global_subscribers.discard(queue)

    def _apply_event_locked(
        self,
        task: ManagedCodeTask,
        event: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        task.updated_at = created_at
        if event == "input":
            text = str(payload.get("text") or "").strip()
            if text:
                task.prompt = text
            task.status = "running"
            task.running = True
            return
        if event == "session":
            task.session_id = str(payload.get("sessionId") or task.session_id)
            task.server_url = str(payload.get("serverUrl") or task.server_url)
            task.workspace_path = str(payload.get("workspacePath") or task.workspace_path)
            task.title = str(payload.get("title") or task.title)
            task.status = "session"
            task.running = True
            return
        if event == "status":
            task.status = str(payload.get("status") or payload.get("message") or task.status)
            if task.status not in {"idle", "done", "stopped", "error"}:
                task.running = True
            return
        if event == "question":
            request_id = str(payload.get("requestId") or "")
            if request_id:
                task.pending_questions[request_id] = payload
            task.status = "waiting_input"
            task.running = True
            return
        if event == "question_result":
            request_id = str(payload.get("requestId") or "")
            if request_id:
                task.pending_questions.pop(request_id, None)
            task.status = "running"
            task.running = True
            return
        if event == "done":
            task.status = str(payload.get("status") or "done")
            task.running = False
            task.pending_questions.clear()
            return
        if event == "error":
            task.status = "error"
            task.running = False
            task.pending_questions.clear()

    def _trim_tasks_locked(self) -> None:
        if len(self._tasks) <= MAX_TASKS:
            return
        sorted_items = sorted(
            self._tasks.items(),
            key=lambda item: item[1].updated_at,
            reverse=True,
        )
        keep = {task_id for task_id, _ in sorted_items[:MAX_TASKS]}
        self._tasks = {task_id: task for task_id, task in self._tasks.items() if task_id in keep}

    def _load_from_disk(self) -> None:
        try:
            if not TASK_STORE_PATH.exists():
                return
            raw = json.loads(TASK_STORE_PATH.read_text(encoding="utf-8"))
            items = raw.get("tasks") if isinstance(raw, dict) else raw
            if not isinstance(items, list):
                return
            for item in items[:MAX_TASKS]:
                if not isinstance(item, dict):
                    continue
                task = self._task_from_stored_dict(item)
                if task is not None:
                    self._tasks[task.id] = task
            self._trim_tasks_locked()
        except Exception:
            self._tasks = {}

    def _save_locked(self) -> None:
        try:
            TASK_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "savedAt": utc_now_iso(),
                "tasks": [
                    self._task_to_stored_dict(task)
                    for task in sorted(
                        self._tasks.values(),
                        key=lambda item: item.updated_at,
                        reverse=True,
                    )
                ],
            }
            tmp_path = TASK_STORE_PATH.with_suffix(f"{TASK_STORE_PATH.suffix}.tmp")
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp_path, TASK_STORE_PATH)
        except Exception:
            return

    def _task_to_stored_dict(self, task: ManagedCodeTask) -> dict[str, Any]:
        return {
            "id": task.id,
            "title": task.title,
            "prompt": task.prompt,
            "workspacePath": task.workspace_path,
            "status": task.status,
            "sessionId": task.session_id,
            "serverUrl": task.server_url,
            "running": task.running,
            "createdAt": task.created_at,
            "updatedAt": task.updated_at,
            "events": [event.to_dict() for event in task.events],
            "pendingQuestions": list(task.pending_questions.values()),
            "snapshot": task.snapshot,
            "nextSeq": task._next_seq,
        }

    def _task_from_stored_dict(self, item: dict[str, Any]) -> ManagedCodeTask | None:
        task_id = str(item.get("id") or "").strip()
        if not task_id:
            return None
        now = utc_now_iso()
        status = str(item.get("status") or "idle")
        stored_running = bool(item.get("running"))
        if stored_running and status not in TERMINAL_TASK_STATUSES:
            status = "stopped"
        stored_session_id, stored_server_url = extract_snapshot_session(item)
        task = ManagedCodeTask(
            id=task_id,
            title=str(item.get("title") or "OpenCode 任务"),
            prompt=str(item.get("prompt") or ""),
            workspace_path=str(item.get("workspacePath") or ""),
            status=status,
            session_id=stored_session_id,
            server_url=stored_server_url,
            running=False,
            created_at=str(item.get("createdAt") or now),
            updated_at=str(item.get("updatedAt") or now),
        )
        events = item.get("events")
        if isinstance(events, list):
            for event_item in events[-MAX_TASK_EVENTS:]:
                if not isinstance(event_item, dict):
                    continue
                payload = event_item.get("payload")
                if not isinstance(payload, dict):
                    payload = {}
                task.events.append(
                    CodeTaskEventRecord(
                        seq=int(event_item.get("seq") or 0),
                        event=str(event_item.get("event") or "log"),
                        payload=payload,
                        created_at=str(event_item.get("createdAt") or now),
                    )
                )
        pending_questions = item.get("pendingQuestions")
        if isinstance(pending_questions, list):
            for question in pending_questions:
                if not isinstance(question, dict):
                    continue
                request_id = str(question.get("requestId") or "")
                if request_id:
                    task.pending_questions[request_id] = question
        snapshot = item.get("snapshot")
        if isinstance(snapshot, dict):
            task.snapshot = snapshot
        task._next_seq = int(item.get("nextSeq") or (task.events[-1].seq if task.events else 0))
        if not task.session_id or not task.server_url:
            snapshot_session_id, snapshot_server_url = extract_snapshot_session(task.snapshot)
            task.session_id = task.session_id or snapshot_session_id
            task.server_url = task.server_url or snapshot_server_url
        return task

    @staticmethod
    def _offer(queue: asyncio.Queue[Any], record: Any) -> None:
        try:
            queue.put_nowait(record)
            return
        except asyncio.QueueFull:
            pass
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(record)
        except asyncio.QueueFull:
            return


code_task_hub = CodeTaskHub()
