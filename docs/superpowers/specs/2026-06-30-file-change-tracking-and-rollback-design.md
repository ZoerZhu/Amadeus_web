# 文件变更追踪 + Rollback 设计文档

**日期:** 2026-06-30
**状态:** Approved

## 目标

解决 Agent 执行过程中文件修改不可见、不可回滚的问题：

1. **文件变更追踪** — Agent 每次 `file_write` / `file_edit` / `shell_exec` 修改工作区文件时，自动记录变更（统一 diff），让用户看到 Agent 改了什么。
2. **Rollback** — 用户可在任务完成后一键回滚所有变更，恢复到任务执行前的状态。

## 架构

采用 **Git-first + 逐文件快照兜底** 双轨策略：

- 若工作区是 Git 仓库：任务开始时记录 HEAD commit，任务中用 `git diff` 生成变更，回滚用 `git checkout` / `git stash` 恢复到初始 commit。
- 若工作区非 Git 仓库：`file_write` / `file_edit` 执行前对原文件做内容快照（存入数据库），回滚时从快照恢复。

变更记录统一存入新表 `orchestrator_file_changes`，通过统一 diff 格式在前端渲染。

## 技术栈

Python 3.12 / asyncio / SQLite / Git CLI（可选） / React + TypeScript + diff-viewer

## 设计决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 追踪策略 | Git-first + 快照兜底 | Git 仓库有原生 diff/rollback 能力，非 Git 场景用快照兜底，覆盖所有情况 |
| 快照存储位置 | SQLite `orchestrator_file_changes` 表 | 与任务事件同一存储层，查询方便，事务一致 |
| 回滚触发方式 | 仅用户手动触发 | 自动回滚可能丢失 Agent 有价值的变更，让用户决定 |
| 回滚粒度 | 整任务级（恢复到任务开始前） | 精确到单步回滚复杂度过高，且用户通常只需"撤销整个任务" |
| Diff 格式 | 统一 diff（unified diff） | 前端有成熟 diff 渲染库，Git 原生输出此格式 |
| 新增文件追踪 | 记录为 `new_file` 类型 | 新文件没有旧版本快照，但仍需追踪和回滚（回滚 = 删除） |
| shell_exec 变更追踪 | 任务结束后做一次 Git diff（Git 模式） | shell_exec 修改的文件无法逐条拦截，用 Git diff 统一捕获 |

---

## 组件设计

### 1. 数据模型

**新表:** `orchestrator_file_changes`

```sql
CREATE TABLE IF NOT EXISTS orchestrator_file_changes (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    seq INTEGER NOT NULL,           -- 变更序号，同任务的变更按序排列
    relative_path TEXT NOT NULL,    -- 相对于工作区根目录的路径
    change_type TEXT NOT NULL,      -- "modified" | "created" | "deleted"
    diff_text TEXT DEFAULT '',       -- unified diff 文本（新建文件为空）
    old_snapshot TEXT DEFAULT '',    -- 非 Git 模式下的原始内容快照
    new_snapshot TEXT DEFAULT '',    -- 非 Git 模式下的新内容快照（仅小文件）
    source TEXT DEFAULT 'file_write', -- "file_write" | "file_edit" | "shell_exec" | "git_diff"
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES orchestrator_tasks(id)
);
```

### 2. FileChangeTracker 模块

**文件:** `backend/amadeus_app/orchestrator/file_change_tracker.py`（新建）

```python
class FileChangeTracker:
    """追踪任务执行期间的文件变更，支持回滚。"""

    def __init__(self, storage: SQLiteStorage, workspace_path: str, task_id: str):
        self._storage = storage
        self._workspace = Path(workspace_path).resolve()
        self._task_id = task_id
        self._is_git_repo: bool | None = None
        self._git_baseline_ref: str | None = None  # 任务开始时的 HEAD commit
        self._seq = 0

    async def initialize(self) -> None:
        """任务开始时调用。Git 仓库记录 baseline ref。"""
        self._is_git_repo = await self._detect_git()
        if self._is_git_repo:
            self._git_baseline_ref = await self._git_head_ref()

    async def record_write(
        self, rel_path: str, old_content: str | None, new_content: str
    ) -> dict[str, Any]:
        """file_write / file_edit 调用后记录变更。"""
        # Git 模式：不存 old/new snapshot，任务结束时用 git diff 统一生成
        # 非 Git 模式：存 old_snapshot，生成 diff_text

    async def collect_git_diff(self) -> list[dict[str, Any]]:
        """任务结束时（Git 模式）调用，用 git diff baseline..HEAD 生成所有变更。"""

    async def rollback(self) -> dict[str, Any]:
        """回滚到任务开始前的状态。"""
        # Git 模式：git checkout baseline_ref + git stash
        # 非 Git 模式：遍历 file_changes 记录，恢复 old_snapshot / 删除新文件
```

### 3. 集成点

#### 3.1 AgentLoopRunner 集成

**文件:** `backend/amadeus_app/orchestrator/agent_loop_runner.py`（修改）

- `run()` 方法开头创建 `FileChangeTracker`，调用 `initialize()`
- `run()` 结束时（done/failed/cancelled）调用 `collect_git_diff()`（Git 模式）
- 将 tracker 实例存入 `CapabilityExecutionContext`，供 adapter 访问

#### 3.2 file_write / file_edit adapter 集成

**文件:** `backend/amadeus_app/orchestrator/capability_adapters.py`（修改）

- `_file_write`: 写入前读取原文件内容（若存在），写入后调用 `tracker.record_write()`
- `_file_edit`: 同上，但 `old_content` 是编辑前的版本
- `change_type` 判定：原文件不存在 → `created`，否则 → `modified`

#### 3.3 Rollback API

**文件:** `backend/amadeus_app/routers/orchestrator.py`（修改）

现有 `"rollback": "rolled_back"` 状态映射（第 283 行）已存在但无实现。新增：

```
POST /api/orchestrator/tasks/{task_id}/rollback
```

- 调用 `FileChangeTracker.rollback()`
- 更新任务状态为 `rolled_back`
- 发送 `rollback` 事件

#### 3.4 前端渲染

**文件:** `src/components/TaskWorkspace.tsx`（修改）

- 新增 "文件变更" 卡片区域：展示 `orchestrator_file_changes` 列表
- 每条变更显示：文件路径、变更类型图标（modified/created/deleted）、可展开的 diff 视图
- Diff 渲染：使用 `react-diff-viewer` 或自研轻量 diff 组件
- Rollback 按钮：任务状态为 `done` / `failed` 时显示，点击后二次确认

### 4. Git 操作封装

**文件:** `backend/amadeus_app/orchestrator/file_change_tracker.py`

```python
async def _detect_git(self) -> bool:
    """检查工作区是否为 Git 仓库。"""
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "--is-inside-work-tree",
        cwd=str(self._workspace),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    return proc.returncode == 0

async def _git_head_ref(self) -> str:
    """获取当前 HEAD commit hash 作为 baseline。"""
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "HEAD",
        cwd=str(self._workspace),
        stdout=asyncio.subprocess.PIPE,
    )
    out = await proc.stdout.read()
    return out.decode().strip()

async def collect_git_diff(self) -> list[dict[str, Any]]:
    """生成 baseline..HEAD 的 diff。"""
    # git diff --no-color <baseline>..HEAD
    # 解析输出，按文件分割，存入 orchestrator_file_changes

async def rollback_git(self) -> None:
    """回滚到 baseline commit。"""
    # git checkout -- . (丢弃工作区修改)
    # git reset --hard <baseline> (如果 agent 做了 commit)
```

### 5. 非 Git 模式快照回滚

```python
async def rollback_snapshots(self) -> None:
    """遍历 file_changes，逆序恢复。"""
    changes = await list_file_changes(self._storage, self._task_id)
    for change in reversed(changes):
        full_path = self._workspace / change["relative_path"]
        if change["change_type"] == "created":
            full_path.unlink(missing_ok=True)
        elif change["change_type"] == "modified":
            full_path.write_text(change["old_snapshot"], encoding="utf-8")
```

---

## 数据流

```
任务开始
  │
  ▼
FileChangeTracker.initialize()
  ├─ Git 仓库 → 记录 baseline HEAD ref
  └─ 非 Git → 标记为快照模式
  │
  ▼
Agent Loop 执行
  ├─ file_write("a.py", ...)
  │   ├─ 读旧内容（若有）
  │   ├─ 写入新内容
  │   └─ tracker.record_write("a.py", old, new)
  │       ├─ Git 模式 → 跳过（任务结束统一 diff）
  │       └─ 非 Git → 存 old_snapshot + 生成 diff_text
  │
  ├─ shell_exec("npm install")
  │   └─ Git 模式下变更会在 collect_git_diff 中捕获
  │
  ▼
任务结束（done/failed）
  └─ Git 模式 → tracker.collect_git_diff()
     └─ 生成所有变更记录存入 DB
  │
  ▼
用户点击 Rollback
  └─ POST /rollback
     ├─ Git 模式 → git checkout/reset to baseline
     └─ 非 Git → 逆序恢复 old_snapshot / 删除新文件
     └─ 更新任务状态为 rolled_back
```

## 错误处理

| 场景 | 处理 |
|---|---|
| Git 不可用 | 回退到快照模式，记录 warning 事件 |
| 快照文件过大（>1MB） | 不存 full snapshot，仅存 diff + 文件路径，回滚时标记为"需手动恢复" |
| 回滚失败 | 记录 error 事件，任务状态保持原样，不标为 rolled_back |
| Agent 删除文件 | 记录 `change_type=deleted`，存 old_snapshot，回滚时重建文件 |
| 二进制文件 | 不生成 diff，仅记录变更类型和路径 |

## 测试策略

1. **单元测试** — `FileChangeTracker` 的 Git 检测、快照存储、diff 生成、回滚逻辑
2. **集成测试** — 在临时 Git 仓库中运行 Agent Loop，验证 file_write 后 file_changes 表有记录
3. **回滚测试** — Git 模式和非 Git 模式各一组，验证回滚后文件恢复到初始状态
4. **边界测试** — 空工作区、大文件、二进制文件、新建文件、删除文件
