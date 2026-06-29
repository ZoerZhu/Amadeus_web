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

import json
import re
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
    from ..orchestrator_integrations.tool_registry import unified_tool_registry

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

    Raises ``RuntimeError`` when the MCP call fails or reports an error.
    """
    from ..orchestrator_integrations.tool_registry import unified_tool_registry

    args: dict[str, Any] = {"query": query, "limit": max_results}
    if domains:
        args["includeDomains"] = domains
    if exclude_domains:
        args["excludeDomains"] = exclude_domains
    if freshness and freshness != "any":
        args["tbs"] = _freshness_to_tbs(freshness)

    response = await unified_tool_registry.call(tool_name, args)
    unwrapped = _unwrap_mcp_response(response)
    if not unwrapped["ok"]:
        raise RuntimeError(
            f"firecrawl_search failed: {_sanitize_error(unwrapped.get('error', 'unknown'))}"
        )

    raw_items = _extract_search_items(unwrapped["data"])
    results: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or "").strip()
        if not url:
            continue
        markdown = str(item.get("markdown") or "")
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
                "contentSummary": _truncate(markdown, 500) if markdown else "",
                "text": markdown,
                "publishedAt": str(item.get("publishedDate") or ""),
                "retrievedAt": retrieved_at,
                "score": 0.0,
                "scores": {},
                "evidence": [],
                # If Firecrawl search already returned markdown, mark as fetched
                # so reader_agent does not waste a redundant scrape call (P2 fix).
                "fetchStatus": "ok" if markdown else "not_fetched",
            }
        )
    return results


async def firecrawl_scrape_via_mcp(tool_name: str, url: str) -> dict[str, Any]:
    """Call ``firecrawl_scrape`` MCP tool and return normalized content fields.

    Returns a dict with ``text``, ``title``, ``contentSummary``, ``publishedAt``,
    ``fetchStatus`` keys suitable for merging into a :class:`SearchResult`.
    """
    from ..orchestrator_integrations.tool_registry import unified_tool_registry

    response = await unified_tool_registry.call(
        tool_name, {"url": url, "formats": ["markdown"]}
    )
    unwrapped = _unwrap_mcp_response(response)
    if not unwrapped["ok"]:
        raise RuntimeError(
            f"firecrawl_scrape failed: {_sanitize_error(unwrapped.get('error', 'unknown'))}"
        )
    data = unwrapped["data"]
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
    from ..orchestrator_integrations.tool_registry import unified_tool_registry

    response = await unified_tool_registry.call(tool_name, {"url": url})
    unwrapped = _unwrap_mcp_response(response)
    if not unwrapped["ok"]:
        raise RuntimeError(
            f"fetch MCP failed: {_sanitize_error(unwrapped.get('error', 'unknown'))}"
        )
    data = unwrapped["data"]
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


def _unwrap_mcp_response(response: Any) -> dict[str, Any]:
    """Normalize a ``unified_tool_registry.call()`` response into ``{ok, data, error}``.

    Handles three response shapes:

    1. ``{"ok": True/False, "data": ..., "error": ...}`` — registry wrapper used
       by test fakes and returned directly when the handler already returns this
       shape. Passed through unchanged.
    2. ``{"content": [{"type": "text", "text": "..."}], "isError": bool}`` — raw
       MCP ``tools/call`` result. The ``content`` text items are concatenated and
       parsed as JSON when possible. ``isError=True`` maps to ``ok=False``.
    3. Plain dict / list — treated as successful data.

    This ensures real MCP tools (whose handlers return raw MCP results without
    an ``ok`` field) are not mistakenly treated as failures (P1 fix).
    """
    if not isinstance(response, dict):
        return {"ok": True, "data": response, "error": None}

    # Case 1: already has "ok" field (test fakes, registry-level errors)
    if "ok" in response:
        return {
            "ok": bool(response.get("ok")),
            "data": response.get("data"),
            "error": response.get("error"),
        }

    # Case 2: raw MCP tools/call response {content: [...], isError: bool}
    content = response.get("content")
    if isinstance(content, list):
        if response.get("isError"):
            error_text = ""
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    error_text += str(item.get("text", ""))
            return {
                "ok": False,
                "data": None,
                "error": error_text or "MCP tool returned isError",
            }
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
        combined = "\n".join(text_parts).strip()
        if combined:
            try:
                parsed = json.loads(combined)
                return {"ok": True, "data": parsed, "error": None}
            except (json.JSONDecodeError, ValueError):
                return {"ok": True, "data": combined, "error": None}
        return {"ok": True, "data": None, "error": None}

    # Case 3: plain dict — treat as successful data
    return {"ok": True, "data": response, "error": None}


def _extract_search_items(data: Any) -> list[Any]:
    """Extract the list of search result items from unwrapped Firecrawl data.

    Handles:
      - Direct list of items
      - ``{"data": [...]}`` / ``{"result": [...]}`` / ``{"results": [...]}``
      - Single dict with a url (treated as one item)
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "result", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        if data.get("url") or data.get("link"):
            return [data]
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


# Patterns that may carry API keys / tokens — sanitized from error messages
# to avoid leaking credentials into warnings or debug logs (P2 fix).
_SENSITIVE_PATTERNS = [
    # Firecrawl API key in URL path: https://api.firecrawl.dev/v1/fc-xxxxxxxx
    (re.compile(r"fc-[a-zA-Z0-9_-]{10,}"), "fc-***"),
    # Query params: key=..., token=..., api_key=..., apikey=...
    (re.compile(r"([?&](?:api_?key|token|key|secret)=)[^&\s]+", re.IGNORECASE), r"\1***"),
    # URL userinfo: https://user:pass@host
    (re.compile(r"(https?://)[^:@/\s]+:[^@/\s]+@"), r"\1***@"),
]


def _sanitize_error(text: str) -> str:
    """Strip API keys / tokens from error text before it enters warnings or logs."""
    if not text:
        return text
    result = str(text)
    for pattern, replacement in _SENSITIVE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result
