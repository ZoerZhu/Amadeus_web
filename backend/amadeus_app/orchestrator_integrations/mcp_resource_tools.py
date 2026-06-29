"""Builtin tools that expose MCP resources and prompts to orchestrator flows.

These tools let the agent read MCP server resources and fetch prompts through
the unified tool registry, complementing the per-server MCP tool handlers
registered by :mod:`amadeus_app.orchestrator_integrations.mcp_client`.

Four read-only builtin tools are provided:

- ``mcp_resource_read``:  read a single resource by URI from a connected server.
- ``mcp_prompt_get``:     fetch a named prompt (with optional arguments).
- ``mcp_list_resources``: list resources from one or all connected servers.
- ``mcp_list_prompts``:   list prompts from one or all connected servers.

All handlers are async and return ``{"ok": bool, ...}`` dicts; they never
raise exceptions to the caller.
"""

from __future__ import annotations

from typing import Any

from ..logging_config import get_logger
from .mcp_client import mcp_manager
from .tool_registry import UnifiedTool, unified_tool_registry

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# mcp_resource_read
# ---------------------------------------------------------------------------


def make_mcp_resource_read_tool() -> UnifiedTool:
    """Build the ``mcp_resource_read`` builtin tool."""

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        server_id = str(args.get("serverId", "")).strip()
        uri = str(args.get("uri", "")).strip()
        if not server_id:
            return {
                "ok": False,
                "error": "serverId is required",
                "summary": "missing serverId",
            }
        if not uri:
            return {
                "ok": False,
                "error": "uri is required",
                "summary": "missing uri",
            }

        client = mcp_manager.get(server_id)
        if client is None or not client.connected:
            return {
                "ok": False,
                "error": "MCP server not connected",
                "summary": f"server {server_id} not connected",
            }

        try:
            response = await client.read_resource(uri)
        except Exception as error:  # noqa: BLE001
            _log.warning("mcp_resource_read failed: %s/%s -> %s", server_id, uri, error)
            return {
                "ok": False,
                "error": str(error) or error.__class__.__name__,
                "summary": f"failed to read resource {uri} from {server_id}",
            }

        return {
            "ok": True,
            "result": response,
            "summary": f"read resource {uri} from {server_id}",
        }

    return UnifiedTool(
        name="mcp_resource_read",
        description="读取已连接 MCP server 的 resource 内容（只读）。"
                    "需要提供 serverId 和 resource URI，返回该 resource 的内容。",
        parameters={
            "type": "object",
            "properties": {
                "serverId": {
                    "type": "string",
                    "description": "目标 MCP server 的 ID",
                },
                "uri": {
                    "type": "string",
                    "description": "要读取的 resource URI",
                },
            },
            "required": ["serverId", "uri"],
        },
        handler=handler,
        source="builtin",
        permission="safe",
    )


# ---------------------------------------------------------------------------
# mcp_prompt_get
# ---------------------------------------------------------------------------


def make_mcp_prompt_get_tool() -> UnifiedTool:
    """Build the ``mcp_prompt_get`` builtin tool."""

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        server_id = str(args.get("serverId", "")).strip()
        name = str(args.get("name", "")).strip()
        if not server_id:
            return {
                "ok": False,
                "error": "serverId is required",
                "summary": "missing serverId",
            }
        if not name:
            return {
                "ok": False,
                "error": "name is required",
                "summary": "missing prompt name",
            }

        raw_arguments = args.get("arguments")
        arguments: dict[str, Any] | None = None
        if isinstance(raw_arguments, dict):
            arguments = raw_arguments

        client = mcp_manager.get(server_id)
        if client is None or not client.connected:
            return {
                "ok": False,
                "error": "MCP server not connected",
                "summary": f"server {server_id} not connected",
            }

        try:
            response = await client.get_prompt(name, arguments)
        except Exception as error:  # noqa: BLE001
            _log.warning("mcp_prompt_get failed: %s/%s -> %s", server_id, name, error)
            return {
                "ok": False,
                "error": str(error) or error.__class__.__name__,
                "summary": f"failed to get prompt {name} from {server_id}",
            }

        return {
            "ok": True,
            "result": response,
            "summary": f"got prompt {name} from {server_id}",
        }

    return UnifiedTool(
        name="mcp_prompt_get",
        description="获取已连接 MCP server 的 prompt（只读）。"
                    "需要提供 serverId 和 prompt 名称，可选传入 arguments 参数对象，"
                    "返回该 prompt 渲染后的消息列表。",
        parameters={
            "type": "object",
            "properties": {
                "serverId": {
                    "type": "string",
                    "description": "目标 MCP server 的 ID",
                },
                "name": {
                    "type": "string",
                    "description": "要获取的 prompt 名称",
                },
                "arguments": {
                    "type": "object",
                    "description": "可选的 prompt 参数键值对",
                },
            },
            "required": ["serverId", "name"],
        },
        handler=handler,
        source="builtin",
        permission="safe",
    )


# ---------------------------------------------------------------------------
# mcp_list_resources
# ---------------------------------------------------------------------------


def _serialize_resource(resource: Any, server_id: str) -> dict[str, Any]:
    return {
        "serverId": server_id,
        "uri": getattr(resource, "uri", ""),
        "name": getattr(resource, "name", ""),
        "description": getattr(resource, "description", ""),
        "mimeType": getattr(resource, "mimeType", getattr(resource, "mime_type", "")),
    }


def make_mcp_list_resources_tool() -> UnifiedTool:
    """Build the ``mcp_list_resources`` builtin tool."""

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        server_id = str(args.get("serverId", "")).strip()

        if server_id:
            client = mcp_manager.get(server_id)
            if client is None or not client.connected:
                return {
                    "ok": False,
                    "error": "MCP server not connected",
                    "summary": f"server {server_id} not connected",
                }
            resources = [_serialize_resource(r, server_id) for r in client.resources]
            return {
                "ok": True,
                "result": {
                    "servers": [server_id],
                    "resources": resources,
                    "total": len(resources),
                },
                "summary": f"listed {len(resources)} resources from {server_id}",
            }

        # No serverId: aggregate across all connected clients.
        resources: list[dict[str, Any]] = []
        servers: list[str] = []
        for client in mcp_manager.list_clients():
            if not client.connected:
                continue
            servers.append(client.server_id)
            resources.extend(_serialize_resource(r, client.server_id) for r in client.resources)
        return {
            "ok": True,
            "result": {
                "servers": servers,
                "resources": resources,
                "total": len(resources),
            },
            "summary": f"listed {len(resources)} resources from {len(servers)} servers",
        }

    return UnifiedTool(
        name="mcp_list_resources",
        description="列出已连接 MCP server 的可用 resources（只读）。"
                    "可指定 serverId 仅列出该 server 的 resources；不指定则汇总所有已连接 server。",
        parameters={
            "type": "object",
            "properties": {
                "serverId": {
                    "type": "string",
                    "description": "可选，目标 MCP server 的 ID；不填则列出所有已连接 server",
                },
            },
            "required": [],
        },
        handler=handler,
        source="builtin",
        permission="safe",
    )


# ---------------------------------------------------------------------------
# mcp_list_prompts
# ---------------------------------------------------------------------------


def _serialize_prompt(prompt: Any, server_id: str) -> dict[str, Any]:
    return {
        "serverId": server_id,
        "name": getattr(prompt, "name", ""),
        "description": getattr(prompt, "description", ""),
        "arguments": getattr(prompt, "arguments", []),
    }


def make_mcp_list_prompts_tool() -> UnifiedTool:
    """Build the ``mcp_list_prompts`` builtin tool."""

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        server_id = str(args.get("serverId", "")).strip()

        if server_id:
            client = mcp_manager.get(server_id)
            if client is None or not client.connected:
                return {
                    "ok": False,
                    "error": "MCP server not connected",
                    "summary": f"server {server_id} not connected",
                }
            prompts = [_serialize_prompt(p, server_id) for p in client.prompts]
            return {
                "ok": True,
                "result": {
                    "servers": [server_id],
                    "prompts": prompts,
                    "total": len(prompts),
                },
                "summary": f"listed {len(prompts)} prompts from {server_id}",
            }

        # No serverId: aggregate across all connected clients.
        prompts: list[dict[str, Any]] = []
        servers: list[str] = []
        for client in mcp_manager.list_clients():
            if not client.connected:
                continue
            servers.append(client.server_id)
            prompts.extend(_serialize_prompt(p, client.server_id) for p in client.prompts)
        return {
            "ok": True,
            "result": {
                "servers": servers,
                "prompts": prompts,
                "total": len(prompts),
            },
            "summary": f"listed {len(prompts)} prompts from {len(servers)} servers",
        }

    return UnifiedTool(
        name="mcp_list_prompts",
        description="列出已连接 MCP server 的可用 prompts（只读）。"
                    "可指定 serverId 仅列出该 server 的 prompts；不指定则汇总所有已连接 server。",
        parameters={
            "type": "object",
            "properties": {
                "serverId": {
                    "type": "string",
                    "description": "可选，目标 MCP server 的 ID；不填则列出所有已连接 server",
                },
            },
            "required": [],
        },
        handler=handler,
        source="builtin",
        permission="safe",
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_mcp_resource_prompt_tools() -> list[UnifiedTool]:
    """Register all MCP resource/prompt tools into the unified registry."""
    tools = [
        make_mcp_resource_read_tool(),
        make_mcp_prompt_get_tool(),
        make_mcp_list_resources_tool(),
        make_mcp_list_prompts_tool(),
    ]
    for tool in tools:
        unified_tool_registry.register(tool)
    return tools
