from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import CapabilityDefinition, CapabilityRisk


READ_CAPABILITIES = {
    "web_search",
    "file_read",
    "file_list",
    "memory",
    "vision",
    "mcp_resource",
    "document_convert",
    "code_search",
    "todo_task",
    "desktop_screenshot",
    "ask_user",
}

WRITE_CAPABILITIES = {
    "file_write",
    "file_append",
    "file_patch",
    "file_mkdir",
    "doc_writer",
    "code_agent",
    "shell_exec",
}

DANGEROUS_CAPABILITIES = {
    "browser",
    "file_delete",
    "file_move",
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

    def decide_for_write(
        self,
        name: str,
        tool_args: dict[str, Any],
        *,
        workspace_path: str,
        read_external_paths: list[str] | None = None,
        authorized_write_paths: list[str] | None = None,
    ) -> CapabilityDecision:
        """Path-aware decision for file-write tools.

        Enforces the design: "only new non-code files auto-approve in trust mode;
        code/config writes and cross-workspace writes require confirmation."

        - Cross-workspace writes: always require confirmation (even in trust mode).
          If the path is already in ``authorized_write_paths`` (task-scoped), it is
          allowed in trust mode.
        - Code/config writes (new or existing): always require confirmation.
        - Existing-file overwrites: always require confirmation.
        - New non-code files: fall back to ``decide()`` (auto-approve in trust mode).
        """
        # Dangerous tools (file_delete, file_move) keep their base classification.
        base_risk = self.classify(name)
        if base_risk == "dangerous":
            return self.decide(name)

        path_text = str(
            tool_args.get("path")
            or tool_args.get("src")
            or tool_args.get("dst")
            or ""
        )
        if not path_text:
            return self.decide(name)

        workspace_root = Path(workspace_path or ".").expanduser().resolve()
        candidate = Path(path_text).expanduser()
        if not candidate.is_absolute():
            candidate = workspace_root / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            return self.decide(name)

        is_external = not _is_within(resolved, workspace_root)

        if is_external:
            # Cross-workspace write: check task-scoped authorization
            authorized = [
                Path(p).expanduser().resolve()
                for p in (authorized_write_paths or [])
                if p
            ]
            is_authorized = any(
                _is_within(resolved, root) or _is_within(root, resolved)
                for root in authorized
            )
            if not is_authorized:
                return CapabilityDecision(
                    name=name,
                    risk="confirm",
                    allowed=False,
                    reason="cross-workspace write requires authorization",
                )
            # Authorized cross-workspace write: still requires confirmation unless trust mode
            if self.trust_mode:
                return CapabilityDecision(
                    name=name,
                    risk="confirm",
                    allowed=True,
                    reason="trustMode allows authorized cross-workspace write",
                )
            return CapabilityDecision(
                name=name,
                risk="confirm",
                allowed=False,
                reason="cross-workspace write requires confirmation",
            )

        # Workspace-internal write: check code/config suffix and existing files
        if _is_code_or_config(resolved):
            return CapabilityDecision(
                name=name,
                risk="confirm",
                allowed=False,
                reason="code/config file write requires confirmation",
            )
        if resolved.exists():
            return CapabilityDecision(
                name=name,
                risk="confirm",
                allowed=False,
                reason="overwriting existing file requires confirmation",
            )

        # New non-code file in workspace: fall back to default policy
        return self.decide(name)


def capability_catalog() -> list[CapabilityDefinition]:
    return [
        CapabilityDefinition(name="web_search", description="Search the web through built-in or MCP-backed providers.", risk="safe", workerRoles=["researcher"]),
        CapabilityDefinition(name="file_read", description="Read a single workspace file. Returns content, sha256, sizeBytes, mtime, encoding, truncated.", risk="safe", workerRoles=["researcher", "coder", "writer"]),
        CapabilityDefinition(name="file_list", description="List a directory in the workspace. Returns entries with metadata.", risk="safe", workerRoles=["researcher", "coder", "writer"]),
        CapabilityDefinition(name="code_search", description="Search code/text files. Prefers ripgrep, falls back to Python scan.", risk="safe", workerRoles=["researcher", "coder"]),
        CapabilityDefinition(name="file_write", description="Write content to a file. Overwrites require expectedHash from a prior file_read.", risk="confirm", workerRoles=["writer"]),
        CapabilityDefinition(name="file_append", description="Append content to a file. Existing files require expectedHash.", risk="confirm", workerRoles=["writer"]),
        CapabilityDefinition(name="file_patch", description="Apply an exact oldText→newText replacement. Requires expectedHash.", risk="confirm", workerRoles=["coder"]),
        CapabilityDefinition(name="file_mkdir", description="Create a directory (and parents).", risk="confirm", workerRoles=["writer"]),
        CapabilityDefinition(name="file_move", description="Move a file. Always requires confirmation.", risk="dangerous", workerRoles=["writer"]),
        CapabilityDefinition(name="file_delete", description="Delete a regular file or empty directory. Always requires confirmation.", risk="dangerous", workerRoles=["writer"]),
        CapabilityDefinition(name="code_agent", description="Delegate code changes and command execution to OpenCode.", risk="confirm", workerRoles=["coder"]),
        CapabilityDefinition(name="doc_writer", description="Create structured documents.", risk="confirm", workerRoles=["writer"]),
        CapabilityDefinition(name="memory", description="Recall or save structured memory.", risk="safe", workerRoles=["memory"]),
        CapabilityDefinition(name="vision", description="Understand uploaded images or screenshots.", risk="safe", workerRoles=["researcher"]),
        CapabilityDefinition(name="document_convert", description="Convert documents to Markdown via MCP-backed converters or local fallback.", risk="safe", workerRoles=["researcher", "writer"]),
        CapabilityDefinition(name="todo_task", description="Manage structured local todo tasks through the task graph.", risk="safe", workerRoles=["coordinator"]),
        CapabilityDefinition(name="browser", description="Open, inspect, screenshot, or interact with web pages.", risk="dangerous", workerRoles=["browser"]),
        CapabilityDefinition(name="mcp_resource", description="Read MCP resources and prompts through adapters.", risk="safe", workerRoles=["researcher"]),
        CapabilityDefinition(name="shell_exec", description="Execute shell commands in the task workspace. Low-risk commands auto-approve in trust mode; high-risk commands require permission.", risk="confirm", workerRoles=["coder"]),
        CapabilityDefinition(name="desktop_screenshot", description="Capture a desktop screenshot for visual context. Only available in Electron environment with screenshot enabled.", risk="safe", workerRoles=["researcher"]),
        CapabilityDefinition(name="ask_user", description="Ask the user a question and wait for their answer. Use when you need clarification or a decision before proceeding.", risk="safe", workerRoles=["coordinator", "coder", "writer"]),
    ]


_CODE_CONFIG_SUFFIXES = frozenset({
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh", ".ps1",
    ".bat", ".cmd", ".env", ".vue", ".svelte", ".go", ".rs", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php",
})


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_code_or_config(path: Path) -> bool:
    name = path.name
    if name == ".env" or name.startswith(".env."):
        return True
    return path.suffix.lower() in _CODE_CONFIG_SUFFIXES


def prompt_requires_code_agent(prompt: str) -> bool:
    text = prompt.lower()
    change_terms = ("修改", "实现", "修复", "新增", "重构", "提交", "运行测试", "write", "modify", "fix", "implement", "refactor")
    code_terms = ("代码", "文件", "函数", "组件", "api", "backend", "frontend", "python", "typescript", "react", "opencode")
    return any(term in text for term in change_terms) and any(term in text for term in code_terms)
