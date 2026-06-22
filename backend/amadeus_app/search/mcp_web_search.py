"""MCP-priority web search routing layer.

Discovers Firecrawl/Fetch MCP tools registered in :data:`unified_tool_registry`
and provides adapter functions that normalize MCP responses into the
:class:`SearchResult` shape used by :mod:`web_search_agent`.

The :mod:`web_search_agent` calls :func:`discover_mcp_web_tools` at the start
of ``search_agent`` / ``reader_agent`` and prefers MCP tools when available,
falling back to the built-in SearxNG/DuckDuckGo/httpx path on error or empty
results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


@dataclass
class McpWebTools:
    """Discovered MCP web search/scrape tools.

    Each field holds the unified tool name (e.g. ``mcp__firecrawl__firecrawl_search``)
    when an MCP tool with the matching ``meta.toolName`` is registered, or ``None``
    when unavailable.
    """

    firecrawl_search: str | None = None
    firecrawl_scrape: str | None = None
    fetch: str | None = None

    @property
    def has_firecrawl(self) -> bool:
        return self.firecrawl_search is not None

    @property
    def has_scrape(self) -> bool:
        return self.firecrawl_scrape is not None or self.fetch is not None


def discover_mcp_web_tools() -> McpWebTools:
    """Scan ``unified_tool_registry`` for Firecrawl/Fetch MCP tools by ``meta.toolName``.

    Matching is case-insensitive on ``meta.toolName``. Returns a :class:`McpWebTools`
    with whatever was found (fields stay ``None`` when absent).
    """
    from ..complex_agent.tool_registry import unified_tool_registry

    tools = McpWebTools()
    for tool in unified_tool_registry.list_tools():
        if tool.source != "mcp":
            continue
        tool_name = str(tool.meta.get("toolName", "")).lower()
        if tool_name == "firecrawl_search":
            tools.firecrawl_search = tool.name
        elif tool_name == "firecrawl_scrape":
            tools.firecrawl_scrape = tool.name
        elif tool_name == "fetch":
            tools.fetch = tool.name
    return tools


async def firecrawl_search_via_mcp(
    tool_name: str,
    query: str,
    *,
    max_results: int,
    domains: list[str],
    exclude_domains: list[str],
    freshness: str,
    retrieved_at: str,
) -> list[dict[str, Any]]:
    """Call ``firecrawl_search`` MCP tool and normalize results to SearchResult dicts.

    Handles multiple response shapes:
      - ``{"ok": True, "data": {"data": [...], "success": True}}`` (Firecrawl MCP wrapper)
      - ``{"ok": True, "data": [...]}`` (direct list)
      - ``{"ok": True, "data": {"result": [...]}}`` / ``{"results": [...]}`` (alt keys)
      - ``{"ok": True, "result": [...]}`` (top-level result)

    Raises ``RuntimeError`` when the MCP call reports ``ok=False``.
    """
    from ..complex_agent.tool_registry import unified_tool_registry

    args: dict[str, Any] = {"query": query, "limit": max_results}
    if domains:
        args["includeDomains"] = domains
    if exclude_domains:
        args["excludeDomains"] = exclude_domains
    if freshness and freshness != "any":
        args["tbs"] = _freshness_to_tbs(freshness)

    response = await unified_tool_registry.call(tool_name, args)
    if not response.get("ok"):
        raise RuntimeError(
            f"firecrawl_search failed: {response.get('error', 'unknown')}"
        )

    raw_items = _extract_search_items(response)
    results: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or "").strip()
        if not url:
            continue
        results.append(
            {
                "id": "",
                "title": str(item.get("title") or ""),
                "url": url,
                "displayUrl": "",
                "domain": _extract_domain(url),
                "sourceType": "unknown",
                "snippet": str(
                    item.get("description")
                    or item.get("snippet")
                    or item.get("content")
                    or ""
                ),
                "contentSummary": "",
                "text": str(item.get("markdown") or ""),
                "publishedAt": str(item.get("publishedDate") or ""),
                "retrievedAt": retrieved_at,
                "score": 0.0,
                "scores": {},
                "evidence": [],
                "fetchStatus": "not_fetched",
            }
        )
    return results


async def firecrawl_scrape_via_mcp(tool_name: str, url: str) -> dict[str, Any]:
    """Call ``firecrawl_scrape`` MCP tool and return normalized content fields.

    Returns a dict with ``text``, ``title``, ``contentSummary``, ``publishedAt``,
    ``fetchStatus`` keys suitable for merging into a :class:`SearchResult`.
    """
    from ..complex_agent.tool_registry import unified_tool_registry

    response = await unified_tool_registry.call(
        tool_name, {"url": url, "formats": ["markdown"]}
    )
    if not response.get("ok"):
        raise RuntimeError(
            f"firecrawl_scrape failed: {response.get('error', 'unknown')}"
        )
    data = response.get("data", {})
    inner = data.get("data", data) if isinstance(data, dict) else {}
    if not isinstance(inner, dict):
        inner = {}
    markdown = str(inner.get("markdown") or inner.get("content") or "")
    metadata = inner.get("metadata", {}) if isinstance(inner, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "text": markdown,
        "title": str(metadata.get("title") or metadata.get("ogTitle") or ""),
        "contentSummary": _truncate(markdown, 500),
        "publishedAt": str(metadata.get("publishedDate") or ""),
        "fetchStatus": "ok" if markdown else "empty",
    }


async def fetch_via_mcp(tool_name: str, url: str) -> dict[str, Any]:
    """Call Fetch MCP tool and return normalized content fields."""
    from ..complex_agent.tool_registry import unified_tool_registry

    response = await unified_tool_registry.call(tool_name, {"url": url})
    if not response.get("ok"):
        raise RuntimeError(f"fetch MCP failed: {response.get('error', 'unknown')}")
    data = response.get("data", {})
    inner = data.get("data", data) if isinstance(data, dict) else {}
    if not isinstance(inner, dict):
        inner = {}
    text = str(
        inner.get("content") or inner.get("text") or inner.get("markdown") or ""
    )
    return {
        "text": text,
        "title": str(inner.get("title") or ""),
        "contentSummary": _truncate(text, 500),
        "fetchStatus": "ok" if text else "empty",
    }


def _extract_search_items(response: dict[str, Any]) -> list[Any]:
    """Extract the list of search result items from a Firecrawl MCP response.

    Handles the response shape variations documented on
    :func:`firecrawl_search_via_mcp`.
    """
    data = response.get("data")
    # Case: top-level result list ({"ok": True, "result": [...]})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "result", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        # Fall through: treat dict itself as a single item if it has a url
        if data.get("url") or data.get("link"):
            return [data]
    # Case: top-level result key alongside data
    top_result = response.get("result")
    if isinstance(top_result, list):
        return top_result
    return []


def _freshness_to_tbs(freshness: str) -> str:
    """Map our freshness enum to Firecrawl/Google ``tbs`` format."""
    return {
        "day": "qdr:d",
        "week": "qdr:w",
        "month": "qdr:m",
        "year": "qdr:y",
    }.get(freshness, "")


def _extract_domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def _truncate(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
