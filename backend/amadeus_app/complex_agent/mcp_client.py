"""MCP Client implementation.

Implements the Model Context Protocol client side per the official spec
(https://modelcontextprotocol.io). Supports two transports:

- **stdio**: spawns a child process, communicates via JSON-RPC over stdin/stdout.
- **Streamable HTTP**: single endpoint, POST requests with optional SSE
  response stream (the modern transport that supersedes the old HTTP+SSE
  dual-endpoint transport).

Lifecycle:
1. ``connect()`` — sends ``initialize`` with protocol version + client capabilities.
2. ``initialize`` response contains server capabilities + protocol version.
3. ``initialized`` notification sent to server.
4. Tools/resources/prompts are discovered via ``tools/list``, ``resources/list``,
   ``prompts/list`` and registered into the unified tool registry.
5. Server may send ``notifications/progress``, ``notifications/message``,
   ``sampling/createMessage`` and ``elicitation/create`` requests which are
   dispatched to the registered handlers.
6. ``disconnect()`` cancels in-flight requests and closes the transport.

All calls support cancellation via JSON-RPC ``notifications/cancel``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import site
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4
from urllib.parse import urlparse

import httpx

from ..logging_config import get_logger
from .domain import McpServerConfig
from .tool_registry import register_mcp_tool, unregister_mcp_server_tools

_log = get_logger(__name__)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "amadeus-web"
CLIENT_VERSION = "0.1.0"

# JSON-RPC error codes (per MCP spec)
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603


_ENV_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_header_value(value: str) -> str:
    """Expand ${ENV_NAME} placeholders in MCP header values."""
    return _ENV_PLACEHOLDER_RE.sub(lambda match: os.environ.get(match.group(1), match.group(0)), value)


def _extra_stdio_search_paths() -> list[str]:
    """Return common local tool directories missing from Windows service PATH."""
    candidates: list[Path] = []

    for raw_path in (sys.prefix, sys.base_prefix, os.path.dirname(sys.executable)):
        if raw_path:
            root = Path(raw_path)
            candidates.extend([root, root / "Scripts", root.parent / "Scripts"])

    try:
        user_site = Path(site.getusersitepackages())
        candidates.append(user_site.parent / "Scripts")
    except Exception:  # noqa: BLE001
        pass

    # Common Node.js install locations used by the migrated desktop project.
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        raw_path = os.environ.get(env_name)
        if raw_path:
            candidates.append(Path(raw_path) / "nodejs")
    for raw_path in ("D:/nodejs", "E:/nodejs"):
        candidates.append(Path(raw_path))

    seen: set[str] = set()
    results: list[str] = []
    for candidate in candidates:
        try:
            resolved = str(candidate.expanduser().resolve())
        except OSError:
            resolved = str(candidate)
        key = resolved.lower()
        if key in seen or not Path(resolved).exists():
            continue
        seen.add(key)
        results.append(resolved)
    return results


def _stdio_env(config: McpServerConfig) -> dict[str, str]:
    env = {**os.environ, **(config.env or {})}
    extra_paths = _extra_stdio_search_paths()
    if extra_paths:
        existing = env.get("PATH", "")
        existing_parts = [part for part in existing.split(os.pathsep) if part]
        known = {part.lower() for part in existing_parts}
        merged = [part for part in extra_paths if part.lower() not in known]
        if merged:
            env["PATH"] = os.pathsep.join([*merged, *existing_parts])
    return env


def _resolve_stdio_command(command: str, env: dict[str, str]) -> str:
    """Resolve local MCP commands such as uvx/npx when PATH is incomplete."""
    if not command:
        return command
    expanded = os.path.expandvars(os.path.expanduser(command))
    command_path = Path(expanded)
    if command_path.exists():
        return str(command_path)

    search_path = env.get("PATH") or os.environ.get("PATH")
    for name in [expanded, f"{expanded}.exe", f"{expanded}.cmd", f"{expanded}.bat"]:
        found = shutil.which(name, path=search_path)
        if found:
            return found
    return expanded


def _mcp_http_headers(config: McpServerConfig) -> dict[str, str]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    headers.update({key: _expand_header_value(value) for key, value in (config.headers or {}).items()})
    if config.auth_type == "bearer" and config.auth_token:
        headers["Authorization"] = f"Bearer {config.auth_token}"
    elif config.auth_type == "api_key" and config.auth_token:
        headers["X-API-Key"] = config.auth_token
        hostname = urlparse(config.url or "").hostname or ""
        if hostname.endswith("context7.com"):
            headers["CONTEXT7_API_KEY"] = config.auth_token
    return headers


def _is_firecrawl_mcp(config: McpServerConfig) -> bool:
    hostname = urlparse(config.url or "").hostname or ""
    return hostname == "mcp.firecrawl.dev"


def _validate_discovered_capabilities(config: McpServerConfig, client: "McpClient") -> None:
    if _is_firecrawl_mcp(config) and not client.tools:
        raise McpClientError(
            "Firecrawl MCP 未返回工具。请确认 URL 使用完整的 "
            "https://mcp.firecrawl.dev/fc-你的APIKEY/v2/mcp，且 API key 有效。"
        )


def _has_mcp_capability(capabilities: dict[str, Any], name: str) -> bool:
    if name not in capabilities:
        return False
    value = capabilities.get(name)
    return value is not None and value is not False


@dataclass
class McpServerCapabilities:
    """Negotiated server capabilities from the initialize response."""

    tools: bool = False
    resources: bool = False
    prompts: bool = False
    logging: bool = False
    sampling: bool = False
    elicitation: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class McpResourceInfo:
    uri: str
    name: str
    description: str = ""
    mime_type: str = ""


@dataclass
class McpPromptInfo:
    name: str
    description: str
    arguments: list[dict[str, Any]] = field(default_factory=list)


ProgressHandler = Callable[[str, float, float | None, dict[str, Any]], Awaitable[None]]
LogHandler = Callable[[str, str, dict[str, Any]], Awaitable[None]]
SamplingHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
ElicitationHandler = Callable[[str, dict[str, Any] | None], Awaitable[dict[str, Any]]]


class McpClientError(RuntimeError):
    """Raised when an MCP JSON-RPC call returns an error response."""


class McpClient:
    """Single MCP server client session.

    Not thread-safe; intended to be used from the asyncio event loop.
    """

    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self.server_id = config.id or uuid4().hex
        self.capabilities = McpServerCapabilities()
        self.protocol_version: str = ""
        self.server_info: dict[str, Any] = {}
        self.tools: list[McpToolInfo] = []
        self.resources: list[McpResourceInfo] = []
        self.prompts: list[McpPromptInfo] = []
        self._next_request_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._transport: _Transport | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._connected = False
        self._lock = asyncio.Lock()
        # Host-side handlers (set by the agent task hub)
        self.on_progress: ProgressHandler | None = None
        self.on_log: LogHandler | None = None
        self.on_sampling: SamplingHandler | None = None
        self.on_elicitation: ElicitationHandler | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        if self._connected:
            return
        if self.config.transport == "stdio":
            self._transport = await _StdioTransport.start(self.config)
        elif self.config.transport == "http":
            self._transport = await _HttpTransport.start(self.config)
        else:
            raise McpClientError(f"Unsupported transport: {self.config.transport}")
        self._reader_task = asyncio.create_task(self._read_loop(), name=f"mcp-read-{self.server_id}")
        await self._initialize()
        await self._discover()
        self._connected = True
        _log.info("MCP server %s connected (proto=%s tools=%d resources=%d prompts=%d)",
                  self.server_id, self.protocol_version, len(self.tools), len(self.resources), len(self.prompts))

    async def disconnect(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._reader_task = None
        if self._transport is not None:
            await self._transport.close()
            self._transport = None
        # Fail any in-flight requests
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(McpClientError("MCP client disconnected"))
        self._pending.clear()
        unregister_mcp_server_tools(self.server_id)
        self._connected = False

    # ------------------------------------------------------------------
    # JSON-RPC request / notification
    # ------------------------------------------------------------------

    async def _request(self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> dict[str, Any]:
        request_id = self._next_request_id
        self._next_request_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._transport.send(payload)  # type: ignore[union-attr]
        effective_timeout = timeout if timeout is not None else float(self.config.timeout_seconds)
        try:
            return await asyncio.wait_for(future, timeout=effective_timeout)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            await self._notify_cancel(request_id, "timeout")
            raise McpClientError(f"MCP request {method} timed out after {effective_timeout}s")

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        await self._transport.send(payload)  # type: ignore[union-attr]

    async def _notify_cancel(self, request_id: int, reason: str) -> None:
        try:
            await self._notify("notifications/cancel", {"requestId": request_id, "reason": reason})
        except Exception:  # noqa: BLE001
            pass

    async def _initialize(self) -> None:
        response = await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "roots": {"listChanged": True},
                    "sampling": {},
                    "elicitation": {},
                },
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
            timeout=20.0,
        )
        result = response.get("result", {})
        self.protocol_version = str(result.get("protocolVersion", PROTOCOL_VERSION))
        caps = result.get("capabilities", {}) or {}
        self.capabilities = McpServerCapabilities(
            tools=_has_mcp_capability(caps, "tools"),
            resources=_has_mcp_capability(caps, "resources"),
            prompts=_has_mcp_capability(caps, "prompts"),
            logging=_has_mcp_capability(caps, "logging"),
            sampling=_has_mcp_capability(caps, "sampling"),
            elicitation=_has_mcp_capability(caps, "elicitation"),
            raw=caps,
        )
        self.server_info = result.get("serverInfo", {}) or {}
        await self._notify("notifications/initialized")

    async def _discover(self) -> None:
        if self.capabilities.tools:
            try:
                response = await self._request("tools/list", {})
                self.tools = [
                    McpToolInfo(
                        name=str(t.get("name", "")),
                        description=str(t.get("description", "")),
                        input_schema=t.get("inputSchema") or {"type": "object", "properties": {}},
                    )
                    for t in (response.get("result", {}).get("tools") or [])
                ]
                self._register_tools()
            except McpClientError as error:
                _log.warning("MCP %s tools/list failed: %s", self.server_id, error)
        if self.capabilities.resources:
            try:
                response = await self._request("resources/list", {})
                self.resources = [
                    McpResourceInfo(
                        uri=str(r.get("uri", "")),
                        name=str(r.get("name", "")),
                        description=str(r.get("description", "")),
                        mime_type=str(r.get("mimeType", "")),
                    )
                    for r in (response.get("result", {}).get("resources") or [])
                ]
            except McpClientError as error:
                _log.warning("MCP %s resources/list failed: %s", self.server_id, error)
        if self.capabilities.prompts:
            try:
                response = await self._request("prompts/list", {})
                self.prompts = [
                    McpPromptInfo(
                        name=str(p.get("name", "")),
                        description=str(p.get("description", "")),
                        arguments=p.get("arguments") or [],
                    )
                    for p in (response.get("result", {}).get("prompts") or [])
                ]
            except McpClientError as error:
                _log.warning("MCP %s prompts/list failed: %s", self.server_id, error)

    def _register_tools(self) -> None:
        allowed = set(self.config.allowed_tools) if self.config.allowed_tools else None

        async def handler(args: dict[str, Any], _tool_name: str = "") -> dict[str, Any]:
            return await self.call_tool(_tool_name, args)

        for tool in self.tools:
            if allowed is not None and tool.name not in allowed:
                continue

            def _make_handler(tool_name: str) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
                async def _h(args: dict[str, Any]) -> dict[str, Any]:
                    return await self.call_tool(tool_name, args)
                return _h

            register_mcp_tool(
                server_id=self.server_id,
                tool_name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
                handler=_make_handler(tool.name),
                meta={"serverName": self.config.name},
            )

    # ------------------------------------------------------------------
    # Public MCP operations
    # ------------------------------------------------------------------

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = await self._request("tools/call", {"name": name, "arguments": arguments})
        result = response.get("result", {})
        if response.get("error"):
            raise McpClientError(f"tools/call error: {response['error']}")
        return result

    async def read_resource(self, uri: str) -> dict[str, Any]:
        response = await self._request("resources/read", {"uri": uri})
        return response.get("result", {})

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"name": name}
        if arguments:
            params["arguments"] = arguments
        response = await self._request("prompts/get", params)
        return response.get("result", {})

    async def list_roots(self) -> dict[str, Any]:
        """List roots exposed by the server (server-as-host scenario).

        Amadeus is the host, so this is rarely used; included for completeness.
        """
        response = await self._request("roots/list", {})
        return response.get("result", {})

    async def send_roots_list_changed(self) -> None:
        await self._notify("notifications/roots/list_changed")

    async def set_log_level(self, level: str) -> None:
        await self._request("logging/setLevel", {"level": level})

    # ------------------------------------------------------------------
    # Read loop + inbound dispatch
    # ------------------------------------------------------------------

    async def _read_loop(self) -> None:
        transport = self._transport
        if transport is None:
            return
        try:
            async for message in transport.iter_messages():
                await self._handle_message(message)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            _log.warning("MCP %s read loop ended: %s", self.server_id, error)
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(McpClientError(f"transport closed: {error}"))
            self._pending.clear()

    async def _handle_message(self, message: dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message):
            # Response to a request we sent
            request_id = message["id"]
            future = self._pending.pop(request_id, None)
            if future is None or future.done():
                return
            if "error" in message:
                future.set_exception(McpClientError(f"server error: {message['error']}"))
            else:
                future.set_result(message)
            return
        method = message.get("method", "")
        params = message.get("params") or {}
        if "id" in message:
            # Server -> client request (sampling, elicitation)
            request_id = message["id"]
            try:
                result = await self._handle_server_request(method, params)
                await self._transport.send({  # type: ignore[union-attr]
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": result,
                })
            except Exception as error:  # noqa: BLE001
                await self._transport.send({  # type: ignore[union-attr]
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": JSONRPC_INTERNAL_ERROR, "message": str(error)},
                })
        else:
            # Notification
            await self._handle_notification(method, params)

    async def _handle_server_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "sampling/createMessage":
            if self.on_sampling is None:
                raise McpClientError("sampling not supported by host")
            return await self.on_sampling(params)
        if method == "elicitation/create":
            if self.on_elicitation is None:
                raise McpClientError("elicitation not supported by host")
            return await self.on_elicitation(params.get("message", ""), params.get("requestedSchema"))
        raise McpClientError(f"unsupported server request: {method}")

    async def _handle_notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "notifications/progress":
            if self.on_progress is not None:
                await self.on_progress(
                    str(params.get("progressToken", "")),
                    float(params.get("progress", 0)),
                    params.get("total"),
                    params,
                )
        elif method == "notifications/message":
            if self.on_log is not None:
                await self.on_log(
                    str(params.get("level", "info")),
                    str(params.get("data", "")),
                    params,
                )
        elif method == "notifications/tools/list_changed":
            await self._refresh_tools()
        elif method == "notifications/resources/list_changed":
            await self._refresh_resources()
        elif method == "notifications/prompts/list_changed":
            await self._refresh_prompts()
        else:
            _log.debug("MCP %s unhandled notification %s", self.server_id, method)

    async def _refresh_tools(self) -> None:
        unregister_mcp_server_tools(self.server_id)
        self.tools = []
        if self.capabilities.tools:
            response = await self._request("tools/list", {})
            self.tools = [
                McpToolInfo(
                    name=str(t.get("name", "")),
                    description=str(t.get("description", "")),
                    input_schema=t.get("inputSchema") or {"type": "object", "properties": {}},
                )
                for t in (response.get("result", {}).get("tools") or [])
            ]
            self._register_tools()

    async def _refresh_resources(self) -> None:
        if not self.capabilities.resources:
            return
        response = await self._request("resources/list", {})
        self.resources = [
            McpResourceInfo(
                uri=str(r.get("uri", "")),
                name=str(r.get("name", "")),
                description=str(r.get("description", "")),
                mime_type=str(r.get("mimeType", "")),
            )
            for r in (response.get("result", {}).get("resources") or [])
        ]

    async def _refresh_prompts(self) -> None:
        if not self.capabilities.prompts:
            return
        response = await self._request("prompts/list", {})
        self.prompts = [
            McpPromptInfo(
                name=str(p.get("name", "")),
                description=str(p.get("description", "")),
                arguments=p.get("arguments") or [],
            )
            for p in (response.get("result", {}).get("prompts") or [])
        ]


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


class _Transport:
    """Abstract transport: sends JSON payloads and yields inbound messages."""

    async def send(self, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    async def iter_messages(self):  # type: ignore[override]
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


class _StdioTransport(_Transport):
    """JSON-RPC over a child process's stdin/stdout (newline-delimited JSON)."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    @classmethod
    async def start(cls, config: McpServerConfig) -> "_StdioTransport":
        if not config.command:
            raise McpClientError("stdio MCP server requires a command")
        env = _stdio_env(config)
        command = _resolve_stdio_command(config.command, env)
        try:
            process = await asyncio.create_subprocess_exec(
                command,
                *config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=config.cwd or None,
                env=env,
            )
        except FileNotFoundError as error:
            raise McpClientError(f"stdio MCP command not found: {config.command}") from error
        return cls(process)

    async def send(self, payload: dict[str, Any]) -> None:
        if self._process.stdin is None:
            raise McpClientError("stdio MCP stdin closed")
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self._process.stdin.write(data)
        await self._process.stdin.drain()

    async def iter_messages(self):
        if self._process.stdout is None:
            return
        reader = self._process.stdout
        while True:
            line = await reader.readline()
            if not line:
                break
            line_text = line.decode("utf-8", errors="replace").strip()
            if not line_text:
                continue
            try:
                yield json.loads(line_text)
            except json.JSONDecodeError:
                _log.debug("MCP stdio non-JSON line: %s", line_text[:200])

    async def close(self) -> None:
        if self._process.stdin is not None:
            try:
                self._process.stdin.close()
                await self._process.stdin.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        try:
            self._process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(self._process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass


class _HttpTransport(_Transport):
    """Streamable HTTP transport (single endpoint, POST + optional SSE response).

    Per the MCP spec, the client POSTs JSON-RPC messages to the server URL.
    Responses may be:
    - A single JSON object (for simple requests), or
    - An SSE stream of JSON-RPC messages (for batched/long-running responses
      and server-initiated requests).
    """

    def __init__(self, config: McpServerConfig, client: httpx.AsyncClient, endpoint: str) -> None:
        self._config = config
        self._client = client
        self._endpoint = endpoint
        self._session_id: str | None = None
        self._receiver_task: asyncio.Task[None] | None = None
        self._incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._closed = False

    @classmethod
    async def start(cls, config: McpServerConfig) -> "_HttpTransport":
        if not config.url:
            raise McpClientError("http MCP server requires a url")
        headers = _mcp_http_headers(config)
        client = httpx.AsyncClient(timeout=httpx.Timeout(float(config.timeout_seconds), connect=10.0), headers=headers)
        return cls(config, client, config.url)

    async def send(self, payload: dict[str, Any]) -> None:
        if self._closed:
            raise McpClientError("http MCP transport closed")
        # Notifications (no id) are fire-and-forget POSTs.
        # Requests (with id) may return either JSON or SSE.
        is_notification = "id" not in payload
        headers = {"Mcp-Session-Id": self._session_id} if self._session_id else None
        try:
            response = await self._client.post(self._endpoint, json=payload, headers=headers)
        except httpx.HTTPError as error:
            raise McpClientError(f"http MCP POST failed: {error}") from error
        # Track session id returned by the server (Mcp-Session-Id header)
        session_id = response.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        content_type = response.headers.get("content-type", "")
        if response.status_code == 202:
            # Accepted (notification ack)
            return
        if response.status_code >= 400:
            raise McpClientError(f"http MCP error {response.status_code}: {response.text[:300]}")
        if "text/event-stream" in content_type:
            # SSE stream: parse and enqueue each event's JSON payload
            await self._consume_sse(response)
        elif "application/json" in content_type:
            try:
                message = response.json()
            except json.JSONDecodeError as error:
                raise McpClientError(f"http MCP invalid JSON: {error}") from error
            await self._incoming.put(message)
        else:
            # Some servers return text/plain; try to parse as JSON
            try:
                message = response.json()
                await self._incoming.put(message)
            except json.JSONDecodeError:
                pass

    async def _consume_sse(self, response: httpx.Response) -> None:
        """Parse an SSE response stream and enqueue each data: payload."""
        async for raw_line in response.aiter_lines():
            if self._closed:
                break
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith("data:"):
                data = line[5:].lstrip()
                try:
                    message = json.loads(data)
                except json.JSONDecodeError:
                    continue
                await self._incoming.put(message)

    async def iter_messages(self):
        while not self._closed:
            try:
                message = await self._incoming.get()
            except asyncio.CancelledError:
                break
            if message is None:
                break
            yield message

    async def close(self) -> None:
        self._closed = True
        await self._incoming.put(None)  # type: ignore[arg-type]
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Connection manager (singleton)
# ---------------------------------------------------------------------------


class McpConnectionManager:
    """Manages all MCP client connections for the application."""

    def __init__(self) -> None:
        self._clients: dict[str, McpClient] = {}
        self._capability_cache: dict[str, dict[str, Any]] = {}

    def get(self, server_id: str) -> McpClient | None:
        return self._clients.get(server_id)

    def list_clients(self) -> list[McpClient]:
        return list(self._clients.values())

    async def connect_server(self, config: McpServerConfig) -> McpClient:
        # Disconnect existing client with same id
        existing = self._clients.get(config.id)
        if existing is not None:
            await existing.disconnect()
        client = McpClient(config)
        try:
            await client.connect()
            _validate_discovered_capabilities(config, client)
        except Exception as error:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self.update_capability_cache_error(config.id, str(error) or error.__class__.__name__)
            raise
        self._clients[config.id] = client
        self._capability_cache[config.id] = {
            "tools": [{"name": t.name, "description": t.description} for t in client.tools],
            "resources": [{"uri": r.uri, "name": r.name, "description": r.description} for r in client.resources],
            "prompts": [{"name": p.name, "description": p.description} for p in client.prompts],
            "lastConnectedAt": _now_iso(),
            "lastError": None,
        }
        return client

    async def disconnect_server(self, server_id: str) -> bool:
        client = self._clients.pop(server_id, None)
        if client is None:
            return False
        await client.disconnect()
        # Preserve capability cache but mark as disconnected
        if server_id in self._capability_cache:
            self._capability_cache[server_id]["lastError"] = "disconnected"
        return True

    async def disconnect_all(self) -> None:
        for server_id in list(self._clients.keys()):
            await self.disconnect_server(server_id)

    def get_capability_cache(self, server_id: str) -> dict[str, Any] | None:
        return self._capability_cache.get(server_id)

    def list_capability_cache(self) -> dict[str, dict[str, Any]]:
        return dict(self._capability_cache)

    def update_capability_cache_error(self, server_id: str, error: str) -> None:
        if server_id not in self._capability_cache:
            self._capability_cache[server_id] = {
                "tools": [], "resources": [], "prompts": [],
                "lastConnectedAt": None, "lastError": error,
            }
        else:
            self._capability_cache[server_id]["lastError"] = error

    async def test_connection(self, config: McpServerConfig) -> dict[str, Any]:
        """Connect, list capabilities, then disconnect. Returns a report."""
        client = McpClient(config)
        try:
            await client.connect()
            _validate_discovered_capabilities(config, client)
            return {
                "ok": True,
                "protocolVersion": client.protocol_version,
                "serverInfo": client.server_info,
                "capabilities": {
                    "tools": client.capabilities.tools,
                    "resources": client.capabilities.resources,
                    "prompts": client.capabilities.prompts,
                    "logging": client.capabilities.logging,
                    "sampling": client.capabilities.sampling,
                    "elicitation": client.capabilities.elicitation,
                },
                "toolCount": len(client.tools),
                "resourceCount": len(client.resources),
                "promptCount": len(client.prompts),
            }
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error) or error.__class__.__name__}
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001
                pass


mcp_manager = McpConnectionManager()
