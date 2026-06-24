"""Unified tool registry for the complex agent.

Merges three sources of tools into a single OpenAI-compatible function
calling schema:

1. Builtin tools registered in :mod:`amadeus_app.agent_registry`.
2. MCP tools exposed by connected MCP servers (``mcp__{serverId}__{toolName}``).
3. Skill-provided tools (declared in a skill's manifest tool allowlist;
   skills themselves do not define new tool handlers, they only whitelist
   existing builtin/MCP tools).

The registry is task-scoped: each agent task gets a snapshot of available
tools filtered by the task's active skills' allowlists and the global
agent settings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..agent_registry import ToolDefinition, tool_registry


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

# OpenAI-compatible function names only allow [a-zA-Z0-9_-]; we use
# ``mcp__{server}__{tool}`` to stay within that charset.
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")

# ---------------------------------------------------------------------------
# Tool permission classification
# ---------------------------------------------------------------------------
#
# Tools are classified into three risk levels:
#   - safe:      auto-execute, no confirmation needed
#   - confirm:   require user approval unless trustMode is on
#   - dangerous: always require user approval, even in trustMode
#
# Builtin tools that are read-only / side-effect-free are "safe".
# Tools that write files, execute code, or interact with the browser are
# "confirm". MCP tools default to "confirm" for untrusted servers and
# "safe" for trusted servers (unless explicitly in the dangerous set).

AUTO_EXECUTE_TOOLS: set[str] = {
    "get_current_time",
    "calculate",
    "echo",
    "describe_capabilities",
    "file_reader",
    "recall_memory",
    "browse_memory_tree",
    "search_memory",
    "search_web",
    "web_search",
    "image_understand",
    "todo_task",
}

CONFIRM_TOOLS: set[str] = {
    "file_writer",
    "opencode_delegate",
    "doc_writer",
    "save_memory",
    "browser",
}

DANGEROUS_TOOL_PATTERNS: tuple[str, ...] = (
    # Browser actions that interact with pages (not just read)
    "browser_click",
    "browser_type",
    "browser_screenshot",
    # MCP tools from untrusted servers are dangerous by default
)


def classify_tool_permission(
    tool_name: str,
    *,
    source: str = "builtin",
    mcp_trusted: bool | None = None,
    mcp_allowed_tools: set[str] | None = None,
    arguments: dict[str, Any] | None = None,
) -> str:
    """Return the risk level for a tool: ``safe`` / ``confirm`` / ``dangerous``.

    - Builtin read-only tools → ``safe``
    - Builtin write/exec tools → ``confirm``
    - Browser tool: action-level classification (open/extract_text=safe,
      screenshot=confirm, click/type=dangerous)
    - MCP tools from trusted servers in allowedTools → ``safe``
    - MCP tools from trusted servers not in allowedTools → ``confirm``
    - MCP tools from untrusted servers → ``dangerous``
    - Unknown builtin tools → ``confirm``
    """
    # Browser action-level classification
    if tool_name == "browser" and arguments is not None:
        action = str(arguments.get("action", ""))
        return classify_browser_action(action)

    if source == "mcp":
        if mcp_trusted is True:
            if mcp_allowed_tools and tool_name in mcp_allowed_tools:
                return "safe"
            return "confirm"
        # Untrusted MCP server
        return "dangerous"

    # Builtin tools
    if tool_name in AUTO_EXECUTE_TOOLS:
        return "safe"
    if tool_name in CONFIRM_TOOLS:
        return "confirm"
    # Check dangerous patterns (e.g. browser_click, browser_type)
    for pattern in DANGEROUS_TOOL_PATTERNS:
        if pattern in tool_name:
            return "dangerous"
    # Unknown builtin tool → confirm by default
    return "confirm"


# Browser action risk levels
BROWSER_SAFE_ACTIONS: set[str] = {"open", "extract_text", "close"}
BROWSER_CONFIRM_ACTIONS: set[str] = {"screenshot"}
BROWSER_DANGEROUS_ACTIONS: set[str] = {"click", "type"}


def classify_browser_action(action: str) -> str:
    """Classify a browser action into a risk level.

    - safe:      open, extract_text, close (read-only / no side effects)
    - confirm:   screenshot (captures page state, no mutation)
    - dangerous: click, type (interacts with page, may trigger side effects)
    """
    if action in BROWSER_DANGEROUS_ACTIONS:
        return "dangerous"
    if action in BROWSER_CONFIRM_ACTIONS:
        return "confirm"
    if action in BROWSER_SAFE_ACTIONS:
        return "safe"
    # Unknown action → confirm by default
    return "confirm"


def _sanitize_name_part(name: str) -> str:
    """Replace characters that are invalid in OpenAI function names with ``_``."""
    return _SAFE_NAME_RE.sub("_", name)


def mcp_tool_name(server_id: str, tool_name: str) -> str:
    """Return the unified tool name for an MCP tool."""
    return f"mcp__{_sanitize_name_part(server_id)}__{_sanitize_name_part(tool_name)}"


@dataclass
class UnifiedTool:
    """A tool surfaced to the complex agent.

    ``source`` is one of ``builtin`` / ``mcp`` / ``skill`` and is used for
    audit logging and allowlist filtering.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    source: str = "builtin"
    permission: str = "safe"
    meta: dict[str, Any] = field(default_factory=dict)

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class UnifiedToolRegistry:
    """Holds builtin + MCP tools and produces filtered schemas per task."""

    def __init__(self) -> None:
        self._tools: dict[str, UnifiedTool] = {}

    def register(self, tool: UnifiedTool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> UnifiedTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[UnifiedTool]:
        return list(self._tools.values())

    def clear_source(self, source: str) -> None:
        """Remove all tools from a given source (e.g. when an MCP server disconnects)."""
        self._tools = {n: t for n, t in self._tools.items() if t.source != source}

    def openai_schema(
        self,
        *,
        allowlist: list[str] | None = None,
        denylist: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return OpenAI function-calling schemas, optionally filtered.

        - If ``allowlist`` is non-empty, only tools whose names are in the
          allowlist are returned.
        - ``denylist`` always wins (a denied tool is never returned).
        """
        deny = set(denylist or [])
        allow = set(allowlist) if allowlist else None
        schemas: list[dict[str, Any]] = []
        for tool in self._tools.values():
            if tool.name in deny:
                continue
            if allow is not None and tool.name not in allow:
                continue
            schemas.append(tool.to_openai_schema())
        return schemas

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            return {"ok": False, "error": f"Unknown tool: {name}"}
        try:
            result = await tool.handler(arguments)
        except Exception as error:  # noqa: BLE001
            return {"ok": False, "error": str(error) or error.__class__.__name__}
        return result if isinstance(result, dict) else {"ok": True, "data": result}


# ---------------------------------------------------------------------------
# Singleton + bootstrap from the existing builtin registry
# ---------------------------------------------------------------------------


unified_tool_registry = UnifiedToolRegistry()


def _wrap_builtin_handler(handler: ToolHandler) -> ToolHandler:
    """Builtin handlers already return ``{summary, data}`` dicts; pass through."""
    return handler


def sync_builtin_tools() -> None:
    """Mirror builtin tools from :data:`tool_registry` into the unified registry.

    Call this once at startup and again whenever a builtin tool is registered
    dynamically (e.g. memory tools).
    """
    for tool in tool_registry.list_tools():
        if tool.handler is None:
            continue
        unified_tool_registry.register(
            UnifiedTool(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
                handler=_wrap_builtin_handler(tool.handler),
                source="builtin",
                permission=tool.permission,
            )
        )


def register_mcp_tool(
    *,
    server_id: str,
    tool_name: str,
    description: str,
    input_schema: dict[str, Any],
    handler: ToolHandler,
    meta: dict[str, Any] | None = None,
) -> str:
    """Register a single MCP tool and return its unified name."""
    unified_name = mcp_tool_name(server_id, tool_name)
    unified_tool_registry.register(
        UnifiedTool(
            name=unified_name,
            description=description or f"MCP tool {tool_name} from server {server_id}",
            parameters=input_schema or {"type": "object", "properties": {}},
            handler=handler,
            source="mcp",
            permission="safe",
            meta={"serverId": server_id, "toolName": tool_name, **(meta or {})},
        )
    )
    return unified_name


def unregister_mcp_server_tools(server_id: str) -> None:
    """Remove all tools contributed by a disconnected MCP server."""
    safe_server = _sanitize_name_part(server_id)
    prefix = f"mcp__{safe_server}__"
    for name in list(unified_tool_registry._tools.keys()):
        if name.startswith(prefix):
            unified_tool_registry.unregister(name)
