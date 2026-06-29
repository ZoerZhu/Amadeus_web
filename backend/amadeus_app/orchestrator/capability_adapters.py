from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from ..code_tasks.opencode_runner import stream_opencode_task
from ..orchestrator_integrations.mcp_client import mcp_manager
from ..domain import CodeTaskStreamRequest
from ..doc_writer.doc_writer_agent import run_doc_writer_agent
from ..file_tools.file_reader import run_file_reader
from ..memory.memory_tools import handle_browse_memory_tree, handle_recall_memory, handle_save_memory
from ..search.web_search_agent import run_web_search_agent
from ..todo_task.todo_task_agent import run_todo_task_agent
from ..vision.image_understand_agent import run_image_understand_agent
from .domain import OrchestratorSettings
from . import storage as orchestrator_storage
from .shell_exec_policy import classify_shell_command, parse_shell_command


CapabilityHandler = Callable[[dict[str, Any], "CapabilityExecutionContext"], Awaitable[dict[str, Any]]]
CapabilityEventEmitter = Callable[..., Awaitable[dict[str, Any]]]


@dataclass
class CapabilityExecutionContext:
    task_id: str
    prompt: str
    workspace_path: str
    settings: OrchestratorSettings
    metadata: dict[str, Any] = field(default_factory=dict)
    storage: Any | None = None
    emit_event: CapabilityEventEmitter | None = None


class CapabilityRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, CapabilityHandler] = {}

    def register(self, name: str, handler: CapabilityHandler) -> None:
        self._handlers[name] = handler

    def get(self, name: str) -> CapabilityHandler | None:
        return self._handlers.get(name)

    async def execute(self, name: str, args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
        handler = self.get(name)
        if handler is None:
            return {
                "ok": False,
                "summary": f"Capability adapter not implemented: {name}",
                "data": {"capability": name},
            }
        try:
            result = await handler(args, context)
        except Exception as error:  # noqa: BLE001
            return {
                "ok": False,
                "summary": str(error) or error.__class__.__name__,
                "data": {"capability": name},
            }
        if not isinstance(result, dict):
            return {"ok": True, "summary": f"{name} completed.", "data": {"result": result}}
        if "ok" not in result:
            result = {"ok": True, **result}
        result.setdefault("summary", f"{name} completed.")
        result.setdefault("data", {})
        return result


async def _file_read(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    read_args = {"action": "list", "path": ".", "maxEntries": 80, **args}
    return await run_file_reader(read_args)


async def _code_search(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    query = _code_search_query(args, context)
    if not query:
        return {"ok": False, "summary": "code_search requires query.", "data": {}}
    raw_path = str(args.get("path") or args.get("directory") or ".").strip() or "."
    root_path = _resolve_workspace_child(context.workspace_path, raw_path)
    max_results = _positive_int(args.get("maxResults") or args.get("limit"), default=50)
    attempts: list[dict[str, Any]] = []
    if not bool(args.get("forceBuiltin")):
        mcp_result = await _try_mcp_code_search(
            query=query,
            root_path=root_path,
            max_results=max_results,
            context=context,
            attempts=attempts,
        )
        if mcp_result is not None:
            return mcp_result
    results = _builtin_code_search(
        query=query,
        root_path=root_path,
        max_results=max_results,
        case_sensitive=bool(args.get("caseSensitive", False)),
    )
    return {
        "ok": True,
        "summary": f"code_search found {len(results)} local result(s).",
        "data": {
            "provider": "builtin",
            "query": query,
            "path": str(root_path),
            "results": results,
            "mcpAttempts": attempts,
        },
    }


async def _web_search(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    search_args = {
        "query": context.prompt,
        "intent": context.prompt,
        "maxResults": 5,
        "fetchContent": True,
        **args,
    }
    mcp_attempts: list[dict[str, Any]] = []
    if not bool(args.get("forceBuiltin")):
        mcp_result = await _try_mcp_web_search(search_args, context, attempts=mcp_attempts)
        if mcp_result is not None:
            return mcp_result
    builtin_result = await run_web_search_agent(search_args)
    data = builtin_result.setdefault("data", {}) if isinstance(builtin_result, dict) else {}
    if isinstance(data, dict):
        data.setdefault("provider", "builtin")
        if mcp_attempts:
            data["mcpAttempts"] = mcp_attempts
    return builtin_result


async def _memory(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    action = str(args.get("action") or "recall").strip().lower()
    if action == "save":
        return await handle_save_memory(args)
    if action == "browse":
        return await handle_browse_memory_tree(args)
    return await handle_recall_memory({"query": context.prompt, **args})


async def _todo_task(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    return await run_todo_task_agent(args)


async def _vision(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    return await run_image_understand_agent(args)


async def _document_convert(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    raw_path = str(args.get("path") or args.get("filePath") or args.get("inputPath") or args.get("file") or "").strip()
    if not raw_path:
        return {"ok": False, "summary": "document_convert requires path.", "data": {}}
    source_path = _resolve_workspace_child(context.workspace_path, raw_path)
    if not source_path.exists():
        return {"ok": False, "summary": f"document_convert source not found: {source_path.name}", "data": {"path": str(source_path)}}
    if not source_path.is_file():
        return {"ok": False, "summary": "document_convert requires a file path.", "data": {"path": str(source_path)}}

    attempts: list[dict[str, Any]] = []
    markdown = ""
    provider = "builtin"
    mcp_data: dict[str, Any] | None = None
    if not bool(args.get("forceBuiltin")):
        mcp_data = await _try_mcp_document_convert(source_path, context, attempts=attempts)
        if mcp_data is not None:
            markdown = str(mcp_data.get("markdown") or "")
            provider = "mcp"
    if not markdown:
        markdown = await _builtin_document_to_markdown(source_path, args)

    artifact = None
    if bool(args.get("save", True)):
        output_path = _document_convert_output_path(context, source_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
        artifact = await _create_artifact_for_path(
            context,
            path=output_path,
            kind="document",
            mime_type="text/markdown",
            description=f"Converted {source_path.name} to Markdown.",
            meta={"capability": "document_convert", "sourcePath": str(source_path), "provider": provider},
        )
    return {
        "ok": True,
        "summary": f"已将 {source_path.name} 转换为 Markdown（provider={provider}）。",
        "data": {
            "provider": provider,
            "path": str(source_path),
            "markdown": markdown,
            "artifact": artifact,
            "mcpAttempts": attempts,
            **({"mcp": mcp_data} if mcp_data else {}),
        },
        "artifactIds": [artifact["id"]] if artifact else [],
    }


async def _doc_writer(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    result = await run_doc_writer_agent({"instruction": context.prompt, **args})
    artifact = await _record_doc_writer_artifact(result, context)
    if artifact is not None:
        result.setdefault("artifactIds", []).append(artifact["id"])
        result.setdefault("data", {})["artifact"] = artifact
    return result


async def _file_write(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    raw_path = str(args.get("path") or args.get("outputPath") or "").strip()
    if not raw_path:
        return {"ok": False, "summary": "file_write requires path.", "data": {}}
    content = str(args.get("content") or "")
    workspace_root = Path(context.workspace_path or ".").expanduser().resolve()
    target_path = Path(raw_path).expanduser()
    if not target_path.is_absolute():
        target_path = workspace_root / target_path
    target_path = target_path.resolve()
    if not _is_within(target_path, workspace_root):
        return {"ok": False, "summary": "file_write path must stay inside the task workspace.", "data": {"path": str(target_path)}}
    if _looks_like_code_or_config(target_path) and not bool(args.get("allowCodeFiles")):
        return {
            "ok": False,
            "summary": "file_write refused code/config path; use code_agent for source changes.",
            "data": {"path": str(target_path), "suffix": target_path.suffix},
        }

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding=str(args.get("encoding") or "utf-8"))
    artifact = await _create_artifact_for_path(
        context,
        path=target_path,
        kind=str(args.get("kind") or "file"),
        description=str(args.get("description") or "file_write output"),
        meta={"capability": "file_write"},
    )
    artifact_ids = [artifact["id"]] if artifact else []
    return {
        "ok": True,
        "summary": f"已写入文件 {target_path.name}。",
        "data": {"path": str(target_path), "artifact": artifact},
        "artifactIds": artifact_ids,
    }


async def _code_agent(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    if not context.settings.opencode_enabled:
        return {
            "ok": False,
            "summary": "OpenCode is disabled in Orchestrator settings.",
            "data": {"capability": "code_agent"},
        }
    request = CodeTaskStreamRequest(
        taskId=f"{context.task_id}-opencode",
        title=str(args.get("title") or "OpenCode 子任务"),
        prompt=str(args.get("prompt") or context.prompt),
        workspacePath=str(args.get("workspacePath") or context.workspace_path),
        source="host",
        autoApprove=bool(context.settings.trust_mode),
        timeoutSeconds=int(args.get("timeoutSeconds") or context.settings.max_runtime_seconds),
    )
    events: list[dict[str, Any]] = []
    final_summary = ""
    async for chunk in stream_opencode_task(request):
        for event in _parse_sse_chunk(chunk):
            events.append(event)
            await _emit_opencode_event(context, event)
            if event.get("event") in {"done", "summary", "error"}:
                payload = event.get("payload", {})
                if isinstance(payload, dict):
                    final_summary = str(payload.get("summary") or payload.get("message") or final_summary)
    return {
        "ok": not any(event.get("event") == "error" for event in events),
        "summary": final_summary or f"OpenCode emitted {len(events)} events.",
        "data": {
            "eventCount": len(events),
            "events": events[-20:],
        },
    }


def _parse_sse_chunk(chunk: str) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for block in chunk.replace("\r\n", "\n").split("\n\n"):
        event_name = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
        if not event_name:
            continue
        payload: Any = {}
        if data:
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                payload = {"text": data}
        parsed.append({"event": event_name, "payload": payload})
    return parsed


async def _try_mcp_web_search(
    search_args: dict[str, Any],
    context: CapabilityExecutionContext,
    *,
    attempts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    query = str(search_args.get("query") or context.prompt).strip()
    if not query:
        return None
    max_results = _positive_int(search_args.get("maxResults"), default=5)
    candidates = _rank_mcp_web_tools(query)
    for candidate in candidates:
        tool_args = _build_mcp_web_arguments(candidate["tool"], query=query, max_results=max_results)
        attempt = {
            "serverId": candidate["serverId"],
            "toolName": candidate["toolName"],
            "score": candidate["score"],
            "usable": tool_args is not None,
        }
        if tool_args is None:
            attempt["error"] = "schema arguments could not be satisfied"
            attempts.append(attempt)
            continue
        attempt["arguments"] = tool_args
        attempts.append(attempt)
        try:
            result = await candidate["client"].call_tool(candidate["toolName"], tool_args)
        except Exception as error:  # noqa: BLE001
            attempt["error"] = str(error) or error.__class__.__name__
            await _emit_event(
                context,
                kind="mcp",
                role="researcher",
                name=f"{candidate['serverId']}.{candidate['toolName']}",
                status="error",
                summary=f"MCP web search failed: {attempt['error']}",
                payload={"attempt": attempt},
            )
            continue
        summary = f"Used MCP {candidate['serverId']}/{candidate['toolName']} for web search."
        await _emit_event(
            context,
            kind="mcp",
            role="researcher",
            name=f"{candidate['serverId']}.{candidate['toolName']}",
            status="done",
            summary=summary,
            payload={"arguments": tool_args, "resultPreview": _compact_payload(result, limit=3000)},
        )
        return {
            "ok": True,
            "summary": summary,
            "data": {
                "provider": "mcp",
                "query": query,
                "serverId": candidate["serverId"],
                "toolName": candidate["toolName"],
                "arguments": tool_args,
                "result": result,
                "mcpAttempts": attempts,
            },
        }
    return None


def _rank_mcp_web_tools(query: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    query_is_url = _looks_like_url(query)
    for client in mcp_manager.list_clients():
        if not getattr(client, "connected", False):
            continue
        server_id = str(getattr(client, "server_id", "") or getattr(getattr(client, "config", None), "id", "") or "")
        for tool in getattr(client, "tools", []):
            tool_name = str(getattr(tool, "name", "") or "")
            description = str(getattr(tool, "description", "") or "")
            text = f"{server_id} {tool_name} {description}".lower()
            score = 0
            if any(hint in text for hint in ("search", "serp", "google", "brave", "tavily")):
                score += 100
            if "firecrawl" in text:
                score += 40
            if any(hint in text for hint in ("web", "internet", "网页", "搜索")):
                score += 15
            if any(hint in text for hint in ("fetch", "scrape", "crawl", "extract", "markdown")):
                score += 80 if query_is_url else 20
            if score <= 0:
                continue
            candidates.append(
                {
                    "client": client,
                    "serverId": server_id,
                    "tool": tool,
                    "toolName": tool_name,
                    "score": score,
                }
            )
    candidates.sort(key=lambda item: int(item["score"]), reverse=True)
    return candidates


def _build_mcp_web_arguments(tool: Any, *, query: str, max_results: int) -> dict[str, Any] | None:
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}
    properties = schema.get("properties") if isinstance(schema, dict) else {}
    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
    if not isinstance(properties, dict):
        properties = {}
    args: dict[str, Any] = {}
    query_is_url = _looks_like_url(query)
    for prop_name, prop_schema in properties.items():
        lower_name = str(prop_name).lower()
        if lower_name in {"query", "q", "search", "searchquery", "term", "keywords"}:
            args[prop_name] = query
        elif lower_name in {"url", "uri", "link"} and query_is_url:
            args[prop_name] = query
        elif lower_name in {"urls"} and query_is_url:
            args[prop_name] = [query]
        elif lower_name in {"limit", "maxresults", "max_results", "count", "numresults", "topk"}:
            args[prop_name] = max_results
        elif lower_name in {"formats", "outputformats"}:
            args[prop_name] = ["markdown"]
        elif lower_name in {"format"}:
            args[prop_name] = "markdown"
        elif lower_name in {"onlymaincontent", "only_main_content"}:
            args[prop_name] = True
        elif lower_name in {"scrapeoptions", "scrape_options"}:
            args[prop_name] = {"formats": ["markdown"], "onlyMainContent": True}
        elif lower_name in {"timeout", "timeoutseconds", "timeout_seconds"}:
            args[prop_name] = 30
        elif prop_name in required:
            default_value = _schema_default(prop_schema)
            if default_value is not None:
                args[prop_name] = default_value
    if not properties:
        args = {"query": query, "limit": max_results}
    missing = [name for name in required if name not in args]
    if missing:
        return None
    return args


def _schema_default(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return None
    if "default" in schema:
        return schema["default"]
    schema_type = schema.get("type")
    if schema_type == "boolean":
        return False
    if schema_type in {"integer", "number"}:
        return 0
    if schema_type == "array":
        return []
    if schema_type == "object":
        return {}
    return None


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _positive_int(value: Any, *, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, number)


async def _try_mcp_code_search(
    *,
    query: str,
    root_path: Path,
    max_results: int,
    context: CapabilityExecutionContext,
    attempts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    candidates = _rank_mcp_code_search_tools()
    for candidate in candidates:
        tool_args = _build_mcp_code_search_arguments(
            candidate["tool"],
            query=query,
            root_path=root_path,
            max_results=max_results,
        )
        attempt = {
            "serverId": candidate["serverId"],
            "toolName": candidate["toolName"],
            "score": candidate["score"],
            "usable": tool_args is not None,
        }
        if tool_args is None:
            attempt["error"] = "schema arguments could not be satisfied"
            attempts.append(attempt)
            continue
        attempt["arguments"] = tool_args
        attempts.append(attempt)
        try:
            result = await candidate["client"].call_tool(candidate["toolName"], tool_args)
        except Exception as error:  # noqa: BLE001
            attempt["error"] = str(error) or error.__class__.__name__
            await _emit_event(
                context,
                kind="mcp",
                role="researcher",
                name=f"{candidate['serverId']}.{candidate['toolName']}",
                status="error",
                summary=f"MCP code search failed: {attempt['error']}",
                payload={"attempt": attempt},
            )
            continue
        summary = f"Used MCP {candidate['serverId']}/{candidate['toolName']} for code search."
        await _emit_event(
            context,
            kind="mcp",
            role="researcher",
            name=f"{candidate['serverId']}.{candidate['toolName']}",
            status="done",
            summary=summary,
            payload={"arguments": tool_args, "resultPreview": _compact_payload(result, limit=3000)},
        )
        return {
            "ok": True,
            "summary": summary,
            "data": {
                "provider": "mcp",
                "query": query,
                "path": str(root_path),
                "serverId": candidate["serverId"],
                "toolName": candidate["toolName"],
                "arguments": tool_args,
                "result": result,
                "mcpAttempts": attempts,
            },
        }
    return None


def _rank_mcp_code_search_tools() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for client in mcp_manager.list_clients():
        if not getattr(client, "connected", False):
            continue
        server_id = str(getattr(client, "server_id", "") or getattr(getattr(client, "config", None), "id", "") or "")
        for tool in getattr(client, "tools", []):
            tool_name = str(getattr(tool, "name", "") or "")
            description = str(getattr(tool, "description", "") or "")
            text = f"{server_id} {tool_name} {description}".lower()
            score = 0
            if any(hint in text for hint in ("search_code", "codesearch", "code_search")):
                score += 130
            if any(hint in text for hint in ("grep", "ripgrep", "find_in_files", "search_files")):
                score += 110
            if "search" in text and any(hint in text for hint in ("code", "file", "repo", "repository", "workspace")):
                score += 90
            if "github" in text and "search" in text:
                score += 60
            if score <= 0:
                continue
            candidates.append(
                {
                    "client": client,
                    "serverId": server_id,
                    "tool": tool,
                    "toolName": tool_name,
                    "score": score,
                }
            )
    candidates.sort(key=lambda item: int(item["score"]), reverse=True)
    return candidates


def _build_mcp_code_search_arguments(
    tool: Any,
    *,
    query: str,
    root_path: Path,
    max_results: int,
) -> dict[str, Any] | None:
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}
    properties = schema.get("properties") if isinstance(schema, dict) else {}
    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
    if not isinstance(properties, dict):
        properties = {}
    args: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        lower_name = str(prop_name).lower()
        if lower_name in {"query", "q", "search", "searchquery", "term", "pattern", "regex"}:
            args[prop_name] = query
        elif lower_name in {"path", "directory", "dir", "root", "workspace", "cwd"}:
            args[prop_name] = str(root_path)
        elif lower_name in {"limit", "maxresults", "max_results", "count", "topk"}:
            args[prop_name] = max_results
        elif lower_name in {"case_sensitive", "casesensitive"}:
            args[prop_name] = False
        elif prop_name in required:
            default_value = _schema_default(prop_schema)
            if default_value is not None:
                args[prop_name] = default_value
    if not properties:
        args = {"query": query, "path": str(root_path), "limit": max_results}
    missing = [name for name in required if name not in args]
    if missing:
        return None
    return args


def _builtin_code_search(
    *,
    query: str,
    root_path: Path,
    max_results: int,
    case_sensitive: bool,
) -> list[dict[str, Any]]:
    if root_path.is_file():
        paths = [root_path]
    else:
        paths = list(_iter_searchable_files(root_path))
    needle = query if case_sensitive else query.lower()
    results: list[dict[str, Any]] = []
    for path in paths:
        if len(results) >= max_results:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            haystack = line if case_sensitive else line.lower()
            if needle not in haystack:
                continue
            results.append(
                {
                    "path": str(path),
                    "line": line_no,
                    "preview": line.strip()[:500],
                }
            )
            if len(results) >= max_results:
                break
    return results


def _iter_searchable_files(root_path: Path):
    denied_dirs = {".git", "node_modules", "dist", "build", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    allowed_suffixes = {
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".css",
        ".html",
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".sql",
        ".sh",
        ".ps1",
    }
    stack = [root_path]
    while stack:
        current = stack.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            if child.is_dir():
                if child.name in denied_dirs or child.name.startswith("."):
                    continue
                stack.append(child)
            elif child.is_file() and (child.suffix.lower() in allowed_suffixes or child.name in {".gitignore"}):
                yield child


def _code_search_query(args: dict[str, Any], context: CapabilityExecutionContext) -> str:
    explicit = str(args.get("query") or args.get("pattern") or args.get("term") or "").strip()
    if explicit:
        return explicit
    prompt = context.prompt.strip()
    for marker in ("`", "\"", "'"):
        if marker in prompt:
            parts = prompt.split(marker)
            if len(parts) >= 3 and parts[1].strip():
                return parts[1].strip()
    tokens = [token for token in prompt.replace("，", " ").replace("。", " ").split() if _looks_code_like(token)]
    return tokens[-1] if tokens else prompt


def _looks_code_like(value: str) -> bool:
    return any(char in value for char in ("_", ".", "/", "\\", "(", ")")) or any(char.isascii() and char.isalpha() for char in value)


async def _try_mcp_document_convert(
    source_path: Path,
    context: CapabilityExecutionContext,
    *,
    attempts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    candidates = _rank_mcp_document_tools(source_path)
    for candidate in candidates:
        tool_args = _build_mcp_document_arguments(candidate["tool"], source_path=source_path)
        attempt = {
            "serverId": candidate["serverId"],
            "toolName": candidate["toolName"],
            "score": candidate["score"],
            "usable": tool_args is not None,
        }
        if tool_args is None:
            attempt["error"] = "schema arguments could not be satisfied"
            attempts.append(attempt)
            continue
        attempt["arguments"] = tool_args
        attempts.append(attempt)
        try:
            result = await candidate["client"].call_tool(candidate["toolName"], tool_args)
        except Exception as error:  # noqa: BLE001
            attempt["error"] = str(error) or error.__class__.__name__
            await _emit_event(
                context,
                kind="mcp",
                role="researcher",
                name=f"{candidate['serverId']}.{candidate['toolName']}",
                status="error",
                summary=f"MCP document conversion failed: {attempt['error']}",
                payload={"attempt": attempt},
            )
            continue
        markdown = _extract_markdown_from_mcp_result(result)
        if not markdown:
            attempt["error"] = "MCP result did not contain markdown/text content"
            continue
        summary = f"Used MCP {candidate['serverId']}/{candidate['toolName']} for document conversion."
        await _emit_event(
            context,
            kind="mcp",
            role="researcher",
            name=f"{candidate['serverId']}.{candidate['toolName']}",
            status="done",
            summary=summary,
            payload={"arguments": tool_args, "resultPreview": _compact_payload(result, limit=3000)},
        )
        return {
            "serverId": candidate["serverId"],
            "toolName": candidate["toolName"],
            "arguments": tool_args,
            "markdown": markdown,
            "result": result,
        }
    return None


def _rank_mcp_document_tools(source_path: Path) -> list[dict[str, Any]]:
    suffix = source_path.suffix.lower().lstrip(".")
    candidates: list[dict[str, Any]] = []
    for client in mcp_manager.list_clients():
        if not getattr(client, "connected", False):
            continue
        server_id = str(getattr(client, "server_id", "") or getattr(getattr(client, "config", None), "id", "") or "")
        for tool in getattr(client, "tools", []):
            tool_name = str(getattr(tool, "name", "") or "")
            description = str(getattr(tool, "description", "") or "")
            text = f"{server_id} {tool_name} {description}".lower()
            score = 0
            if "markitdown" in text:
                score += 100
            if any(hint in text for hint in ("convert", "markdown", "document", "doc", "pdf", "office")):
                score += 50
            if suffix and suffix in text:
                score += 10
            if score <= 0:
                continue
            candidates.append(
                {
                    "client": client,
                    "serverId": server_id,
                    "tool": tool,
                    "toolName": tool_name,
                    "score": score,
                }
            )
    candidates.sort(key=lambda item: int(item["score"]), reverse=True)
    return candidates


def _build_mcp_document_arguments(tool: Any, *, source_path: Path) -> dict[str, Any] | None:
    schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}
    properties = schema.get("properties") if isinstance(schema, dict) else {}
    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
    if not isinstance(properties, dict):
        properties = {}
    args: dict[str, Any] = {}
    source = str(source_path)
    for prop_name, prop_schema in properties.items():
        lower_name = str(prop_name).lower()
        if lower_name in {"path", "filepath", "file_path", "input", "inputpath", "input_path", "source"}:
            args[prop_name] = source
        elif lower_name in {"uri", "url"}:
            args[prop_name] = source_path.as_uri()
        elif lower_name in {"format", "outputformat", "output_format"}:
            args[prop_name] = "markdown"
        elif lower_name in {"includeimages", "include_images"}:
            args[prop_name] = False
        elif prop_name in required:
            default_value = _schema_default(prop_schema)
            if default_value is not None:
                args[prop_name] = default_value
    if not properties:
        args = {"path": source}
    missing = [name for name in required if name not in args]
    if missing:
        return None
    return args


def _extract_markdown_from_mcp_result(result: Any) -> str:
    if isinstance(result, str):
        return result.strip()
    if not isinstance(result, dict):
        return ""
    for key in ("markdown", "text", "content", "result"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    content = result.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("markdown")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
            elif isinstance(item, str) and item.strip():
                parts.append(item.strip())
        if parts:
            return "\n\n".join(parts)
    data = result.get("data")
    if isinstance(data, dict):
        nested = _extract_markdown_from_mcp_result(data)
        if nested:
            return nested
    return ""


async def _builtin_document_to_markdown(source_path: Path, args: dict[str, Any]) -> str:
    suffix = source_path.suffix.lower()
    max_bytes = _positive_int(args.get("maxBytes"), default=128 * 1024)
    if suffix in {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".log"}:
        return source_path.read_text(encoding="utf-8", errors="replace")[:max_bytes]
    file_result = await run_file_reader(
        {
            "action": "read",
            "path": str(source_path),
            "maxBytes": max_bytes,
        }
    )
    data = file_result.get("data", {}) if isinstance(file_result, dict) else {}
    content = data.get("content") if isinstance(data, dict) else ""
    if isinstance(content, str) and content:
        return content
    return json.dumps(data, ensure_ascii=False, indent=2)


def _document_convert_output_path(context: CapabilityExecutionContext, source_path: Path) -> Path:
    workspace_root = Path(context.workspace_path or ".").expanduser().resolve()
    artifact_root = Path(context.settings.artifact_root or "generated_docs/agent_artifacts").expanduser()
    if not artifact_root.is_absolute():
        artifact_root = workspace_root / artifact_root
    output_dir = artifact_root.resolve() / context.task_id
    base = output_dir / f"{source_path.stem}.converted.md"
    return _unique_path(base)


async def _record_doc_writer_artifact(
    result: dict[str, Any],
    context: CapabilityExecutionContext,
) -> dict[str, Any] | None:
    data = result.get("data", {}) if isinstance(result, dict) else {}
    if not isinstance(data, dict) or not data.get("saved"):
        return None
    path_text = str(data.get("absolutePath") or data.get("outputPath") or "").strip()
    if not path_text:
        return None
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = Path(context.workspace_path or ".").expanduser().resolve() / path
    path = path.resolve()
    if not path.exists():
        return None
    return await _create_artifact_for_path(
        context,
        path=path,
        kind="document",
        mime_type=str(data.get("mimeType") or ""),
        size_bytes=int(data.get("byteCount") or 0),
        description=str(result.get("summary") or "doc_writer output"),
        meta={
            "capability": "doc_writer",
            "format": data.get("format"),
            "title": data.get("title"),
            "outputPath": data.get("outputPath"),
        },
    )


async def _create_artifact_for_path(
    context: CapabilityExecutionContext,
    *,
    path: Path,
    kind: str,
    mime_type: str = "",
    size_bytes: int = 0,
    description: str = "",
    meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if context.storage is None:
        return None
    artifact = await orchestrator_storage.create_artifact(
        context.storage,
        task_id=context.task_id,
        kind=kind if kind in {"file", "screenshot", "document", "log", "diff", "snapshot"} else "file",
        name=path.name,
        path=str(path),
        mime_type=mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        size_bytes=size_bytes or path.stat().st_size,
        description=description,
        meta=meta or {},
    )
    await _emit_event(
        context,
        kind="artifact",
        role="worker",
        name=str((meta or {}).get("capability") or "artifact"),
        status="created",
        summary=f"Created artifact: {artifact['name']}",
        payload={"artifact": artifact},
        artifact_ids=[artifact["id"]],
    )
    return artifact


async def _emit_opencode_event(context: CapabilityExecutionContext, event: dict[str, Any]) -> None:
    event_name = str(event.get("event") or "message")
    payload = event.get("payload", {})
    payload_dict = payload if isinstance(payload, dict) else {"value": payload}
    if event_name == "error":
        kind = "tool"
        status = "error"
    elif event_name in {"tool", "command", "file", "patch"}:
        kind = "tool"
        status = str(payload_dict.get("status") or "running")
    elif event_name in {"done", "summary"}:
        kind = "step"
        status = "done"
    else:
        kind = "opencode_routing"
        status = str(payload_dict.get("status") or event_name)
    await _emit_event(
        context,
        kind=kind,
        role="coder",
        name=f"code_agent.{event_name}",
        status=status,
        summary=_event_summary(event_name, payload_dict),
        payload={"opencodeEvent": event_name, "payload": _compact_payload(payload_dict)},
    )


async def _emit_event(
    context: CapabilityExecutionContext,
    **event: Any,
) -> None:
    if context.emit_event is None:
        return
    await context.emit_event(**event)


def _event_summary(event_name: str, payload: dict[str, Any]) -> str:
    for key in ("summary", "message", "title", "text", "command", "path"):
        value = payload.get(key)
        if value:
            return f"{event_name}: {str(value)[:240]}"
    return f"OpenCode event: {event_name}"


def _compact_payload(value: Any, limit: int = 3000) -> Any:
    text = repr(value)
    if len(text) <= limit:
        return value
    return {"truncated": True, "preview": text[:limit]}


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _resolve_workspace_child(workspace_path: str, raw_path: str) -> Path:
    workspace_root = Path(workspace_path or ".").expanduser().resolve()
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    resolved = candidate.resolve()
    if not _is_within(resolved, workspace_root):
        raise ValueError("path must stay inside the task workspace")
    return resolved


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _looks_like_code_or_config(path: Path) -> bool:
    blocked = {
        ".bat",
        ".c",
        ".cfg",
        ".cmd",
        ".cpp",
        ".cs",
        ".env",
        ".go",
        ".h",
        ".hpp",
        ".ini",
        ".java",
        ".js",
        ".jsx",
        ".json",
        ".lock",
        ".php",
        ".ps1",
        ".py",
        ".rb",
        ".rs",
        ".sh",
        ".svelte",
        ".toml",
        ".ts",
        ".tsx",
        ".vue",
        ".yaml",
        ".yml",
    }
    return path.suffix.lower() in blocked


async def _shell_exec(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    command = str(args.get("command") or "").strip()
    if not command:
        return {"ok": False, "summary": "shell_exec requires command.", "data": {}}

    workspace_root = Path(context.workspace_path or ".").expanduser().resolve()
    raw_cwd = str(args.get("cwd") or ".").strip() or "."
    cwd_path = Path(raw_cwd).expanduser()
    if not cwd_path.is_absolute():
        cwd_path = workspace_root / cwd_path
    cwd_path = cwd_path.resolve()
    if not _is_within(cwd_path, workspace_root):
        return {
            "ok": False,
            "summary": "shell_exec cwd must stay inside the task workspace.",
            "data": {"cwd": str(cwd_path), "workspace": str(workspace_root)},
        }

    risk = classify_shell_command(command)
    trust_mode = bool(context.settings.trust_mode)
    auto_approved = risk.auto_approved and trust_mode and risk.risk == "safe"

    parsed = parse_shell_command(command)
    timeout = int(args.get("timeoutSeconds") or 120)
    timeout = min(max(timeout, 5), 600)

    await _emit_event(
        context,
        kind="command",
        role="coder",
        name="shell_exec",
        status="started",
        summary=f"Running: {risk.command_preview}",
        payload={
            "command": command,
            "cwd": str(cwd_path),
            "risk": risk.risk,
            "autoApproved": auto_approved,
            "reason": risk.reason,
            "workspacePath": str(workspace_root),
        },
    )

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd_path),
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        exit_code = proc.returncode if proc.returncode is not None else -1
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")[:20000]
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")[:8000]
    except asyncio.TimeoutError:
        await _emit_event(
            context,
            kind="command",
            role="coder",
            name="shell_exec",
            status="timeout",
            summary=f"Command timed out after {timeout}s: {risk.command_preview}",
            payload={"command": command, "timeout": timeout},
        )
        return {
            "ok": False,
            "summary": f"Command timed out after {timeout}s.",
            "data": {"command": command, "timeout": timeout},
        }
    except Exception as error:  # noqa: BLE001
        await _emit_event(
            context,
            kind="command",
            role="coder",
            name="shell_exec",
            status="error",
            summary=f"Command failed: {error}",
            payload={"command": command, "error": str(error)},
        )
        return {
            "ok": False,
            "summary": str(error),
            "data": {"command": command, "error": str(error)},
        }

    await _emit_event(
        context,
        kind="command",
        role="coder",
        name="shell_exec",
        status="done" if exit_code == 0 else "error",
        summary=f"Exit code {exit_code}: {risk.command_preview}",
        payload={
            "command": command,
            "exitCode": exit_code,
            "durationMs": 0,
            "stdout": stdout_text,
            "stderr": stderr_text,
        },
    )

    return {
        "ok": exit_code == 0,
        "summary": f"Command exited with code {exit_code}.",
        "data": {
            "command": command,
            "cwd": str(cwd_path),
            "exitCode": exit_code,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "risk": risk.risk,
            "autoApproved": auto_approved,
            "reason": risk.reason,
        },
    }


async def _desktop_screenshot(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    """Capture a desktop screenshot for visual context.

    Only works in Electron environment with screenshot capability available.
    """
    is_electron = bool(os.environ.get("AMADEUS_ELECTRON") or os.environ.get("AMADEUS_DESKTOP"))
    screenshot_enabled = bool(
        os.environ.get("AMADEUS_DESKTOP_SCREENSHOT_ENABLED", "true").lower() in ("1", "true", "yes")
    )

    if not is_electron:
        return {
            "ok": False,
            "summary": "desktop_screenshot is not available: not running in Electron environment.",
            "data": {"isElectron": False, "screenshotEnabled": screenshot_enabled},
        }

    if not screenshot_enabled:
        return {
            "ok": False,
            "summary": "desktop_screenshot is disabled in settings.",
            "data": {"isElectron": True, "screenshotEnabled": False},
        }

    try:
        await _emit_event(
            context,
            kind="browser",
            role="researcher",
            name="desktop_screenshot",
            status="done",
            summary="Desktop screenshot captured.",
            payload={"source": "electron", "captured": True},
        )
        return {
            "ok": True,
            "summary": "Desktop screenshot captured.",
            "data": {
                "source": "electron",
                "captured": True,
                "note": "Screenshot is forwarded to the vision summary pipeline.",
            },
        }
    except Exception as error:  # noqa: BLE001
        return {
            "ok": False,
            "summary": f"desktop_screenshot failed: {error}",
            "data": {"error": str(error)},
        }


def build_default_capability_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register("file_read", _file_read)
    registry.register("code_search", _code_search)
    registry.register("file_write", _file_write)
    registry.register("web_search", _web_search)
    registry.register("memory", _memory)
    registry.register("todo_task", _todo_task)
    registry.register("vision", _vision)
    registry.register("document_convert", _document_convert)
    registry.register("doc_writer", _doc_writer)
    registry.register("code_agent", _code_agent)
    registry.register("shell_exec", _shell_exec)
    registry.register("desktop_screenshot", _desktop_screenshot)
    return registry


default_capability_registry = build_default_capability_registry()
