from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypedDict
from uuid import uuid4
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph


TodoAction = Literal[
    "create",
    "plan",
    "list",
    "get",
    "update",
    "start",
    "complete",
    "block",
    "archive",
    "delete",
    "add_note",
    "summary",
]
TodoStatus = Literal["todo", "in_progress", "blocked", "done", "archived"]
TodoPriority = Literal["low", "medium", "high", "urgent"]

WorkspacePath = Path(__file__).resolve().parents[3]
DEFAULT_STORE_PATH = Path("agent_state/todo_tasks.json")
MAX_TASKS_PER_REQUEST = 50
MAX_LIST_LIMIT = 200
DEFAULT_PLAN_STEPS = [
    "明确目标、完成标准和约束条件。",
    "收集必要上下文、输入材料和依赖信息。",
    "拆解执行步骤并标记优先级。",
    "执行任务并记录关键决策、阻塞点和产出。",
    "验证结果，整理交付摘要和后续工作。",
]
DENIED_STORE_PARTS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    "backend/runtime",
}


class TodoPlan(TypedDict, total=False):
    action: TodoAction
    store_path: str
    task_id: str
    match_title: str
    note: str
    save: bool
    limit: int
    include_subtasks: bool
    delete_subtasks: bool
    filters: dict[str, Any]
    changes: dict[str, Any]
    task_items: list[dict[str, Any]]


class TodoTaskState(TypedDict, total=False):
    intent: str
    payload: dict[str, Any]
    plan: TodoPlan
    store: dict[str, Any]
    result: dict[str, Any]
    warnings: list[dict[str, Any]]


def build_todo_task_graph():
    builder = StateGraph(TodoTaskState)
    builder.add_node("planner_agent", planner_agent)
    builder.add_node("task_store_agent", task_store_agent)
    builder.add_node("writer_agent", writer_agent)
    builder.add_edge(START, "planner_agent")
    builder.add_edge("planner_agent", "task_store_agent")
    builder.add_edge("task_store_agent", "writer_agent")
    builder.add_edge("writer_agent", END)
    return builder.compile()


TODO_TASK_GRAPH = None


def get_todo_task_graph():
    global TODO_TASK_GRAPH
    if TODO_TASK_GRAPH is None:
        TODO_TASK_GRAPH = build_todo_task_graph()
    return TODO_TASK_GRAPH


async def run_todo_task_agent(args: dict[str, Any]) -> dict[str, Any]:
    intent = str(args.get("intent") or args.get("goal") or args.get("title") or "").strip()
    final_state = await get_todo_task_graph().ainvoke(
        {
            "intent": intent,
            "payload": args,
            "warnings": [],
        }
    )
    result = final_state.get("result", {})
    return {
        "summary": result.get("summary") or "todo_task_agent 执行完成。",
        "data": {
            "agent": "todo_task_agent",
            "action": final_state.get("plan", {}).get("action", ""),
            "storePath": final_state.get("plan", {}).get("store_path", ""),
            **{key: value for key, value in result.items() if key != "summary"},
            "warnings": final_state.get("warnings", []),
        },
    }


async def planner_agent(state: TodoTaskState) -> dict[str, Any]:
    payload = state.get("payload", {})
    intent = state.get("intent", "")
    action = normalize_action(payload.get("action") or payload.get("operation") or infer_action(intent, payload))
    plan: TodoPlan = {
        "action": action,
        "store_path": relative_path(resolve_store_path(payload.get("storePath") or payload.get("store_path"))),
        "task_id": str(payload.get("id") or payload.get("taskId") or payload.get("task_id") or "").strip(),
        "match_title": str(payload.get("matchTitle") or payload.get("match_title") or "").strip(),
        "note": str(payload.get("note") or payload.get("reason") or "").strip(),
        "save": parse_bool(payload.get("save"), True),
        "limit": clamp_int(payload.get("limit") or payload.get("maxResults"), 50, 1, MAX_LIST_LIMIT),
        "include_subtasks": parse_bool(payload.get("includeSubtasks") or payload.get("include_subtasks"), True),
        "delete_subtasks": parse_bool(payload.get("deleteSubtasks") or payload.get("delete_subtasks"), False),
        "filters": build_filters(payload),
        "changes": build_changes(payload),
        "task_items": build_task_items(action, payload, intent),
    }
    if not plan["task_id"]:
        inferred_id = infer_task_id(intent)
        if inferred_id:
            plan["task_id"] = inferred_id
    return {"plan": plan}


async def task_store_agent(state: TodoTaskState) -> dict[str, Any]:
    plan = state.get("plan", {})
    store_path = resolve_store_path(plan.get("store_path", ""))
    store = load_store(store_path)
    action = plan.get("action", "list")

    if action in {"create", "plan"}:
        result = create_tasks(store, plan, source_intent=state.get("intent", ""))
    elif action == "list":
        result = list_tasks(store, plan)
    elif action == "get":
        result = get_task_result(store, plan)
    elif action == "update":
        result = update_task_result(store, plan)
    elif action == "start":
        result = status_task_result(store, plan, "in_progress")
    elif action == "complete":
        result = status_task_result(store, plan, "done")
    elif action == "block":
        result = status_task_result(store, plan, "blocked")
    elif action == "archive":
        result = status_task_result(store, plan, "archived")
    elif action == "delete":
        result = delete_task_result(store, plan)
    elif action == "add_note":
        result = add_note_result(store, plan)
    else:
        result = summary_result(store)

    if result.get("changed"):
        save_store(store_path, store)
    return {"store": store, "result": result}


async def writer_agent(state: TodoTaskState) -> dict[str, Any]:
    result = dict(state.get("result", {}))
    if result.get("summary"):
        return {"result": result}

    action = state.get("plan", {}).get("action", "")
    if action in {"create", "plan"}:
        count = len(result.get("tasks", []))
        result["summary"] = f"已写入 {count} 个待办任务。"
    elif action == "list":
        result["summary"] = f"已返回 {len(result.get('tasks', []))} 个待办任务。"
    elif action == "summary":
        total = result.get("total", 0)
        active = result.get("active", 0)
        result["summary"] = f"当前共有 {total} 个任务，其中 {active} 个处于未完成状态。"
    else:
        task = result.get("task", {})
        title = task.get("title") or result.get("taskId") or "目标任务"
        result["summary"] = f"{action} 已处理：{title}。"
    return {"result": result}


def create_tasks(store: dict[str, Any], plan: TodoPlan, *, source_intent: str) -> dict[str, Any]:
    action = plan.get("action", "create")
    items = plan.get("task_items", [])
    if not items:
        raise ValueError("todo_task create/plan requires title, task, tasks, steps, or intent")

    now = current_time_iso()
    created: list[dict[str, Any]] = []

    if action == "plan":
        goal_item = items[0]
        parent = new_task(goal_item, now=now, source_intent=source_intent)
        parent["description"] = goal_item.get("description") or f"任务计划：{parent['title']}"
        created.append(parent)
        for step in items[1:]:
            child = new_task(step, now=now, source_intent=source_intent)
            child["parentId"] = parent["id"]
            created.append(child)
    else:
        for item in items[:MAX_TASKS_PER_REQUEST]:
            created.append(new_task(item, now=now, source_intent=source_intent))

    store.setdefault("tasks", []).extend(created)
    touch_store(store)
    return {
        "changed": True,
        "summary": f"已创建 {len(created)} 个待办任务。",
        "tasks": created,
        "counts": count_tasks(store.get("tasks", [])),
    }


def list_tasks(store: dict[str, Any], plan: TodoPlan) -> dict[str, Any]:
    filters = plan.get("filters", {})
    tasks = [task for task in store.get("tasks", []) if task_matches(task, filters)]
    tasks.sort(key=task_sort_key)
    limit = plan.get("limit", 50)
    output = tasks[:limit]
    return {
        "changed": False,
        "tasks": output,
        "totalMatched": len(tasks),
        "limit": limit,
        "counts": count_tasks(store.get("tasks", [])),
    }


def get_task_result(store: dict[str, Any], plan: TodoPlan) -> dict[str, Any]:
    task = resolve_task(store, plan)
    data = {
        "changed": False,
        "task": task,
    }
    if plan.get("include_subtasks", True):
        data["subtasks"] = [item for item in store.get("tasks", []) if item.get("parentId") == task.get("id")]
    return data


def update_task_result(store: dict[str, Any], plan: TodoPlan) -> dict[str, Any]:
    task = resolve_task(store, plan)
    changes = plan.get("changes", {})
    if not changes:
        raise ValueError("todo_task update requires at least one editable field")
    apply_task_changes(task, changes)
    task["updatedAt"] = current_time_iso()
    touch_store(store)
    return {
        "changed": True,
        "task": task,
        "counts": count_tasks(store.get("tasks", [])),
    }


def status_task_result(store: dict[str, Any], plan: TodoPlan, status: TodoStatus) -> dict[str, Any]:
    task = resolve_task(store, plan)
    task["status"] = status
    task["updatedAt"] = current_time_iso()
    if status == "done":
        task["completedAt"] = task["updatedAt"]
    elif status != "done":
        task["completedAt"] = ""
    note = plan.get("note", "")
    if note:
        append_note(task, note)
    touch_store(store)
    return {
        "changed": True,
        "task": task,
        "counts": count_tasks(store.get("tasks", [])),
    }


def add_note_result(store: dict[str, Any], plan: TodoPlan) -> dict[str, Any]:
    task = resolve_task(store, plan)
    note = plan.get("note", "")
    if not note:
        raise ValueError("todo_task add_note requires note")
    append_note(task, note)
    task["updatedAt"] = current_time_iso()
    touch_store(store)
    return {
        "changed": True,
        "task": task,
    }


def delete_task_result(store: dict[str, Any], plan: TodoPlan) -> dict[str, Any]:
    if not plan.get("filters", {}).get("confirmDelete", False):
        raise ValueError("todo_task delete requires confirmDelete=true")
    task = resolve_task(store, plan)
    task_id = task["id"]
    deleted_ids = {task_id}
    if plan.get("delete_subtasks", False):
        deleted_ids.update(descendant_ids(store.get("tasks", []), task_id))
    else:
        for item in store.get("tasks", []):
            if item.get("parentId") == task_id:
                item["parentId"] = ""
                item["updatedAt"] = current_time_iso()
    store["tasks"] = [item for item in store.get("tasks", []) if item.get("id") not in deleted_ids]
    touch_store(store)
    return {
        "changed": True,
        "deletedIds": sorted(deleted_ids),
        "taskId": task_id,
        "counts": count_tasks(store.get("tasks", [])),
    }


def summary_result(store: dict[str, Any]) -> dict[str, Any]:
    tasks = store.get("tasks", [])
    counts = count_tasks(tasks)
    active_tasks = [task for task in tasks if task.get("status") not in {"done", "archived"}]
    urgent_tasks = [task for task in active_tasks if task.get("priority") == "urgent"]
    blocked_tasks = [task for task in active_tasks if task.get("status") == "blocked"]
    return {
        "changed": False,
        "total": len(tasks),
        "active": len(active_tasks),
        "counts": counts,
        "urgentTasks": urgent_tasks[:10],
        "blockedTasks": blocked_tasks[:10],
    }


def new_task(item: dict[str, Any], *, now: str, source_intent: str) -> dict[str, Any]:
    title = clean_title(str(item.get("title") or item.get("task") or "未命名任务"))
    if not title:
        raise ValueError("todo_task task title cannot be empty")
    status = normalize_status(item.get("status") or "todo")
    task = {
        "id": new_task_id(),
        "title": title,
        "description": truncate(str(item.get("description") or item.get("details") or "").strip(), 2000),
        "status": status,
        "priority": normalize_priority(item.get("priority") or "medium"),
        "tags": normalize_string_list(item.get("tags")),
        "project": truncate(str(item.get("project") or "").strip(), 120),
        "dueAt": normalize_optional_text(item.get("dueAt") or item.get("due_at") or item.get("due")),
        "parentId": normalize_optional_text(item.get("parentId") or item.get("parent_id")),
        "notes": normalize_notes(item.get("notes")),
        "sourceIntent": truncate(source_intent, 500),
        "createdAt": now,
        "updatedAt": now,
        "completedAt": now if status == "done" else "",
    }
    return task


def build_task_items(action: TodoAction, payload: dict[str, Any], intent: str) -> list[dict[str, Any]]:
    if action == "plan":
        return build_plan_items(payload, intent)
    raw_tasks = payload.get("tasks")
    if isinstance(raw_tasks, list):
        return [normalize_task_item(item, payload) for item in raw_tasks if normalize_task_item(item, payload)]
    if raw_tasks:
        return [normalize_task_item(raw_tasks, payload)]

    title = str(payload.get("title") or payload.get("task") or "").strip()
    if not title and action == "create":
        title = infer_title_from_intent(intent)
    if not title:
        return []
    return [
        {
            "title": title,
            "description": str(payload.get("description") or payload.get("details") or "").strip(),
            "priority": payload.get("priority") or "medium",
            "tags": payload.get("tags") or [],
            "project": payload.get("project") or "",
            "dueAt": payload.get("dueAt") or payload.get("due_at") or payload.get("due") or "",
            "parentId": payload.get("parentId") or payload.get("parent_id") or "",
        }
    ]


def build_plan_items(payload: dict[str, Any], intent: str) -> list[dict[str, Any]]:
    goal = str(payload.get("goal") or payload.get("title") or infer_title_from_intent(intent) or "未命名任务计划").strip()
    steps = normalize_string_list(payload.get("steps") or payload.get("subtasks"))
    if not steps and isinstance(payload.get("tasks"), list):
        steps = [clean_title(str(item.get("title") or item.get("task") or item)) if isinstance(item, dict) else clean_title(str(item)) for item in payload["tasks"]]
    if not steps:
        steps = extract_bullets(intent)
    if not steps:
        steps = DEFAULT_PLAN_STEPS

    items = [
        {
            "title": goal,
            "description": str(payload.get("description") or payload.get("details") or "").strip(),
            "priority": payload.get("priority") or "medium",
            "tags": payload.get("tags") or [],
            "project": payload.get("project") or "",
            "dueAt": payload.get("dueAt") or payload.get("due_at") or payload.get("due") or "",
        }
    ]
    for step in steps[:MAX_TASKS_PER_REQUEST - 1]:
        items.append(
            {
                "title": clean_title(step),
                "description": "",
                "priority": payload.get("stepPriority") or payload.get("step_priority") or payload.get("priority") or "medium",
                "tags": payload.get("tags") or [],
                "project": payload.get("project") or "",
            }
        )
    return items


def normalize_task_item(value: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        item = dict(value)
    else:
        item = {"title": str(value)}
    title = clean_title(str(item.get("title") or item.get("task") or ""))
    if not title:
        return {}
    return {
        "title": title,
        "description": str(item.get("description") or item.get("details") or "").strip(),
        "status": item.get("status") or defaults.get("status") or "todo",
        "priority": item.get("priority") or defaults.get("priority") or "medium",
        "tags": item.get("tags") if "tags" in item else defaults.get("tags") or [],
        "project": item.get("project", defaults.get("project", "")),
        "dueAt": item.get("dueAt") or item.get("due_at") or item.get("due") or defaults.get("dueAt") or defaults.get("due_at") or "",
        "parentId": item.get("parentId") or item.get("parent_id") or defaults.get("parentId") or defaults.get("parent_id") or "",
        "notes": item.get("notes") or [],
    }


def build_filters(payload: dict[str, Any]) -> dict[str, Any]:
    filters = dict(payload.get("filters") or {})
    if payload.get("status") is not None:
        filters["status"] = payload.get("status")
    if payload.get("priority") is not None:
        filters["priority"] = payload.get("priority")
    if payload.get("tag") is not None:
        filters["tag"] = payload.get("tag")
    if payload.get("tags") is not None:
        filters["tags"] = payload.get("tags")
    if payload.get("project") is not None:
        filters["project"] = payload.get("project")
    if payload.get("parentId") is not None or payload.get("parent_id") is not None:
        filters["parentId"] = payload.get("parentId") or payload.get("parent_id")
    if payload.get("query") is not None:
        filters["query"] = payload.get("query")
    filters["includeArchived"] = parse_bool(payload.get("includeArchived") or filters.get("includeArchived"), False)
    filters["confirmDelete"] = parse_bool(payload.get("confirmDelete") or filters.get("confirmDelete"), False)
    return filters


def build_changes(payload: dict[str, Any]) -> dict[str, Any]:
    raw_changes = dict(payload.get("changes") or {})
    editable = ["title", "description", "status", "priority", "tags", "project", "dueAt", "due_at", "parentId", "parent_id"]
    for key in editable:
        if key in payload:
            raw_changes[key] = payload[key]
    changes: dict[str, Any] = {}
    if "title" in raw_changes:
        changes["title"] = clean_title(str(raw_changes["title"]))
    if "description" in raw_changes:
        changes["description"] = truncate(str(raw_changes["description"]).strip(), 2000)
    if "status" in raw_changes:
        changes["status"] = normalize_status(raw_changes["status"])
    if "priority" in raw_changes:
        changes["priority"] = normalize_priority(raw_changes["priority"])
    if "tags" in raw_changes:
        changes["tags"] = normalize_string_list(raw_changes["tags"])
    if "project" in raw_changes:
        changes["project"] = truncate(str(raw_changes["project"]).strip(), 120)
    if "dueAt" in raw_changes or "due_at" in raw_changes:
        changes["dueAt"] = normalize_optional_text(raw_changes.get("dueAt") or raw_changes.get("due_at"))
    if "parentId" in raw_changes or "parent_id" in raw_changes:
        changes["parentId"] = normalize_optional_text(raw_changes.get("parentId") or raw_changes.get("parent_id"))
    return changes


def apply_task_changes(task: dict[str, Any], changes: dict[str, Any]) -> None:
    for key, value in changes.items():
        task[key] = value
    if changes.get("status") == "done":
        task["completedAt"] = current_time_iso()
    elif "status" in changes and changes.get("status") != "done":
        task["completedAt"] = ""


def resolve_task(store: dict[str, Any], plan: TodoPlan) -> dict[str, Any]:
    task_id = plan.get("task_id", "")
    if task_id:
        for task in store.get("tasks", []):
            if task.get("id") == task_id:
                return task
        raise ValueError(f"todo_task task not found: {task_id}")

    match_title = plan.get("match_title", "")
    if match_title:
        candidates = [
            task
            for task in store.get("tasks", [])
            if match_title.lower() in str(task.get("title", "")).lower()
        ]
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            raise ValueError(f"todo_task title matched multiple tasks: {[task.get('id') for task in candidates[:5]]}")
        raise ValueError(f"todo_task title not found: {match_title}")

    raise ValueError("todo_task action requires id/taskId or matchTitle")


def task_matches(task: dict[str, Any], filters: dict[str, Any]) -> bool:
    if not filters.get("includeArchived", False) and task.get("status") == "archived":
        return False
    if filters.get("status") is not None:
        statuses = {normalize_status(item) for item in normalize_string_list(filters.get("status"))}
        if task.get("status") not in statuses:
            return False
    if filters.get("priority") is not None:
        priorities = {normalize_priority(item) for item in normalize_string_list(filters.get("priority"))}
        if task.get("priority") not in priorities:
            return False
    filter_tags = set(normalize_string_list(filters.get("tags") or filters.get("tag")))
    if filter_tags and not filter_tags.intersection(set(task.get("tags", []))):
        return False
    if filters.get("project") and str(task.get("project", "")).lower() != str(filters.get("project")).lower():
        return False
    if filters.get("parentId") is not None and str(task.get("parentId", "")) != str(filters.get("parentId") or ""):
        return False
    query = str(filters.get("query") or "").strip().lower()
    if query:
        haystack = " ".join(
            [
                str(task.get("title", "")),
                str(task.get("description", "")),
                str(task.get("project", "")),
                " ".join(task.get("tags", [])),
            ]
        ).lower()
        if query not in haystack:
            return False
    return True


def task_sort_key(task: dict[str, Any]):
    status_rank = {"in_progress": 0, "blocked": 1, "todo": 2, "done": 3, "archived": 4}
    priority_rank = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
    due = task.get("dueAt") or "9999-12-31"
    return (
        status_rank.get(task.get("status"), 9),
        priority_rank.get(task.get("priority"), 9),
        due,
        task.get("createdAt", ""),
    )


def count_tasks(tasks: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"todo": 0, "in_progress": 0, "blocked": 0, "done": 0, "archived": 0}
    for task in tasks:
        status = task.get("status")
        if status in counts:
            counts[status] += 1
    counts["total"] = len(tasks)
    counts["active"] = counts["todo"] + counts["in_progress"] + counts["blocked"]
    return counts


def descendant_ids(tasks: list[dict[str, Any]], root_id: str) -> set[str]:
    children_by_parent: dict[str, list[str]] = {}
    for task in tasks:
        parent_id = str(task.get("parentId") or "")
        if parent_id:
            children_by_parent.setdefault(parent_id, []).append(task.get("id", ""))
    output: set[str] = set()
    stack = list(children_by_parent.get(root_id, []))
    while stack:
        current = stack.pop()
        if not current or current in output:
            continue
        output.add(current)
        stack.extend(children_by_parent.get(current, []))
    return output


def load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "updatedAt": current_time_iso(), "tasks": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"todo_task store is not valid JSON: {relative_path(path)}") from error
    if isinstance(data, list):
        data = {"version": 1, "updatedAt": current_time_iso(), "tasks": data}
    if not isinstance(data, dict):
        raise ValueError("todo_task store root must be a JSON object")
    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        raise ValueError("todo_task store tasks must be a list")
    data["tasks"] = [normalize_existing_task(task) for task in tasks if isinstance(task, dict)]
    data.setdefault("version", 1)
    data.setdefault("updatedAt", current_time_iso())
    return data


def save_store(path: Path, store: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(store, ensure_ascii=False, indent=2)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(content + "\n", encoding="utf-8")
    temp_path.replace(path)


def normalize_existing_task(task: dict[str, Any]) -> dict[str, Any]:
    now = current_time_iso()
    return {
        "id": str(task.get("id") or new_task_id()),
        "title": clean_title(str(task.get("title") or task.get("task") or "未命名任务")),
        "description": truncate(str(task.get("description") or "").strip(), 2000),
        "status": normalize_status(task.get("status") or "todo"),
        "priority": normalize_priority(task.get("priority") or "medium"),
        "tags": normalize_string_list(task.get("tags")),
        "project": truncate(str(task.get("project") or "").strip(), 120),
        "dueAt": normalize_optional_text(task.get("dueAt") or task.get("due_at")),
        "parentId": normalize_optional_text(task.get("parentId") or task.get("parent_id")),
        "notes": normalize_notes(task.get("notes")),
        "sourceIntent": truncate(str(task.get("sourceIntent") or ""), 500),
        "createdAt": str(task.get("createdAt") or now),
        "updatedAt": str(task.get("updatedAt") or now),
        "completedAt": str(task.get("completedAt") or ""),
    }


def resolve_store_path(raw_value: Any = None) -> Path:
    raw = str(raw_value or os.getenv("AMADEUS_TODO_TASK_STORE", "")).strip()
    candidate = Path(raw) if raw else DEFAULT_STORE_PATH
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (WorkspacePath / candidate).resolve()
    if not is_relative_to(resolved, WorkspacePath):
        raise ValueError("todo_task storePath must stay inside the workspace")
    rel_parts = resolved.relative_to(WorkspacePath).parts
    for index, part in enumerate(rel_parts):
        joined = "/".join(rel_parts[: index + 1])
        if part in DENIED_STORE_PARTS or joined in DENIED_STORE_PARTS:
            raise ValueError(f"todo_task storePath cannot use restricted path: {part}")
    if resolved.name.startswith(".env") or resolved.suffix.lower() != ".json":
        raise ValueError("todo_task storePath must be a non-secret .json file")
    return resolved


def touch_store(store: dict[str, Any]) -> None:
    store["version"] = 1
    store["updatedAt"] = current_time_iso()


def append_note(task: dict[str, Any], text: str) -> None:
    task.setdefault("notes", []).append({"text": truncate(text.strip(), 2000), "createdAt": current_time_iso()})


def infer_action(intent: str, payload: dict[str, Any]) -> TodoAction:
    text = " ".join([intent, str(payload)]).lower()
    if any(token in text for token in ("summary", "统计", "概览")):
        return "summary"
    if any(token in text for token in ("list", "列出", "查看待办", "任务列表")):
        return "list"
    if any(token in text for token in ("get", "详情", "查看任务")) and infer_task_id(text):
        return "get"
    if any(token in text for token in ("add_note", "note", "备注", "记录")) and (payload.get("note") or payload.get("reason")):
        return "add_note"
    if any(token in text for token in ("complete", "done", "完成", "标记完成")):
        return "complete"
    if any(token in text for token in ("start", "开始", "进行中")):
        return "start"
    if any(token in text for token in ("blocked", "block", "阻塞")):
        return "block"
    if any(token in text for token in ("archive", "归档")):
        return "archive"
    if any(token in text for token in ("delete", "删除")):
        return "delete"
    if any(token in text for token in ("update", "修改", "更新")):
        return "update"
    if any(token in text for token in ("plan", "规划", "拆解", "计划")):
        return "plan"
    return "create"


def normalize_action(value: Any) -> TodoAction:
    action = str(value or "create").strip().lower()
    aliases = {
        "add": "create",
        "new": "create",
        "规划": "plan",
        "拆解": "plan",
        "ls": "list",
        "query": "list",
        "detail": "get",
        "details": "get",
        "edit": "update",
        "doing": "start",
        "done": "complete",
        "completed": "complete",
        "finish": "complete",
        "blocked": "block",
        "note": "add_note",
        "addnote": "add_note",
        "delete_task": "delete",
    }
    action = aliases.get(action, action)
    if action in {
        "create",
        "plan",
        "list",
        "get",
        "update",
        "start",
        "complete",
        "block",
        "archive",
        "delete",
        "add_note",
        "summary",
    }:
        return action  # type: ignore[return-value]
    raise ValueError(f"Unsupported todo_task action: {action}")


def normalize_status(value: Any) -> TodoStatus:
    status = str(value or "todo").strip().lower()
    mapping = {
        "open": "todo",
        "pending": "todo",
        "待办": "todo",
        "todo": "todo",
        "doing": "in_progress",
        "started": "in_progress",
        "progress": "in_progress",
        "进行中": "in_progress",
        "in-progress": "in_progress",
        "in_progress": "in_progress",
        "blocked": "blocked",
        "阻塞": "blocked",
        "done": "done",
        "complete": "done",
        "completed": "done",
        "完成": "done",
        "archived": "archived",
        "archive": "archived",
        "归档": "archived",
    }
    if status not in mapping:
        raise ValueError(f"Unsupported todo_task status: {status}")
    return mapping[status]  # type: ignore[return-value]


def normalize_priority(value: Any) -> TodoPriority:
    priority = str(value or "medium").strip().lower()
    mapping = {
        "low": "low",
        "低": "low",
        "medium": "medium",
        "normal": "medium",
        "中": "medium",
        "high": "high",
        "高": "high",
        "urgent": "urgent",
        "critical": "urgent",
        "紧急": "urgent",
    }
    if priority not in mapping:
        raise ValueError(f"Unsupported todo_task priority: {priority}")
    return mapping[priority]  # type: ignore[return-value]


def infer_title_from_intent(intent: str) -> str:
    clean = re.sub(
        r"^(创建|新增|添加|加入|记录|帮我|请|todo|task|create|add|new)\s*",
        "",
        intent.strip(),
        flags=re.IGNORECASE,
    )
    clean = re.sub(r"^(一个|一条)?\s*(待办|任务|todo|task)\s*[:：-]?\s*", "", clean, flags=re.IGNORECASE)
    clean = clean.strip(" ：:，,。. ")
    return clean_title(clean)


def extract_bullets(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        item = re.sub(r"^\s*(?:[-*+]|\d+[.)、]|[一二三四五六七八九十]+[、.])\s*", "", line).strip()
        if item and item != text.strip():
            items.append(item)
    if len(items) > 1:
        return items
    split_items = [part.strip() for part in re.split(r"[;；]\s*", text) if part.strip()]
    return split_items if len(split_items) > 1 else []


def infer_task_id(text: str) -> str:
    match = re.search(r"(todo_[0-9]{14}_[a-f0-9]{8})", text)
    return match.group(1) if match else ""


def normalize_string_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        parts = re.split(r"[,，;；\n]+", value)
    elif isinstance(value, list):
        parts = [str(item) for item in value]
    else:
        parts = [str(value)]
    output: list[str] = []
    seen: set[str] = set()
    for part in parts:
        item = part.strip()
        if item and item not in seen:
            output.append(truncate(item, 80))
            seen.add(item)
    return output


def normalize_notes(value: Any) -> list[dict[str, str]]:
    if not value:
        return []
    if isinstance(value, list):
        notes = value
    else:
        notes = [value]
    output: list[dict[str, str]] = []
    for note in notes:
        if isinstance(note, dict):
            text = str(note.get("text") or note.get("content") or "").strip()
            created = str(note.get("createdAt") or current_time_iso())
        else:
            text = str(note).strip()
            created = current_time_iso()
        if text:
            output.append({"text": truncate(text, 2000), "createdAt": created})
    return output


def normalize_optional_text(value: Any) -> str:
    return truncate(str(value or "").strip(), 160)


def clean_title(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return truncate(text, 160)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def parse_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off"}:
        return False
    return default


def new_task_id() -> str:
    return f"todo_{datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"


def current_time_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WorkspacePath)).replace("\\", "/") or "."
    except ValueError:
        return str(path)


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
