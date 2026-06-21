"""OpenCode complexity-based routing engine.

Decides whether to expose the ``opencode_delegate`` tool for a given task
based on rule scoring + optional LLM re-judge in the ambiguous zone.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .domain import OpencodeRoutingConfig, OpencodeRoutingDecision, OpencodeRoutingRule


# ---------------------------------------------------------------------------
# Default rules
# ---------------------------------------------------------------------------

def default_opencode_routing_config() -> OpencodeRoutingConfig:
    """Return a config pre-populated with built-in scoring rules."""
    return OpencodeRoutingConfig(
        enabled=True,
        allow_threshold=60,
        ambiguous_threshold=40,
        allow_llm_rejudge=True,
        force_allow_keywords=[
            "opencode", "coding agent", "代码 agent", "强 coding",
            "用 opencode", "使用 opencode", "用 coding agent",
        ],
        force_deny_keywords=[],
        rules=[
            # --- Positive (coding task signals) ---
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
                keywords=["跑测试", "运行测试", "run test", "run tests", "执行测试",
                          "pytest", "jest", "vitest", "unittest", "测试"],
                description="运行测试",
            ),
            OpencodeRoutingRule(
                id="build", name="构建", weight=15,
                keywords=["构建", "build", "编译", "compile"],
                description="构建项目",
            ),
            OpencodeRoutingRule(
                id="install_deps", name="安装依赖", weight=15,
                keywords=["安装依赖", "install dependency", "install dependencies",
                          "npm install", "pip install", "pnpm install", "yarn add"],
                description="安装依赖",
            ),
            OpencodeRoutingRule(
                id="build_config", name="修改构建配置", weight=15,
                keywords=["构建配置", "build config", "webpack", "vite", "tsconfig",
                          "pyproject", "package.json", "cargo.toml", "cmake"],
                description="修改构建配置",
            ),
            OpencodeRoutingRule(
                id="implement_feature", name="实现功能", weight=15,
                keywords=["实现功能", "实现", "implement", "开发功能", "编写代码", "写代码"],
                description="实现新功能",
            ),
            # --- Negative (read-only task signals) ---
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
                keywords=["只读", "read-only", "分析一下", "analyze", "检查一下",
                          "inspect", "审查", "review code", "代码审查"],
                description="只读分析",
            ),
            OpencodeRoutingRule(
                id="write_docs", name="写文档", weight=-15,
                keywords=["写文档", "写说明", "write doc", "write docs", "写 markdown", "写readme"],
                description="写文档",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Text building & rule matching
# ---------------------------------------------------------------------------

def _build_routing_text(
    prompt: str,
    planner_steps: list[dict[str, Any]],
    skill_info: list[dict[str, Any]],
) -> str:
    """Combine all text sources that rules are matched against."""
    parts: list[str] = [prompt]
    for step in planner_steps:
        if isinstance(step, dict):
            goal = step.get("goal", "")
            if goal:
                parts.append(str(goal))
            tools = step.get("tools", [])
            if isinstance(tools, list):
                parts.append(" ".join(str(t) for t in tools))
    for skill in skill_info:
        if isinstance(skill, dict):
            name = skill.get("name", "")
            desc = skill.get("description", "")
            if name:
                parts.append(str(name))
            if desc:
                parts.append(str(desc))
    return "\n".join(parts)


def _rule_matches(rule: OpencodeRoutingRule, text: str, text_lower: str) -> bool:
    """Check if any keyword/regex in the rule matches the text."""
    for kw in rule.keywords:
        if rule.match_type == "regex":
            try:
                if re.search(kw, text, re.IGNORECASE):
                    return True
            except re.error:
                continue
        else:
            if kw.lower() in text_lower:
                return True
    return False


# ---------------------------------------------------------------------------
# Decision computation
# ---------------------------------------------------------------------------

def compute_opencode_routing_decision(
    *,
    prompt: str,
    planner_steps: list[dict[str, Any]],
    skill_info: list[dict[str, Any]],
    config: OpencodeRoutingConfig,
    opencode_enabled: bool,
    llm_rejudge_fn: Callable[[str, list[dict[str, Any]], OpencodeRoutingConfig], bool] | None = None,
) -> OpencodeRoutingDecision:
    """Compute whether OpenCode should be exposed for this task.

    Decision flow:
    1. ``opencode_enabled=False`` → hard deny.
    2. ``config.enabled=False`` → fall back to allow (old behavior).
    3. Force-deny keywords → deny (highest priority among keywords).
    4. Force-allow keywords → allow.
    5. Rule scoring: base 30 + matched rule weights.
    6. score >= allow_threshold → allow.
    7. score < ambiguous_threshold → deny.
    8. Ambiguous zone → LLM re-judge (if enabled and fn provided), else deny.
    """
    # 1. Hard close
    if not opencode_enabled:
        return OpencodeRoutingDecision(
            allowed=False, score=0, matched_rules=[],
            reason="opencodeEnabled is false (hard off)", method="force_deny",
        )

    # 2. Routing disabled → old behavior
    if not config.enabled:
        return OpencodeRoutingDecision(
            allowed=True, score=100, matched_rules=[],
            reason="routing disabled, opencodeEnabled true → allow", method="rule",
        )

    text = _build_routing_text(prompt, planner_steps, skill_info)
    text_lower = text.lower()

    # 3. Force deny (highest priority)
    for kw in config.force_deny_keywords:
        if kw.lower() in text_lower:
            return OpencodeRoutingDecision(
                allowed=False, score=0, matched_rules=[],
                reason=f"force deny keyword matched: {kw}", method="force_deny",
            )

    # 4. Force allow
    for kw in config.force_allow_keywords:
        if kw.lower() in text_lower:
            return OpencodeRoutingDecision(
                allowed=True, score=100, matched_rules=[],
                reason=f"force allow keyword matched: {kw}", method="force_allow",
            )

    # 5. Rule scoring
    score = 30  # neutral base
    matched: list[str] = []
    for rule in config.rules:
        if _rule_matches(rule, text, text_lower):
            score += rule.weight
            matched.append(rule.name)

    # 6. Above allow threshold
    if score >= config.allow_threshold:
        return OpencodeRoutingDecision(
            allowed=True, score=score, matched_rules=matched,
            reason=f"score {score} >= allowThreshold {config.allow_threshold}",
            method="rule",
        )

    # 7. Below ambiguous threshold
    if score < config.ambiguous_threshold:
        return OpencodeRoutingDecision(
            allowed=False, score=score, matched_rules=matched,
            reason=f"score {score} < ambiguousThreshold {config.ambiguous_threshold}",
            method="rule",
        )

    # 8. Ambiguous zone → LLM re-judge
    if config.allow_llm_rejudge and llm_rejudge_fn is not None:
        try:
            allowed = bool(llm_rejudge_fn(prompt, planner_steps, config))
            return OpencodeRoutingDecision(
                allowed=allowed, score=score, matched_rules=matched,
                reason=f"LLM re-judge in ambiguous zone ({config.ambiguous_threshold}-"
                       f"{config.allow_threshold}): {allowed}",
                method="llm_rejudge",
            )
        except Exception as error:  # noqa: BLE001
            return OpencodeRoutingDecision(
                allowed=False, score=score, matched_rules=matched,
                reason=f"LLM re-judge failed ({error}), conservative deny",
                method="llm_rejudge",
            )

    # No LLM re-judge available → conservative deny
    return OpencodeRoutingDecision(
        allowed=False, score=score, matched_rules=matched,
        reason=f"ambiguous zone ({config.ambiguous_threshold}-{config.allow_threshold}), "
               f"no LLM re-judge → deny",
        method="rule",
    )


# ---------------------------------------------------------------------------
# LLM re-judge helper
# ---------------------------------------------------------------------------

def build_llm_rejudge_fn(call_llm_fn, model_settings: dict, mode: str):
    """Build an async-to-sync LLM re-judge callable.

    ``call_llm_fn`` is the async ``call_llm`` from agent.py. The returned
    callable is async and should be awaited. It returns ``True`` if the LLM
    decides OpenCode is needed.
    """
    import json

    async def rejudge(prompt: str, steps: list[dict], config: OpencodeRoutingConfig) -> bool:
        steps_text = "\n".join(
            f"- {s.get('goal', '')}" for s in steps if isinstance(s, dict)
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 OpenCode 路由分类器。判断该任务是否需要强 coding agent "
                    "（能修改代码文件、执行命令、运行测试）。"
                    "只读分析、查代码、解释实现不需要。"
                    '只输出 JSON：{"need_opencode": true/false, "reason": "..."}'
                ),
            },
            {
                "role": "user",
                "content": f"任务：{prompt}\n\n规划步骤：\n{steps_text}",
            },
        ]
        data = await call_llm_fn(messages=messages, model_settings=model_settings, mode=mode)
        # Extract text from response
        text = ""
        if isinstance(data, dict):
            choices = data.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                text = msg.get("content", "")
        # Parse JSON
        # Try to find JSON in the text
        import re as _re
        match = _re.search(r'\{[^}]*"need_opencode"[^}]*\}', text, _re.IGNORECASE | _re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                return bool(parsed.get("need_opencode", False))
            except (json.JSONDecodeError, ValueError):
                pass
        # Fallback: look for true/false
        return "true" in text.lower()

    return rejudge
