"""OpenCode delegation tool.

Wraps the existing :mod:`amadeus_app.code_tasks.opencode_runner` so the
complex agent can delegate code-modification and command-execution tasks
to OpenCode as a sub-task. The agent emits ``step``/``tool``/``done``
events as OpenCode progresses; the underlying OpenCode task events are
mirrored into the agent task event stream.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..code_tasks.opencode_runner import stream_opencode_task
from ..domain import CodeTaskStreamRequest
from ..logging_config import get_logger
from .tool_registry import UnifiedTool

_log = get_logger(__name__)


def make_opencode_delegate_tool(
    *,
    task_id: str,
    workspace: str,
    on_event,
    trust_mode: bool = True,
) -> UnifiedTool:
    """Build a task-scoped ``opencode_delegate`` tool.

    ``on_event`` is an async callable that receives ``(kind, payload)``
    tuples to emit into the agent task event stream.

    ``trust_mode`` controls whether OpenCode may auto-approve file edits
    and command execution. The model cannot override this via tool
    arguments — ``autoApprove`` is determined solely by the task's
    trust-mode setting.
    """

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        prompt = str(args.get("prompt", "")).strip()
        if not prompt:
            return {"ok": False, "error": "prompt is required"}
        title = str(args.get("title", "")) or "OpenCode 子任务"
        # autoApprove is enforced by trust_mode, NOT by model arguments.
        auto_approve = bool(trust_mode)
        timeout_seconds = int(args.get("timeoutSeconds", 1800))
        sub_request = CodeTaskStreamRequest(
            task_id=f"{task_id}-opencode",
            title=title,
            prompt=prompt,
            workspace_path=workspace,
            auto_approve=auto_approve,
            timeout_seconds=timeout_seconds,
            source="agent",
        )
        await on_event("step", {"name": "opencode_delegate", "status": "started", "summary": title})
        final_output: dict[str, Any] = {"ok": True, "summary": "OpenCode 子任务完成。", "data": {"events": []}}
        try:
            async for sse_chunk in stream_opencode_task(sub_request):
                # sse_chunk is a string like "event: <name>\ndata: <json>\n\n"
                parsed = _parse_sse(sse_chunk)
                if parsed is None:
                    continue
                event_name, payload = parsed
                final_output["data"]["events"].append({"event": event_name, "payload": payload})
                await on_event("tool", {
                    "name": "opencode_delegate",
                    "status": payload.get("status", event_name),
                    "summary": str(payload.get("message") or payload.get("text") or event_name),
                    "detail": payload,
                })
                if event_name == "done":
                    final_output["summary"] = str(payload.get("message") or "OpenCode 子任务完成。")
                elif event_name == "error":
                    final_output["ok"] = False
                    final_output["summary"] = str(payload.get("message") or "OpenCode 子任务失败。")
        except Exception as error:  # noqa: BLE001
            final_output = {"ok": False, "error": str(error), "summary": f"OpenCode 委托失败：{error}"}
            await on_event("error", {"message": str(error), "source": "opencode_delegate"})
        await on_event("step", {"name": "opencode_delegate", "status": "finished", "summary": final_output["summary"]})
        return final_output

    return UnifiedTool(
        name="opencode_delegate",
        description="Delegate a code-modification or command-execution sub-task to OpenCode. "
                    "OpenCode runs as a sub-task with its own event stream; progress is mirrored "
                    "into the agent task. Requires opencode installed and in PATH. "
                    "Auto-approval is controlled by the task's trust mode setting.",
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The task prompt for OpenCode"},
                "title": {"type": "string", "description": "Optional sub-task title"},
                "timeoutSeconds": {"type": "integer", "minimum": 30, "maximum": 3600, "description": "Timeout in seconds, default 1800"},
            },
            "required": ["prompt"],
        },
        handler=handler,
        source="builtin",
        permission="safe",
    )


def _parse_sse(chunk: str) -> tuple[str, dict[str, Any]] | None:
    """Parse a single SSE chunk into (event_name, payload)."""
    lines = chunk.split("\n")
    event_name = ""
    data_lines: list[str] = []
    for line in lines:
        if line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if not event_name or not data_lines:
        return None
    import json

    try:
        payload = json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return None
    return event_name, payload
