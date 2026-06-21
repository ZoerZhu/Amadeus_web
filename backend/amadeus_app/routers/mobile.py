"""Mobile-related endpoints extracted from main.py."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from .._common import local_network_addresses, parse_bool, parse_int
from ..chat_service import sse
from ..code_tasks.opencode_runner import (
    drain_opencode_task,
    reply_code_task_question_by_task,
    reply_opencode_question,
    send_remote_code_task_message,
    stop_all_opencode_processes,
    wait_for_code_task_result,
)
from ..code_tasks.task_hub import code_task_hub, utc_now_iso
from ..domain import (
    CodeTaskMobileQuestionReplyRequest,
    CodeTaskRemoteMessageRequest,
    CodeTaskStreamRequest,
    CodeTaskViewPublishRequest,
)
from ..logging_config import get_logger

_log = get_logger(__name__)

router = APIRouter(prefix="/api/mobile", tags=["mobile"])


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------


@router.get("/connect-info")
async def mobile_connect_info(request: Request) -> dict:
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    addresses = local_network_addresses()
    return {
        "scheme": request.url.scheme,
        "port": port,
        "addresses": addresses,
        "urls": [f"{request.url.scheme}://{address}:{port}" for address in addresses],
        "endpoints": {
            "tasks": "/api/mobile/code-tasks",
            "events": "/api/mobile/code-tasks/{taskId}/events",
            "websocket": "/api/mobile/code-tasks/{taskId}/ws",
            "message": "/api/mobile/code-tasks/{taskId}/messages",
            "questionReply": "/api/mobile/code-tasks/{taskId}/question/reply",
            "bridgeWebSocket": "/api/mobile/bridge/ws",
            "bridgeTaskFallback": "/api/mobile/bridge/task",
            "bridgeQuestionReplyFallback": "/api/mobile/bridge/question/reply",
            "taskViews": "/api/mobile/task-views",
            "taskViewEvents": "/api/mobile/task-views/events",
        },
    }


@router.post("/task-views")
async def publish_mobile_task_view(request: CodeTaskViewPublishRequest) -> dict:
    task_id = request.task_id.strip() or str(request.snapshot.get("taskId") or "").strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="Missing taskId")
    record = await code_task_hub.publish_view_snapshot(task_id, request.snapshot)
    _log.debug("[mobile-view] published %s", describe_mobile_view_record(record))
    return {"ok": True, "seq": record.seq, "taskId": task_id}


@router.get("/task-views")
async def mobile_task_views() -> dict:
    return {"views": await code_task_hub.list_view_snapshots()}


@router.get("/task-views/events")
async def mobile_task_view_events(replay: bool = True) -> StreamingResponse:
    return StreamingResponse(
        stream_mobile_task_view_events(replay=replay),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/code-tasks")
async def mobile_code_tasks() -> dict:
    return {"tasks": await code_task_hub.list_tasks()}


@router.get("/code-tasks/{task_id}")
async def mobile_code_task_detail(task_id: str) -> dict:
    task = await code_task_hub.get_detail(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Code task not found")
    return {"task": task}


@router.get("/code-tasks/{task_id}/wait")
async def mobile_code_task_wait(
    task_id: str,
    afterSeq: int = 0,
    ignoreQuestionRequestId: str = "",
    timeoutSeconds: int = 180,
) -> dict:
    try:
        return await wait_for_code_task_result(
            task_id,
            after_seq=afterSeq,
            ignore_question_request_id=ignoreQuestionRequestId,
            timeout_seconds=timeoutSeconds,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error


@router.get("/code-tasks/{task_id}/events")
async def mobile_code_task_events(task_id: str, replay: bool = True) -> StreamingResponse:
    task = await code_task_hub.get_detail(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Code task not found")
    return StreamingResponse(
        stream_mobile_code_task_events(task_id, replay=replay),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/code-tasks/{task_id}/messages")
async def mobile_code_task_message(task_id: str, request: CodeTaskRemoteMessageRequest) -> dict:
    try:
        return await send_remote_code_task_message(task_id, request)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error


@router.post("/code-tasks/{task_id}/question/reply")
async def mobile_code_task_question_reply(task_id: str, request: CodeTaskMobileQuestionReplyRequest) -> dict:
    try:
        return await reply_code_task_question_by_task(task_id, request)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error


@router.post("/bridge/task")
async def mobile_bridge_task(request: Request) -> dict:
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("WebSocket fallback payload must be an object.")
        return await handle_mobile_bridge_task_command(data)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error


@router.post("/bridge/question/reply")
async def mobile_bridge_question_reply(request: Request) -> dict:
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("WebSocket fallback payload must be an object.")
        return await handle_mobile_bridge_question_reply(data)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error) or error.__class__.__name__) from error


# ---------------------------------------------------------------------------
# WebSocket endpoints
# ---------------------------------------------------------------------------


@router.websocket("/code-tasks/{task_id}/ws")
async def mobile_code_task_websocket(websocket: WebSocket, task_id: str) -> None:
    await websocket.accept()
    send_lock = asyncio.Lock()
    task = await code_task_hub.get_detail(task_id)
    if task is None:
        async with send_lock:
            await websocket.send_json({"type": "error", "message": "Code task not found"})
        await websocket.close(code=1008)
        return
    async with send_lock:
        await websocket.send_json({"type": "task", "task": task})

    sender = asyncio.create_task(send_mobile_ws_events(websocket, task_id, send_lock))
    receiver = asyncio.create_task(receive_mobile_ws_commands(websocket, task_id, send_lock))
    done, pending = await asyncio.wait(
        {sender, receiver},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task_item in pending:
        task_item.cancel()
    for task_item in done:
        try:
            await task_item
        except WebSocketDisconnect:
            pass
        except Exception:
            pass


@router.websocket("/bridge/ws")
async def mobile_bridge_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    client = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
    _log.info("[mobile-bridge] client connected %s", client)
    send_lock = asyncio.Lock()
    async with send_lock:
        await websocket.send_json(
            {
                "type": "hello",
                "protocolVersion": 2,
                "serverTime": utc_now_iso(),
                "message": "Amadeus mobile bridge connected.",
                "commands": ["task", "question_reply", "ping"],
                "events": ["task_view", "ack", "error", "pong"],
            }
        )

    sender = asyncio.create_task(send_mobile_bridge_views(websocket, send_lock))
    receiver = asyncio.create_task(receive_mobile_bridge_commands(websocket, send_lock))
    done, pending = await asyncio.wait(
        {sender, receiver},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task_item in pending:
        task_item.cancel()
    for task_item in done:
        try:
            await task_item
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
    _log.info("[mobile-bridge] client disconnected %s", client)


# ---------------------------------------------------------------------------
# Helper functions (extracted from main.py)
# ---------------------------------------------------------------------------


def describe_mobile_view_record(record: Any) -> str:
    raw_snapshot = getattr(record, "snapshot", {}) or {}
    snapshot = raw_snapshot if isinstance(raw_snapshot, dict) else {}
    raw_view = snapshot.get("view")
    raw_task = snapshot.get("task")
    view = raw_view if isinstance(raw_view, dict) else {}
    task = raw_task if isinstance(raw_task, dict) else {}
    kind = str(snapshot.get("kind") or "unknown")
    status = str(snapshot.get("status") or task.get("status") or "")
    running = snapshot.get("running")
    turn_count = 0
    event_count = 0
    if view:
        turn_count = int(view.get("turnCount") or 0)
        event_count = int(view.get("eventCount") or 0)
    task_id = str(getattr(record, "task_id", "") or snapshot.get("taskId") or "")
    return (
        f"seq={getattr(record, 'seq', '-')}"
        f" taskId={task_id or '-'}"
        f" kind={kind}"
        f" status={status or '-'}"
        f" running={running}"
        f" turns={turn_count}"
        f" events={event_count}"
    )


async def stream_mobile_code_task_events(task_id: str, *, replay: bool):
    task = await code_task_hub.get_detail(task_id)
    if task is not None:
        task_snapshot = dict(task)
        task_snapshot.pop("events", None)
        yield sse("task", {"task": task_snapshot})
    try:
        async for record in code_task_hub.subscribe(task_id, replay=replay):
            yield sse(record.event, record.payload)
    except KeyError:
        yield sse("error", {"message": "Code task not found", "taskId": task_id})


async def stream_code_task_events(*, replay: bool):
    async for record in code_task_hub.subscribe_all(replay=replay):
        yield sse(record.event, record.payload)


async def stream_mobile_task_view_events(*, replay: bool):
    async for record in code_task_hub.subscribe_view_snapshots(replay=replay):
        yield sse("task_view", record.to_dict())


async def send_mobile_bridge_views(websocket: WebSocket, send_lock: asyncio.Lock) -> None:
    async for record in code_task_hub.subscribe_view_snapshots(replay=True):
        async with send_lock:
            payload = record.to_dict()
            payload_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            payload_size = len(payload_text.encode("utf-8"))
            _log.debug("[mobile-bridge] send %s bytes=%s", describe_mobile_view_record(record), payload_size)
            await websocket.send_text(payload_text)


async def receive_mobile_bridge_commands(websocket: WebSocket, send_lock: asyncio.Lock) -> None:
    while True:
        try:
            data = await websocket.receive_json()
        except WebSocketDisconnect:
            raise
        except Exception as error:
            async with send_lock:
                await websocket.send_json({"type": "error", "message": str(error) or "Invalid websocket payload"})
            continue

        if not isinstance(data, dict):
            async with send_lock:
                await websocket.send_json({"type": "error", "message": "WebSocket payload must be an object."})
            continue

        command_type = str(data.get("type") or data.get("action") or "").strip()
        client_request_id = str(data.get("clientRequestId") or data.get("requestId") or "")
        try:
            if command_type in {"ping", "heartbeat"}:
                async with send_lock:
                    _log.debug("[mobile-bridge] pong clientRequestId=%s", client_request_id or '-')
                    await websocket.send_json(
                        {
                            "type": "pong",
                            "clientRequestId": client_request_id or None,
                            "serverTime": utc_now_iso(),
                        }
                    )
                continue

            if command_type in {"task", "send_task", "message", "prompt"}:
                result = await handle_mobile_bridge_task_command(data)
                async with send_lock:
                    await websocket.send_json(
                        {
                            "type": "ack",
                            "action": "task",
                            "clientRequestId": client_request_id or None,
                            "result": result,
                        }
                    )
                continue

            if command_type in {"question_reply", "answer_question", "option", "options"}:
                result = await handle_mobile_bridge_question_reply(data)
                async with send_lock:
                    await websocket.send_json(
                        {
                            "type": "ack",
                            "action": "question_reply",
                            "clientRequestId": client_request_id or None,
                            "result": result,
                        }
                    )
                continue

            async with send_lock:
                await websocket.send_json(
                    {
                        "type": "error",
                        "clientRequestId": client_request_id or None,
                        "message": "Unsupported command type. Use task or question_reply.",
                    }
                )
        except Exception as error:
            async with send_lock:
                await websocket.send_json(
                    {
                        "type": "error",
                        "clientRequestId": client_request_id or None,
                        "message": str(error) or error.__class__.__name__,
                    }
                )


async def handle_mobile_bridge_task_command(data: dict[str, Any]) -> dict[str, Any]:
    task_id = str(data.get("taskId") or data.get("task_id") or "").strip()
    prompt = str(data.get("prompt") or data.get("text") or data.get("intent") or "").strip()
    if not prompt:
        raise ValueError("任务内容不能为空。")

    task = await code_task_hub.get_summary(task_id) if task_id else None
    if task_id and task is not None:
        return await send_remote_code_task_message(
            task_id,
            CodeTaskRemoteMessageRequest(
                prompt=prompt,
                title=str(data.get("title") or ""),
                agent=str(data.get("agent") or ""),
                providerId=str(data.get("providerId") or data.get("provider_id") or ""),
                modelId=str(data.get("modelId") or data.get("model_id") or ""),
                autoApprove=parse_bool(data.get("autoApprove") if "autoApprove" in data else data.get("auto_approve")),
                timeoutSeconds=parse_int(data.get("timeoutSeconds") or data.get("timeout_seconds"), 1800),
            ),
        )

    next_task_id = task_id or uuid4().hex
    workspace_path = str(data.get("workspacePath") or data.get("workspace_path") or "")
    title = str(data.get("title") or "").strip() or prompt.replace("\n", " ")[:32] or "OpenCode 任务"
    await code_task_hub.ensure_task(
        task_id=next_task_id,
        title=title,
        prompt=prompt,
        workspace_path=workspace_path,
        status="queued",
        running=True,
        session_id=str(data.get("sessionId") or data.get("session_id") or ""),
    )
    request = CodeTaskStreamRequest(
        taskId=next_task_id,
        title=title,
        prompt=prompt,
        workspacePath=workspace_path,
        agent=str(data.get("agent") or ""),
        providerId=str(data.get("providerId") or data.get("provider_id") or ""),
        modelId=str(data.get("modelId") or data.get("model_id") or ""),
        sessionId=str(data.get("sessionId") or data.get("session_id") or ""),
        source="mobile",
        autoApprove=parse_bool(data.get("autoApprove") if "autoApprove" in data else data.get("auto_approve")),
        timeoutSeconds=parse_int(data.get("timeoutSeconds") or data.get("timeout_seconds"), 1800),
    )
    background_task = asyncio.create_task(drain_opencode_task(request))
    await code_task_hub.attach_background_task(next_task_id, background_task)
    return {
        "ok": True,
        "taskId": next_task_id,
        "mode": "background",
        "summary": "已在主机端创建并启动 OpenCode 任务。",
    }


async def handle_mobile_bridge_question_reply(data: dict[str, Any]) -> dict[str, Any]:
    task_id = str(data.get("taskId") or data.get("task_id") or "").strip()
    if not task_id:
        raise ValueError("question_reply 缺少 taskId。")
    return await reply_code_task_question_by_task(
        task_id,
        CodeTaskMobileQuestionReplyRequest(
            requestId=str(data.get("requestId") or data.get("request_id") or ""),
            answers=normalize_mobile_question_answers(data),
            v2=parse_bool(data.get("v2"), default=True),
            reject=parse_bool(data.get("reject")),
        ),
    )


def normalize_mobile_question_answers(data: dict[str, Any]) -> list[list[str]]:
    answers = data.get("answers")
    if isinstance(answers, list):
        normalized: list[list[str]] = []
        for answer in answers:
            if isinstance(answer, list):
                normalized.append([str(item).strip() for item in answer if str(item).strip()])
            elif str(answer).strip():
                normalized.append([str(answer).strip()])
        return normalized
    answer = data.get("answer")
    if isinstance(answer, list):
        return [[str(item).strip() for item in answer if str(item).strip()]]
    if str(answer or "").strip():
        return [[str(answer).strip()]]
    return []


async def send_mobile_ws_events(websocket: WebSocket, task_id: str, send_lock: asyncio.Lock) -> None:
    async for record in code_task_hub.subscribe(task_id, replay=False):
        async with send_lock:
            await websocket.send_json(
                {
                    "type": "event",
                    "event": record.event,
                    "payload": record.payload,
                    "seq": record.seq,
                    "createdAt": record.created_at,
                }
            )


async def receive_mobile_ws_commands(websocket: WebSocket, task_id: str, send_lock: asyncio.Lock) -> None:
    while True:
        try:
            data = await websocket.receive_json()
        except WebSocketDisconnect:
            raise
        except Exception as error:
            async with send_lock:
                await websocket.send_json({"type": "error", "message": str(error) or "Invalid websocket payload"})
            continue

        if not isinstance(data, dict):
            async with send_lock:
                await websocket.send_json({"type": "error", "message": "WebSocket payload must be an object."})
            continue

        command_type = str(data.get("type") or data.get("action") or "").strip()
        try:
            if command_type in {"message", "prompt", "send_message"}:
                result = await send_remote_code_task_message(
                    task_id,
                    CodeTaskRemoteMessageRequest(
                        prompt=str(data.get("prompt") or data.get("text") or ""),
                        title=str(data.get("title") or ""),
                        agent=str(data.get("agent") or ""),
                        providerId=str(data.get("providerId") or data.get("provider_id") or ""),
                        modelId=str(data.get("modelId") or data.get("model_id") or ""),
                        autoApprove=parse_bool(data.get("autoApprove") if "autoApprove" in data else data.get("auto_approve")),
                        timeoutSeconds=parse_int(data.get("timeoutSeconds") or data.get("timeout_seconds"), 1800),
                    ),
                )
                async with send_lock:
                    await websocket.send_json({"type": "ack", "action": "message", "result": result})
                continue

            if command_type in {"question_reply", "answer_question"}:
                result = await reply_code_task_question_by_task(
                    task_id,
                    CodeTaskMobileQuestionReplyRequest(
                        requestId=str(data.get("requestId") or data.get("request_id") or ""),
                        answers=data.get("answers") if isinstance(data.get("answers"), list) else [],
                        v2=parse_bool(data.get("v2"), default=True),
                        reject=parse_bool(data.get("reject")),
                    ),
                )
                async with send_lock:
                    await websocket.send_json({"type": "ack", "action": "question_reply", "result": result})
                continue

            async with send_lock:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": "Unsupported command type. Use message or question_reply.",
                    }
                )
        except Exception as error:
            async with send_lock:
                await websocket.send_json({"type": "error", "message": str(error) or error.__class__.__name__})