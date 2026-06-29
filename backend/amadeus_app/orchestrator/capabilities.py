from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .domain import CapabilityDefinition, CapabilityRisk


READ_CAPABILITIES = {
    "web_search",
    "file_read",
    "memory",
    "vision",
    "mcp_resource",
    "document_convert",
    "code_search",
    "todo_task",
}

WRITE_CAPABILITIES = {
    "file_write",
    "doc_writer",
    "code_agent",
}

DANGEROUS_CAPABILITIES = {
    "browser",
}


@dataclass
class CapabilityDecision:
    name: str
    risk: CapabilityRisk
    allowed: bool
    reason: str


class CapabilityGateway:
    """Central capability policy for orchestrator workers.

    The first implementation establishes a single decision point. Concrete
    capability adapters can be added incrementally without changing worker code.
    """

    def __init__(self, *, trust_mode: bool = True, raw_mcp_allowed: bool = False) -> None:
        self.trust_mode = trust_mode
        self.raw_mcp_allowed = raw_mcp_allowed

    def list_capabilities(self) -> list[dict[str, Any]]:
        return [item.model_dump(by_alias=True) for item in capability_catalog()]

    def classify(self, name: str) -> CapabilityRisk:
        if name in DANGEROUS_CAPABILITIES:
            return "dangerous"
        if name in WRITE_CAPABILITIES:
            return "confirm"
        if name in READ_CAPABILITIES:
            return "safe"
        if name.startswith("mcp__"):
            return "confirm" if self.raw_mcp_allowed else "dangerous"
        return "confirm"

    def decide(self, name: str) -> CapabilityDecision:
        risk = self.classify(name)
        if risk == "safe":
            return CapabilityDecision(name=name, risk=risk, allowed=True, reason="safe capability")
        if risk == "confirm" and self.trust_mode:
            return CapabilityDecision(name=name, risk=risk, allowed=True, reason="trustMode allows confirm capability")
        return CapabilityDecision(name=name, risk=risk, allowed=False, reason=f"{risk} capability requires approval")


def capability_catalog() -> list[CapabilityDefinition]:
    return [
        CapabilityDefinition(name="web_search", description="Search the web through built-in or MCP-backed providers.", risk="safe", workerRoles=["researcher"]),
        CapabilityDefinition(name="file_read", description="Read allowed workspace files.", risk="safe", workerRoles=["researcher", "coder", "writer"]),
        CapabilityDefinition(name="code_search", description="Search code and text files through MCP-backed search or local workspace fallback.", risk="safe", workerRoles=["researcher", "coder"]),
        CapabilityDefinition(name="file_write", description="Write non-code artifacts and files.", risk="confirm", workerRoles=["writer"]),
        CapabilityDefinition(name="code_agent", description="Delegate code changes and command execution to OpenCode.", risk="confirm", workerRoles=["coder"]),
        CapabilityDefinition(name="doc_writer", description="Create structured documents.", risk="confirm", workerRoles=["writer"]),
        CapabilityDefinition(name="memory", description="Recall or save structured memory.", risk="safe", workerRoles=["memory"]),
        CapabilityDefinition(name="vision", description="Understand uploaded images or screenshots.", risk="safe", workerRoles=["researcher"]),
        CapabilityDefinition(name="document_convert", description="Convert documents to Markdown via MCP-backed converters or local fallback.", risk="safe", workerRoles=["researcher", "writer"]),
        CapabilityDefinition(name="todo_task", description="Manage structured local todo tasks through the task graph.", risk="safe", workerRoles=["coordinator"]),
        CapabilityDefinition(name="browser", description="Open, inspect, screenshot, or interact with web pages.", risk="dangerous", workerRoles=["browser"]),
        CapabilityDefinition(name="mcp_resource", description="Read MCP resources and prompts through adapters.", risk="safe", workerRoles=["researcher"]),
    ]


def prompt_requires_code_agent(prompt: str) -> bool:
    text = prompt.lower()
    change_terms = ("修改", "实现", "修复", "新增", "重构", "提交", "运行测试", "write", "modify", "fix", "implement", "refactor")
    code_terms = ("代码", "文件", "函数", "组件", "api", "backend", "frontend", "python", "typescript", "react", "opencode")
    return any(term in text for term in change_terms) and any(term in text for term in code_terms)
