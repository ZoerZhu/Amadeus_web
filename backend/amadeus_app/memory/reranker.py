"""记忆重排模块 — 对召回的记忆节点进行加权重排与强规则过滤。

这是 Kurisu Agent 层级记忆树系统的一部分。tree_search 召回候选节点后，
本模块按 v2.0 设计的加权公式融合向量相似度、关键词匹配、重要性、置信度、
新鲜度、项目匹配度与访问强化分，输出最终排序结果；同时在主动注入场景下
应用强规则过滤掉过期、低置信或已停用的节点。

加权公式：
  final = vec * VECTOR_WEIGHT
        + kw  * KEYWORD_WEIGHT
        + imp * IMPORTANCE_WEIGHT
        + conf* CONFIDENCE_WEIGHT
        + rec * RECENCY_WEIGHT
        + proj* PROJECT_WEIGHT
        + acc * ACCESS_WEIGHT

所有函数均为纯同步、无 IO、无 LLM 调用，对异常输入容错（解析失败用默认值）。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from ..logging_config import get_logger

_log = get_logger(__name__)

# ---- 权重配置（从环境变量读取，有默认值）----
VECTOR_WEIGHT = float(os.getenv("AMADEUS_MEMORY_VECTOR_WEIGHT", "0.35"))
KEYWORD_WEIGHT = float(os.getenv("AMADEUS_MEMORY_KEYWORD_WEIGHT", "0.20"))
IMPORTANCE_WEIGHT = float(os.getenv("AMADEUS_MEMORY_IMPORTANCE_WEIGHT", "0.15"))
CONFIDENCE_WEIGHT = float(os.getenv("AMADEUS_MEMORY_CONFIDENCE_WEIGHT", "0.10"))
RECENCY_WEIGHT = float(os.getenv("AMADEUS_MEMORY_RECENCY_WEIGHT", "0.08"))
PROJECT_WEIGHT = float(os.getenv("AMADEUS_MEMORY_PROJECT_WEIGHT", "0.07"))
ACCESS_WEIGHT = float(os.getenv("AMADEUS_MEMORY_ACCESS_WEIGHT", "0.05"))
MIN_CONFIDENCE = float(os.getenv("AMADEUS_MEMORY_MIN_CONFIDENCE", "0.5"))
MIN_RELEVANCE_SCORE = float(os.getenv("AMADEUS_MEMORY_MIN_RELEVANCE", "0.15"))


# ------------------------------------------------------------------
# 内部辅助函数
# ------------------------------------------------------------------


def _parse_iso_datetime(value: Any) -> datetime | None:
    """解析 ISO 格式时间字符串，返回带时区的 datetime；失败返回 None。

    兼容带时区和不带时区的 ISO 字符串；不带时区时假设为 UTC。
    Python 3.10 的 fromisoformat 不支持 'Z' 后缀，需手动替换。
    """
    if not value or not isinstance(value, str):
        return None
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _recency_score(updated_at: str | None) -> float:
    """计算新鲜度分数（0-1）。越近更新分数越高，90 天后归零。

    解析失败返回 0.5。
    """
    dt = _parse_iso_datetime(updated_at)
    if dt is None:
        return 0.5
    now = datetime.now(timezone.utc)
    days_since = (now - dt).days
    return max(0.0, 1.0 - days_since / 90.0)


def _access_score(access_count: int) -> float:
    """计算访问强化分数（0-1）。访问 10 次满分。"""
    try:
        count = int(access_count)
    except (ValueError, TypeError):
        count = 0
    return min(1.0, count / 10.0)


def _is_expired(expires_at: str | None) -> bool:
    """检查节点是否已过期。

    expires_at 为 None 返回 False；解析失败也返回 False（容错）。
    """
    if not expires_at:
        return False
    dt = _parse_iso_datetime(expires_at)
    if dt is None:
        return False
    now = datetime.now(timezone.utc)
    return now > dt


def _project_match(node_project_id: str | None, current_project_id: str | None) -> float:
    """计算项目匹配分数。

    - 两者都为 None: 0.5（中性）
    - 两者相等且非空: 1.0
    - 否则: 0.0
    """
    if node_project_id is None and current_project_id is None:
        return 0.5
    if (
        node_project_id
        and current_project_id
        and node_project_id == current_project_id
    ):
        return 1.0
    return 0.0


def _safe_float(value: Any, default: float) -> float:
    """安全转 float，失败返回默认值。"""
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


# ------------------------------------------------------------------
# 主重排函数
# ------------------------------------------------------------------


def weighted_rerank(
    candidates: list[dict[str, Any]],
    vector_scores: dict[str, float],
    keyword_scores: dict[str, float],
    current_project_id: str | None = None,
    active_injection: bool = True,
) -> list[tuple[str, float]]:
    """对候选节点加权重排，返回 [(node_id, score), ...]（按分数降序）。

    Args:
        candidates: 节点 dict 列表（含 id, importance, confidence, updatedAt,
            accessCount, expiresAt, projectId, isActive 等字段）
        vector_scores: {node_id: 向量相似度 0-1}
        keyword_scores: {node_id: 已归一化的 FTS 分数 0-1}
        current_project_id: 当前会话绑定的项目 id
        active_injection: True 表示主动注入场景（需过滤低置信/过期/停用），
            False 表示工具检索场景（不过滤）

    Returns:
        按 final 分数降序排列的 (node_id, score) 列表
    """
    scored: list[tuple[str, float]] = []

    for node in candidates:
        node_id = node.get("id")
        if not node_id:
            continue

        # a. 强规则过滤（仅 active_injection=True 时）
        if active_injection:
            if _is_expired(node.get("expiresAt")):
                _log.debug("节点 %s 已过期，跳过", node_id)
                continue
            confidence = _safe_float(node.get("confidence", 0.7), 0.7)
            if confidence < MIN_CONFIDENCE:
                _log.debug(
                    "节点 %s 置信度 %.3f 低于阈值 %.3f，跳过",
                    node_id, confidence, MIN_CONFIDENCE,
                )
                continue
            if node.get("isActive") is False:
                _log.debug("节点 %s 已停用，跳过", node_id)
                continue

        # b. 计算各项分数
        vec = float(vector_scores.get(node_id, 0.0))
        kw = float(keyword_scores.get(node_id, 0.0))

        # b2. 相关性底线（仅 active_injection=True 时）：
        # 主动注入要求向量或关键词至少有一个命中，否则不允许仅凭基础分进入上下文
        if active_injection:
            if vec < MIN_RELEVANCE_SCORE and kw < MIN_RELEVANCE_SCORE:
                _log.debug(
                    "节点 %s 相关性不足 (vec=%.3f kw=%.3f < %.3f)，跳过主动注入",
                    node_id, vec, kw, MIN_RELEVANCE_SCORE,
                )
                continue

        imp = _safe_float(node.get("importance", 0.5), 0.5)
        conf = _safe_float(node.get("confidence", 0.7), 0.7)
        rec = _recency_score(node.get("updatedAt"))
        proj = _project_match(node.get("projectId"), current_project_id)
        acc = _access_score(node.get("accessCount", 0))

        # c. 加权求和
        final = (
            vec * VECTOR_WEIGHT
            + kw * KEYWORD_WEIGHT
            + imp * IMPORTANCE_WEIGHT
            + conf * CONFIDENCE_WEIGHT
            + rec * RECENCY_WEIGHT
            + proj * PROJECT_WEIGHT
            + acc * ACCESS_WEIGHT
        )

        scored.append((node_id, final))

    # d. 按 final 降序排序
    scored.sort(key=lambda pair: pair[1], reverse=True)

    _log.info(
        "加权重排完成: 候选 %d 个，通过 %d 个 (active_injection=%s)",
        len(candidates), len(scored), active_injection,
    )
    return scored


def rerank_nodes(
    candidates: list[dict[str, Any]],
    vector_scores: dict[str, float],
    keyword_scores: dict[str, float],
    current_project_id: str | None = None,
    active_injection: bool = True,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    """重排节点并返回 top_k 个节点 dict（带 _score 字段）。

    流程：
      1. 调用 weighted_rerank 得到 [(id, score), ...]
      2. 取 top_k
      3. 构建 id→node 映射
      4. 返回 [{...node, "_score": score}, ...]（保持原 dict 字段并添加 _score）

    Args:
        candidates: 节点 dict 列表
        vector_scores: {node_id: 向量相似度 0-1}
        keyword_scores: {node_id: 已归一化的 FTS 分数 0-1}
        current_project_id: 当前会话绑定的项目 id
        active_injection: 是否为主动注入场景
        top_k: 返回的最多节点数

    Returns:
        带 _score 字段的节点 dict 列表，按分数降序排列
    """
    scored = weighted_rerank(
        candidates,
        vector_scores,
        keyword_scores,
        current_project_id=current_project_id,
        active_injection=active_injection,
    )

    top = scored[:top_k]

    id_to_node: dict[str, dict[str, Any]] = {
        node.get("id"): node for node in candidates if node.get("id")
    }

    result: list[dict[str, Any]] = []
    for node_id, score in top:
        node = id_to_node.get(node_id)
        if node is None:
            _log.warning("重排结果中的节点 %s 未在候选列表中找到", node_id)
            continue
        # 保持原 dict 字段并添加 _score（不修改原 dict）
        result.append({**node, "_score": score})

    _log.info("返回 top_%d 节点: %d 个", top_k, len(result))
    return result


def apply_strong_rules(
    candidates: list[dict[str, Any]],
    current_project_id: str | None = None,
    active_injection: bool = True,
) -> list[dict[str, Any]]:
    """应用强规则过滤（不计算分数，只过滤）。

    规则：
      - active_injection=True 时：
          - 跳过 isActive=False
          - 跳过 _is_expired(expiresAt)
          - 跳过 confidence < MIN_CONFIDENCE
      - active_injection=False 时（工具检索）：
          - 只跳过 isActive=False

    Args:
        candidates: 节点 dict 列表
        current_project_id: 当前会话绑定的项目 id（保留接口一致性，当前规则未使用）
        active_injection: 是否为主动注入场景

    Returns:
        过滤后的节点 dict 列表（保持原顺序）
    """
    result: list[dict[str, Any]] = []
    for node in candidates:
        if node.get("isActive") is False:
            continue

        if active_injection:
            if _is_expired(node.get("expiresAt")):
                continue
            confidence = _safe_float(node.get("confidence", 0.7), 0.7)
            if confidence < MIN_CONFIDENCE:
                continue

        result.append(node)

    _log.info(
        "强规则过滤完成: 候选 %d 个，保留 %d 个 (active_injection=%s)",
        len(candidates), len(result), active_injection,
    )
    return result
