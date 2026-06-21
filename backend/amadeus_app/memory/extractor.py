"""记忆提取器 — 从对话中提取叶子记忆候选。"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from ..domain import ModelProviderPreset, ModelSettings
from ..logging_config import get_logger
from ..model_adapter import build_chat_payload
from .models import ExtractedLeafMemory

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """\
你是一个记忆提取助手。你的任务是分析一段对话，从中提取值得长期保存的叶子记忆候选。

## 领域 (domain)
- daily_chat: 日常闲聊、寒暄、情感交流
- task: 任务规划、待办事项、进度追踪
- code: 代码片段、技术方案、编程相关
- environment: 运行环境、配置信息、系统状态
- creative: 创意灵感、写作、设计思路
- knowledge: 事实知识、学习笔记、概念解释

## 类别 (category)
- fact: 客观事实
- decision: 做出的决定或选择
- preference: 用户偏好或习惯
- summary: 内容摘要
- technical: 技术细节或方案
- task_state: 任务状态变更
- tool_result: 工具/函数调用结果

## 保存价值判断
**应保存：**
- 用户明确表达的偏好、习惯、个人信息
- 重要的决定或结论
- 需要后续跟进的任务或承诺
- 有复用价值的技术方案、代码片段
- 关键的事实知识

**不应保存：**
- 简单的寒暄、问候
- 无实质内容的确认（"好的"、"明白了"）
- 临时性的、无后续价值的操作细节
- 已被后续信息取代的过时内容
- 纯粹的情感表达（除非反映长期偏好）

## 评分规则
- importance (0-1): 记忆的长期价值。0.1=几乎无用, 0.5=一般参考, 0.9=关键信息
- confidence (0-1): 提取结果的置信度。0.3=不确定, 0.7=较确定, 0.95=非常确定

## action 字段
- create_leaf: 创建新的叶子记忆（默认）
- update_leaf: 更新已有记忆（需提供 target_node_id）
- deactivate_leaf: 使已有记忆失效（需提供 target_node_id）
- noop: 无需操作（对话无保存价值时返回空数组即可）

## source_message_ids 字段（重要 - 可追溯性）
对话文本中每条消息前会带 `[msg:<id>]` 标记，例如 `[msg:abc-123] [用户]: ...`。
提取记忆时，必须把产生该记忆所依据的消息 id 填入 source_message_ids 数组（去掉 `msg:` 前缀，只填 id 本身）。
- 一条记忆可由多条消息共同产生，应全部填入
- 只填实际参与形成该记忆的消息 id，不要填无关消息
- 若对话消息没有 `[msg:...]` 标记（如外部 API 调用），则填空数组 []

## 输出格式
返回一个 JSON 数组，每个元素代表一条叶子记忆候选。格式如下：
```json
[
  {
    "action": "create_leaf",
    "target_node_id": null,
    "domain": "daily_chat",
    "path_hint": "偏好/饮食",
    "title": "用户喜欢喝咖啡",
    "summary": "用户表示每天早上都要喝咖啡",
    "full_content": "用户提到每天早上都要喝一杯美式咖啡来提神，不喜欢加糖和奶。",
    "category": "preference",
    "keywords": ["咖啡", "美式", "早餐"],
    "importance": 0.6,
    "confidence": 0.85,
    "source_message_ids": ["abc-123", "def-456"],
    "source_summary": "",
    "expires_in_days": null,
    "supersedes_node_ids": []
  }
]
```

如果对话中没有值得保存的内容，返回空数组 `[]`。
只返回 JSON，不要附加任何解释文字。"""

# 敏感信息正则模式
SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)api[_\-]?key\s*[:=]\s*['\"]?\S{8,}"),           # API key
    re.compile(r"(?i)token\s*[:=]\s*['\"]?\S{8,}"),                   # Token
    re.compile(r"(?i)password\s*[:=]\s*['\"]?\S{4,}"),                # Password
    re.compile(r"(?i)secret\s*[:=]\s*['\"]?\S{8,}"),                  # Secret
    re.compile(r"(?i)private[_\-]?key\s*[:=]\s*['\"]?\S{16,}"),       # 私钥
    re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"),     # PEM 私钥头
    re.compile(r"(?i)cookie\s*[:=]\s*['\"]?\S{8,}"),                  # Cookie
    re.compile(r"(?i)authorization\s*[:=]\s*['\"]?bearer\s+\S{8,}"),  # Bearer token
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),                               # OpenAI API key 格式
    re.compile(r"(?i)aws[_\-]?access[_\-]?key[_\-]?id\s*[:=]\s*\S{8,}"),  # AWS access key
    re.compile(r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key\s*[:=]\s*\S{8,}"),  # AWS secret key
]

# 会话摘要 prompt
_SUMMARY_PROMPT = """\
你是一个会话摘要助手。请根据以下对话内容，生成一段 200-400 字的中文摘要。

要求：
1. 保留关键信息：用户需求、重要决定、待办事项、技术方案
2. 省略寒暄、重复内容、无关细节
3. 按时间顺序组织，突出逻辑脉络
4. 如果有之前的摘要，请在此基础上整合新内容，而非简单拼接

{old_summary_section}

对话内容：
{conversation}"""

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def is_sensitive_content(text: str) -> bool:
    """检查文本是否包含敏感信息。"""
    for pattern in SENSITIVE_PATTERNS:
        if pattern.search(text):
            return True
    return False


def format_conversation(messages: list[dict]) -> str:
    """将消息列表格式化为可读文本。

    每条消息前会带上 `[msg:<id>]` 标记，便于 LLM 在 source_message_ids 中引用。
    若消息没有 id 字段，则跳过该标记。
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if not content or not content.strip():
            continue
        # 只处理 user / assistant / system 三种角色
        if role not in ("user", "assistant", "system"):
            continue
        role_label = {"user": "用户", "assistant": "助手", "system": "系统"}.get(role, role)
        msg_id = msg.get("id")
        if msg_id:
            parts.append(f"[msg:{msg_id}] [{role_label}]: {content.strip()}")
        else:
            parts.append(f"[{role_label}]: {content.strip()}")
    return "\n".join(parts)


def parse_extraction_result(raw: str) -> list[ExtractedLeafMemory]:
    """容错解析 LLM 返回的 JSON 提取结果。

    处理以下情况：
    - markdown ```json ... ``` 包裹
    - JSON 解析失败
    - 不完整的 JSON（尝试截断修复）
    """
    text = raw.strip()

    # 去掉 markdown code block 包裹
    if text.startswith("```"):
        # 匹配 ```json ... ``` 或 ``` ... ```
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
        else:
            # 只有开头 ```，没有闭合
            text = re.sub(r"^```(?:json)?\s*\n?", "", text).strip()

    # 尝试直接解析
    parsed = _try_parse_json(text)
    if parsed is not None:
        return _build_leaf_memories(parsed)

    # 尝试提取 JSON 数组（可能前后有额外文字）
    bracket_match = re.search(r"\[.*\]", text, re.DOTALL)
    if bracket_match:
        parsed = _try_parse_json(bracket_match.group(0))
        if parsed is not None:
            return _build_leaf_memories(parsed)

    # 尝试修复不完整的 JSON：补全缺失的闭合括号
    repaired = _try_repair_json(text)
    if repaired is not None:
        return _build_leaf_memories(repaired)

    _log.warning("无法解析 LLM 提取结果为 JSON，返回空列表")
    return []


def _try_parse_json(text: str) -> list[dict] | None:
    """尝试解析 JSON，成功返回列表，失败返回 None。"""
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            # 单个对象，包装为列表
            return [result]
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _try_repair_json(text: str) -> list[dict] | None:
    """尝试修复不完整的 JSON（补全闭合括号）。"""
    # 统计未闭合的括号
    open_brackets = 0
    open_braces = 0
    in_string = False
    escape_next = False

    for char in text:
        if escape_next:
            escape_next = False
            continue
        if char == "\\":
            escape_next = True
            continue
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "[":
            open_brackets += 1
        elif char == "]":
            open_brackets = max(0, open_brackets - 1)
        elif char == "{":
            open_braces += 1
        elif char == "}":
            open_braces = max(0, open_braces - 1)

    # 补全闭合：先闭合内层 {}，再闭合外层 []
    repaired = text + "}" * open_braces + "]" * open_brackets
    return _try_parse_json(repaired)


def _build_leaf_memories(items: list[dict]) -> list[ExtractedLeafMemory]:
    """将解析后的字典列表转换为 ExtractedLeafMemory 列表，跳过无效项。"""
    results: list[ExtractedLeafMemory] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            leaf = ExtractedLeafMemory(**item)
            results.append(leaf)
        except Exception as error:
            _log.debug("跳过无效的记忆候选项: %s", error)
    return results


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------


async def extract_leaf_memories(
    messages: list[dict],
    settings: ModelSettings,
    provider: ModelProviderPreset,
) -> list[ExtractedLeafMemory]:
    """调用 LLM 从对话中提取叶子记忆候选。

    流程：
    1. 格式化对话文本
    2. 构造 LLM 请求并调用
    3. 解析 JSON 结果
    4. 过滤敏感信息和低价值内容
    """
    conversation_text = format_conversation(messages)
    if not conversation_text.strip():
        _log.debug("对话内容为空，跳过记忆提取")
        return []

    # 构造请求消息
    llm_messages: list[dict[str, Any]] = [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {"role": "user", "content": f"请从以下对话中提取值得保存的记忆：\n\n{conversation_text}"},
    ]

    # 构造 endpoint 和 headers
    endpoint = f"{settings.base_url.rstrip('/')}/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    # 构造请求体
    payload = build_chat_payload(settings, provider, "fast", llm_messages, stream=False)

    # 调用 LLM
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            response_data = response.json()
    except httpx.HTTPStatusError as error:
        _log.error("记忆提取 LLM 请求失败 (HTTP %s): %s", error.response.status_code, error)
        return []
    except httpx.RequestError as error:
        _log.error("记忆提取 LLM 请求异常: %s", error)
        return []
    except Exception as error:
        _log.error("记忆提取未知错误: %s", error)
        return []

    # 提取响应文本
    try:
        raw_content = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        _log.error("记忆提取 LLM 响应格式异常: %s", json.dumps(response_data, ensure_ascii=False)[:500])
        return []

    if not raw_content or not raw_content.strip():
        _log.debug("LLM 返回空内容，无记忆提取")
        return []

    # 解析结果
    candidates = parse_extraction_result(raw_content)

    # 收集实际消息 id 集合，用于校验 source_message_ids
    valid_msg_ids: set[str] = {
        str(m["id"]) for m in messages if m.get("id")
    }

    # 过滤：敏感信息 + 低价值内容 + 清洗 source_message_ids
    filtered: list[ExtractedLeafMemory] = []
    for candidate in candidates:
        # 跳过包含敏感信息的内容
        if is_sensitive_content(candidate.full_content):
            _log.debug("跳过包含敏感信息的记忆: %s", candidate.title)
            continue
        # 跳过低价值内容
        if candidate.importance < 0.3:
            _log.debug("跳过低价值记忆 (importance=%.2f): %s", candidate.importance, candidate.title)
            continue
        # 清洗 source_message_ids
        candidate.source_message_ids = _clean_source_message_ids(
            candidate.source_message_ids, valid_msg_ids
        )
        filtered.append(candidate)

    _log.info("记忆提取完成: 候选 %d 条, 过滤后 %d 条", len(candidates), len(filtered))
    return filtered


def _clean_source_message_ids(
    raw_ids: list[str], valid_msg_ids: set[str]
) -> list[str]:
    """清洗 LLM 返回的 source_message_ids。

    - 去掉 LLM 可能误带的 msg: 前缀
    - 过滤掉不在实际消息集合中的 id（若 valid_msg_ids 非空）
    - 去重，保持顺序
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for mid in raw_ids:
        if not isinstance(mid, str):
            continue
        mid = mid.strip()
        # 去掉 LLM 可能误带的 msg: 前缀
        if mid.startswith("msg:"):
            mid = mid[4:]
        if not mid or mid in seen:
            continue
        # 若有实际消息集合，则只保留真实存在的 id；否则保留全部
        if valid_msg_ids and mid not in valid_msg_ids:
            _log.debug("source_message_id 不在实际消息集合中，丢弃: %s", mid)
            continue
        seen.add(mid)
        cleaned.append(mid)
    return cleaned


async def generate_session_summary(
    old_summary: str,
    messages: list[dict],
    settings: ModelSettings,
    provider: ModelProviderPreset,
) -> str:
    """生成会话滚动摘要（200-400字），用于替代硬截断。

    参数:
        old_summary: 之前的摘要（可为空字符串）
        messages: 新的对话消息列表
        settings: 模型设置
        provider: 模型提供商预设

    返回:
        整合后的摘要文本
    """
    conversation_text = format_conversation(messages)
    if not conversation_text.strip():
        # 没有新内容，返回旧摘要
        return old_summary

    # 构造旧摘要部分
    if old_summary and old_summary.strip():
        old_summary_section = f"之前的摘要：\n{old_summary.strip()}"
    else:
        old_summary_section = "（这是首次摘要，没有之前的摘要）"

    # 构造请求消息
    prompt = _SUMMARY_PROMPT.format(
        old_summary_section=old_summary_section,
        conversation=conversation_text,
    )
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

    # 调用 LLM
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            response_data = response.json()
    except httpx.HTTPStatusError as error:
        _log.error("会话摘要 LLM 请求失败 (HTTP %s): %s", error.response.status_code, error)
        return old_summary
    except httpx.RequestError as error:
        _log.error("会话摘要 LLM 请求异常: %s", error)
        return old_summary
    except Exception as error:
        _log.error("会话摘要未知错误: %s", error)
        return old_summary

    # 提取响应文本
    try:
        summary = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        _log.error("会话摘要 LLM 响应格式异常: %s", json.dumps(response_data, ensure_ascii=False)[:500])
        return old_summary

    if not summary or not summary.strip():
        _log.debug("LLM 返回空摘要，保留旧摘要")
        return old_summary

    _log.info("会话摘要生成完成，长度: %d 字", len(summary.strip()))
    return summary.strip()
