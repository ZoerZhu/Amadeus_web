"""Main-chat routing heuristics for orchestrator tasks.

The chat service calls :func:`detect_orchestrator_task_intent` on the user's latest
message. If the intent indicates a multi-step orchestrator task, the chat stream
creates an orchestrator task and emits an ``orchestrator_task`` SSE event so the
frontend can follow progress through ``/api/orchestrator/tasks/{id}/events``.
"""

from __future__ import annotations

import re
from typing import Any

from ..orchestrator_integrations import skills as skills_module
from ..orchestrator_integrations.skills import match_skills_by_trigger
from .domain import OrchestratorSettings


ORCHESTRATOR_TASK_MARKER = "__ORCHESTRATOR_TASK__"

EXPLICIT_PHRASES = (
    "交给 orchestrator",
    "使用 orchestrator",
    "orchestrator 执行",
    "任务编排",
    "编排任务",
    "交给复杂 agent",
    "交给复杂agent",
    "复杂 agent 执行",
    "复杂agent执行",
    "用 skill",
    "使用 skill",
    "复杂任务",
    "多步任务",
    "agent task",
    "complex task",
)

MULTI_STEP_INDICATORS = (
    "然后",
    "接着",
    "步骤",
    "第一步",
    "第二步",
    "第三步",
    "流程",
    "并保存",
    "并生成",
    "并跑测试",
    "并执行",
    "并修改",
    "并写",
    "and save",
    "and run",
    "and modify",
)


def detect_orchestrator_task_intent(
    user_text: str,
    *,
    enabled_skills: list[dict[str, Any]],
    orchestrator_settings: OrchestratorSettings | None = None,
    agent_settings: OrchestratorSettings | None = None,
) -> dict[str, Any] | None:
    """Return a routing decision dict, or None if the task should stay simple."""
    settings = orchestrator_settings or agent_settings
    if settings is None:
        raise TypeError("orchestrator_settings is required")
    if not settings.enabled:
        return None
    text = user_text.strip()
    if not text:
        return None

    lowered = text.lower()
    for phrase in EXPLICIT_PHRASES:
        if phrase.lower() in lowered:
            skill_ids: list[str] = []
            match = re.search(r"(?:用|使用)\s*skill\s+([A-Za-z0-9_-]+)", text, re.IGNORECASE)
            if match:
                skill_ids.append(match.group(1).lower())
            return {"reason": f"explicit phrase: {phrase}", "skillIds": skill_ids}

    skills = [skills_module.SkillPackageInfo.model_validate(s) for s in enabled_skills]
    matched = match_skills_by_trigger(skills, text)
    if matched:
        return {
            "reason": "skill trigger matched",
            "skillIds": [s.id for s in matched],
        }

    if len(text) > 200 and any(indicator in lowered for indicator in MULTI_STEP_INDICATORS):
        return {"reason": "long multi-step prompt", "skillIds": []}

    return None


def scan_for_orchestrator_task_marker(content: str) -> bool:
    """Return True if the model emitted the orchestrator-task marker."""
    return ORCHESTRATOR_TASK_MARKER in content


ORCHESTRATOR_TASK_SYSTEM_PROMPT = (
    "\n\n## Orchestrator 任务路由\n"
    "如果用户请求明显是多步骤、需要修改代码、生成文档并保存、或需要浏览器自动化，"
    f"请在回复开头输出 `{ORCHESTRATOR_TASK_MARKER}` 然后简短说明将创建 Orchestrator 任务。"
    "否则正常回复，不要输出该标记。"
)
