from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
from asyncio.subprocess import DEVNULL, Process
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from ..domain import CodeTaskStreamRequest


ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_TASK_TIMEOUT_SECONDS = 1800
SERVER_START_TIMEOUT_SECONDS = 20


def sse_event(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def stream_opencode_task(request: CodeTaskStreamRequest) -> AsyncIterator[str]:
    workspace = resolve_code_workspace(request.workspace_path)
    prompt = request.prompt.strip()
    if not prompt:
        yield sse_event("error", {"message": "任务内容不能为空。"})
        return

    command = find_opencode_command()
    if not command:
        yield sse_event("error", {"message": "未找到 opencode 命令，请先安装 OpenCode 并确认 opencode 在 PATH 中。"})
        return

    port = find_free_port()
    server_url = f"http://127.0.0.1:{port}"
    process: Process | None = None
    timeout_seconds = clamp_timeout(request.timeout_seconds)

    try:
        yield sse_event(
            "status",
            {
                "status": "starting",
                "message": "正在启动 OpenCode server。",
                "workspacePath": str(workspace),
            },
        )
        process = await asyncio.create_subprocess_exec(
            command,
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
            cwd=str(workspace),
            stdin=DEVNULL,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
        await wait_for_opencode_health(server_url)

        async with httpx.AsyncClient(base_url=server_url, timeout=None) as client:
            session_id = request.session_id.strip()
            if not session_id:
                session = await create_opencode_session(client, request)
                session_id = str(session.get("id") or "")
                if not session_id:
                    raise RuntimeError("OpenCode 未返回 session id。")

            yield sse_event(
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
            yield sse_event(
                "status",
                {
                    "status": "running",
                    "message": "OpenCode 已接收任务。",
                    "sessionId": session_id,
                },
            )

            finished = False
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            while not finished:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    yield sse_event(
                        "error",
                        {
                            "message": f"OpenCode 任务超过 {timeout_seconds} 秒仍未完成，已停止等待。",
                            "sessionId": session_id,
                        },
                    )
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=min(remaining, 1.0))
                except TimeoutError:
                    if process.returncode is not None:
                        yield sse_event(
                            "error",
                            {
                                "message": f"OpenCode server 已退出，退出码：{process.returncode}。",
                                "sessionId": session_id,
                            },
                        )
                        break
                    continue

                event_name = str(item.get("event") or "log")
                payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
                yield sse_event(event_name, payload)
                if event_name == "done":
                    finished = True

            reader.cancel()
            await cancel_task(reader)
            if not finished:
                yield sse_event(
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
        yield sse_event("error", {"message": str(error) or error.__class__.__name__})
    finally:
        if process is not None:
            await stop_process(process)


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


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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
        diff = properties.get("diff")
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

    return None


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
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for item in value[:12]:
        if not isinstance(item, dict):
            output.append({"summary": truncate(str(item), 300)})
            continue
        summary: dict[str, Any] = {}
        for key in ("file", "path", "oldPath", "newPath", "status", "type"):
            if item.get(key):
                summary[key] = truncate(str(item[key]), 300)
        if "additions" in item:
            summary["additions"] = item.get("additions")
        if "deletions" in item:
            summary["deletions"] = item.get("deletions")
        if not summary:
            summary["summary"] = truncate(compact_json(item), 500)
        output.append(summary)
    if len(value) > len(output):
        output.append({"summary": f"还有 {len(value) - len(output)} 个 diff 条目未显示。"})
    return output


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


async def stop_process(process: Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=4)
    except TimeoutError:
        process.kill()
        await process.wait()
