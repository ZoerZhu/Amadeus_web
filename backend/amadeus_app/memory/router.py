"""记忆树路由器 — 将新提取的叶子记忆路由到正确的 domain → topic → cluster 路径。

路由策略：
1. 在目标 domain 下用向量相似度搜索最接近的 topic
2. 在该 topic 下搜索最接近的 cluster
3. 若未找到合适 topic/cluster，则创建新节点（降低 confidence）
4. 检查与同父节点下已有 leaf 是否重复
5. 创建 leaf 节点并写入 leaf 向量
6. 触发父节点摘要更新任务

embedding 不可用时降级为 FTS 关键词检索。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from ..domain import ModelSettings
from ..logging_config import get_logger
from ..storage import DEFAULT_USER_ID
from .embedding import (
    build_leaf_embedding_text,
    build_node_embedding_text,
    get_embedding,
)
from .models import ExtractedLeafMemory, MemoryNodeType
from .tree_store import MemoryTreeStore

_log = get_logger(__name__)

# ---- 路由配置（可通过环境变量覆盖）----
ROUTE_CONFIDENCE = float(os.getenv("AMADEUS_MEMORY_ROUTE_CONFIDENCE", "0.65"))
DEDUPE_SIMILARITY = float(os.getenv("AMADEUS_MEMORY_DEDUPE_SIMILARITY", "0.88"))
NODE_MERGE_SIMILARITY = float(os.getenv("AMADEUS_MEMORY_NODE_MERGE_SIMILARITY", "0.86"))


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------


def _build_route_query_text(leaf: ExtractedLeafMemory) -> str:
    """构造路由搜索用的查询文本（title + summary + keywords）。"""
    parts = [leaf.title, leaf.summary]
    if leaf.keywords:
        parts.append(" ".join(leaf.keywords))
    return "\n".join(p for p in parts if p)


def _distance_to_similarity(distance: float) -> float:
    """将 sqlite-vec 的 L2 距离转换为相似度（越大越相似，上限 1.0）。"""
    return max(0.0, 1.0 - distance)


def _enum_value(value: Any) -> str:
    """安全获取枚举的字符串值。"""
    return value.value if hasattr(value, "value") else str(value)


def _lowered_confidence(base: float) -> float:
    """路由失败时降低新节点的 confidence（下限 0.3）。"""
    return max(0.3, base - 0.15)


# ------------------------------------------------------------------
# 主函数
# ------------------------------------------------------------------


async def route_and_attach_leaf(
    leaf: ExtractedLeafMemory,
    tree_store: MemoryTreeStore,
    settings: ModelSettings,
    user_id: str = DEFAULT_USER_ID,
    conversation_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """主函数：路由叶子记忆到正确的树路径并挂载。

    流程：
      0. 处理 action 字段：noop/update_leaf/deactivate_leaf 提前返回
      a. 获取 domain 节点
      b. 搜索相似 topic
      c. 在 topic 下搜索相似 cluster
      d. 若无合适 topic/cluster 则创建（降低 confidence）
      e. 检查重复
      f. 创建 leaf 节点
      g. 写入 leaf 向量
      h. 处理 supersedes_node_ids（标记旧记忆 inactive + supersedes 边）
      i. 触发父节点摘要更新任务
    """
    # 0. 处理 action 字段
    action = (leaf.action or "create_leaf").strip()
    if action == "noop":
        _log.debug("action=noop，跳过叶子记忆: %s", leaf.title)
        return {}

    # 延迟导入 deduper 避免循环依赖
    from . import deduper as _deduper

    if action == "update_leaf" and leaf.target_node_id:
        # 更新已有叶子记忆
        existing = await tree_store.get_node(leaf.target_node_id)
        if existing:
            _log.info("action=update_leaf，合并到已有节点: %s", leaf.target_node_id)
            return await _deduper.merge_leaf_into_existing(
                leaf, existing, tree_store, settings
            )
        _log.warning(
            "action=update_leaf 但 target_node_id=%s 未找到，降级为 create_leaf",
            leaf.target_node_id,
        )

    if action == "deactivate_leaf" and leaf.target_node_id:
        # 停用已有叶子记忆
        _log.info("action=deactivate_leaf，停用节点: %s", leaf.target_node_id)
        await _deduper.supersede_leaf(
            old_node_id=leaf.target_node_id,
            tree_store=tree_store,
        )
        # 仍可能需要创建新叶子替代
        # 继续走 create_leaf 流程

    domain_value = _enum_value(leaf.domain)

    # a. 获取 domain 节点
    domain_node = await tree_store.get_domain_node(user_id, domain_value)
    if not domain_node:
        _log.error("未找到 domain 节点: %s，无法路由叶子记忆", domain_value)
        raise ValueError(f"domain node not found: {domain_value}")

    # b. 搜索相似 topic
    topic_node = await find_similar_topic(leaf, tree_store, settings, domain_node["id"])
    topic_is_new = topic_node is None
    cluster_is_new = False

    if topic_node:
        # c. 在该 topic 下搜索相似 cluster
        cluster_node = await find_similar_cluster(
            leaf, tree_store, settings, topic_node["id"]
        )
        if cluster_node is None:
            # topic 存在但无合适 cluster，创建新 cluster
            cluster_node = await _create_cluster_node(
                leaf, tree_store, topic_node, user_id, conversation_id, project_id
            )
            cluster_is_new = True
    else:
        # d. 无合适 topic，创建新的 topic 和 cluster
        topic_node, cluster_node = await create_topic_and_cluster(
            leaf, tree_store, domain_node, user_id, conversation_id, project_id
        )
        topic_is_new = True
        cluster_is_new = True

    # 为新建的 topic/cluster 写入节点向量（供后续路由搜索使用）
    if topic_is_new:
        await _ensure_node_vector(topic_node, tree_store, settings)
    if cluster_is_new:
        await _ensure_node_vector(cluster_node, tree_store, settings)

    # e. 检查是否与同父节点下已有 leaf 重复
    dup_id = await check_duplicate(leaf, tree_store, settings, cluster_node["id"])
    if dup_id:
        _log.info("检测到重复 leaf，跳过创建，复用已有节点: %s", dup_id)
        existing = await tree_store.get_node(dup_id)
        if existing:
            await tree_store.touch_node(dup_id)
            return existing

    # f. 创建 leaf 节点
    leaf_data: dict[str, Any] = {
        "nodeType": MemoryNodeType.LEAF.value,
        "parentId": cluster_node["id"],
        "domain": domain_value,
        "label": leaf.title,
        "summary": leaf.summary,
        "fullContent": leaf.full_content,
        "category": _enum_value(leaf.category),
        "keywords": list(leaf.keywords),
        "projectId": project_id,
        "conversationId": conversation_id,
        "sourceMessageIds": list(leaf.source_message_ids),
        "sourceSummary": leaf.source_summary,
        "importance": leaf.importance,
        "confidence": leaf.confidence,
    }
    if leaf.expires_in_days is not None:
        leaf_data["expiresAt"] = (
            datetime.now(timezone.utc) + timedelta(days=leaf.expires_in_days)
        ).isoformat()

    created_leaf = await tree_store.create_node(leaf_data)

    # g. 生成 leaf embedding 并写入 leaf_vectors
    try:
        leaf_emb = await get_embedding(
            build_leaf_embedding_text(created_leaf), settings
        )
        await tree_store.upsert_leaf_vector(created_leaf["id"], leaf_emb)
    except Exception as e:
        _log.warning("写入 leaf 向量失败（降级跳过）: %s", e)

    # h. 处理 supersedes_node_ids：标记旧记忆 inactive + 建立 supersedes 边
    if leaf.supersedes_node_ids:
        for old_id in leaf.supersedes_node_ids:
            try:
                await _deduper.supersede_leaf(
                    old_node_id=old_id,
                    tree_store=tree_store,
                    new_node_id=created_leaf["id"],
                )
            except Exception as e:
                _log.warning("supersede 旧节点失败 (old=%s): %s", old_id, e)

    # i. 触发父节点摘要更新任务（cluster 与 topic）
    #    用 leaf id 触发，summarizer 会从 leaf 的父节点(cluster)开始沿父链
    #    向上更新 cluster → topic → domain 的 summary 和 node 向量
    try:
        await tree_store.enqueue_job(
            user_id, "summarize_node", conversation_id,
            {"node_id": created_leaf["id"]},
        )
    except Exception as e:
        _log.warning("enqueue summarize_node 任务失败: %s", e)

    _log.info(
        "叶子记忆已挂载: domain=%s topic=%s cluster=%s leaf=%s",
        domain_value, topic_node["id"], cluster_node["id"], created_leaf["id"],
    )
    return created_leaf


# ------------------------------------------------------------------
# 相似节点搜索
# ------------------------------------------------------------------


async def find_similar_topic(
    leaf: ExtractedLeafMemory,
    tree_store: MemoryTreeStore,
    settings: ModelSettings,
    domain_node_id: str,
) -> dict[str, Any] | None:
    """在 domain 子树下找相似 topic 节点。

    用 leaf 的 summary+keywords 生成 embedding，在 domain 下的 topic 节点向量中搜索。
    embedding 不可用时降级为 FTS。
    """
    candidates = await tree_store.get_children(domain_node_id)
    topic_candidates = [
        c for c in candidates if c["nodeType"] == MemoryNodeType.TOPIC.value
    ]
    if not topic_candidates:
        return None

    candidate_ids = [c["id"] for c in topic_candidates]
    query_text = _build_route_query_text(leaf)

    try:
        query_emb = await get_embedding(query_text, settings)
        results = await tree_store.search_node_vectors(
            query_emb, top_k=5, candidate_ids=candidate_ids
        )
        for node_id, distance in results:
            similarity = _distance_to_similarity(distance)
            if similarity >= ROUTE_CONFIDENCE:
                node = await tree_store.get_node(node_id)
                if node:
                    _log.debug(
                        "找到相似 topic: %s (similarity=%.3f)", node_id, similarity
                    )
                    return node
    except Exception as e:
        _log.warning("topic 向量搜索失败，降级到 FTS: %s", e)
        # FTS 降级：在候选 topic 中取关键词匹配的 top 结果
        fts_results = await tree_store.fts_search(
            query_text, node_type=MemoryNodeType.TOPIC.value, limit=10
        )
        cand_set = set(candidate_ids)
        for node_id, _score in fts_results:
            if node_id in cand_set:
                node = await tree_store.get_node(node_id)
                if node:
                    _log.debug("FTS 降级命中 topic: %s", node_id)
                    return node

    return None


async def find_similar_cluster(
    leaf: ExtractedLeafMemory,
    tree_store: MemoryTreeStore,
    settings: ModelSettings,
    topic_node_id: str,
) -> dict[str, Any] | None:
    """在 topic 子树下找相似 cluster 节点。

    用 leaf 的 summary+keywords 生成 embedding，在 topic 下的 cluster 节点向量中搜索。
    embedding 不可用时降级为 FTS。
    """
    candidates = await tree_store.get_children(topic_node_id)
    cluster_candidates = [
        c for c in candidates if c["nodeType"] == MemoryNodeType.CLUSTER.value
    ]
    if not cluster_candidates:
        return None

    candidate_ids = [c["id"] for c in cluster_candidates]
    query_text = _build_route_query_text(leaf)

    try:
        query_emb = await get_embedding(query_text, settings)
        results = await tree_store.search_node_vectors(
            query_emb, top_k=5, candidate_ids=candidate_ids
        )
        for node_id, distance in results:
            similarity = _distance_to_similarity(distance)
            if similarity >= ROUTE_CONFIDENCE:
                node = await tree_store.get_node(node_id)
                if node:
                    _log.debug(
                        "找到相似 cluster: %s (similarity=%.3f)", node_id, similarity
                    )
                    return node
    except Exception as e:
        _log.warning("cluster 向量搜索失败，降级到 FTS: %s", e)
        fts_results = await tree_store.fts_search(
            query_text, node_type=MemoryNodeType.CLUSTER.value, limit=10
        )
        cand_set = set(candidate_ids)
        for node_id, _score in fts_results:
            if node_id in cand_set:
                node = await tree_store.get_node(node_id)
                if node:
                    _log.debug("FTS 降级命中 cluster: %s", node_id)
                    return node

    return None


# ------------------------------------------------------------------
# 节点创建
# ------------------------------------------------------------------


async def create_topic_and_cluster(
    leaf: ExtractedLeafMemory,
    tree_store: MemoryTreeStore,
    domain_node: dict[str, Any],
    user_id: str,
    conversation_id: str | None,
    project_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """创建新的 topic 和 cluster 节点。

    路由置信度不足时调用，新节点 confidence 适当降低。
    返回 (topic_node, cluster_node)。
    """
    domain_value = domain_node.get("domain", "")
    lowered_conf = _lowered_confidence(leaf.confidence)

    # 创建 topic
    topic_data: dict[str, Any] = {
        "nodeType": MemoryNodeType.TOPIC.value,
        "parentId": domain_node["id"],
        "domain": domain_value,
        "label": (leaf.title or "未命名主题")[:60],
        "summary": leaf.summary,
        "fullContent": "",
        "category": _enum_value(leaf.category),
        "keywords": list(leaf.keywords),
        "projectId": project_id,
        "conversationId": conversation_id,
        "importance": leaf.importance,
        "confidence": lowered_conf,
    }
    topic_node = await tree_store.create_node(topic_data)

    # 创建 cluster
    cluster_node = await _create_cluster_node(
        leaf, tree_store, topic_node, user_id, conversation_id, project_id
    )

    _log.info(
        "新建 topic+cluster: domain=%s topic=%s cluster=%s",
        domain_value, topic_node["id"], cluster_node["id"],
    )
    return topic_node, cluster_node


async def _create_cluster_node(
    leaf: ExtractedLeafMemory,
    tree_store: MemoryTreeStore,
    topic_node: dict[str, Any],
    user_id: str,
    conversation_id: str | None,
    project_id: str | None,
) -> dict[str, Any]:
    """在指定 topic 下创建新的 cluster 节点（路由失败时 confidence 降低）。"""
    domain_value = topic_node.get("domain", "")
    lowered_conf = _lowered_confidence(leaf.confidence)

    cluster_data: dict[str, Any] = {
        "nodeType": MemoryNodeType.CLUSTER.value,
        "parentId": topic_node["id"],
        "domain": domain_value,
        "label": (leaf.title or "未命名聚类")[:60],
        "summary": leaf.summary,
        "fullContent": "",
        "category": _enum_value(leaf.category),
        "keywords": list(leaf.keywords),
        "projectId": project_id,
        "conversationId": conversation_id,
        "importance": leaf.importance,
        "confidence": lowered_conf,
    }
    cluster_node = await tree_store.create_node(cluster_data)
    _log.debug("新建 cluster: %s under topic=%s", cluster_node["id"], topic_node["id"])
    return cluster_node


async def _ensure_node_vector(
    node: dict[str, Any],
    tree_store: MemoryTreeStore,
    settings: ModelSettings,
) -> None:
    """为新创建的 topic/cluster 节点生成并写入节点向量。

    失败时降级跳过（后续路由会退化为 FTS）。
    """
    try:
        emb = await get_embedding(build_node_embedding_text(node), settings)
        await tree_store.upsert_node_vector(node["id"], emb)
    except Exception as e:
        _log.warning("写入节点向量失败（降级跳过）: node=%s err=%s", node.get("id"), e)


# ------------------------------------------------------------------
# 去重检查
# ------------------------------------------------------------------


async def check_duplicate(
    leaf: ExtractedLeafMemory,
    tree_store: MemoryTreeStore,
    settings: ModelSettings,
    parent_node_id: str,
) -> str | None:
    """检查是否与同父节点下已有 leaf 重复。

    用 leaf 的 full_content 生成 embedding，在父节点下的 leaf 向量中搜索。
    相似度 >= DEDUPE_SIMILARITY 时视为重复，返回重复 leaf 的 node_id；否则返回 None。
    embedding 不可用时降级为 FTS。
    """
    candidates = await tree_store.get_children(parent_node_id)
    leaf_candidates = [
        c for c in candidates if c["nodeType"] == MemoryNodeType.LEAF.value
    ]
    if not leaf_candidates:
        return None

    candidate_ids = [c["id"] for c in leaf_candidates]
    query_text = leaf.full_content or leaf.summary

    try:
        query_emb = await get_embedding(query_text, settings)
        results = await tree_store.search_leaf_vectors(
            query_emb, top_k=5, candidate_ids=candidate_ids
        )
        for node_id, distance in results:
            similarity = _distance_to_similarity(distance)
            if similarity >= DEDUPE_SIMILARITY:
                _log.info(
                    "检测到重复 leaf: %s (similarity=%.3f)", node_id, similarity
                )
                return node_id
    except Exception as e:
        _log.warning("去重向量搜索失败，降级到 FTS: %s", e)
        # FTS 降级：在候选 leaf 中取关键词匹配的 top 结果
        fts_results = await tree_store.fts_search(
            query_text, node_type=MemoryNodeType.LEAF.value, limit=10
        )
        cand_set = set(candidate_ids)
        for node_id, _score in fts_results:
            if node_id in cand_set:
                _log.info("FTS 降级命中重复 leaf: %s", node_id)
                return node_id

    return None
