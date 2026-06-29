"""Context builder for the Codex-style agent loop."""
from __future__ import annotations

import platform
from dataclasses import dataclass, field
from typing import Any

from .capabilities import capability_catalog
from ..orchestrator_integrations.mcp_client import mcp_manager


SYSTEM_PROMPT_TEMPLATE = """\
You are Amadeus, an autonomous task agent operating in a Codex-style loop.
Your goal is to complete the user's task by iteratively planning, calling tools, observing results, and deciding the next step.

## Environment
- Workspace: {workspace_path}
- Operating System: {os_type}
- Trust Mode: {trust_mode}

## Available Capabilities
You have access to the following tools. Use them to gather information, execute actions, and verify results.

## Loop Protocol
1. Analyze the task and current context.
2. Decide the next action: call a tool or finish.
3. If calling a tool, provide the tool name and arguments.
4. After observing the tool result, decide whether to continue or finish.
5. When the task is complete, call the `finish` tool with a structured summary.

## Important Rules
- Prefer read-only tools first to gather context before making changes.
- Use `shell_exec` only for verification and inspection (tests, lint, build). Code changes should go through `code_agent`.
- Stay within the workspace boundary. Never access files outside the workspace.
- Each tool call should have a clear purpose. Avoid redundant calls.
- If a tool fails, analyze the error and decide whether to retry with different arguments or proceed differently.
- Do not output chain-of-thought reasoning. Output only actionable plans and observations.
"""


@dataclass
class AgentLoopContext:
    """Accumulated context for the agent loop."""
    system_prompt: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_schemas: list[dict[str, Any]] = field(default_factory=list)
    mcp_snapshot: dict[str, Any] = field(default_factory=dict)
    working_set: dict[str, Any] = field(default_factory=dict)
    rounds: int = 0
    tool_calls: int = 0


def build_agent_loop_system_prompt(
    *,
    workspace_path: str,
    trust_mode: bool = True,
    os_type: str = "",
) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        workspace_path=workspace_path,
        os_type=os_type or platform.system(),
        trust_mode=str(trust_mode).lower(),
    )


_CAPABILITY_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information. Returns search results with content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                    "maxResults": {"type": "integer", "description": "Maximum results (default 5).", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read or list files in the workspace. Use action='list' to list directory, action='read' to read a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "read"], "default": "list"},
                    "path": {"type": "string", "description": "File or directory path (relative to workspace).", "default": "."},
                    "maxEntries": {"type": "integer", "default": 80},
                    "maxBytes": {"type": "integer", "default": 128000},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_search",
            "description": "Search code and text files in the workspace for a query pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query or pattern."},
                    "path": {"type": "string", "default": "."},
                    "maxResults": {"type": "integer", "default": 50},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": "Execute a shell command in the workspace. Low-risk commands (test, build, lint, git status) auto-approve in trust mode. High-risk commands require permission.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute."},
                    "cwd": {"type": "string", "description": "Working directory (relative to workspace).", "default": "."},
                    "timeoutSeconds": {"type": "integer", "default": 120},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_screenshot",
            "description": "Capture a desktop screenshot for visual context. Only available in Electron environment.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory",
            "description": "Recall or save structured memory. action='recall' to search memory, action='save' to save.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["recall", "save", "browse"], "default": "recall"},
                    "query": {"type": "string", "description": "Search query for recall."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_agent",
            "description": "Delegate code changes and command execution to OpenCode. Use for writing/modifying code files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Instructions for the code agent."},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Signal that the task is complete. Provide a structured summary of what was accomplished.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Brief summary of what was accomplished."},
                    "completedItems": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of completed actions.",
                    },
                    "keyFindings": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Key findings from the task.",
                    },
                    "artifacts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of created artifacts.",
                    },
                    "verification": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Verification results.",
                    },
                    "remainingRisks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Remaining risks or caveats.",
                    },
                },
                "required": ["summary"],
            },
        },
    },
]


def build_tool_schemas(
    *,
    enabled_capabilities: set[str] | None = None,
    include_raw_mcp: bool = False,
) -> list[dict[str, Any]]:
    """Build OpenAI tool schemas for the agent loop."""
    schemas = list(_CAPABILITY_TOOL_SCHEMAS)
    if enabled_capabilities is not None:
        enabled = enabled_capabilities | {"finish"}
        schemas = [s for s in schemas if s["function"]["name"] in enabled]

    if include_raw_mcp:
        schemas.extend(_build_raw_mcp_tool_schemas())

    return schemas


def _build_raw_mcp_tool_schemas() -> list[dict[str, Any]]:
    """Build tool schemas for raw MCP tools (mcp__{serverId}__{toolName})."""
    schemas: list[dict[str, Any]] = []
    try:
        for client in mcp_manager.list_clients():
            if not getattr(client, "connected", False):
                continue
            server_id = str(
                getattr(client, "server_id", "")
                or getattr(getattr(client, "config", None), "id", "")
                or ""
            )
            for tool in getattr(client, "tools", []):
                tool_name = str(getattr(tool, "name", "") or "")
                if not tool_name:
                    continue
                full_name = f"mcp__{server_id}__{tool_name}"
                description = str(getattr(tool, "description", "") or "")[:500]
                input_schema = (
                    getattr(tool, "input_schema", None)
                    or getattr(tool, "inputSchema", None)
                    or {"type": "object", "properties": {}}
                )
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": full_name,
                        "description": f"[MCP:{server_id}] {description}",
                        "parameters": input_schema if isinstance(input_schema, dict) else {"type": "object", "properties": {}},
                    },
                })
    except Exception:  # noqa: BLE001
        pass
    return schemas


def build_mcp_snapshot() -> dict[str, Any]:
    """Build a compact MCP capability snapshot for context injection."""
    try:
        raw_cache = mcp_manager.list_capability_cache()
    except Exception as error:  # noqa: BLE001
        return {"available": False, "error": str(error), "servers": {}}
    servers: dict[str, Any] = {}
    for server_id, item in raw_cache.items():
        if not isinstance(item, dict):
            continue
        servers[str(server_id)] = {
            "tools": [
                {"name": str(t.get("name", "")), "description": str(t.get("description", ""))[:200]}
                for t in (item.get("tools") or [])[:30]
                if isinstance(t, dict)
            ],
            "connected": True,
        }
    return {"available": bool(servers), "servers": servers}
