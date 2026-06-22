"""Tests for MCP-priority web search routing.

Covers:
- MCP web tool discovery (Firecrawl search/scrape + Fetch)
- Firecrawl search adapter (response normalization, empty results, errors)
- search_agent node integration (MCP priority with fallback)
- reader_agent node integration (MCP scrape with fallback)
- research-report skill allowlist unchanged
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.amadeus_app.complex_agent.tool_registry import (
    register_mcp_tool,
    unified_tool_registry,
)
from backend.amadeus_app.search.mcp_web_search import (
    McpWebTools,
    discover_mcp_web_tools,
    firecrawl_search_via_mcp,
)
from backend.amadeus_app.search.web_search_agent import (
    reader_agent,
    search_agent,
    search_duckduckgo_html,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unregister(*names: str) -> None:
    for name in names:
        unified_tool_registry.unregister(name)


def _make_state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "query": "test search",
        "intent": "test search",
        "domains": [],
        "exclude_domains": [],
        "max_results": 5,
        "fetch_content": True,
        "freshness": "any",
        "warnings": [],
        "debug": {},
    }
    base.update(overrides)
    return base


async def _fake_firecrawl_search_handler_factory(payload: dict[str, Any]):
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        return payload

    return handler


# ---------------------------------------------------------------------------
# TestMcpWebToolDiscovery
# ---------------------------------------------------------------------------


class TestMcpWebToolDiscovery:
    """discover_mcp_web_tools scans unified_tool_registry for MCP web tools."""

    def test_discover_finds_firecrawl_tools(self):
        async def handler(args):
            return {"ok": True, "data": []}

        name_search = register_mcp_tool(
            server_id="test_firecrawl",
            tool_name="firecrawl_search",
            description="test",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            meta={"serverName": "test"},
        )
        name_scrape = register_mcp_tool(
            server_id="test_firecrawl",
            tool_name="firecrawl_scrape",
            description="test",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            meta={"serverName": "test"},
        )
        try:
            tools = discover_mcp_web_tools()
            assert tools.firecrawl_search == name_search
            assert tools.firecrawl_scrape == name_scrape
            assert tools.has_firecrawl is True
            assert tools.has_scrape is True
        finally:
            _unregister(name_search, name_scrape)

    def test_discover_finds_fetch_tool(self):
        async def handler(args):
            return {"ok": True, "data": {}}

        name_fetch = register_mcp_tool(
            server_id="test_fetch",
            tool_name="fetch",
            description="test",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            meta={"serverName": "test"},
        )
        try:
            tools = discover_mcp_web_tools()
            assert tools.fetch == name_fetch
            assert tools.has_scrape is True
        finally:
            _unregister(name_fetch)

    def test_discover_returns_empty_when_no_mcp(self):
        # Clear any MCP tools that might be registered from other tests
        original_tools = list(unified_tool_registry.list_tools())
        mcp_names = [t.name for t in original_tools if t.source == "mcp"]
        for name in mcp_names:
            unified_tool_registry.unregister(name)
        try:
            tools = discover_mcp_web_tools()
            assert tools.firecrawl_search is None
            assert tools.firecrawl_scrape is None
            assert tools.fetch is None
            assert tools.has_firecrawl is False
            assert tools.has_scrape is False
        finally:
            pass  # do not restore; tests should clean up their own tools


# ---------------------------------------------------------------------------
# TestFirecrawlSearchAdapter
# ---------------------------------------------------------------------------


class TestFirecrawlSearchAdapter:
    """firecrawl_search_via_mcp normalizes MCP responses to SearchResult dicts."""

    @pytest.mark.asyncio
    async def test_firecrawl_search_normalizes_results(self):
        payload = {
            "ok": True,
            "data": [
                {
                    "url": "https://example.com/a",
                    "title": "Example A",
                    "description": "Desc A",
                    "markdown": "# Content A",
                }
            ],
        }

        async def handler(args):
            return payload

        name = register_mcp_tool(
            server_id="test_fc_search_norm",
            tool_name="firecrawl_search",
            description="test",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            meta={"serverName": "test"},
        )
        try:
            results = await firecrawl_search_via_mcp(
                name,
                "test query",
                max_results=5,
                domains=[],
                exclude_domains=[],
                freshness="any",
                retrieved_at="2026-01-01T00:00:00+08:00",
            )
            assert len(results) == 1
            item = results[0]
            assert item["url"] == "https://example.com/a"
            assert item["title"] == "Example A"
            assert item["domain"] == "example.com"
            assert item["snippet"] == "Desc A"
            assert item["text"] == "# Content A"
            assert item["retrievedAt"] == "2026-01-01T00:00:00+08:00"
        finally:
            _unregister(name)

    @pytest.mark.asyncio
    async def test_firecrawl_search_handles_empty_results(self):
        payload = {"ok": True, "data": []}

        async def handler(args):
            return payload

        name = register_mcp_tool(
            server_id="test_fc_search_empty",
            tool_name="firecrawl_search",
            description="test",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            meta={"serverName": "test"},
        )
        try:
            results = await firecrawl_search_via_mcp(
                name,
                "test query",
                max_results=5,
                domains=[],
                exclude_domains=[],
                freshness="any",
                retrieved_at="2026-01-01T00:00:00+08:00",
            )
            assert results == []
        finally:
            _unregister(name)

    @pytest.mark.asyncio
    async def test_firecrawl_search_raises_on_error(self):
        payload = {"ok": False, "error": "API key invalid"}

        async def handler(args):
            return payload

        name = register_mcp_tool(
            server_id="test_fc_search_err",
            tool_name="firecrawl_search",
            description="test",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            meta={"serverName": "test"},
        )
        try:
            with pytest.raises(RuntimeError, match="API key invalid"):
                await firecrawl_search_via_mcp(
                    name,
                    "test query",
                    max_results=5,
                    domains=[],
                    exclude_domains=[],
                    freshness="any",
                    retrieved_at="2026-01-01T00:00:00+08:00",
                )
        finally:
            _unregister(name)


# ---------------------------------------------------------------------------
# TestSearchAgentMcpIntegration
# ---------------------------------------------------------------------------


class TestSearchAgentMcpIntegration:
    """search_agent node uses Firecrawl MCP first, falls back on error/empty."""

    @pytest.mark.asyncio
    async def test_search_agent_uses_firecrawl_when_available(self, monkeypatch):
        # Ensure no SearxNG to avoid env interference
        monkeypatch.delenv("AMADEUS_SEARXNG_URL", raising=False)

        async def handler(args):
            return {
                "ok": True,
                "data": [
                    {
                        "url": "https://example.com/page1",
                        "title": "Page 1",
                        "description": "Snippet 1",
                        "markdown": "# Content 1",
                    },
                    {
                        "url": "https://example.com/page2",
                        "title": "Page 2",
                        "description": "Snippet 2",
                        "markdown": "# Content 2",
                    },
                ],
            }

        name = register_mcp_tool(
            server_id="test_sa_fc",
            tool_name="firecrawl_search",
            description="test",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            meta={"serverName": "test"},
        )
        # Patch duckduckgo to fail loudly if called
        call_log: list[Any] = []

        async def fake_ddg(queries, *, max_results, retrieved_at):
            call_log.append(queries)
            return []

        monkeypatch.setattr(
            "backend.amadeus_app.search.web_search_agent.search_duckduckgo_html",
            fake_ddg,
        )
        try:
            state = _make_state(query="test search", intent="test search")
            result = await search_agent(state)
            assert result["debug"]["provider"] == "firecrawl_mcp"
            assert result["debug"]["mcpTools"]["firecrawl"] is True
            assert len(result["raw_results"]) == 2
            assert call_log == []  # fallback not invoked
        finally:
            _unregister(name)

    @pytest.mark.asyncio
    async def test_search_agent_falls_back_on_firecrawl_error(self, monkeypatch):
        monkeypatch.delenv("AMADEUS_SEARXNG_URL", raising=False)

        async def handler(args):
            raise RuntimeError("firecrawl exploded")

        name = register_mcp_tool(
            server_id="test_sa_fc_err",
            tool_name="firecrawl_search",
            description="test",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            meta={"serverName": "test"},
        )

        async def fake_ddg(queries, *, max_results, retrieved_at):
            return [
                {
                    "id": "src_001",
                    "title": "Fallback Result",
                    "url": "https://fallback.example.com/x",
                    "displayUrl": "fallback.example.com/x",
                    "domain": "fallback.example.com",
                    "sourceType": "unknown",
                    "snippet": "fallback snippet",
                    "contentSummary": "",
                    "text": "",
                    "publishedAt": "",
                    "retrievedAt": retrieved_at,
                    "score": 0.0,
                    "scores": {},
                    "evidence": [],
                    "fetchStatus": "not_fetched",
                }
            ]

        monkeypatch.setattr(
            "backend.amadeus_app.search.web_search_agent.search_duckduckgo_html",
            fake_ddg,
        )
        try:
            state = _make_state(query="test search", intent="test search")
            result = await search_agent(state)
            assert result["debug"]["provider"] == "duckduckgo_html"
            warnings = result["warnings"]
            assert any(w["type"] == "mcp_fallback" for w in warnings)
        finally:
            _unregister(name)

    @pytest.mark.asyncio
    async def test_search_agent_falls_back_on_empty_results(self, monkeypatch):
        monkeypatch.delenv("AMADEUS_SEARXNG_URL", raising=False)

        async def handler(args):
            return {"ok": True, "data": []}

        name = register_mcp_tool(
            server_id="test_sa_fc_empty",
            tool_name="firecrawl_search",
            description="test",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            meta={"serverName": "test"},
        )

        async def fake_ddg(queries, *, max_results, retrieved_at):
            return [
                {
                    "id": "src_001",
                    "title": "Fallback Result",
                    "url": "https://fallback.example.com/y",
                    "displayUrl": "fallback.example.com/y",
                    "domain": "fallback.example.com",
                    "sourceType": "unknown",
                    "snippet": "fallback snippet",
                    "contentSummary": "",
                    "text": "",
                    "publishedAt": "",
                    "retrievedAt": retrieved_at,
                    "score": 0.0,
                    "scores": {},
                    "evidence": [],
                    "fetchStatus": "not_fetched",
                }
            ]

        monkeypatch.setattr(
            "backend.amadeus_app.search.web_search_agent.search_duckduckgo_html",
            fake_ddg,
        )
        try:
            state = _make_state(query="test search", intent="test search")
            result = await search_agent(state)
            assert result["debug"]["provider"] == "duckduckgo_html"
            warnings = result["warnings"]
            assert any(w["type"] == "mcp_fallback" for w in warnings)
        finally:
            _unregister(name)

    @pytest.mark.asyncio
    async def test_search_agent_uses_direct_url_when_url_in_query(self, monkeypatch):
        monkeypatch.delenv("AMADEUS_SEARXNG_URL", raising=False)

        async def handler(args):
            return {
                "ok": True,
                "data": [
                    {
                        "url": "https://example.com/mcp",
                        "title": "MCP Result",
                        "description": "should not be used",
                    }
                ],
            }

        name = register_mcp_tool(
            server_id="test_sa_fc_url",
            tool_name="firecrawl_search",
            description="test",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            meta={"serverName": "test"},
        )

        async def fake_ddg(queries, *, max_results, retrieved_at):
            return []

        monkeypatch.setattr(
            "backend.amadeus_app.search.web_search_agent.search_duckduckgo_html",
            fake_ddg,
        )
        try:
            state = _make_state(
                query="https://example.com/page",
                intent="https://example.com/page",
            )
            result = await search_agent(state)
            assert result["debug"]["provider"] == "direct_url"
        finally:
            _unregister(name)


# ---------------------------------------------------------------------------
# TestReaderAgentMcpIntegration
# ---------------------------------------------------------------------------


class TestReaderAgentMcpIntegration:
    """reader_agent node uses MCP scrape first, falls back on error."""

    @pytest.mark.asyncio
    async def test_reader_agent_uses_mcp_scrape_when_available(self, monkeypatch):
        async def handler(args):
            return {
                "ok": True,
                "data": {
                    "data": {
                        "markdown": "# Fetched via MCP\n\nHello world.",
                        "metadata": {"title": "MCP Title"},
                    }
                },
            }

        name = register_mcp_tool(
            server_id="test_ra_scrape",
            tool_name="firecrawl_scrape",
            description="test",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            meta={"serverName": "test"},
        )
        try:
            state = _make_state(
                fetch_content=True,
                ranked_results=[
                    {
                        "id": "src_001",
                        "title": "Original Title",
                        "url": "https://example.com/page",
                        "displayUrl": "example.com/page",
                        "domain": "example.com",
                        "sourceType": "unknown",
                        "snippet": "snippet",
                        "contentSummary": "",
                        "text": "",
                        "publishedAt": "",
                        "retrievedAt": "2026-01-01T00:00:00+08:00",
                        "score": 0.0,
                        "scores": {},
                        "evidence": [],
                        "fetchStatus": "not_fetched",
                    }
                ],
            )
            result = await reader_agent(state)
            assert result["debug"]["mcpFetchUsed"] >= 1
            ranked = result["ranked_results"]
            assert len(ranked) == 1
            assert "Fetched via MCP" in ranked[0]["text"]
            assert ranked[0]["title"] == "MCP Title"
        finally:
            _unregister(name)

    @pytest.mark.asyncio
    async def test_reader_agent_falls_back_on_scrape_error(self, monkeypatch):
        async def handler(args):
            raise RuntimeError("scrape failed")

        name = register_mcp_tool(
            server_id="test_ra_scrape_err",
            tool_name="firecrawl_scrape",
            description="test",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
            meta={"serverName": "test"},
        )

        async def fake_fetch_and_extract(client, result, query):
            return {
                "text": "fallback content",
                "title": "Fallback Title",
                "contentSummary": "fallback summary",
                "publishedAt": "",
                "evidence": [],
                "fetchStatus": "ok",
            }

        monkeypatch.setattr(
            "backend.amadeus_app.search.web_search_agent.fetch_and_extract",
            fake_fetch_and_extract,
        )
        try:
            state = _make_state(
                fetch_content=True,
                ranked_results=[
                    {
                        "id": "src_001",
                        "title": "Original Title",
                        "url": "https://example.com/page",
                        "displayUrl": "example.com/page",
                        "domain": "example.com",
                        "sourceType": "unknown",
                        "snippet": "snippet",
                        "contentSummary": "",
                        "text": "",
                        "publishedAt": "",
                        "retrievedAt": "2026-01-01T00:00:00+08:00",
                        "score": 0.0,
                        "scores": {},
                        "evidence": [],
                        "fetchStatus": "not_fetched",
                    }
                ],
            )
            result = await reader_agent(state)
            warnings = result["warnings"]
            assert any(w["type"] == "mcp_fallback" for w in warnings)
            ranked = result["ranked_results"]
            assert len(ranked) == 1
            assert ranked[0]["text"] == "fallback content"
        finally:
            _unregister(name)


# ---------------------------------------------------------------------------
# TestResearchReportSkillAllowlist
# ---------------------------------------------------------------------------


class TestResearchReportSkillAllowlist:
    """research-report skill allowlist must still use web_search, not MCP tools."""

    def test_research_report_allowlist_still_uses_web_search(self):
        from backend.amadeus_app.complex_agent.builtin_skills import BUILTIN_SKILLS

        research_skill = None
        for skill in BUILTIN_SKILLS:
            if skill["id"] == "research-report":
                research_skill = skill
                break
        assert research_skill is not None, "research-report skill must exist"
        allowlist = research_skill["toolAllowlist"]
        assert "web_search" in allowlist
        for name in allowlist:
            assert not name.startswith("mcp__"), (
                f"research-report allowlist must not contain MCP tool name: {name}"
            )
