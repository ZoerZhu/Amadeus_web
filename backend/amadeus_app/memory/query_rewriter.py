"""检索查询改写模块 — 将省略式用户消息改写为结构化检索意图。

这是 Kurisu Agent 层级记忆树系统的一部分。用户消息常是省略式的
（"继续"、"之前那个方案"、"这个项目怎么跑"），直接用原始消息做向量检索召回率低。
本模块结合最近上下文、会话摘要、项目名调用 LLM 构造结构化检索意图 MemorySearchIntent，
失败时降级为简单 intent，不影响主流程。
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ..domain import ModelProviderPreset, ModelSettings
from ..logging_config import get_logger
from ..model_adapter import build_chat_payload
from .models import MemoryDomain, MemorySearchIntent

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 触发词正则模式（大小写不敏感）
_RECALL_TRIGGER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"之前|上次|刚才|继续|我们说过|你记得|你知道我", re.IGNORECASE),
    re.compile(r"我喜欢|我不喜欢|以后都|记住", re.IGNORECASE),
    re.compile(r"这个项目|当前项目|这个文件|这个报错", re.IGNORECASE),
    re.compile(r"那个方案|那个问题|那个错误", re.IGNORECASE),
    # 隐式记忆问题：用户画像/偏好/身份相关
    re.compile(r"我叫什么|我的名字|我是谁|我常用|我偏好|按我的习惯|我的习惯|你应该知道|不是说过吗|我之前提过", re.IGNORECASE),
    re.compile(r"my name|what do you know about me|my preference|as usual|like i said", re.IGNORECASE),
]

# 用户画像类问题模式：这类问题应始终触发 profile 记忆召回
_PROFILE_QUERY_PATTERNS = [
    re.compile(r"我叫什么|我的名字|我是谁|我是谁", re.IGNORECASE),
    re.compile(r"我常用|我偏好|我的习惯|按我的习惯|我喜欢|我不喜欢", re.IGNORECASE),
    re.compile(r"你应该知道我|你了解我|你知道我什么", re.IGNORECASE),
    re.compile(r"my name|what do you know about me|my preference|about me", re.IGNORECASE),
]

# 消息"足够具体"的最小长度阈值（字符数）：低于此长度且无触发词时仍需改写
_SPECIFIC_MIN_LENGTH = 10

# 领域字符串 → MemoryDomain 枚举映射
_VALID_DOMAINS: dict[str, MemoryDomain] = {d.value: d for d in MemoryDomain}

# 角色 → 中文标签映射
_ROLE_LABELS: dict[str, str] = {
    "user": "用户",
    "assistant": "助手",
    "system": "系统",
}

# 合法 query_type 集合
_VALID_QUERY_TYPES: set[str] = {
    "project",
    "preference",
    "factual_recall",
    "task_state",
    "general",
}

# 检索意图分析 prompt（{{ }} 为字面花括号，{ } 为格式化占位符）
_QUERY_REWRITE_PROMPT = """\
你是一个记忆检索意图分析助手。用户发送了一条消息，需要从长期记忆中检索相关历史对话。请分析用户意图，生成结构化检索查询。

当前用户消息: {user_message}

最近对话上下文:
{recent_context}

会话摘要: {session_summary}

当前项目: {project_name}

请分析：
1. 用户真正想检索什么？（补充省略的上下文，如"继续"→"继续刚才讨论的部署问题"）
2. 属于哪类检索？project(项目相关) / preference(用户偏好) / factual_recall(事实回忆) / task_state(任务状态) / general(通用)
3. 应该在哪些领域检索？daily_chat/task/code/environment/creative/knowledge
4. 有哪些树路径提示？（如 /code/项目名）
5. 关键词有哪些？

返回 JSON 格式：
{{
  "rewritten_query": "改写后的检索查询",
  "query_type": "project|preference|factual_recall|task_state|general",
  "project_hint": "项目名或null",
  "domains": ["领域1", "领域2"],
  "path_hints": ["/路径1"],
  "keywords": ["关键词1", "关键词2"]
}}

只返回 JSON，不要附加解释。"""


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _detect_recall_triggers(message: str) -> bool:
    """检测消息是否包含需要更强检索的表达。

    返回 True 表示需要检索记忆。包括：
    - 时间引用（之前/上次/继续）
    - 偏好表达（我喜欢/记住）
    - 项目/文件引用（这个项目/这个报错）
    - 指代表达（那个方案/那个问题）
    - 隐式记忆问题（我叫什么/我常用/按我的习惯/你应该知道）
    """
    return any(p.search(message) for p in _RECALL_TRIGGER_PATTERNS)


def is_profile_query(message: str) -> bool:
    """检测消息是否为用户画像类问题。

    这类问题应始终触发 profile 记忆召回，即使没有完整的树检索。
    例如："我叫什么名字"、"我常用什么技术栈"、"按我的习惯来"
    """
    return any(p.search(message) for p in _PROFILE_QUERY_PATTERNS)


def _format_recent_messages(messages: list[dict], max_count: int = 6) -> str:
    """格式化最近消息为可读文本。

    跳过空内容和工具消息（只保留 user/assistant/system 角色）。
    取最后 max_count 条有效消息，格式为 "[角色]: 内容"。
    """
    if not messages:
        return ""

    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        # 跳过非 user/assistant/system 角色（如 tool/function）
        if role not in _ROLE_LABELS:
            continue
        # 跳过空内容
        if not content or not str(content).strip():
            continue
        role_label = _ROLE_LABELS[role]
        parts.append(f"[{role_label}]: {str(content).strip()}")

    if not parts:
        return ""

    # 取最后 max_count 条
    recent = parts[-max_count:] if len(parts) > max_count else parts
    return "\n".join(recent)


def _parse_intent_json(raw: str, user_message: str) -> MemorySearchIntent:
    """容错解析 LLM 返回的 JSON 为 MemorySearchIntent。

    处理：
    - markdown ```json``` 包裹
    - JSON 解析失败时返回简单 intent
    - domains 字段是字符串列表，需转为 MemoryDomain 枚举（无效值跳过）
    - project_hint 为 "null" 字符串时转为 None
    """
    text = raw.strip()

    # 去掉 markdown code block 包裹
    if text.startswith("```"):
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
        else:
            # 只有开头 ```，没有闭合
            text = re.sub(r"^```(?:json)?\s*\n?", "", text).strip()

    # 尝试解析 JSON
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as error:
        _log.warning("无法解析检索意图 JSON: %s", error)
        return MemorySearchIntent(
            raw_user_message=user_message,
            rewritten_query=user_message,
            query_type="general",
        )

    if not isinstance(data, dict):
        _log.warning("检索意图 JSON 不是对象: %s", type(data).__name__)
        return MemorySearchIntent(
            raw_user_message=user_message,
            rewritten_query=user_message,
            query_type="general",
        )

    # 提取 rewritten_query
    rewritten_query = data.get("rewritten_query")
    if not isinstance(rewritten_query, str) or not rewritten_query.strip():
        rewritten_query = user_message
    else:
        rewritten_query = rewritten_query.strip()

    # 提取 query_type
    query_type = data.get("query_type")
    if not isinstance(query_type, str) or query_type not in _VALID_QUERY_TYPES:
        query_type = "general"

    # 提取 project_hint（"null" 字符串或空值转为 None）
    project_hint: Any = data.get("project_hint")
    if isinstance(project_hint, str):
        project_hint = project_hint.strip()
        if project_hint.lower() == "null" or not project_hint:
            project_hint = None
    elif project_hint is not None:
        # 非字符串值（如实际 null/数字）统一转为 None 或字符串
        project_hint = None if project_hint is None else str(project_hint)

    # 提取 domains（字符串列表 → MemoryDomain 枚举，跳过无效值）
    domains: list[MemoryDomain] = []
    raw_domains = data.get("domains")
    if isinstance(raw_domains, list):
        for item in raw_domains:
            if isinstance(item, str):
                domain = _VALID_DOMAINS.get(item.strip())
                if domain is not None:
                    domains.append(domain)

    # 提取 path_hints
    path_hints: list[str] = []
    raw_path_hints = data.get("path_hints")
    if isinstance(raw_path_hints, list):
        for item in raw_path_hints:
            if isinstance(item, str) and item.strip():
                path_hints.append(item.strip())

    # 提取 keywords
    keywords: list[str] = []
    raw_keywords = data.get("keywords")
    if isinstance(raw_keywords, list):
        for item in raw_keywords:
            if isinstance(item, str) and item.strip():
                keywords.append(item.strip())

    return MemorySearchIntent(
        raw_user_message=user_message,
        rewritten_query=rewritten_query,
        query_type=query_type,
        project_hint=project_hint,
        domains=domains,
        path_hints=path_hints,
        keywords=keywords,
    )


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------


async def build_search_intent(
    user_message: str,
    recent_messages: list[dict] | None = None,
    session_summary: str | None = None,
    project_name: str | None = None,
    settings: ModelSettings | None = None,
    provider: ModelProviderPreset | None = None,
) -> MemorySearchIntent:
    """构造检索意图。

    结合最近上下文、会话摘要、项目名，将省略式用户消息改写为结构化检索意图。

    流程：
    1. 若 settings/provider 为空，或未触发检索增强（无触发词且消息足够具体），返回简单 intent
    2. 构造 LLM prompt，调用 LLM 分析意图
    3. 解析 JSON 结果（容错，失败则返回简单 intent）
    4. 返回 MemorySearchIntent

    LLM 调用失败/超时（15 秒）时降级为简单 intent，不影响主流程。
    """
    # 降级：空消息或缺少模型配置
    if not user_message or not settings or not provider:
        _log.debug("缺少 user_message/settings/provider，返回简单检索意图")
        return MemorySearchIntent(
            raw_user_message=user_message,
            rewritten_query=user_message,
            query_type="general",
        )

    # 判断是否需要检索增强改写
    has_triggers = _detect_recall_triggers(user_message)
    is_specific = len(user_message.strip()) >= _SPECIFIC_MIN_LENGTH
    if not has_triggers and is_specific:
        _log.debug("消息足够具体且无触发词，返回简单检索意图")
        return MemorySearchIntent(
            raw_user_message=user_message,
            rewritten_query=user_message,
            query_type="general",
        )

    # 构造最近上下文
    recent_context = _format_recent_messages(recent_messages) if recent_messages else ""
    if not recent_context:
        recent_context = "无"

    # 构造会话摘要 / 项目名
    summary_text = session_summary.strip() if session_summary and session_summary.strip() else "无"
    project_text = project_name.strip() if project_name and project_name.strip() else "无"

    # 构造 prompt
    prompt = _QUERY_REWRITE_PROMPT.format(
        user_message=user_message,
        recent_context=recent_context,
        session_summary=summary_text,
        project_name=project_text,
    )

    # 构造 LLM 请求消息
    llm_messages: list[dict[str, Any]] = [
        {"role": "user", "content": prompt},
    ]

    # 构造 endpoint 和 headers
    endpoint = f"{settings.base_url.rstrip('/')}/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    # 构造请求体
    payload = build_chat_payload(settings, provider, "fast", llm_messages, stream=False)

    # 调用 LLM（超时 15 秒，检索不能太慢）
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            response_data = response.json()
    except httpx.HTTPStatusError as error:
        _log.error("检索意图 LLM 请求失败 (HTTP %s): %s", error.response.status_code, error)
        return MemorySearchIntent(
            raw_user_message=user_message,
            rewritten_query=user_message,
            query_type="general",
        )
    except httpx.RequestError as error:
        _log.error("检索意图 LLM 请求异常: %s", error)
        return MemorySearchIntent(
            raw_user_message=user_message,
            rewritten_query=user_message,
            query_type="general",
        )
    except Exception as error:
        _log.error("检索意图未知错误: %s", error)
        return MemorySearchIntent(
            raw_user_message=user_message,
            rewritten_query=user_message,
            query_type="general",
        )

    # 提取响应文本
    try:
        raw_content = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        _log.error(
            "检索意图 LLM 响应格式异常: %s",
            json.dumps(response_data, ensure_ascii=False)[:500],
        )
        return MemorySearchIntent(
            raw_user_message=user_message,
            rewritten_query=user_message,
            query_type="general",
        )

    if not raw_content or not str(raw_content).strip():
        _log.debug("LLM 返回空内容，返回简单检索意图")
        return MemorySearchIntent(
            raw_user_message=user_message,
            rewritten_query=user_message,
            query_type="general",
        )

    # 解析 JSON 结果
    intent = _parse_intent_json(str(raw_content), user_message)
    _log.info(
        "检索意图构造完成: query_type=%s, domains=%s, keywords=%s",
        intent.query_type,
        [d.value for d in intent.domains],
        intent.keywords,
    )
    return intent
