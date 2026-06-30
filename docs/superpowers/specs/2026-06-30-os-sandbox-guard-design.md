# OS 沙箱守卫设计文档

**日期:** 2026-06-30
**状态:** Approved

## 目标

为 Agent 的命令执行（`shell_exec`）和代码执行（`code_agent`）提供应用级安全守卫层，防止 Agent 通过子进程越权访问工作区外的文件系统、执行危险系统命令。

当前安全机制（路径守卫、命令分类、权限网关、变更追踪）均为应用层检查，**不覆盖子进程的 OS 级行为**。本设计补齐这一层。

## 架构

采用**方案 A：应用级守卫层**。新建 `SandboxGuard` 模块，作为所有执行类工具的强制前置检查层，在 `CapabilityGateway` 权限确认通过后、实际执行前调用。

```
Agent Loop → ToolExecutor → CapabilityGateway (权限) → SandboxGuard (安全) → capability_adapters (执行)
```

## 技术栈

Python 3.12 / asyncio / regex / React + TypeScript

## 设计决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 隔离方案 | 应用级守卫（方案 A） | 跨平台、可测试、不依赖外部运行时，与 #1 变更追踪+回滚配合形成纵深防御 |
| 拦截策略 | 黑名单 + 路径边界 + 环境净化 | 三层叠加，黑名单挡已知危险命令，路径边界挡越界访问，环境净化减少信息泄露 |
| 黑名单更新 | 代码内常量，版本更新时维护 | 命令模式相对稳定，无需动态更新 |
| sandboxMode 级别 | off / guard / strict | guard=拦截黑名单；strict=黑名单+dangerous 全走 dry-run |
| dry-run 行为 | 返回命令预览，不执行 | 用户确认后才真正执行，类似 Codex 的 "propose" 模式 |
| 环境净化范围 | PATH、USERPROFILE、TEMP 等敏感变量 | 限制子进程能发现和访问的系统路径 |

---

## 组件设计

### 1. SandboxGuard 模块

**文件:** `backend/amadeus_app/orchestrator/sandbox_guard.py`（新建）

```python
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SandboxResult:
    allowed: bool
    reason: str = ""
    action: str = "pass"        # "pass" | "block" | "dry_run"
    preview: str = ""           # dry_run 模式下的命令预览


class SandboxGuard:
    """应用级安全守卫，拦截危险命令和越界路径。"""

    # 危险命令模式黑名单（大小写不敏感）
    BLOCKED_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"\bdel\s+/[fsq]\s+[A-Z]:", re.IGNORECASE),    # del /f C:\...
        re.compile(r"\brmdir\s+/s", re.IGNORECASE),                 # rmdir /s
        re.compile(r"\berase\s+/[fsq]", re.IGNORECASE),             # erase /f
        re.compile(r"\bformat\s+[A-Z]:", re.IGNORECASE),            # format C:
        re.compile(r"\breg\s+delete", re.IGNORECASE),               # reg delete
        re.compile(r"\bdiskpart", re.IGNORECASE),                   # diskpart
        re.compile(r"\bshutdown\s+/", re.IGNORECASE),               # shutdown /s
        re.compile(r"\btaskkill\s+/f\s+/im", re.IGNORECASE),        # taskkill /f /im
        re.compile(r"\.\.\\\\", re.IGNORECASE),                     # path traversal ..\..
        re.compile(r"\brm\s+-rf\s+/", re.IGNORECASE),               # rm -rf / (WSL)
        re.compile(r"\bcd\s+[A-Z]:\\Windows", re.IGNORECASE),       # cd C:\Windows
        re.compile(r"\bicacls\s+.*\s+/deny", re.IGNORECASE),        # icacls deny
        re.compile(r"\bnet\s+(user|localgroup)\s+/add", re.IGNORECASE),  # net user add
        re.compile(r"\bschtasks\s+/create", re.IGNORECASE),         # schtasks create
        re.compile(r"\breg\s+add\s+.*\\Run", re.IGNORECASE),        # reg add Run key
    ]

    # 敏感环境变量（净化时移除或替换）
    SENSITIVE_ENV_KEYS = {
        "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
        "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432",
        "SYSTEMROOT", "WINDIR", "COMSPEC",
        "USERNAME", "USERDOMAIN", "LOGONSERVER",
        "USERPROFILE",  # 替换为工作区路径
    }

    def __init__(self, workspace_path: str, mode: str = "guard") -> None:
        self._workspace = Path(workspace_path or ".").expanduser().resolve()
        self._mode = mode  # "off" | "guard" | "strict"

    def check_command(self, command: str, risk_level: str = "") -> SandboxResult:
        """
        检查命令是否安全。
        - off: 直接放行
        - guard: 检查黑名单，匹配则 block
        - strict: 检查黑名单 + dangerous 级走 dry_run
        """
        if self._mode == "off":
            return SandboxResult(allowed=True)

        # 黑名单检查（guard 和 strict 都执行）
        for pattern in self.BLOCKED_PATTERNS:
            if pattern.search(command):
                return SandboxResult(
                    allowed=False,
                    reason=f"Blocked by sandbox: matched pattern '{pattern.pattern}'",
                    action="block",
                )

        # strict 模式下，dangerous 命令走 dry-run
        if self._mode == "strict" and risk_level == "dangerous":
            return SandboxResult(
                allowed=False,
                reason="Dry-run: dangerous command requires confirmation",
                action="dry_run",
                preview=command,
            )

        return SandboxResult(allowed=True)

    def check_path(self, raw_path: str) -> bool:
        """检查路径是否在工作区内。"""
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
        """
        净化环境变量：
        - 移除敏感系统路径变量
        - 将 USERPROFILE 替换为工作区路径
        - 保留 PATH 但移除系统目录
        """
        import os
        clean = dict(env or os.environ)

        for key in self.SENSITIVE_ENV_KEYS:
            if key == "USERPROFILE":
                clean[key] = str(self._workspace)
            elif key in clean:
                del clean[key]

        # 净化 PATH：移除系统目录，保留开发工具路径
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

        # 设置工作区为当前目录
        clean["CD"] = str(self._workspace)

        return clean
```

### 2. 集成到 _shell_exec

**文件:** `backend/amadeus_app/orchestrator/capability_adapters.py`（修改）

在 `_shell_exec`（第 1087 行）中，`classify_shell_command` 之后、`create_subprocess_shell` 之前插入：

```python
async def _shell_exec(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    # ... 现有命令解析、cwd 检查 ...
    risk = classify_shell_command(command)
    # ... 现有权限确认 ...

    # === 新增：SandboxGuard 检查 ===
    sandbox_mode = str(context.settings.sandbox_mode or "guard")
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

    # === SandboxGuard 检查结束 ===

    # 净化环境变量
    sanitized_env = guard.sanitize_env()

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd_path),
            env=sanitized_env,  # === 新增：使用净化后的环境 ===
        )
        # ... 现有执行逻辑 ...
```

### 3. 集成到 _code_agent

**文件:** `backend/amadeus_app/orchestrator/capability_adapters.py`（修改）

在 `_code_agent`（第 261 行）中验证 workspacePath：

```python
async def _code_agent(args: dict[str, Any], context: CapabilityExecutionContext) -> dict[str, Any]:
    # ... 现有检查 ...

    # === 新增：SandboxGuard 路径验证 ===
    sandbox_mode = str(context.settings.sandbox_mode or "guard")
    guard = SandboxGuard(context.workspace_path, mode=sandbox_mode)
    requested_workspace = str(args.get("workspacePath") or context.workspace_path)
    if not guard.check_path(requested_workspace):
        return {
            "ok": False,
            "summary": "code_agent workspace must stay inside the task workspace.",
            "data": {"requestedPath": requested_workspace},
        }
    # === SandboxGuard 验证结束 ===

    request = CodeTaskStreamRequest(
        # ... 现有参数 ...
    )
    # ...
```

### 4. OrchestratorSettings 扩展

**文件:** `backend/amadeus_app/orchestrator/domain.py`（修改）

```python
class OrchestratorSettings(BaseModel):
    # ... 现有字段 ...
    sandbox_mode: Literal["off", "guard", "strict"] = Field(
        default="guard", alias="sandboxMode"
    )
```

**文件:** `src/types.ts`（修改）

```typescript
interface OrchestratorSettings {
    // ... 现有字段 ...
    sandboxMode?: "off" | "guard" | "strict";
}
```

### 5. 前端设置面板

**文件:** `src/components/OrchestratorSettingsPanel.tsx`（修改）

新增 Sandbox 配置区域：

```tsx
<div className="settings-section">
    <h4>沙箱守卫</h4>
    <label>
        模式
        <select value={settings.sandboxMode || "guard"}>
            <option value="off">关闭 — 不做任何拦截</option>
            <option value="guard">守卫 — 拦截危险命令（推荐）</option>
            <option value="strict">严格 — 危险命令走 dry-run</option>
        </select>
    </label>
</div>
```

### 6. 前端事件渲染

**文件:** `src/components/TaskWorkspace.tsx`（修改）

在事件列表中，新增 `blocked` 和 `dry_run` 状态的渲染：

- `status="blocked"`：红色警示卡片，显示被拦截的命令和原因
- `status="dry_run"`：黄色提示卡片，显示命令预览，提示"未执行"

---

## 数据流

```
Agent Loop → shell_exec tool_call
  │
  ▼
CapabilityGateway → classify_shell_command → 权限确认
  │ (approved)
  ▼
SandboxGuard.check_command(command, risk_level)
  ├─ off → 直接放行
  ├─ guard:
  │   ├─ 匹配黑名单 → block → 返回错误 + error 事件
  │   └─ 不匹配 → 放行
  └─ strict:
      ├─ 匹配黑名单 → block
      ├─ dangerous 级 → dry_run → 返回预览（不执行）
      └─ safe/confirm 级 → 放行
  │
  ▼ (放行)
SandboxGuard.sanitize_env() → 净化后的环境变量
  │
  ▼
asyncio.create_subprocess_shell(command, env=sanitized_env)
```

## 错误处理

| 场景 | 处理 |
|---|---|
| 黑名单误报 | 用户可切换 sandboxMode 为 off 临时绕过；或调整黑名单模式 |
| 环境净化导致工具不可用 | PATH 过滤可能移除了需要的工具路径；用户可切换 sandboxMode 为 off 临时绕过，或后续版本支持 PATH 白名单 |
| code_agent 工作区路径越界 | 拒绝执行，返回错误 |
| sandboxMode 配置无效 | 默认回退到 "guard" |

## 测试策略

1. **单元测试** — `SandboxGuard.check_command` 各模式、各黑名单模式、path 边界、env 净化
2. **集成测试** — `_shell_exec` 中 sandbox block → 返回错误 + 事件、dry_run → 返回预览
3. **集成测试** — `_code_agent` 路径越界 → 拒绝
4. **前端测试** — 设置面板 sandboxMode 切换、blocked/dry_run 事件渲染
5. **边界测试** — 空命令、超长命令、非 ASCII 命令、无环境变量的子进程
