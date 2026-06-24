"""Playwright Chromium browser automation tool.

Provides: open, click, type, screenshot, extract_text, download.

The browser runs in an isolated Playwright-managed context and never
controls the user's default browser. Screenshots are saved as task
artifacts.

If Playwright is not installed, the tool returns a clear error directing
the user to install it (``pip install playwright && playwright install chromium``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..logging_config import get_logger
from . import agent_storage
from .tool_registry import UnifiedTool

_log = get_logger(__name__)


def _check_playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


class BrowserSession:
    """Manages a single Playwright browser instance for a task."""

    def __init__(self, *, artifact_root: str, task_id: str) -> None:
        self._artifact_root = Path(artifact_root) / task_id / "browser"
        self._task_id = task_id
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._downloads: list[dict[str, Any]] = []

    async def start(self) -> None:
        if not _check_playwright_available():
            raise RuntimeError(
                "Playwright is not installed. Install with: pip install playwright && playwright install chromium"
            )
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._context = await self._browser.new_context(accept_downloads=True)
        self._page = await self._context.new_page()
        self._artifact_root.mkdir(parents=True, exist_ok=True)

    async def close(self) -> None:
        for resource in (self._page, self._context, self._browser, self._playwright):
            if resource is None:
                continue
            try:
                await resource.close()
            except Exception:  # noqa: BLE001
                pass
        self._page = self._context = self._browser = self._playwright = None

    async def open(self, url: str) -> dict[str, Any]:
        if self._page is None:
            raise RuntimeError("browser not started")
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return {"url": self._page.url, "title": await self._page.title()}

    async def click(self, selector: str) -> dict[str, Any]:
        if self._page is None:
            raise RuntimeError("browser not started")
        await self._page.click(selector, timeout=10000)
        return {"selector": selector, "url": self._page.url}

    async def type(self, selector: str, text: str) -> dict[str, Any]:
        if self._page is None:
            raise RuntimeError("browser not started")
        await self._page.fill(selector, text, timeout=10000)
        return {"selector": selector, "text": text}

    async def screenshot(self, *, storage, full_page: bool = False) -> dict[str, Any]:
        if self._page is None:
            raise RuntimeError("browser not started")
        shot_path = self._artifact_root / f"shot_{len(self._downloads):04d}.png"
        await self._page.screenshot(path=str(shot_path), full_page=full_page)
        size = shot_path.stat().st_size
        artifact = await agent_storage.create_artifact(
            storage,
            task_id=self._task_id,
            kind="screenshot",
            name=shot_path.name,
            path=str(shot_path),
            mime_type="image/png",
            size_bytes=size,
            description=f"browser screenshot of {self._page.url}",
            meta={"url": self._page.url, "fullPage": full_page},
        )
        return {"path": str(shot_path), "artifactId": artifact["id"], "sizeBytes": size}

    async def extract_text(self) -> dict[str, Any]:
        if self._page is None:
            raise RuntimeError("browser not started")
        text = await self._page.inner_text("body")
        return {"url": self._page.url, "text": text[:20000]}

    async def current_url(self) -> str:
        return self._page.url if self._page is not None else ""


def make_browser_tool(*, storage, task_id: str, artifact_root: str) -> UnifiedTool:
    """Build a task-scoped ``browser`` tool that drives a Playwright session.

    The session is lazily started on first call and reused for subsequent
    calls within the same task.
    """
    session = BrowserSession(artifact_root=artifact_root, task_id=task_id)
    state: dict[str, Any] = {"started": False}

    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action", "")).lower()
        if action == "open":
            if not state["started"]:
                await session.start()
                state["started"] = True
            return await session.open(str(args.get("url", "")))
        if not state["started"]:
            return {"ok": False, "error": "browser not started; call action=open first"}
        if action == "click":
            return await session.click(str(args.get("selector", "")))
        if action == "type":
            return await session.type(str(args.get("selector", "")), str(args.get("text", "")))
        if action == "screenshot":
            return await session.screenshot(storage=storage, full_page=bool(args.get("fullPage", False)))
        if action == "extract_text":
            return await session.extract_text()
        if action == "close":
            await session.close()
            state["started"] = False
            return {"ok": True, "summary": "browser closed"}
        return {"ok": False, "error": f"unknown browser action: {action}"}

    return UnifiedTool(
        name="browser",
        description="Drive a headless Playwright Chromium browser. Actions: open, click, type, screenshot, extract_text, close. "
                    "Screenshots are saved as task artifacts. Requires playwright + chromium installed.",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["open", "click", "type", "screenshot", "extract_text", "close"],
                    "description": "Browser action to perform",
                },
                "url": {"type": "string", "description": "URL to navigate to (action=open)"},
                "selector": {"type": "string", "description": "CSS selector (action=click/type)"},
                "text": {"type": "string", "description": "Text to type (action=type)"},
                "fullPage": {"type": "boolean", "description": "Capture full page (action=screenshot)"},
            },
            "required": ["action"],
        },
        handler=handler,
        source="builtin",
        permission="safe",
        meta={"requires": "playwright"},
    )


async def close_browser_session(tool: UnifiedTool) -> None:
    """Best-effort close of any browser session backing a tool."""
    # The session is captured in the handler closure; we trigger close via the tool.
    try:
        await tool.handler({"action": "close"})
    except Exception:  # noqa: BLE001
        pass
