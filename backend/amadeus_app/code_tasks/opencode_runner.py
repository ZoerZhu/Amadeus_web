from __future__ import annotations

import asyncio
import json
import os
import signal
import shutil
import socket
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from uuid import uuid4

import httpx

from ..domain import (
    CodeTaskMobileQuestionReplyRequest,
    CodeTaskQuestionReplyRequest,
    CodeTaskRemoteMessageRequest,
    CodeTaskStreamRequest,
)
from .task_hub import code_task_hub


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_TASK_TIMEOUT_SECONDS = 1800
SERVER_START_TIMEOUT_SECONDS = 20
OPENCODE_IDLE_GRACE_SECONDS = int(os.getenv("AMADEUS_OPENCODE_IDLE_GRACE_SECONDS", "25"))
ACTIVE_OPENCODE_PROCESSES: set[subprocess.Popen] = set()
CODE_TASK_WAIT_POLL_SECONDS = 2.0
CODE_TASK_WAIT_MAX_TIMEOUT_SECONDS = 600
CODE_TASK_TERMINAL_STATUSES = {
    "idle",
    "done",
    "completed",
    "complete",
    "finished",
    "success",
    "succeeded",
    "stopped",
    "cancelled",
    "canceled",
    "error",
    "failed",
    "failure",
}


def sse_event(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def publish_task_event(task_id: str, event: str, payload: dict[str, Any]) -> str:
    record = await code_task_hub.publish(task_id, event, payload)
    return sse_event(record.event, record.payload)


async def stream_opencode_task(request: CodeTaskStreamRequest) -> AsyncIterator[str]:
    task_id = request.task_id.strip() or uuid4().hex
    workspace = resolve_code_workspace(request.workspace_path)
    prompt = request.prompt.strip()
    final_status = "stopped"
    if not prompt:
        await code_task_hub.ensure_task(
            task_id=task_id,
            title=request.title or "OpenCode 任务",
            prompt="",
            workspace_path=str(workspace),
            status="error",
            running=False,
            session_id=request.session_id.strip(),
        )
        yield await publish_task_event(task_id, "error", {"message": "任务内容不能为空。"})
        await code_task_hub.finish_task(task_id, "error")
        return

    await code_task_hub.ensure_task(
        task_id=task_id,
        title=request.title or "OpenCode 任务",
        prompt=prompt,
        workspace_path=str(workspace),
        status="connecting",
        running=True,
        session_id=request.session_id.strip(),
    )
    yield await publish_task_event(
        task_id,
        "input",
        {
            "text": prompt,
            "source": request.source.strip() or "host",
            "workspacePath": str(workspace),
            "message": "手机端发送了任务推进消息。" if request.source == "mobile" else "主机端发送了任务请求。",
        },
    )

    command = find_opencode_command()
    if not command:
        final_status = "error"
        yield await publish_task_event(
            task_id,
            "error",
            {"message": "未找到 opencode 命令，请先安装 OpenCode 并确认 opencode 在 PATH 中。"},
        )
        await code_task_hub.finish_task(task_id, "error")
        return

    port = find_free_port()
    server_url = f"http://127.0.0.1:{port}"
    process: subprocess.Popen | None = None
    timeout_seconds = clamp_timeout(request.timeout_seconds)

    try:
        yield await publish_task_event(
            task_id,
            "status",
            {
                "status": "starting",
                "message": "正在启动 OpenCode server。",
                "workspacePath": str(workspace),
            },
        )
        process = start_opencode_process(command=command, workspace=workspace, port=port)
        ACTIVE_OPENCODE_PROCESSES.add(process)
        await wait_for_opencode_health(server_url)

        async with httpx.AsyncClient(base_url=server_url, timeout=None) as client:
            session_id = request.session_id.strip()
            if not session_id:
                session = await create_opencode_session(client, request)
                session_id = str(session.get("id") or "")
                if not session_id:
                    raise RuntimeError("OpenCode 未返回 session id。")

            yield await publish_task_event(
                task_id,
                "session",
                {
                    "sessionId": session_id,
                    "serverUrl": server_url,
                    "workspacePath": str(workspace),
                    "title": request.title or "OpenCode 任务",
                    "resumed": bool(request.session_id.strip()),
                },
            )

            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            reader = asyncio.create_task(
                read_opencode_events(
                    client,
                    session_id=session_id,
                    auto_approve=request.auto_approve,
                    queue=queue,
                )
            )

            await send_opencode_prompt(client, session_id=session_id, request=request)
            yield await publish_task_event(
                task_id,
                "status",
                {
                    "status": "running",
                    "message": "OpenCode 已接收任务。",
                    "sessionId": session_id,
                },
            )

            finished = False
            last_activity = asyncio.get_running_loop().time()
            saw_completion_signal = False
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            while not finished:
                now = asyncio.get_running_loop().time()
                remaining = deadline - now
                if remaining <= 0:
                    final_status = "error"
                    yield await publish_task_event(
                        task_id,
                        "error",
                        {
                            "message": f"OpenCode 任务超过 {timeout_seconds} 秒仍未完成，已停止等待。",
                            "sessionId": session_id,
                        },
                    )
                    break
                if saw_completion_signal and now - last_activity >= OPENCODE_IDLE_GRACE_SECONDS:
                    final_status = "idle"
                    yield await publish_task_event(
                        task_id,
                        "done",
                        {
                            "status": "idle",
                            "message": "OpenCode 已完成任务并进入空闲等待。",
                            "sessionId": session_id,
                            "workspacePath": str(workspace),
                        },
                    )
                    finished = True
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=min(remaining, 1.0))
                except TimeoutError:
                    exit_code = process.poll()
                    if exit_code is not None:
                        final_status = "error"
                        yield await publish_task_event(
                            task_id,
                            "error",
                            {
                                "message": f"OpenCode server 已退出，退出码：{exit_code}。",
                                "sessionId": session_id,
                            },
                        )
                        break
                    continue

                event_name = str(item.get("event") or "log")
                payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                payload = dict(payload)
                payload.setdefault("sessionId", session_id)
                payload.setdefault("serverUrl", server_url)
                payload.setdefault("workspacePath", str(workspace))
                last_activity = asyncio.get_running_loop().time()
                if is_completion_signal(event_name, payload):
                    saw_completion_signal = True
                yield await publish_task_event(task_id, event_name, payload)
                if event_name == "done":
                    final_status = str(payload.get("status") or "done")
                    finished = True

            reader.cancel()
            await cancel_task(reader)
            if not finished:
                yield await publish_task_event(
                    task_id,
                    "done",
                    {
                        "status": "stopped",
                        "sessionId": session_id,
                        "workspacePath": str(workspace),
                    },
                )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        final_status = "error"
        yield await publish_task_event(task_id, "error", {"message": str(error) or error.__class__.__name__})
    finally:
        await code_task_hub.finish_task(task_id, final_status)
        if process is not None:
            try:
                await stop_process(process)
            finally:
                ACTIVE_OPENCODE_PROCESSES.discard(process)


def resolve_code_workspace(value: str) -> Path:
    raw = value.strip()
    if not raw:
        raw = os.getenv("AMADEUS_WORKSPACE_PATH", "").strip() or str(ROOT_DIR)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = ROOT_DIR / candidate
    resolved = candidate.resolve()
    if not resolved.exists():
        raise ValueError(f"OpenCode 工作目录不存在：{resolved}")
    if not resolved.is_dir():
        raise ValueError(f"OpenCode 工作目录必须是文件夹：{resolved}")
    return resolved


def find_opencode_command() -> str:
    configured = os.getenv("AMADEUS_OPENCODE_COMMAND", "").strip()
    if configured:
        return configured
    return shutil.which("opencode.cmd") or shutil.which("opencode") or ""


def start_opencode_process(*, command: str, workspace: Path, port: int) -> subprocess.Popen:
    creationflags = 0
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    return subprocess.Popen(
        [
            command,
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(workspace),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        **popen_kwargs,
    )


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def is_completion_signal(event_name: str, payload: dict[str, Any]) -> bool:
    if event_name in {"output", "file", "diff"}:
        return True
    if event_name in {"command", "tool"}:
        return str(payload.get("status") or "").lower() in {"completed", "success", "failed"}
    return False


async def wait_for_opencode_health(server_url: str) -> None:
    deadline = asyncio.get_running_loop().time() + SERVER_START_TIMEOUT_SECONDS
    last_error = ""
    async with httpx.AsyncClient(timeout=2) as client:
        while asyncio.get_running_loop().time() < deadline:
            try:
                response = await client.get(f"{server_url}/global/health")
                if response.status_code == 200 and response.json().get("healthy") is True:
                    return
                last_error = f"HTTP {response.status_code}"
            except Exception as error:
                last_error = str(error) or error.__class__.__name__
            await asyncio.sleep(0.35)
    raise RuntimeError(f"OpenCode server 启动超时：{last_error}")


async def create_opencode_session(client: httpx.AsyncClient, request: CodeTaskStreamRequest) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if request.title.strip():
        body["title"] = request.title.strip()
    if request.agent.strip():
        body["agent"] = request.agent.strip()
    if request.provider_id.strip() and request.model_id.strip():
        body["model"] = {
            "providerID": request.provider_id.strip(),
            "id": request.model_id.strip(),
        }
    response = await client.post("/session", json=body)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict):
        return data
    raise RuntimeError("OpenCode session 响应格式异常。")


async def send_opencode_prompt(client: httpx.AsyncClient, *, session_id: str, request: CodeTaskStreamRequest) -> None:
    body: dict[str, Any] = {
        "parts": [
            {
                "type": "text",
                "text": request.prompt.strip(),
            }
        ]
    }
    if request.agent.strip():
        body["agent"] = request.agent.strip()
    if request.provider_id.strip() and request.model_id.strip():
        body["model"] = {
            "providerID": request.provider_id.strip(),
            "modelID": request.model_id.strip(),
        }
    response = await client.post(f"/session/{quote(session_id)}/prompt_async", json=body)
    response.raise_for_status()


async def read_opencode_events(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    auto_approve: bool,
    queue: asyncio.Queue[dict[str, Any]],
) -> None:
    buffer: list[str] = []
    async with client.stream("GET", "/event") as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line:
                await process_sse_block(
                    buffer,
                    client=client,
                    session_id=session_id,
                    auto_approve=auto_approve,
                    queue=queue,
                )
                buffer = []
            else:
                buffer.append(line)


async def process_sse_block(
    lines: list[str],
    *,
    client: httpx.AsyncClient,
    session_id: str,
    auto_approve: bool,
    queue: asyncio.Queue[dict[str, Any]],
) -> None:
    data_lines = [line[5:].strip() for line in lines if line.startswith("data:")]
    if not data_lines:
        return
    try:
        raw_event = json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return
    if not isinstance(raw_event, dict):
        return
    properties = raw_event.get("properties")
    if not isinstance(properties, dict):
        return
    event_session_id = str(properties.get("sessionID") or "")
    if event_session_id and event_session_id != session_id:
        return
    mapped = map_opencode_event(raw_event)
    if mapped is not None:
        await queue.put(mapped)
    if raw_event.get("type") in {"permission.asked", "permission.v2.asked"}:
        await handle_permission_event(
            client,
            session_id=session_id,
            event=raw_event,
            auto_approve=auto_approve,
            queue=queue,
        )


def map_opencode_event(event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(event.get("type") or "")
    properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}

    if event_type in {"session.next.text.delta", "message.part.delta"}:
        delta = str(properties.get("delta") or "")
        if delta:
            return {"event": "output", "payload": {"text": delta}}
        return None

    if event_type == "session.next.reasoning.delta":
        return None

    if event_type == "session.next.tool.called":
        tool = str(properties.get("tool") or "tool")
        return {
            "event": "tool",
            "payload": {
                "status": "started",
                "name": tool,
                "message": f"调用工具 {tool}",
                "detail": compact_json(properties.get("input")),
            },
        }

    if event_type == "session.next.tool.progress":
        return {
            "event": "tool",
            "payload": {
                "status": "running",
                "name": str(properties.get("tool") or "tool"),
                "message": str(properties.get("message") or "工具执行中。"),
            },
        }

    if event_type == "session.next.tool.success":
        return {
            "event": "tool",
            "payload": {
                "status": "completed",
                "name": str(properties.get("tool") or "tool"),
                "message": "工具调用完成。",
                "detail": summarize_tool_result(properties),
            },
        }

    if event_type == "session.next.tool.failed":
        return {
            "event": "tool",
            "payload": {
                "status": "failed",
                "name": str(properties.get("tool") or "tool"),
                "message": str(properties.get("error") or "工具调用失败。"),
            },
        }

    if event_type == "session.next.shell.started":
        command = str(properties.get("command") or "")
        return {
            "event": "command",
            "payload": {
                "status": "started",
                "command": command,
                "message": f"执行命令：{command}" if command else "执行命令。",
            },
        }

    if event_type == "session.next.shell.ended":
        output = str(properties.get("output") or "")
        return {
            "event": "command",
            "payload": {
                "status": "completed",
                "message": "命令执行完成。",
                "output": truncate(output, 2200),
            },
        }

    if event_type in {"file.edited", "file.watcher.updated"}:
        file_path = str(properties.get("file") or "")
        if file_path:
            return {
                "event": "file",
                "payload": {
                    "path": file_path,
                    "message": f"文件变更：{file_path}",
                },
            }

    if event_type == "session.diff":
        diff = first_present(properties, "diff", "files", "changes", "patches", "edits", "entries")
        if diff is None:
            diff = properties
        return {
            "event": "diff",
            "payload": {
                "message": "OpenCode 生成了文件差异。",
                "diff": summarize_diff(diff),
            },
        }

    if event_type == "session.status":
        status = properties.get("status")
        status_type = status.get("type") if isinstance(status, dict) else str(status or "")
        return {
            "event": "status",
            "payload": {
                "status": status_type or "running",
                "message": f"OpenCode 状态：{status_type or 'running'}",
            },
        }

    if event_type == "session.idle":
        return {
            "event": "done",
            "payload": {
                "status": "idle",
                "message": "OpenCode 任务已结束。",
            },
        }

    if event_type == "session.error":
        return {
            "event": "error",
            "payload": {
                "message": stringify_error(properties.get("error") or properties),
            },
        }

    if event_type in {"permission.asked", "permission.v2.asked"}:
        request_id = str(properties.get("id") or "")
        return {
            "event": "permission",
            "payload": {
                "requestId": request_id,
                "action": str(properties.get("action") or properties.get("permission") or ""),
                "resources": properties.get("resources") or properties.get("patterns") or [],
                "message": "OpenCode 请求权限。",
            },
        }

    if event_type in {"question.asked", "question.v2.asked"}:
        request_id = str(properties.get("id") or "")
        questions = normalize_question_items(properties.get("questions"))
        return {
            "event": "question",
            "payload": {
                "requestId": request_id,
                "sessionId": str(properties.get("sessionID") or ""),
                "questions": questions,
                "message": "OpenCode 请求你选择或补充信息。",
                "tool": properties.get("tool") if isinstance(properties.get("tool"), dict) else None,
                "v2": event_type == "question.v2.asked",
            },
        }

    if event_type in {"question.replied", "question.v2.replied"}:
        answers = properties.get("answers")
        return {
            "event": "question_result",
            "payload": {
                "requestId": str(properties.get("requestID") or ""),
                "message": "OpenCode 已收到选择。",
                "answers": answers if isinstance(answers, list) else [],
            },
        }

    if event_type in {"question.rejected", "question.v2.rejected"}:
        return {
            "event": "question_result",
            "payload": {
                "requestId": str(properties.get("requestID") or ""),
                "message": "已拒绝 OpenCode 的选择请求。",
                "rejected": True,
            },
        }

    return None


async def reply_opencode_question(request: CodeTaskQuestionReplyRequest) -> dict[str, Any]:
    server_url = normalize_local_server_url(request.server_url)
    session_id = request.session_id.strip()
    request_id = request.request_id.strip()
    if not session_id:
        raise ValueError("OpenCode question 缺少 sessionId。")
    if not request_id:
        raise ValueError("OpenCode question 缺少 requestId。")

    answers = normalize_question_answers(request.answers)
    async with httpx.AsyncClient(base_url=server_url, timeout=15) as client:
        if request.reject:
            await post_first_success(
                client,
                [
                    f"/api/session/{quote(session_id)}/question/request/{quote(request_id)}/reject",
                    f"/question/{quote(request_id)}/reject",
                ]
                if request.v2
                else [
                    f"/question/{quote(request_id)}/reject",
                    f"/api/session/{quote(session_id)}/question/request/{quote(request_id)}/reject",
                ],
            )
            return {"ok": True, "rejected": True}

        if not answers:
            raise ValueError("OpenCode question 至少需要一个回答。")
        await post_first_success(
            client,
            [
                f"/api/session/{quote(session_id)}/question/request/{quote(request_id)}/reply",
                f"/question/{quote(request_id)}/reply",
            ]
            if request.v2
            else [
                f"/question/{quote(request_id)}/reply",
                f"/api/session/{quote(session_id)}/question/request/{quote(request_id)}/reply",
            ],
            json_body={"answers": answers},
        )
        return {"ok": True, "answers": answers}


async def send_remote_code_task_message(
    task_id: str,
    request: CodeTaskRemoteMessageRequest,
) -> dict[str, Any]:
    task = await code_task_hub.get_summary(task_id)
    if task is None:
        raise LookupError("Code task not found")
    prompt = request.prompt.strip()
    if not prompt:
        raise ValueError("远程任务推进消息不能为空。")

    session_id = str(task.get("sessionId") or "").strip()
    server_url = str(task.get("serverUrl") or "").strip()
    running = bool(task.get("running"))
    if running and (not server_url or not session_id):
        raise RuntimeError("任务正在启动 OpenCode 会话，稍后再发送远程消息。")
    if running and server_url and session_id:
        await code_task_hub.publish(
            task_id,
            "input",
            {
                "text": prompt,
                "source": "mobile",
                "message": "手机端发送了任务推进消息。",
            },
        )
        try:
            async with httpx.AsyncClient(base_url=normalize_local_server_url(server_url), timeout=20) as client:
                await send_opencode_prompt(
                    client,
                    session_id=session_id,
                    request=CodeTaskStreamRequest(
                        taskId=task_id,
                        title=str(task.get("title") or request.title or "OpenCode 任务"),
                        prompt=prompt,
                        workspacePath=str(task.get("workspacePath") or ""),
                        agent=request.agent,
                        providerId=request.provider_id,
                        modelId=request.model_id,
                        sessionId=session_id,
                        source="mobile",
                        autoApprove=request.auto_approve,
                        timeoutSeconds=request.timeout_seconds,
                    ),
                )
        except Exception as error:
            await code_task_hub.publish(
                task_id,
                "error",
                {"message": f"手机端任务推进发送失败：{str(error) or error.__class__.__name__}"},
            )
            raise
        return {
            "ok": True,
            "taskId": task_id,
            "mode": "live",
            "summary": "已把手机端消息发送到当前 OpenCode 会话。",
        }

    background = task.get("backgroundRunning")
    if background:
        raise RuntimeError("任务已有后台推进请求正在运行。")
    if not session_id:
        await code_task_hub.publish(
            task_id,
            "error",
            {"message": "该任务没有可继承的 OpenCode sessionId，无法作为已有任务继续。"},
        )
        raise RuntimeError("该任务没有可继承的 OpenCode sessionId，无法作为已有任务继续。")

    resumed_request = CodeTaskStreamRequest(
        taskId=task_id,
        title=request.title or str(task.get("title") or "OpenCode 任务"),
        prompt=prompt,
        workspacePath=str(task.get("workspacePath") or ""),
        agent=request.agent,
        providerId=request.provider_id,
        modelId=request.model_id,
        sessionId=session_id,
        source="mobile",
        autoApprove=request.auto_approve,
        timeoutSeconds=request.timeout_seconds,
    )
    background_task = asyncio.create_task(drain_opencode_task(resumed_request))
    await code_task_hub.attach_background_task(task_id, background_task)
    return {
        "ok": True,
        "taskId": task_id,
        "mode": "background",
        "summary": "已在主机端后台启动 OpenCode 任务推进。",
    }


async def reply_code_task_question_by_task(
    task_id: str,
    request: CodeTaskMobileQuestionReplyRequest,
) -> dict[str, Any]:
    task = await code_task_hub.get_detail(task_id)
    if task is None:
        raise LookupError("Code task not found")
    request_id = request.request_id.strip()
    server_url, session_id = resolve_code_task_question_target(task, request_id)
    if not server_url or not session_id:
        raise ValueError("该任务没有可用的 OpenCode serverUrl 或 sessionId。")
    result = await reply_opencode_question(
        CodeTaskQuestionReplyRequest(
            serverUrl=server_url,
            sessionId=session_id,
            requestId=request_id,
            answers=request.answers,
            v2=request.v2,
            reject=request.reject,
        )
    )
    await code_task_hub.publish(
        task_id,
        "question_result",
        {
            "requestId": request_id,
            "message": "手机端已提交 OpenCode 选择。",
            "answers": result.get("answers") if isinstance(result.get("answers"), list) else request.answers,
            "rejected": bool(result.get("rejected")),
            "sessionId": session_id,
            "serverUrl": server_url,
        },
    )
    if request.wait_for_result:
        wait_result = await wait_for_code_task_result(
            task_id,
            after_seq=request.after_seq,
            ignore_question_request_id=request_id,
            timeout_seconds=request.timeout_seconds,
        )
        result["task"] = wait_result.get("task")
        result["timedOut"] = bool(wait_result.get("timedOut"))
    return result


async def wait_for_code_task_result(
    task_id: str,
    *,
    after_seq: int = 0,
    ignore_question_request_id: str = "",
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    timeout = max(1, min(timeout_seconds, CODE_TASK_WAIT_MAX_TIMEOUT_SECONDS))
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        task = await code_task_hub.get_detail(task_id)
        if task is None:
            raise LookupError("Code task not found")
        if code_task_wait_target_reached(task, after_seq, ignore_question_request_id):
            return {"task": filter_code_task_ignored_question(task, ignore_question_request_id), "timedOut": False}

        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return {"task": filter_code_task_ignored_question(task, ignore_question_request_id), "timedOut": True}

        subscription = code_task_hub.subscribe(task_id, replay=False)
        try:
            try:
                await asyncio.wait_for(anext(subscription), timeout=min(remaining, CODE_TASK_WAIT_POLL_SECONDS))
            except TimeoutError:
                pass
        finally:
            await subscription.aclose()


def code_task_wait_target_reached(
    task: dict[str, Any],
    after_seq: int,
    ignore_question_request_id: str,
) -> bool:
    last_seq = int(task.get("eventCount") or 0)
    last_event = task.get("lastEvent") if isinstance(task.get("lastEvent"), dict) else None
    if isinstance(task.get("events"), list) and task["events"]:
        last_item = task["events"][-1]
        if isinstance(last_item, dict):
            last_event = last_item
            last_seq = int(last_item.get("seq") or last_seq)

    if after_seq > 0 and last_seq <= after_seq:
        return False

    if last_event is not None and str(last_event.get("event") or "") in {"done", "error"}:
        return True

    status = code_task_detail_status(task)
    running = bool(task.get("running")) or bool(task.get("backgroundRunning"))
    if is_code_task_terminal_status(status) and not running:
        return True

    return len(filtered_pending_questions(task, ignore_question_request_id)) > 0


def code_task_detail_status(task: dict[str, Any]) -> str:
    task_status = str(task.get("status") or "").strip()
    snapshot = task.get("snapshot") if isinstance(task.get("snapshot"), dict) else {}
    snapshot_status = str(snapshot.get("status") or "").strip()
    if not is_code_task_terminal_status(task_status) and is_code_task_terminal_status(snapshot_status):
        return snapshot_status
    return task_status or snapshot_status


def is_code_task_terminal_status(status: str) -> bool:
    return status.lower() in CODE_TASK_TERMINAL_STATUSES


def filtered_pending_questions(task: dict[str, Any], ignore_question_request_id: str) -> list[dict[str, Any]]:
    pending = task.get("pendingQuestions")
    if not isinstance(pending, list):
        return []
    ignored = ignore_question_request_id.strip()
    output: list[dict[str, Any]] = []
    for item in pending:
        if not isinstance(item, dict):
            continue
        request_id = str(item.get("requestId") or item.get("requestID") or item.get("id") or "").strip()
        if ignored and request_id == ignored:
            continue
        output.append(item)
    return output


def filter_code_task_ignored_question(task: dict[str, Any], ignore_question_request_id: str) -> dict[str, Any]:
    if not ignore_question_request_id.strip():
        return task
    filtered_questions = filtered_pending_questions(task, ignore_question_request_id)
    current_questions = task.get("pendingQuestions")
    if not isinstance(current_questions, list) or len(filtered_questions) == len(current_questions):
        return task
    output = dict(task)
    output["pendingQuestions"] = filtered_questions
    return output


async def drain_opencode_task(request: CodeTaskStreamRequest) -> None:
    task_id = request.task_id.strip()
    background_task = asyncio.current_task()
    try:
        async for _event in stream_opencode_task(request):
            pass
    except Exception as error:
        if task_id:
            await code_task_hub.publish(
                task_id,
                "error",
                {"message": str(error) or error.__class__.__name__},
            )
            await code_task_hub.finish_task(task_id, "error")
    finally:
        if task_id and background_task is not None:
            await code_task_hub.clear_background_task(task_id, background_task)


async def handle_permission_event(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    event: dict[str, Any],
    auto_approve: bool,
    queue: asyncio.Queue[dict[str, Any]],
) -> None:
    properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}
    request_id = str(properties.get("id") or "")
    if not request_id:
        return
    if not auto_approve:
        await queue.put(
            {
                "event": "permission",
                "payload": {
                    "requestId": request_id,
                    "message": "任务等待权限。请重新发起任务并启用“允许 OpenCode 修改/执行”，或在 OpenCode 配置中放行对应权限。",
                    "blocked": True,
                },
            }
        )
        return

    try:
        if event.get("type") == "permission.v2.asked":
            response = await client.post(
                f"/api/session/{quote(session_id)}/permission/request/{quote(request_id)}/reply",
                json={"reply": "once"},
            )
        else:
            response = await client.post(
                f"/permission/{quote(request_id)}/reply",
                json={"reply": "once", "message": "Approved by Amadeus code task runner."},
            )
            if response.status_code >= 400:
                response = await client.post(
                    f"/session/{quote(session_id)}/permissions/{quote(request_id)}",
                    json={"response": "once"},
                )
        response.raise_for_status()
        await queue.put(
            {
                "event": "permission",
                "payload": {
                    "requestId": request_id,
                    "message": "已批准本次 OpenCode 权限请求。",
                    "approved": True,
                },
            }
        )
    except Exception as error:
        await queue.put(
            {
                "event": "error",
                "payload": {
                    "message": f"OpenCode 权限批准失败：{str(error) or error.__class__.__name__}",
                    "requestId": request_id,
                },
            }
        )


def normalize_question_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    questions: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        options: list[dict[str, str]] = []
        raw_options = item.get("options")
        if isinstance(raw_options, list):
            for option in raw_options:
                if not isinstance(option, dict):
                    continue
                label = str(option.get("label") or "").strip()
                if not label:
                    continue
                options.append(
                    {
                        "label": label,
                        "description": str(option.get("description") or "").strip(),
                    }
                )
        question = str(item.get("question") or "").strip()
        header = str(item.get("header") or "").strip()
        if not question and not header:
            continue
        questions.append(
            {
                "question": question or header,
                "header": header or question[:30],
                "options": options,
                "multiple": bool(item.get("multiple")),
                "custom": bool(item.get("custom")),
            }
        )
    return questions


def normalize_question_answers(value: Any) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    answers: list[list[str]] = []
    for item in value:
        if isinstance(item, list):
            labels = [str(label).strip() for label in item if str(label).strip()]
        else:
            labels = [str(item).strip()] if str(item).strip() else []
        answers.append(labels)
    return answers


def clean_string(value: Any) -> str:
    return str(value or "").strip()


def question_request_id(value: dict[str, Any]) -> str:
    return clean_string(value.get("requestId") or value.get("requestID") or value.get("id"))


def session_context_from_payload(value: dict[str, Any]) -> tuple[str, str]:
    session_id = clean_string(value.get("sessionId") or value.get("sessionID") or value.get("session_id"))
    server_url = clean_string(value.get("serverUrl") or value.get("serverURL") or value.get("server_url"))
    return session_id, server_url


def resolve_code_task_question_target(task: dict[str, Any], request_id: str) -> tuple[str, str]:
    server_url = clean_string(task.get("serverUrl"))
    session_id = clean_string(task.get("sessionId"))
    question_server_url = ""
    question_session_id = ""

    def remember_question(candidate: Any) -> None:
        nonlocal question_server_url, question_session_id
        if not isinstance(candidate, dict):
            return
        if question_request_id(candidate) != request_id:
            return
        candidate_session_id, candidate_server_url = session_context_from_payload(candidate)
        question_session_id = candidate_session_id or question_session_id
        question_server_url = candidate_server_url or question_server_url

    pending_questions = task.get("pendingQuestions")
    if isinstance(pending_questions, list):
        for question in pending_questions:
            remember_question(question)

    snapshot = task.get("snapshot")
    if isinstance(snapshot, dict):
        snapshot_session_id, snapshot_server_url = session_context_from_payload(snapshot)
        session_id = session_id or snapshot_session_id
        server_url = server_url or snapshot_server_url
        messages = snapshot.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                if not isinstance(message, dict):
                    continue
                remember_question(message.get("question"))
                payload = message.get("payload")
                if isinstance(payload, dict):
                    remember_question(payload)

    matching_session_server_url = ""
    matching_session_id = ""
    latest_session_server_url = ""
    latest_session_id = ""
    events = task.get("events")
    event_list = events if isinstance(events, list) else []
    for event in reversed(event_list):
        if not isinstance(event, dict):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if clean_string(event.get("event")) == "question":
            remember_question(payload)

    for event in reversed(event_list):
        if isinstance(event, dict):
            payload = event.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            event_name = clean_string(event.get("event"))
        else:
            payload = {}
            event_name = ""
        if event_name != "session":
            continue
        event_session_id, event_server_url = session_context_from_payload(payload)
        if event_session_id and not latest_session_id:
            latest_session_id = event_session_id
        if event_server_url and not latest_session_server_url:
            latest_session_server_url = event_server_url
        if (
            event_server_url
            and not matching_session_server_url
            and (not question_session_id or not event_session_id or event_session_id == question_session_id)
        ):
            matching_session_server_url = event_server_url
            matching_session_id = event_session_id

    resolved_session_id = question_session_id or matching_session_id or latest_session_id or session_id
    resolved_server_url = question_server_url or matching_session_server_url or latest_session_server_url or server_url
    return resolved_server_url, resolved_session_id


def normalize_local_server_url(value: str) -> str:
    raw = value.strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme != "http":
        raise ValueError("OpenCode serverUrl 只允许 http。")
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("OpenCode serverUrl 只允许本机地址。")
    if not parsed.port:
        raise ValueError("OpenCode serverUrl 缺少端口。")
    return raw


async def post_first_success(
    client: httpx.AsyncClient,
    paths: list[str],
    *,
    json_body: dict[str, Any] | None = None,
) -> httpx.Response:
    errors: list[str] = []
    for path in paths:
        try:
            response = await client.post(path, json=json_body)
            if response.status_code < 400:
                return response
            errors.append(f"{path}: HTTP {response.status_code} {truncate(response.text, 300)}")
        except Exception as error:
            errors.append(f"{path}: {str(error) or error.__class__.__name__}")
    raise RuntimeError("OpenCode question 回复失败：" + "；".join(errors))


def compact_json(value: Any) -> str:
    if value is None:
        return ""
    try:
        return truncate(json.dumps(value, ensure_ascii=False, indent=2), 1600)
    except TypeError:
        return truncate(str(value), 1600)


def summarize_tool_result(properties: dict[str, Any]) -> str:
    content = properties.get("content")
    if isinstance(content, list) and content:
        parts = []
        for item in content[:3]:
            if isinstance(item, dict):
                text = item.get("text") or item.get("path") or item.get("type")
                if text:
                    parts.append(str(text))
        if parts:
            return truncate("\n".join(parts), 2000)
    if "result" in properties:
        return compact_json(properties.get("result"))
    return ""


def summarize_diff(value: Any) -> list[dict[str, Any]]:
    items = normalize_diff_items(value)
    if not items:
        return [{"summary": "OpenCode diff 事件未包含可解析的具体差异内容。"}]
    output: list[dict[str, Any]] = []
    for item in items[:12]:
        if not isinstance(item, dict):
            output.append({"summary": truncate(str(item), 300)})
            continue
        summary: dict[str, Any] = {}
        for key in ("file", "path", "filename", "name", "oldPath", "newPath", "status", "type", "kind"):
            if item.get(key):
                summary[key] = truncate(str(item[key]), 300)
        if "additions" in item:
            summary["additions"] = item.get("additions")
        if "deletions" in item:
            summary["deletions"] = item.get("deletions")
        for key in ("added", "removed", "modified"):
            if isinstance(item.get(key), (int, float, str)):
                summary[key] = item.get(key)
        patch = first_present(item, "patch", "diff", "changes", "content", "text")
        if patch is not None and not isinstance(patch, (list, dict)):
            summary["patch"] = truncate(str(patch), 1200)
        if not summary:
            summary["summary"] = truncate(compact_json(item), 500)
        output.append(summary)
    if len(items) > len(output):
        output.append({"summary": f"还有 {len(items) - len(output)} 个 diff 条目未显示。"})
    return output


def normalize_diff_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        return [{"patch": text}] if text else []
    if isinstance(value, dict):
        for key in ("diff", "files", "changes", "patches", "edits", "entries", "items"):
            nested = value.get(key)
            if nested is not None and nested is not value:
                items = normalize_diff_items(nested)
                if items:
                    return items
        mapped_items: list[dict[str, Any]] = []
        for key, nested in value.items():
            if isinstance(nested, dict):
                item = {"path": str(key), **nested}
                mapped_items.append(item)
            elif isinstance(nested, str) and nested.strip():
                mapped_items.append({"path": str(key), "patch": nested.strip()})
        if mapped_items:
            return mapped_items
        return [value]
    return [value]


def first_present(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in source:
            return source[key]
    return None


def stringify_error(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("message", "name", "type"):
            if value.get(key):
                return str(value[key])
        return compact_json(value)
    return str(value)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def clamp_timeout(value: int) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = DEFAULT_TASK_TIMEOUT_SECONDS
    return max(30, min(seconds, 7200))


async def cancel_task(task: asyncio.Task[Any]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        return
    except Exception:
        return


async def stop_process(process: subprocess.Popen) -> None:
    if os.name == "nt":
        await kill_windows_process_tree(process)
        return
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.to_thread(process.wait, 4)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        await asyncio.to_thread(process.wait)


async def kill_windows_process_tree(process: subprocess.Popen) -> None:
    if process.pid is None:
        return
    await asyncio.to_thread(
        subprocess.run,
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if process.poll() is None:
        try:
            await asyncio.to_thread(process.wait, 4)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                return
            await asyncio.to_thread(process.wait)


async def stop_all_opencode_processes() -> None:
    processes = list(ACTIVE_OPENCODE_PROCESSES)
    for process in processes:
        try:
            await stop_process(process)
        finally:
            ACTIVE_OPENCODE_PROCESSES.discard(process)
