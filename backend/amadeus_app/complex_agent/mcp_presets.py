"""MCP server preset templates.

Built-in recommended MCP server configurations that users can add with
one click from the settings panel. Each preset includes sensible defaults
for transport, URL/command, recommended allowlist, and trusted flag.
"""

from __future__ import annotations

from typing import Any

# Each preset is a dict matching McpServerConfig fields (camelCase keys).
MCP_PRESETS: list[dict[str, Any]] = [
    {
        "id": "github",
        "name": "GitHub MCP",
        "description": "GitHub repos, issues, PRs, actions. Requires GITHUB_TOKEN env.",
        "transport": "http",
        "url": "https://api.githubcopilot.com/mcp/",
        "authType": "bearer",
        "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"},
        "trusted": False,
        "allowedTools": [],
        "allowedResources": [],
        "enabled": False,
        "timeoutSeconds": 30,
        "_preset": True,
        "_category": "remote",
    },
    {
        "id": "context7",
        "name": "Context7",
        "description": "Up-to-date, version-specific library documentation.",
        "transport": "http",
        "url": "https://mcp.context7.com/mcp",
        "authType": "api_key",
        "headers": {},
        "trusted": False,
        "allowedTools": [],
        "allowedResources": [],
        "enabled": False,
        "timeoutSeconds": 30,
        "_preset": True,
        "_category": "remote",
    },
    {
        "id": "playwright",
        "name": "Playwright MCP",
        "description": "Structured browser automation via Playwright. Local stdio.",
        "transport": "stdio",
        "command": "npx",
        "args": ["@playwright/mcp@latest"],
        "cwd": "",
        "env": {},
        "trusted": False,
        "allowedTools": [],
        "allowedResources": [],
        "enabled": False,
        "timeoutSeconds": 60,
        "_preset": True,
        "_category": "local",
    },
    {
        "id": "markitdown",
        "name": "MarkItDown MCP",
        "description": "Convert PDF/Office/HTML to Markdown. Local stdio.",
        "transport": "stdio",
        "command": "uvx",
        "args": ["markitdown-mcp"],
        "cwd": "",
        "env": {},
        "trusted": False,
        "allowedTools": [],
        "allowedResources": [],
        "enabled": False,
        "timeoutSeconds": 60,
        "_preset": True,
        "_category": "local",
    },
    {
        "id": "sequential-thinking",
        "name": "Sequential Thinking",
        "description": "Dynamic problem-solving through thought sequences. Local stdio.",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "cwd": "",
        "env": {},
        "trusted": True,
        "allowedTools": ["mcp__sequential-thinking__sequentialthinking"],
        "allowedResources": [],
        "enabled": False,
        "timeoutSeconds": 60,
        "_preset": True,
        "_category": "local",
    },
    {
        "id": "fetch",
        "name": "Fetch MCP",
        "description": "Fetch and process web content. Local stdio.",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "cwd": "",
        "env": {},
        "trusted": False,
        "allowedTools": [],
        "allowedResources": [],
        "enabled": False,
        "timeoutSeconds": 30,
        "_preset": True,
        "_category": "local",
    },
]


def list_presets() -> list[dict[str, Any]]:
    """Return all available MCP presets."""
    return [dict(p) for p in MCP_PRESETS]


def get_preset(preset_id: str) -> dict[str, Any] | None:
    """Return a single preset by id, or None."""
    for p in MCP_PRESETS:
        if p["id"] == preset_id:
            return dict(p)
    return None
