"""Main-chat complex task routing.

The chat service calls :func:`detect_complex_task_intent` on the user's
latest message. If the intent indicates a complex multi-step task, the
helper creates an Agent Task and returns a ``complex_task`` SSE event
that the chat stream emits to the frontend. The frontend then opens a
separate connection to ``/api/agent/tasks/{id}/events`` to follow the
task progress.

Detection heuristics (first version, no extra LLM round-trip):
- Explicit user phrases like "交给复杂 agent", "用 skill X 执行",
  "复杂任务", "多步任务".
- Task-length heuristic: prompt longer than 200 chars AND contains
  multi-step indicators ("然后", "接着", "步骤", "第一步", "流程",
  "并保存", "并生成", "并跑测试").
- Skill trigger match: any enabled skill's trigger appears in the text.

The model can also emit a ``__COMPLEX_TASK__`` marker in its output;
the chat service scans for this marker and, if found, creates the task
from the original user prompt.
"""

from __future__ import annotations

import re
from typing import Any

from ..logging_config import get_logger
from . import agent_storage, skills as skills_module
from .domain import AgentSettings
from .skills import match_skills_by_trigger

_log = get_logger(__name__)


COMPLEX_TASK_MARKER = "__COMPLEX_TASK__"

EXPLICIT_PHRASES = (
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


def detect_complex_task_intent(
    user_text: str,
    *,
    enabled_skills: list[dict[str, Any]],
    agent_settings: AgentSettings,
) -> dict[str, Any] | None:
    """Return a routing decision dict, or None if the task should stay simple.

    The dict has keys:
    - ``reason``: why the task was routed to the complex agent.
    - ``skillIds``: list of skill ids whose triggers matched.
    """
    if not agent_settings.enabled:
        return None
    text = user_text.strip()
    if not text:
        return None

    # 1. Explicit phrases
    lowered = text.lower()
    for phrase in EXPLICIT_PHRASES:
        if phrase.lower() in lowered:
            # Try to extract skill id from "用 skill X 执行"
            skill_ids: list[str] = []
            match = re.search(r"(?:用|使用)\s*skill\s+([A-Za-z0-9_-]+)", text, re.IGNORECASE)
            if match:
                skill_ids.append(match.group(1).lower())
            return {"reason": f"explicit phrase: {phrase}", "skillIds": skill_ids}

    # 2. Skill trigger match
    skills = [skills_module.SkillPackageInfo.model_validate(s) for s in enabled_skills]
    matched = match_skills_by_trigger(skills, text)
    if matched:
        return {
            "reason": "skill trigger matched",
            "skillIds": [s.id for s in matched],
        }

    # 3. Length + multi-step indicators
    if len(text) > 200 and any(indicator in lowered for indicator in MULTI_STEP_INDICATORS):
        return {"reason": "long multi-step prompt", "skillIds": []}

    return None


def scan_for_complex_task_marker(content: str) -> bool:
    """Return True if the model emitted the complex-task marker."""
    return COMPLEX_TASK_MARKER in content


COMPLEX_TASK_SYSTEM_PROMPT = (
    "\n\n## 复杂任务路由\n"
    "如果用户请求明显是多步骤、需要修改代码、生成文档并保存、或需要浏览器自动化，"
    f"请在回复开头输出 `{COMPLEX_TASK_MARKER}` 然后简短说明将创建复杂任务。"
    "否则正常回复，不要输出该标记。"
)
