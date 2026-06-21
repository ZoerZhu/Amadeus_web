"""Built-in recommended skill packages.

These skills are shipped with the application and can be installed to the
user's skills directory on first run or via the "Reset built-in skills"
button in the settings panel.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..logging_config import get_logger

_log = get_logger(__name__)

# Each skill is a tuple of (relative_path, content).
# Files are written under {skills_dir}/{skill_id}/

BUILTIN_SKILLS: list[dict[str, Any]] = [
    {
        "id": "codebase-navigation",
        "name": "代码库导航",
        "version": "1.0.0",
        "description": "查代码、定位文件、解释实现。使用 code_search/file_reader/local_git 工具。",
        "triggers": ["查代码", "找文件", "代码怎么实现", "where is", "find code", "codebase"],
        "roles": ["researcher"],
        "toolAllowlist": ["code_search", "file_reader", "local_git", "mcp_list_resources"],
        "mcpServers": [],
        "skillMd": """# 代码库导航

你是一个代码库导航专家。当用户询问代码位置、实现方式或项目结构时，按以下步骤操作：

1. 使用 `code_search` 搜索关键词或模式
2. 使用 `local_git` 了解文件历史和变更
3. 使用 `file_reader` 读取找到的文件内容
4. 总结发现，给出文件路径和关键代码片段

## 注意事项
- 搜索时优先使用具体的函数名/类名
- 如果一次搜索结果太多，缩小范围或添加 glob 过滤
- 引用代码时给出文件路径和行号
""",
    },
    {
        "id": "github-workflow",
        "name": "GitHub 工作流",
        "version": "1.0.0",
        "description": "查 GitHub repo、issue、PR、release。依赖 GitHub MCP，默认只读。",
        "triggers": ["GitHub", "PR", "issue", "repo", "pull request", "github"],
        "roles": ["researcher"],
        "toolAllowlist": ["mcp_resource_read", "mcp_list_resources", "mcp_list_prompts"],
        "mcpServers": ["github"],
        "skillMd": """# GitHub 工作流

你是一个 GitHub 工作流助手。当用户需要查看 GitHub 仓库信息、issue、PR 或 release 时：

1. 确认 GitHub MCP server 已连接
2. 使用 MCP 工具查询 repo/issue/PR 信息
3. 总结关键信息，给出链接

## 注意事项
- 默认只读操作，不创建/修改/删除资源
- 如果 GitHub MCP 未连接，提示用户在设置中配置
- PR review 时关注改动文件和风险点
""",
    },
    {
        "id": "library-docs",
        "name": "库文档查询",
        "version": "1.0.0",
        "description": "查最新库文档。依赖 Context7 MCP，适合 React/FastAPI/Electron/LangGraph 等。",
        "triggers": ["文档", "docs", "API", "怎么用", "how to use", "library docs"],
        "roles": ["researcher"],
        "toolAllowlist": ["mcp_resource_read", "mcp_list_resources", "mcp_prompt_get", "mcp_list_prompts"],
        "mcpServers": ["context7"],
        "skillMd": """# 库文档查询

你是一个库文档查询专家。当用户需要查找某个库/框架的文档时：

1. 确认 Context7 MCP server 已连接
2. 使用 MCP 工具查询库的最新文档
3. 结合用户问题，给出具体的 API 用法和示例

## 注意事项
- 优先使用 Context7 获取版本相关的最新文档
- 给出代码示例时标注版本号
- 如果文档中没有直接答案，说明并给出最接近的参考
""",
    },
    {
        "id": "document-ingest",
        "name": "文档摄取",
        "version": "1.0.0",
        "description": "读 PDF/Office/Markdown 并总结。使用 markitdown_convert/file_reader。",
        "triggers": ["PDF", "Word", "文档总结", "read document", "ingest", "总结文档"],
        "roles": ["researcher", "document_writer"],
        "toolAllowlist": ["markitdown_convert", "file_reader"],
        "mcpServers": [],
        "skillMd": """# 文档摄取

你是一个文档摄取和总结专家。当用户需要读取和总结 PDF/Office/HTML 文档时：

1. 使用 `markitdown_convert` 将文档转换为 Markdown
2. 阅读转换后的内容
3. 总结关键信息，按结构化格式输出

## 注意事项
- 只能处理工作区内的文件
- 大文件会被截断，注意提示用户
- 总结时保留原文的关键数据和结论
""",
    },
    {
        "id": "research-report",
        "name": "研究报告",
        "version": "1.0.0",
        "description": "上网检索并写报告。使用 web_search/doc_writer/file_writer。",
        "triggers": ["研究报告", "调研", "research", "调查", "写报告", "write report"],
        "roles": ["researcher", "document_writer"],
        "toolAllowlist": ["web_search", "doc_writer", "file_writer", "file_reader"],
        "mcpServers": [],
        "skillMd": """# 研究报告

你是一个研究报告撰写专家。当用户需要就某个主题进行调研并生成报告时：

1. 使用 `web_search` 搜索相关信息
2. 收集和整理多个来源的信息
3. 使用 `doc_writer` 或 `file_writer` 生成报告文件
4. 在对话中给出摘要和关键发现

## 注意事项
- 标注信息来源
- 区分事实和观点
- 报告结构：摘要、背景、发现、结论、来源
""",
    },
    {
        "id": "browser-qa",
        "name": "浏览器验证",
        "version": "1.0.0",
        "description": "打开页面、提取文本、截图验证。使用现有 browser tool。",
        "triggers": ["打开网页", "截图", "browser", "网页验证", "open page", "screenshot"],
        "roles": ["browser_operator"],
        "toolAllowlist": ["browser"],
        "mcpServers": [],
        "skillMd": """# 浏览器验证

你是一个浏览器自动化验证专家。当用户需要打开网页、提取内容或截图验证时：

1. 使用 `browser` 工具的 `open` action 打开页面
2. 使用 `extract_text` 提取页面文本
3. 使用 `screenshot` 截图保存
4. 点击和输入操作需要用户确认

## 注意事项
- open/extract_text 是安全操作，自动执行
- click/type 是高风险操作，需要用户确认
- 截图作为 artifact 保存
""",
    },
    {
        "id": "project-memory",
        "name": "项目记忆",
        "version": "1.0.0",
        "description": "保存项目决策、偏好、任务状态。使用记忆树工具。",
        "triggers": ["记住", "项目记忆", "save memory", "remember", "项目决策"],
        "roles": ["document_writer"],
        "toolAllowlist": ["save_memory", "recall_memory", "browse_memory_tree"],
        "mcpServers": [],
        "skillMd": """# 项目记忆

你是一个项目记忆管理专家。当用户需要保存或回忆项目信息时：

1. 使用 `save_memory` 保存决策、偏好、任务状态
2. 使用 `recall_memory` 回忆相关信息
3. 使用 `browse_memory_tree` 浏览记忆结构

## 注意事项
- 记忆按主题/领域组织
- 保存时给出清晰的摘要
- 回忆时关联相关记忆节点
""",
    },
]


def install_builtin_skills(skills_dir: Path) -> list[str]:
    """Install all built-in skills to the given directory.

    Returns list of installed skill IDs.
    Overwrites existing files if they exist.
    """
    skills_dir.mkdir(parents=True, exist_ok=True)
    installed = []
    for skill in BUILTIN_SKILLS:
        skill_dir = skills_dir / skill["id"]
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Write SKILL.md
        (skill_dir / "SKILL.md").write_text(skill["skillMd"], encoding="utf-8")

        # Write manifest.json
        manifest = {
            "id": skill["id"],
            "name": skill["name"],
            "version": skill["version"],
            "description": skill["description"],
            "triggers": skill["triggers"],
            "roles": skill["roles"],
            "toolAllowlist": skill["toolAllowlist"],
            "mcpServers": skill["mcpServers"],
        }
        (skill_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        installed.append(skill["id"])

    _log.info("installed %d built-in skills to %s", len(installed), skills_dir)
    return installed


def list_builtin_skill_ids() -> list[str]:
    """Return list of built-in skill IDs."""
    return [s["id"] for s in BUILTIN_SKILLS]


def get_builtin_skill_info(skill_id: str) -> dict[str, Any] | None:
    """Return metadata for a built-in skill, or None."""
    for s in BUILTIN_SKILLS:
        if s["id"] == skill_id:
            return {
                "id": s["id"],
                "name": s["name"],
                "version": s["version"],
                "description": s["description"],
                "triggers": s["triggers"],
                "roles": s["roles"],
                "toolAllowlist": s["toolAllowlist"],
                "mcpServers": s["mcpServers"],
                "builtin": True,
            }
    return None
