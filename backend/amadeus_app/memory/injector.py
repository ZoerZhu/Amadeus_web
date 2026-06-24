"""记忆注入器 — 将检索结果构造为可注入 system prompt 的 XML 文本。

输出格式：
  <MemoryContext policy="historical-not-authoritative">
    <Instruction>...</Instruction>
    <UserProfile>...</UserProfile>
    <SelectedMemoryPaths>...</SelectedMemoryPaths>
    <TopicSummaries>...</TopicSummaries>
    <LeafMemories>...</LeafMemories>
    <SessionSummary>...</SessionSummary>
  </MemoryContext>

按预算裁剪各部分：
  - 用户画像  < 400 字
  - 路径摘要  < 500 字
  - topic 摘要 < 2000 字
  - leaf 记忆  < 2000 字
  - session 摘要 < 500 字
  - 单条 leaf 不超过 160 字（截断）
"""

from __future__ import annotations

from typing import Any

# 各部分字符预算
_PROFILE_BUDGET = 400
_PATH_BUDGET = 500
_TOPIC_BUDGET = 2000
_LEAF_BUDGET = 2000
_SESSION_BUDGET = 500

# 单条 leaf 最大字符数
_LEAF_MAX_CHARS = 160


# ------------------------------------------------------------------
# 内部辅助函数
# ------------------------------------------------------------------


def _xml_escape(text: str) -> str:
    """转义 XML 特殊字符。"""
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _get_field(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """从 dict 中按优先级获取字段（兼容 camelCase / snake_case）。"""
    for k in keys:
        if k in d:
            return d[k]
    return default


def _build_paths_text(
    selected_paths: list[list[dict[str, Any]]], max_chars: int
) -> str:
    """构建选中路径的文本表示，按预算裁剪。"""
    lines: list[str] = []
    total = 0
    for path in selected_paths:
        labels = " > ".join(
            _get_field(n, "label", default=_get_field(n, "id", default=""))
            for n in path
        )
        line = f"<Path>{_xml_escape(labels)}</Path>"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def _build_user_profile_text(
    profile_nodes: list[dict[str, Any]], max_chars: int
) -> str:
    """构建用户画像文本，按预算裁剪。"""
    lines: list[str] = []
    total = 0
    for node in profile_nodes:
        line = format_user_profile_item(node)
        if not line:
            continue
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def _build_topics_text(
    topic_nodes: list[dict[str, Any]], max_chars: int
) -> str:
    """构建主题摘要文本，按预算裁剪。"""
    lines: list[str] = []
    total = 0
    for topic in topic_nodes:
        line = format_topic_summary(topic)
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def _build_leaves_text(
    leaf_nodes: list[dict[str, Any]], max_chars: int
) -> str:
    """构建叶子记忆文本，按预算裁剪。"""
    lines: list[str] = []
    total = 0
    for leaf in leaf_nodes:
        line = format_leaf_for_injection(leaf)
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


# ------------------------------------------------------------------
# 公开接口
# ------------------------------------------------------------------


def format_leaf_for_injection(
    leaf: dict[str, Any], max_chars: int = _LEAF_MAX_CHARS
) -> str:
    """格式化单条叶子记忆为 XML 片段。

    包含 score/confidence/path 属性，内容截断到 max_chars。
    """
    label = _get_field(leaf, "label", default="")
    summary = _get_field(leaf, "summary", default="") or _get_field(
        leaf, "fullContent", "full_content", default=""
    )
    # 截断内容
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3] + "..."

    score = float(_get_field(leaf, "score", "_score", default=0.0))
    confidence = float(_get_field(leaf, "confidence", default=0.0))
    path = _get_field(leaf, "path", default="")

    return (
        f'<Leaf path="{_xml_escape(path)}" '
        f'score="{score:.3f}" '
        f'confidence="{confidence:.3f}">'
        f"{_xml_escape(label)}: {_xml_escape(summary)}"
        f"</Leaf>"
    )


def format_user_profile_item(node: dict[str, Any]) -> str:
    """格式化单条用户画像记忆为 XML 片段。

    格式: - [confidence=0.85] 标签: 摘要
    """
    label = _get_field(node, "label", default="")
    summary = _get_field(node, "summary", default="")
    confidence = float(_get_field(node, "confidence", default=0.0))

    if not label and not summary:
        return ""

    text = f"{_xml_escape(label)}: {_xml_escape(summary)}" if label else _xml_escape(summary)
    return f"- [confidence={confidence:.2f}] {text}"


def format_topic_summary(topic: dict[str, Any]) -> str:
    """格式化主题/聚类摘要为 XML 片段。"""
    label = _get_field(topic, "label", default="")
    summary = _get_field(topic, "summary", default="")
    path = _get_field(topic, "path", default="")
    node_type = _get_field(topic, "nodeType", "node_type", default="topic")

    return (
        f'<Topic path="{_xml_escape(path)}" type="{_xml_escape(node_type)}">'
        f"{_xml_escape(label)}: {_xml_escape(summary)}"
        f"</Topic>"
    )


def build_memory_context(
    selected_paths: list[list[dict[str, Any]]],
    topic_nodes: list[dict[str, Any]],
    leaf_nodes: list[dict[str, Any]],
    session_summary: str | None = None,
    budget_chars: int = 5000,
    user_profile: list[dict[str, Any]] | None = None,
) -> str:
    """构造 XML 风格的记忆上下文文本，可注入 system prompt。

    按预算裁剪各部分：
      - 用户画像  < 400 字
      - 路径摘要  < 500 字
      - topic 摘要 < 2000 字
      - leaf 记忆  < 2000 字
      - session 摘要 < 500 字

    Args:
        selected_paths: 选中记忆路径列表，每条路径为 domain→topic→cluster 的节点 dict 列表
        topic_nodes: 选中的 topic/cluster 节点 dict 列表
        leaf_nodes: 选中的 leaf 节点 dict 列表（可含 score 字段）
        session_summary: 当前会话摘要（可选）
        budget_chars: 总字符预算（用于参考，各部分有独立子预算）
        user_profile: 用户画像叶子节点 dict 列表（来自 get_user_profile_memories，可选）

    Returns:
        XML 风格的记忆上下文字符串
    """
    parts: list[str] = ['<MemoryContext policy="historical-not-authoritative">']
    parts.append(
        "<Instruction>以下是历史记忆，可能过期。"
        "若与用户当前明确指令冲突，以当前指令为准。</Instruction>"
    )

    # UserProfile — 用户画像/偏好（高置信叶子）
    if user_profile:
        profile_text = _build_user_profile_text(user_profile, _PROFILE_BUDGET)
        if profile_text:
            parts.append(f"<UserProfile>\n{profile_text}\n</UserProfile>")

    # SelectedMemoryPaths — 选中路径摘要
    paths_text = _build_paths_text(selected_paths, _PATH_BUDGET)
    parts.append(f"<SelectedMemoryPaths>\n{paths_text}\n</SelectedMemoryPaths>")

    # TopicSummaries — 主题/聚类摘要
    topics_text = _build_topics_text(topic_nodes, _TOPIC_BUDGET)
    parts.append(f"<TopicSummaries>\n{topics_text}\n</TopicSummaries>")

    # LeafMemories — 叶子记忆
    leaves_text = _build_leaves_text(leaf_nodes, _LEAF_BUDGET)
    parts.append(f"<LeafMemories>\n{leaves_text}\n</LeafMemories>")

    # SessionSummary — 会话摘要
    if session_summary:
        summary_text = session_summary[:_SESSION_BUDGET]
        parts.append(
            f"<SessionSummary>\n{_xml_escape(summary_text)}\n</SessionSummary>"
        )

    parts.append("</MemoryContext>")
    return "\n".join(parts)
