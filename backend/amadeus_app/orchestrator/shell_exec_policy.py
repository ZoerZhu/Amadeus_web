"""Risk classification for shell_exec commands."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ShellCommandRisk:
    risk: str  # "safe" | "confirm" | "dangerous"
    reason: str
    auto_approved: bool
    command_preview: str
    matched_pattern: str = ""


# Low-risk command patterns (auto-approved in trust mode)
_LOW_RISK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("npm test/build/lint/typecheck", re.compile(
        r"^(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:test|build|lint|typecheck|tsc|check)\b",
        re.IGNORECASE,
    )),
    ("python pytest/compileall", re.compile(
        r"^python(?:3)?\s+-m\s+(?:pytest|compileall|py_compile)\b",
        re.IGNORECASE,
    )),
    ("git read-only", re.compile(
        r"^git\s+(?:status|diff|log|show|branch|blame)\b",
        re.IGNORECASE,
    )),
    ("rg/search", re.compile(
        r"^(?:rg|ripgrep|grep|findstr|find)\b",
        re.IGNORECASE,
    )),
    ("dir/ls/cat/Get-Content", re.compile(
        r"^(?:dir|ls|cat|Get-Content|gc|type|head|tail|more|less)\b",
        re.IGNORECASE,
    )),
    ("echo/type", re.compile(r"^(?:echo|type|printf|print)\b", re.IGNORECASE)),
    ("where/which", re.compile(r"^(?:where|which)\b", re.IGNORECASE)),
]

# Dangerous command patterns (always require permission, never auto-approved)
_DANGEROUS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("rm -rf", re.compile(r"\brm\s+(-[a-z]*r[a-z]*\s+)?-?\s*/", re.IGNORECASE)),
    ("force delete", re.compile(r"\brm\s+-rf\b", re.IGNORECASE)),
    ("git push", re.compile(r"^git\s+push\b", re.IGNORECASE)),
    ("git commit", re.compile(r"^git\s+commit\b", re.IGNORECASE)),
    ("git reset --hard", re.compile(r"^git\s+reset\s+--hard\b", re.IGNORECASE)),
    ("curl/wget download", re.compile(r"^(?:curl|wget|Invoke-WebRequest|iwr)\b", re.IGNORECASE)),
    ("pip/npm install global", re.compile(
        r"^(?:npm|pnpm|yarn)\s+(?:install|add|i)\s+.*-g\b",
        re.IGNORECASE,
    )),
    ("sudo", re.compile(r"^\s*sudo\b", re.IGNORECASE)),
    ("chmod", re.compile(r"^chmod\b", re.IGNORECASE)),
    ("format/del", re.compile(r"^(?:format|del|erase)\b", re.IGNORECASE)),
]

# Confirm-level patterns (require permission even in trust mode)
_CONFIRM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("install dependencies", re.compile(
        r"^(?:npm|pnpm|yarn)\s+(?:install|add|i)\b",
        re.IGNORECASE,
    )),
    ("pip install", re.compile(r"^pip(?:3)?\s+install\b", re.IGNORECASE)),
    ("delete/move", re.compile(r"^(?:del|move|mv|Remove-Item|ri)\b", re.IGNORECASE)),
    ("git commit/push", re.compile(r"^git\s+(?:commit|push|merge|rebase)\b", re.IGNORECASE)),
    ("unknown script", re.compile(
        r"^(?:node|python3?|ruby|perl|bash|sh|powershell|pwsh)\s+\S+\.(?:js|py|rb|pl|sh|ps1)\b",
        re.IGNORECASE,
    )),
    ("background process", re.compile(r"&\s*$|nohup|screen\s", re.IGNORECASE)),
    ("write redirect", re.compile(r">\s*\S")),
]


def classify_shell_command(command: str) -> ShellCommandRisk:
    """Classify a shell command's risk level."""
    raw = command.strip()
    preview = raw[:200]
    if not raw:
        return ShellCommandRisk(
            risk="dangerous", reason="empty command", auto_approved=False, command_preview=""
        )

    # Check dangerous first (highest priority)
    for name, pattern in _DANGEROUS_PATTERNS:
        if pattern.search(raw):
            return ShellCommandRisk(
                risk="dangerous",
                reason=f"dangerous pattern matched: {name}",
                auto_approved=False,
                command_preview=preview,
                matched_pattern=name,
            )

    # Check low-risk (auto-approved)
    for name, pattern in _LOW_RISK_PATTERNS:
        if pattern.match(raw):
            return ShellCommandRisk(
                risk="safe",
                reason=f"low-risk command: {name}",
                auto_approved=True,
                command_preview=preview,
                matched_pattern=name,
            )

    # Check confirm-level
    for name, pattern in _CONFIRM_PATTERNS:
        if pattern.search(raw):
            return ShellCommandRisk(
                risk="confirm",
                reason=f"requires confirmation: {name}",
                auto_approved=False,
                command_preview=preview,
                matched_pattern=name,
            )

    # Default: confirm (unknown command)
    return ShellCommandRisk(
        risk="confirm",
        reason="unknown command, requires confirmation",
        auto_approved=False,
        command_preview=preview,
        matched_pattern="unknown",
    )


def parse_shell_command(command: str) -> dict[str, Any]:
    """Parse a shell command into its components for logging."""
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return {
        "command": command,
        "program": parts[0] if parts else "",
        "arguments": parts[1:] if len(parts) > 1 else [],
        "preview": command[:200],
    }
