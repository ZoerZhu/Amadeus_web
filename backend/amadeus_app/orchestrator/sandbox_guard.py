# backend/amadeus_app/orchestrator/sandbox_guard.py
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SandboxResult:
    allowed: bool
    reason: str = ""
    action: str = "pass"        # "pass" | "block" | "dry_run"
    preview: str = ""           # dry_run mode command preview


class SandboxGuard:
    """Application-level security guard that blocks dangerous commands
    and validates path boundaries for subprocess execution."""

    BLOCKED_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"\bdel\s+/[fsq]\s+[A-Z]:", re.IGNORECASE),
        re.compile(r"\brmdir\s+/s", re.IGNORECASE),
        re.compile(r"\berase\s+/[fsq]", re.IGNORECASE),
        re.compile(r"\bformat\s+[A-Z]:", re.IGNORECASE),
        re.compile(r"\breg\s+delete", re.IGNORECASE),
        re.compile(r"\bdiskpart", re.IGNORECASE),
        re.compile(r"\bshutdown\s+/", re.IGNORECASE),
        re.compile(r"\btaskkill\s+/f\s+/im", re.IGNORECASE),
        re.compile(r"\.\.[/\\]", re.IGNORECASE),
        re.compile(r"\brm\s+-rf\s+/", re.IGNORECASE),
        re.compile(r"\bcd\s+[A-Z]:\\Windows", re.IGNORECASE),
        re.compile(r"\bicacls\s+.*\s+/deny", re.IGNORECASE),
        re.compile(r"\bnet\s+(user|localgroup)\s+/add", re.IGNORECASE),
        re.compile(r"\bschtasks\s+/create", re.IGNORECASE),
        re.compile(r"\breg\s+add\s+.*\\Run", re.IGNORECASE),
    ]

    SENSITIVE_ENV_KEYS = {
        "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
        "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
        "SYSTEMROOT", "WINDIR", "COMSPEC",
        "USERNAME", "USERDOMAIN", "LOGONSERVER",
    }

    def __init__(self, workspace_path: str, mode: str = "guard") -> None:
        self._workspace = Path(workspace_path or ".").expanduser().resolve()
        self._mode = mode if mode in ("off", "guard", "strict") else "guard"

    def check_command(self, command: str, risk_level: str = "") -> SandboxResult:
        if self._mode == "off":
            return SandboxResult(allowed=True)

        for pattern in self.BLOCKED_PATTERNS:
            if pattern.search(command):
                return SandboxResult(
                    allowed=False,
                    reason=f"Blocked by sandbox: matched pattern '{pattern.pattern}'",
                    action="block",
                )

        if self._mode == "strict" and risk_level == "dangerous":
            return SandboxResult(
                allowed=False,
                reason="Dry-run: dangerous command requires confirmation",
                action="dry_run",
                preview=command,
            )

        return SandboxResult(allowed=True)

    def check_path(self, raw_path: str) -> bool:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = self._workspace / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self._workspace)
            return True
        except ValueError:
            return False

    def sanitize_env(self, env: dict[str, str] | None = None) -> dict[str, str]:
        clean = dict(env or os.environ)

        for key in self.SENSITIVE_ENV_KEYS:
            if key in clean:
                del clean[key]

        clean["USERPROFILE"] = str(self._workspace)

        # Only filter PATH in strict mode — guard mode keeps system tools accessible (I2 fix)
        if self._mode == "strict" and "PATH" in clean:
            path_parts = clean["PATH"].split(";")
            filtered = [
                p for p in path_parts
                if p and not any(
                    sys_dir in p.lower()
                    for sys_dir in ("\\windows\\", "\\program files\\", "\\programdata\\")
                )
            ]
            clean["PATH"] = ";".join(filtered)

        return clean
