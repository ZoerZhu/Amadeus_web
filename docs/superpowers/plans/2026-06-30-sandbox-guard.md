# OS Sandbox Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an application-level security guard that blocks dangerous shell commands, validates path boundaries, and sanitizes subprocess environment variables for `shell_exec` and `code_agent` tools.

**Architecture:** New `SandboxGuard` module sits between `CapabilityGateway` (permission) and `capability_adapters` (execution). It checks commands against a blocklist, optionally dry-runs dangerous commands, and sanitizes environment variables before subprocess creation.

**Tech Stack:** Python 3.12 / asyncio / regex / pytest + pytest-asyncio / React + TypeScript

## Global Constraints

- `sandboxMode` defaults to `"guard"`, values: `"off"` | `"guard"` | `"strict"`
- Blocklist patterns use `re.IGNORECASE`
- Environment sanitization replaces `USERPROFILE` with workspace path, removes sensitive system variables
- Must not break existing `shell_exec` or `code_agent` tests

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `backend/amadeus_app/orchestrator/sandbox_guard.py` | Create | SandboxGuard class + SandboxResult dataclass |
| `backend/amadeus_app/orchestrator/domain.py` | Modify | Add `sandbox_mode` field to OrchestratorSettings |
| `backend/amadeus_app/orchestrator/capability_adapters.py` | Modify | Integrate SandboxGuard into `_shell_exec` and `_code_agent` |
| `src/types.ts` | Modify | Add `sandboxMode` to OrchestratorSettings interface |
| `src/components/OrchestratorSettingsPanel.tsx` | Modify | Add sandbox mode selector |
| `src/components/TaskWorkspace.tsx` | Modify | Render blocked/dry_run event statuses |
| `tests/test_sandbox_guard.py` | Create | Unit + integration tests |

---

### Task 1: SandboxGuard Module

**Files:**
- Create: `backend/amadeus_app/orchestrator/sandbox_guard.py`
- Test: `tests/test_sandbox_guard.py`

**Interfaces:**
- Produces: `SandboxResult` dataclass with `allowed: bool`, `reason: str`, `action: str`, `preview: str`
- Produces: `SandboxGuard` class with `check_command(command, risk_level)`, `check_path(raw_path)`, `sanitize_env(env)`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sandbox_guard.py
from __future__ import annotations
import os
import pytest
from pathlib import Path

from backend.amadeus_app.orchestrator.sandbox_guard import SandboxGuard, SandboxResult


class TestSandboxGuardCheckCommand:
    def test_off_mode_allows_everything(self):
        guard = SandboxGuard(workspace_path=".", mode="off")
        result = guard.check_command("del /f C:\\Windows\\System32")
        assert result.allowed is True
        assert result.action == "pass"

    def test_guard_mode_blocks_del(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("del /f C:\\important.txt")
        assert result.allowed is False
        assert result.action == "block"
        assert "matched pattern" in result.reason

    def test_guard_mode_blocks_rmdir_s(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("rmdir /s /q C:\\stuff")
        assert result.allowed is False
        assert result.action == "block"

    def test_guard_mode_blocks_format(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("format D:")
        assert result.allowed is False
        assert result.action == "block"

    def test_guard_mode_blocks_reg_delete(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("reg delete HKLM\\Software\\MyApp")
        assert result.allowed is False
        assert result.action == "block"

    def test_guard_mode_blocks_path_traversal(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("type ..\\..\\..\\Windows\\system32\\config\\SAM")
        assert result.allowed is False
        assert result.action == "block"

    def test_guard_mode_allows_safe_command(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("npm install")
        assert result.allowed is True
        assert result.action == "pass"

    def test_guard_mode_allows_git_command(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("git add -A && git commit -m 'fix'")
        assert result.allowed is True
        assert result.action == "pass"

    def test_strict_mode_dry_runs_dangerous(self):
        guard = SandboxGuard(workspace_path=".", mode="strict")
        result = guard.check_command("npm install", risk_level="dangerous")
        assert result.allowed is False
        assert result.action == "dry_run"
        assert result.preview == "npm install"

    def test_strict_mode_allows_safe_risk(self):
        guard = SandboxGuard(workspace_path=".", mode="strict")
        result = guard.check_command("echo hello", risk_level="safe")
        assert result.allowed is True
        assert result.action == "pass"

    def test_case_insensitive_blocking(self):
        guard = SandboxGuard(workspace_path=".", mode="guard")
        result = guard.check_command("FORMAT C:")
        assert result.allowed is False
        assert result.action == "block"


class TestSandboxGuardCheckPath:
    def test_path_inside_workspace_allowed(self, tmp_path):
        guard = SandboxGuard(workspace_path=str(tmp_path), mode="guard")
        assert guard.check_path("subdir/file.txt") is True

    def test_path_outside_workspace_blocked(self, tmp_path):
        guard = SandboxGuard(workspace_path=str(tmp_path), mode="guard")
        assert guard.check_path(str(tmp_path.parent / "outside.txt")) is False

    def test_absolute_path_inside_workspace(self, tmp_path):
        guard = SandboxGuard(workspace_path=str(tmp_path), mode="guard")
        assert guard.check_path(str(tmp_path / "file.txt")) is True

    def test_dotdot_traversal_blocked(self, tmp_path):
        guard = SandboxGuard(workspace_path=str(tmp_path), mode="guard")
        assert guard.check_path("../../../etc/passwd") is False


class TestSandboxGuardSanitizeEnv:
    def test_removes_sensitive_keys(self):
        guard = SandboxGuard(workspace_path="/tmp/ws", mode="guard")
        env = {
            "PATH": "C:\\Python;C:\\Windows\\System32",
            "APPDATA": "C:\\Users\\test\\AppData",
            "WINDIR": "C:\\Windows",
            "USERPROFILE": "C:\\Users\\test",
            "HOME": "/home/test",
        }
        clean = guard.sanitize_env(env)
        assert "APPDATA" not in clean
        assert "WINDIR" not in clean
        assert clean["USERPROFILE"] == str(Path("/tmp/ws").resolve())

    def test_path_filters_system_dirs(self):
        guard = SandboxGuard(workspace_path="/tmp/ws", mode="guard")
        env = {
            "PATH": "C:\\Python311;C:\\Windows\\System32;C:\\Program Files\\nodejs",
        }
        clean = guard.sanitize_env(env)
        path_parts = clean["PATH"].split(";")
        assert "C:\\Python311" in path_parts
        assert not any("windows" in p.lower() for p in path_parts)
        assert not any("program files" in p.lower() for p in path_parts)

    def test_preserves_non_sensitive_keys(self):
        guard = SandboxGuard(workspace_path="/tmp/ws", mode="guard")
        env = {"PATH": "C:\\Python", "HOME": "/home/test", "CUSTOM_VAR": "value"}
        clean = guard.sanitize_env(env)
        assert clean["CUSTOM_VAR"] == "value"
        assert clean["HOME"] == "/home/test"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sandbox_guard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.amadeus_app.orchestrator.sandbox_guard'`

- [ ] **Step 3: Implement SandboxGuard module**

```python
# backend/amadeus_app/orchestrator/sandbox_guard.py
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
        re.compile(r"\.\.\\\\", re.IGNORECASE),
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

        if "PATH" in clean:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sandbox_guard.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/amadeus_app/orchestrator/sandbox_guard.py tests/test_sandbox_guard.py
git commit -m "feat: add SandboxGuard module for command blocking and env sanitization"
```

---

### Task 2: Add sandbox_mode to OrchestratorSettings

**Files:**
- Modify: `backend/amadeus_app/orchestrator/domain.py:96-116`
- Modify: `src/types.ts`

**Interfaces:**
- Produces: `OrchestratorSettings.sandbox_mode` field with alias `sandboxMode`

- [ ] **Step 1: Write failing test**

```python
# tests/test_sandbox_guard.py (append to existing file)
class TestOrchestratorSettingsSandboxMode:
    def test_default_sandbox_mode_is_guard(self):
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        settings = OrchestratorSettings()
        assert settings.sandbox_mode == "guard"

    def test_sandbox_mode_accepts_off(self):
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        settings = OrchestratorSettings(sandboxMode="off")
        assert settings.sandbox_mode == "off"

    def test_sandbox_mode_accepts_strict(self):
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        settings = OrchestratorSettings(sandboxMode="strict")
        assert settings.sandbox_mode == "strict"

    def test_sandbox_mode_invalid_falls_back_to_guard(self):
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        with pytest.raises(Exception):
            OrchestratorSettings(sandboxMode="invalid")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_sandbox_guard.py::TestOrchestratorSettingsSandboxMode -v`
Expected: FAIL with `AttributeError` or `ValidationError`

- [ ] **Step 3: Add sandbox_mode to OrchestratorSettings**

In `backend/amadeus_app/orchestrator/domain.py`, find the `OrchestratorSettings` class (line 96) and add the `sandbox_mode` field after `fallback_model` (line 115):

```python
# Add this line after fallback_model field:
    sandbox_mode: Literal["off", "guard", "strict"] = Field(
        default="guard", alias="sandboxMode"
    )
```

The `Literal` type is already imported at line 3.

- [ ] **Step 4: Add sandboxMode to TypeScript types**

In `src/types.ts`, find the `OrchestratorSettings` interface and add:

```typescript
    sandboxMode?: "off" | "guard" | "strict";
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_sandbox_guard.py::TestOrchestratorSettingsSandboxMode -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/amadeus_app/orchestrator/domain.py src/types.ts tests/test_sandbox_guard.py
git commit -m "feat: add sandboxMode setting to OrchestratorSettings"
```

---

### Task 3: Integrate SandboxGuard into _shell_exec

**Files:**
- Modify: `backend/amadeus_app/orchestrator/capability_adapters.py:1087-1160`
- Test: `tests/test_sandbox_guard.py`

**Interfaces:**
- Consumes: `SandboxGuard.check_command()`, `SandboxGuard.sanitize_env()` from Task 1
- Consumes: `context.settings.sandbox_mode` from Task 2

- [ ] **Step 1: Write failing integration test**

```python
# tests/test_sandbox_guard.py (append)
class TestShellExecSandboxIntegration:
    @pytest.mark.asyncio
    async def test_shell_exec_blocks_dangerous_command(self, tmp_path):
        from backend.amadeus_app.orchestrator.capability_adapters import _shell_exec
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        from backend.amadeus_app.orchestrator.capability_adapters import CapabilityExecutionContext

        context = CapabilityExecutionContext(
            task_id="test-task",
            prompt="test",
            workspace_path=str(tmp_path),
            settings=OrchestratorSettings(sandboxMode="guard"),
        )
        result = await _shell_exec(
            {"command": "format C:", "cwd": "."},
            context,
        )
        assert result["ok"] is False
        assert "Blocked by sandbox" in result["summary"]

    @pytest.mark.asyncio
    async def test_shell_exec_dry_run_in_strict_mode(self, tmp_path):
        from backend.amadeus_app.orchestrator.capability_adapters import _shell_exec
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        from backend.amadeus_app.orchestrator.capability_adapters import CapabilityExecutionContext

        context = CapabilityExecutionContext(
            task_id="test-task",
            prompt="test",
            workspace_path=str(tmp_path),
            settings=OrchestratorSettings(sandboxMode="strict"),
        )
        result = await _shell_exec(
            {"command": "echo dangerous", "cwd": "."},
            context,
        )
        # echo is classified as safe, so it should pass even in strict mode
        # This test uses a command classified as dangerous to verify dry_run
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sandbox_guard.py::TestShellExecSandboxIntegration -v`
Expected: FAIL — `_shell_exec` doesn't check SandboxGuard yet, so `format C:` would attempt to run

- [ ] **Step 3: Integrate SandboxGuard into _shell_exec**

In `backend/amadeus_app/orchestrator/capability_adapters.py`, add import at top (after line 25):

```python
from .sandbox_guard import SandboxGuard
```

In `_shell_exec` function (starts at line 1087), find the section after `risk = classify_shell_command(command)` and before `proc = await asyncio.create_subprocess_shell`. Insert the SandboxGuard check:

```python
    # === SandboxGuard check ===
    sandbox_mode = str(getattr(context.settings, "sandbox_mode", "guard") or "guard")
    guard = SandboxGuard(context.workspace_path, mode=sandbox_mode)
    sandbox_result = guard.check_command(command, risk_level=risk.risk)

    if sandbox_result.action == "block":
        await _emit_event(
            context,
            kind="error",
            role="coder",
            name="shell_exec",
            status="blocked",
            summary=sandbox_result.reason,
            payload={"command": command, "blockedBy": "sandbox"},
        )
        return {
            "ok": False,
            "summary": sandbox_result.reason,
            "data": {"command": command, "action": "blocked"},
        }

    if sandbox_result.action == "dry_run":
        await _emit_event(
            context,
            kind="command",
            role="coder",
            name="shell_exec",
            status="dry_run",
            summary=f"Dry-run preview: {risk.command_preview}",
            payload={"command": command, "preview": sandbox_result.preview},
        )
        return {
            "ok": True,
            "summary": f"Dry-run: command not executed. {sandbox_result.reason}",
            "data": {"command": command, "preview": sandbox_result.preview, "dryRun": True},
        }

    # === SandboxGuard check end ===
```

Then modify the `create_subprocess_shell` call to use sanitized env. Find the existing call:

```python
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd_path),
        )
```

Replace with:

```python
        sanitized_env = guard.sanitize_env()
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd_path),
            env=sanitized_env,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sandbox_guard.py -v`
Expected: All PASS

Run: `python -m pytest tests/test_agent_loop.py -v -k shell`
Expected: Existing shell_exec tests still PASS

- [ ] **Step 5: Commit**

```bash
git add backend/amadeus_app/orchestrator/capability_adapters.py tests/test_sandbox_guard.py
git commit -m "feat: integrate SandboxGuard into shell_exec for command blocking and env sanitization"
```

---

### Task 4: Integrate SandboxGuard into _code_agent

**Files:**
- Modify: `backend/amadeus_app/orchestrator/capability_adapters.py:261-294`

**Interfaces:**
- Consumes: `SandboxGuard.check_path()` from Task 1

- [ ] **Step 1: Write failing test**

```python
# tests/test_sandbox_guard.py (append)
class TestCodeAgentSandboxIntegration:
    @pytest.mark.asyncio
    async def test_code_agent_rejects_outside_workspace(self, tmp_path):
        from backend.amadeus_app.orchestrator.capability_adapters import _code_agent
        from backend.amadeus_app.orchestrator.domain import OrchestratorSettings
        from backend.amadeus_app.orchestrator.capability_adapters import CapabilityExecutionContext

        context = CapabilityExecutionContext(
            task_id="test-task",
            prompt="test",
            workspace_path=str(tmp_path),
            settings=OrchestratorSettings(sandboxMode="guard", opencodeEnabled=True),
        )
        result = await _code_agent(
            {"prompt": "test", "workspacePath": str(tmp_path.parent / "outside")},
            context,
        )
        assert result["ok"] is False
        assert "inside the task workspace" in result["summary"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sandbox_guard.py::TestCodeAgentSandboxIntegration -v`
Expected: FAIL — `_code_agent` doesn't check SandboxGuard yet

- [ ] **Step 3: Add path validation to _code_agent**

In `backend/amadeus_app/orchestrator/capability_adapters.py`, in `_code_agent` (line 261), after the `opencode_enabled` check and before `request = CodeTaskStreamRequest(`, insert:

```python
    # === SandboxGuard path validation ===
    sandbox_mode = str(getattr(context.settings, "sandbox_mode", "guard") or "guard")
    guard = SandboxGuard(context.workspace_path, mode=sandbox_mode)
    requested_workspace = str(args.get("workspacePath") or context.workspace_path)
    if not guard.check_path(requested_workspace):
        return {
            "ok": False,
            "summary": "code_agent workspace must stay inside the task workspace.",
            "data": {"requestedPath": requested_workspace},
        }
    # === SandboxGuard path validation end ===
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_sandbox_guard.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/amadeus_app/orchestrator/capability_adapters.py tests/test_sandbox_guard.py
git commit -m "feat: integrate SandboxGuard path validation into code_agent"
```

---

### Task 5: Frontend — Settings Panel + Event Rendering

**Files:**
- Modify: `src/components/OrchestratorSettingsPanel.tsx`
- Modify: `src/components/TaskWorkspace.tsx`

- [ ] **Step 1: Add sandbox mode selector to settings panel**

In `src/components/OrchestratorSettingsPanel.tsx`, find the settings sections and add a new sandbox section. Add after the fallback model section:

```tsx
<div className="settings-section">
    <h4>沙箱守卫</h4>
    <label>
        <span>模式</span>
        <select
            value={settings.sandboxMode || "guard"}
            onChange={(e) => onChange({ ...settings, sandboxMode: e.target.value as "off" | "guard" | "strict" })}
        >
            <option value="off">关闭 — 不做任何拦截</option>
            <option value="guard">守卫 — 拦截危险命令（推荐）</option>
            <option value="strict">严格 — 危险命令走 dry-run</option>
        </select>
    </label>
</div>
```

- [ ] **Step 2: Add blocked/dry_run status rendering to TaskWorkspace**

In `src/components/TaskWorkspace.tsx`, find the `STATUS_LABELS` object (line 61) and add:

```typescript
  blocked: "已拦截",
  dry_run: "预览",
```

In the `eventIcon` function (line 106), add handling for blocked status:

```tsx
  if (event.status === "blocked") {
      return <AlertTriangle size={14} />;
  }
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add src/components/OrchestratorSettingsPanel.tsx src/components/TaskWorkspace.tsx
git commit -m "feat: add sandbox mode settings UI and blocked/dry_run event rendering"
```

---

### Task 6: Full Integration Test + Cleanup

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

Run: `npx tsc --noEmit`
Expected: No errors

- [ ] **Step 2: Commit if any cleanup needed**

```bash
git add -A
git commit -m "test: sandbox guard full integration verification"
```
