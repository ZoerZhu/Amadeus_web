"""OpenCode routing for orchestrator planning.

This module turns the user-configurable ``opencodeRouting`` settings into a
deterministic decision used by the orchestrator planner before it schedules the
``code_agent`` capability.
"""

from __future__ import annotations

import re
from typing import Any

from .domain import OpencodeRoutingConfig, OpencodeRoutingDecision, OpencodeRoutingRule


def default_opencode_routing_config() -> OpencodeRoutingConfig:
    return OpencodeRoutingConfig(
        enabled=True,
        allow_threshold=60,
        ambiguous_threshold=40,
        allow_llm_rejudge=True,
        force_allow_keywords=[
            "opencode",
            "coding agent",
            "代码 agent",
            "强 coding",
            "用 opencode",
            "使用 opencode",
            "用 coding agent",
        ],
        force_deny_keywords=[],
        rules=[
            OpencodeRoutingRule(
                id="multi_file_edit", name="跨文件修改", weight=30,
                keywords=["跨文件", "多个文件", "multi-file", "multifile", "修改多个"],
                description="跨文件修改任务",
            ),
            OpencodeRoutingRule(
                id="fix_bug", name="修 Bug", weight=30,
                keywords=["修 bug", "修复 bug", "fix bug", "bug 修复", "解决 bug", "修bug"],
                description="修复 bug",
            ),
            OpencodeRoutingRule(
                id="refactor", name="重构", weight=20,
                keywords=["重构", "refactor", "重写代码"],
                description="代码重构",
            ),
            OpencodeRoutingRule(
                id="new_module", name="新模块", weight=20,
                keywords=["新模块", "新增模块", "new module", "创建模块", "新建模块"],
                description="新增模块/功能",
            ),
            OpencodeRoutingRule(
                id="run_tests", name="跑测试", weight=30,
                keywords=["跑测试", "运行测试", "run test", "run tests", "执行测试", "pytest", "jest", "vitest", "unittest", "跑一下测试", "跑下测试"],
                description="运行测试",
            ),
            OpencodeRoutingRule(
                id="build", name="构建", weight=15,
                keywords=["构建", "build", "编译", "compile"],
                description="构建项目",
            ),
            OpencodeRoutingRule(
                id="install_deps", name="安装依赖", weight=15,
                keywords=["安装依赖", "install dependency", "install dependencies", "npm install", "pip install", "pnpm install", "yarn add"],
                description="安装依赖",
            ),
            OpencodeRoutingRule(
                id="build_config", name="修改构建配置", weight=15,
                keywords=["构建配置", "build config", "webpack", "vite", "tsconfig", "pyproject", "package.json", "cargo.toml", "cmake"],
                description="修改构建配置",
            ),
            OpencodeRoutingRule(
                id="implement_feature", name="实现功能", weight=15,
                keywords=["实现功能", "实现", "implement", "开发功能", "编写代码", "写代码"],
                description="实现新功能",
            ),
            OpencodeRoutingRule(
                id="search_code", name="查代码", weight=-20,
                keywords=["查代码", "搜索代码", "search code", "find code", "grep", "查找调用"],
                description="只读: 查代码",
            ),
            OpencodeRoutingRule(
                id="find_file", name="找文件", weight=-15,
                keywords=["找文件", "查找文件", "find file", "locate file", "哪个文件"],
                description="只读: 找文件",
            ),
            OpencodeRoutingRule(
                id="explain", name="解释实现", weight=-25,
                keywords=["解释", "explain", "说明实现", "怎么实现", "如何实现", "原理是什么"],
                description="只读: 解释实现",
            ),
            OpencodeRoutingRule(
                id="read_only", name="只读分析", weight=-25,
                keywords=["只读", "read-only", "分析一下", "analyze", "检查一下", "inspect", "审查", "review code", "代码审查"],
                description="只读分析",
            ),
            OpencodeRoutingRule(
                id="write_docs", name="写文档", weight=-15,
                keywords=["写文档", "写说明", "write doc", "write docs", "写 markdown", "写readme"],
                description="写文档",
            ),
        ],
    )


def effective_opencode_routing_config(config: OpencodeRoutingConfig | None) -> OpencodeRoutingConfig:
    default = default_opencode_routing_config()
    if config is None:
        return default
    data = default.model_dump(by_alias=True)
    incoming = config.model_dump(by_alias=True)
    if not incoming.get("rules"):
        incoming["rules"] = data["rules"]
    return OpencodeRoutingConfig.model_validate({**data, **incoming})


def evaluate_opencode_routing(
    *,
    prompt: str,
    config: OpencodeRoutingConfig | None,
    opencode_enabled: bool,
    heuristic_requires_code_agent: bool,
    planner_steps: list[dict[str, Any]] | None = None,
    skill_info: list[dict[str, Any]] | None = None,
) -> OpencodeRoutingDecision:
    effective = effective_opencode_routing_config(config)
    if not opencode_enabled:
        return OpencodeRoutingDecision(
            allowed=False,
            score=0,
            matched_rules=[],
            reason="opencodeEnabled is false",
            method="force_deny",
        )
    if not effective.enabled:
        return OpencodeRoutingDecision(
            allowed=True,
            score=100,
            matched_rules=[],
            reason="opencodeRouting disabled; opencodeEnabled true",
            method="rule",
        )

    prompt_steps_text = _build_prompt_steps_text(prompt, planner_steps or [])
    prompt_steps_lower = prompt_steps_text.lower()
    full_text = _build_routing_text(prompt, planner_steps or [], skill_info or [])
    full_text_lower = full_text.lower()

    for keyword in effective.force_deny_keywords:
        if keyword.lower() in prompt_steps_lower:
            return OpencodeRoutingDecision(
                allowed=False,
                score=0,
                matched_rules=[],
                reason=f"force deny keyword matched: {keyword}",
                method="force_deny",
            )

    for keyword in effective.force_allow_keywords:
        if keyword.lower() in prompt_steps_lower:
            return OpencodeRoutingDecision(
                allowed=True,
                score=100,
                matched_rules=[],
                reason=f"force allow keyword matched: {keyword}",
                method="force_allow",
            )

    score = 30
    matched: list[str] = []
    for rule in effective.rules:
        if _rule_matches(rule, full_text, full_text_lower):
            score += rule.weight
            matched.append(rule.name)

    if heuristic_requires_code_agent:
        score += 30
        matched.append("代码变更启发式")

    if score >= effective.allow_threshold:
        return OpencodeRoutingDecision(
            allowed=True,
            score=score,
            matched_rules=matched,
            reason=f"score {score} >= allowThreshold {effective.allow_threshold}",
            method="rule",
        )
    if score < effective.ambiguous_threshold:
        return OpencodeRoutingDecision(
            allowed=False,
            score=score,
            matched_rules=matched,
            reason=f"score {score} < ambiguousThreshold {effective.ambiguous_threshold}",
            method="rule",
        )
    return OpencodeRoutingDecision(
        allowed=False,
        score=score,
        matched_rules=matched,
        reason=(
            f"ambiguous zone ({effective.ambiguous_threshold}-{effective.allow_threshold}); "
            "planner has no LLM re-judge hook"
        ),
        method="rule",
    )


def _build_routing_text(prompt: str, planner_steps: list[dict[str, Any]], skill_info: list[dict[str, Any]]) -> str:
    parts: list[str] = [prompt]
    for step in planner_steps:
        if isinstance(step, dict):
            for key in ("goal", "capability", "worker"):
                value = step.get(key)
                if value:
                    parts.append(str(value))
    for skill in skill_info:
        if isinstance(skill, dict):
            for key in ("name", "description"):
                value = skill.get(key)
                if value:
                    parts.append(str(value))
    return "\n".join(parts)


def _build_prompt_steps_text(prompt: str, planner_steps: list[dict[str, Any]]) -> str:
    parts: list[str] = [prompt]
    for step in planner_steps:
        if isinstance(step, dict):
            goal = step.get("goal", "")
            if goal:
                parts.append(str(goal))
    return "\n".join(parts)


def _rule_matches(rule: OpencodeRoutingRule, text: str, text_lower: str) -> bool:
    for keyword in rule.keywords:
        if rule.match_type == "regex":
            try:
                if re.search(keyword, text, re.IGNORECASE):
                    return True
            except re.error:
                continue
        elif keyword.lower() in text_lower:
            return True
    return False
