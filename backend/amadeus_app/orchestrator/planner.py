from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..orchestrator_integrations.mcp_client import mcp_manager
from .capabilities import capability_catalog, prompt_requires_code_agent
from .domain import OrchestratorSettings
from .opencode_routing import evaluate_opencode_routing


DOCUMENT_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".md",
    ".markdown",
    ".txt",
    ".html",
    ".htm",
)

_DOCUMENT_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?[^\s\"'`，。；;、]+(?:\.pdf|\.docx?|\.xlsx?|\.pptx?|\.md|\.markdown|\.txt|\.html?))",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OrchestratorPlan:
    steps: list[dict[str, Any]]
    context: dict[str, Any]


class OrchestratorPlanner:
    """Builds deterministic execution plans from user intent and available capabilities."""

    version = "orchestrator_planner_v1"

    def build_plan(
        self,
        prompt: str,
        *,
        settings: OrchestratorSettings | None = None,
        mcp_snapshot: dict[str, Any] | None = None,
    ) -> OrchestratorPlan:
        available_capabilities = _available_capability_names(settings)
        snapshot = mcp_snapshot if mcp_snapshot is not None else _mcp_capability_snapshot()
        reasons: list[str] = []
        steps: list[dict[str, Any]] = []

        def enabled(name: str) -> bool:
            return name in available_capabilities

        if _prompt_asks_memory(prompt) and enabled("memory"):
            reasons.append("memory_intent")
            steps.append(
                {
                    "id": "memory",
                    "worker": "memory",
                    "goal": "按需检索长期记忆。",
                    "capability": "memory",
                    "args": {"query": prompt},
                }
            )

        if _prompt_asks_web(prompt) and enabled("web_search"):
            reasons.append("web_intent")
            steps.append(
                {
                    "id": "web_search",
                    "worker": "researcher",
                    "goal": "通过 web_search capability 收集外部资料。",
                    "capability": "web_search",
                    "args": {"query": prompt, "intent": prompt, "maxResults": 5},
                }
            )

        document_path = _extract_document_path(prompt)
        if document_path and enabled("document_convert"):
            reasons.append("document_convert_intent")
            steps.append(
                {
                    "id": "document_convert",
                    "worker": "researcher",
                    "goal": "将文档转换为可供后续处理的 Markdown。",
                    "capability": "document_convert",
                    "args": {"path": document_path},
                }
            )

        asks_code_search = _prompt_asks_code_search(prompt)
        code_agent_heuristic = prompt_requires_code_agent(prompt)
        code_routing = evaluate_opencode_routing(
            prompt=prompt,
            config=settings.opencode_routing if settings is not None else None,
            opencode_enabled=settings.opencode_enabled if settings is not None else True,
            heuristic_requires_code_agent=code_agent_heuristic,
        )
        if code_routing.allowed and enabled("code_agent"):
            reasons.append("code_agent_intent")
            if enabled("code_search"):
                steps.append(
                    {
                        "id": "code_context",
                        "worker": "researcher",
                        "goal": "检索相关代码上下文。",
                        "capability": "code_search",
                        "args": {"query": _code_search_query_from_prompt(prompt), "path": ".", "maxResults": 50},
                    }
                )
            steps.append(
                {
                    "id": "code",
                    "worker": "coder",
                    "goal": "需要代码变更或命令执行时，通过 code_agent capability 委托。",
                    "capability": "code_agent",
                    "args": {"prompt": prompt},
                }
            )
        else:
            if code_agent_heuristic:
                reasons.append("code_agent_denied")
            if asks_code_search and enabled("code_search"):
                reasons.append("code_search_intent")
                steps.append(
                    {
                        "id": "code_search",
                        "worker": "researcher",
                        "goal": "检索代码或文件中的相关位置。",
                        "capability": "code_search",
                        "args": {"query": _code_search_query_from_prompt(prompt), "path": ".", "maxResults": 50},
                    }
                )

        if not steps and enabled("file_read"):
            reasons.append("default_file_context")
            steps.append(
                {
                    "id": "research",
                    "worker": "researcher",
                    "goal": "使用只读能力收集上下文。",
                    "capability": "file_read",
                    "args": {"action": "list", "path": ".", "maxEntries": 80},
                }
            )

        steps.append(
            {
                "id": "summarize",
                "worker": "summarizer",
                "goal": "输出结构化结果。",
                "capability": "",
            }
        )
        return OrchestratorPlan(
            steps=steps,
            context={
                "plannerVersion": self.version,
                "availableCapabilities": sorted(available_capabilities),
                "mcpCapabilities": snapshot,
                "opencodeRouting": code_routing.model_dump(by_alias=True),
                "reasons": reasons,
            },
        )


default_orchestrator_planner = OrchestratorPlanner()


def _available_capability_names(settings: OrchestratorSettings | None) -> set[str]:
    catalog_names = {item.name for item in capability_catalog()}
    if settings is not None and not settings.browser_enabled:
        catalog_names.discard("browser")
    configured = set(settings.enabled_capabilities if settings else [])
    return catalog_names if not configured else catalog_names.intersection(configured)


def _mcp_capability_snapshot() -> dict[str, Any]:
    try:
        raw_cache = mcp_manager.list_capability_cache()
    except Exception as error:  # noqa: BLE001
        return {"available": False, "error": str(error) or error.__class__.__name__, "servers": {}}
    servers: dict[str, Any] = {}
    for server_id, item in raw_cache.items():
        if not isinstance(item, dict):
            continue
        servers[str(server_id)] = {
            "tools": _compact_named_items(item.get("tools")),
            "resources": _compact_named_items(item.get("resources"), key="uri"),
            "prompts": _compact_named_items(item.get("prompts")),
            "lastConnectedAt": item.get("lastConnectedAt"),
            "lastError": item.get("lastError"),
        }
    return {"available": bool(servers), "servers": servers}


def _compact_named_items(value: Any, *, key: str = "name") -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    compacted: list[dict[str, str]] = []
    for item in value[:50]:
        if not isinstance(item, dict):
            continue
        name = item.get(key) or item.get("name") or item.get("uri")
        if not name:
            continue
        compacted.append(
            {
                "name": str(name),
                "description": str(item.get("description") or "")[:240],
            }
        )
    return compacted


def _prompt_asks_web(prompt: str) -> bool:
    text = prompt.lower()
    keywords = ("搜索", "查询", "查一下", "检索", "上网", "web", "search", "资料", "新闻", "最新")
    return any(keyword in text for keyword in keywords)


def _prompt_asks_memory(prompt: str) -> bool:
    text = prompt.lower()
    keywords = ("之前", "继续", "记得", "回忆", "历史", "memory", "remember", "previous")
    return any(keyword in text for keyword in keywords)


def _prompt_asks_code_search(prompt: str) -> bool:
    text = prompt.lower()
    intent_terms = ("查找", "搜索", "在哪", "位置", "引用", "定义", "解释", "阅读", "search", "find", "where", "reference")
    code_terms = ("代码", "函数", "组件", "类", "接口", "文件", "api", "backend", "frontend", "python", "typescript", "react", "hook")
    return any(term in text for term in intent_terms) and any(term in text for term in code_terms)


def _extract_document_path(prompt: str) -> str:
    text = prompt.strip()
    if not _prompt_asks_document_convert(text):
        return ""
    match = _DOCUMENT_PATH_RE.search(text)
    if not match:
        return ""
    return match.group("path").strip().strip("()[]{}<>")


def _prompt_asks_document_convert(prompt: str) -> bool:
    text = prompt.lower()
    if not any(ext in text for ext in DOCUMENT_EXTENSIONS):
        return False
    intent_terms = ("转换", "转成", "转为", "解析", "提取", "读取", "导入", "convert", "markdown", "extract", "parse")
    return any(term in text for term in intent_terms)


def _code_search_query_from_prompt(prompt: str) -> str:
    text = prompt.strip()
    for marker in ("`", "\"", "'"):
        if marker in text:
            parts = text.split(marker)
            if len(parts) >= 3 and parts[1].strip():
                return parts[1].strip()
    tokens = [token for token in text.replace("，", " ").replace("。", " ").split() if _looks_code_like(token)]
    return tokens[-1] if tokens else text


def _looks_code_like(value: str) -> bool:
    return any(char in value for char in ("_", ".", "/", "\\", "(", ")")) or any(char.isascii() and char.isalpha() for char in value)
