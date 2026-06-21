"""记忆树逐层检索器 — domain 路由 → 节点召回 → 叶子精查。

检索流程：
1. 生成 query embedding（失败则降级为纯 FTS）
2. 领域路由：向量搜索 top-K domain，或使用指定 domains
3. 节点召回：在 domain 子树下向量 + FTS 双通道搜索 topic/cluster
4. 子树裁剪：按 score 选取 top topic/cluster
5. 叶子精查：在选中 topic/cluster 子树下向量 + FTS 搜索 leaf
6. 合并去重，按 score 排序
7. 更新访问统计
8. 构造 MemorySearchResult
"""

from __future__ import annotations

import os
from typing import Any

from ..domain import ModelProviderPreset, ModelSettings
from ..logging_config import get_logger
from ..storage import DEFAULT_USER_ID
from .embedding import get_embedding
from .models import (
    MemoryCategory,
    MemoryDomain,
    MemoryNode,
    MemoryNodeType,
    MemorySearchResult,
)
from .query_rewriter import build_search_intent
from .reranker import rerank_nodes
from .tree_store import MemoryTreeStore

_log = get_logger(__name__)

# ---- 检索配置（可通过环境变量覆盖）----
DOMAIN_TOP_K = int(os.getenv("AMADEUS_MEMORY_DOMAIN_TOP_K", "3"))
NODE_TOP_K = int(os.getenv("AMADEUS_MEMORY_NODE_TOP_K", "8"))
LEAF_TOP_K = int(os.getenv("AMADEUS_MEMORY_LEAF_TOP_K", "8"))
MIN_CONFIDENCE = float(os.getenv("AMADEUS_MEMORY_MIN_CONFIDENCE", "0.5"))


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------


def _distance_to_similarity(distance: float) -> float:
    """将 sqlite-vec 的 L2 距离转换为相似度（越大越相似，上限 1.0）。"""
    return max(0.0, 1.0 - distance)


def _enum_value(value: Any) -> str:
    """安全获取枚举的字符串值。"""
    return value.value if hasattr(value, "value") else str(value)


def _dict_to_memory_node(d: dict[str, Any]) -> MemoryNode:
    """将 tree_store 返回的 camelCase dict 转换为 MemoryNode 模型。"""
    try:
        node_type = MemoryNodeType(d.get("nodeType", "leaf"))
    except ValueError:
        node_type = MemoryNodeType.LEAF
    try:
        category = MemoryCategory(d.get("category", "general"))
    except ValueError:
        category = MemoryCategory.GENERAL
    return MemoryNode(
        id=d["id"],
        parent_id=d.get("parentId"),
        node_type=node_type,
        domain=d.get("domain", ""),
        label=d.get("label", ""),
        path=d.get("path", ""),
        depth=d.get("depth", 0),
        summary=d.get("summary", ""),
        full_content=d.get("fullContent", ""),
        category=category,
        keywords=d.get("keywords", []),
        project_id=d.get("projectId"),
        conversation_id=d.get("conversationId"),
        source_message_ids=d.get("sourceMessageIds", []),
        source_summary=d.get("sourceSummary", ""),
        importance=d.get("importance", 0.5),
        confidence=d.get("confidence", 0.7),
        access_count=d.get("accessCount", 0),
        leaf_count=d.get("leafCount", 0),
        last_accessed_at=d.get("lastAccessedAt"),
        created_at=d.get("createdAt", ""),
        updated_at=d.get("updatedAt", ""),
        expires_at=d.get("expiresAt"),
        is_active=d.get("isActive", True),
    )


def _normalize_fts_scores(results: list[tuple[str, float]]) -> dict[str, float]:
    """将 FTS 的 bm25 分数归一化到 0-1 区间。"""
    if not results:
        return {}
    max_score = max(s for _, s in results)
    if max_score <= 0:
        max_score = 1.0
    return {nid: s / max_score for nid, s in results}


async def _collect_leaf_ids(
    tree_store: MemoryTreeStore, node_id: str
) -> set[str]:
    """递归收集节点下的所有 leaf ID。"""
    result: set[str] = set()
    children = await tree_store.get_children(node_id)
    for child in children:
        if child["nodeType"] == MemoryNodeType.LEAF.value:
            result.add(child["id"])
        else:
            # 递归（topic -> cluster -> leaf）
            result.update(await _collect_leaf_ids(tree_store, child["id"]))
    return result


async def _route_domains(
    tree_store: MemoryTreeStore,
    query: str,
    query_emb: list[float] | None,
    all_domains: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """领域路由：向量搜索 top-K domain，降级为 FTS，最后默认全部。"""
    if not all_domains:
        return []

    domain_ids = [d["id"] for d in all_domains]
    id_to_domain = {d["id"]: d for d in all_domains}

    # 向量搜索
    if query_emb is not None:
        try:
            vec_results = await tree_store.search_node_vectors(
                query_emb, top_k=DOMAIN_TOP_K, candidate_ids=domain_ids
            )
            if vec_results:
                ordered = [
                    id_to_domain[nid]
                    for nid, _ in vec_results
                    if nid in id_to_domain
                ]
                if ordered:
                    return ordered[:DOMAIN_TOP_K]
        except Exception as e:
            _log.warning("domain 向量搜索失败，降级 FTS: %s", e)

    # FTS 降级
    try:
        fts_results = await tree_store.fts_search(
            query, node_type=MemoryNodeType.DOMAIN.value, limit=DOMAIN_TOP_K
        )
        if fts_results:
            ordered = [
                id_to_domain[nid]
                for nid, _ in fts_results
                if nid in id_to_domain
            ]
            if ordered:
                return ordered
    except Exception as e:
        _log.warning("domain FTS 搜索失败: %s", e)

    # 全部 domain 作为兜底（无路由信号时不裁剪，避免因排序不确定漏召回）
    return all_domains


async def _build_selected_paths(
    selected_nodes: list[dict[str, Any]],
    node_id_to_node: dict[str, dict[str, Any]],
    domain_of_node: dict[str, dict[str, Any]],
    tree_store: MemoryTreeStore,
) -> list[list[dict[str, Any]]]:
    """为每个选中的 topic/cluster 构建从 domain 到该节点的路径。

    topic  -> [domain, topic]
    cluster -> [domain, topic, cluster]
    """
    paths: list[list[dict[str, Any]]] = []
    for node in selected_nodes:
        node_type = node.get("nodeType")
        if node_type == MemoryNodeType.TOPIC.value:
            domain_node = domain_of_node.get(node["id"])
            paths.append([domain_node, node] if domain_node else [node])
        elif node_type == MemoryNodeType.CLUSTER.value:
            topic_id = node.get("parentId")
            topic_node = node_id_to_node.get(topic_id)
            if topic_node is None and topic_id:
                # 候选集中未命中，回退到存储层查询
                topic_node = await tree_store.get_node(topic_id)
            domain_node = domain_of_node.get(topic_id) if topic_id else None
            if domain_node is None and topic_node and topic_node.get("parentId"):
                domain_node = await tree_store.get_node(topic_node["parentId"])
            if domain_node and topic_node:
                paths.append([domain_node, topic_node, node])
            elif topic_node:
                paths.append([topic_node, node])
            else:
                paths.append([node])
        else:
            paths.append([node])
    return paths


# ------------------------------------------------------------------
# 主检索函数
# ------------------------------------------------------------------


async def search_memory_tree(
    query: str,
    tree_store: MemoryTreeStore,
    settings: ModelSettings | None = None,
    user_id: str = DEFAULT_USER_ID,
    conversation_id: str | None = None,
    project_id: str | None = None,
    domains: list[str] | None = None,
    node_top_k: int = 8,
    leaf_top_k: int = 8,
    path_hint: str | None = None,
    *,
    provider: ModelProviderPreset | None = None,
    recent_messages: list[dict] | None = None,
    session_summary: str | None = None,
    project_name: str | None = None,
    active_injection: bool = True,
) -> MemorySearchResult:
    """逐层检索记忆树：domain 路由 → 节点召回 → 叶子精查。

    流程：
      a. Query 改写：用当前消息 + 最近上下文 + 会话摘要生成 SearchIntent
      b. 若有 settings，生成 query embedding；失败则降级为纯 FTS
      c. 领域路由：优先使用 intent.domains，否则向量搜索 top-K domain
      d. 节点召回：在选中 domain 子树下向量 + FTS 双通道搜索 topic/cluster
      e. 子树裁剪：用 reranker 加权重排选取 top topic/cluster
      f. 叶子精查：在选中 topic/cluster 子树下向量 + FTS 搜索 leaf
      g. 用 reranker 加权重排选取 top leaf
      h. touch_node 更新访问统计
      i. 构造 MemorySearchResult

    Args:
        path_hint: 路径前缀提示，用于缩小 topic/cluster 候选范围（如 "/code/auth"）
        provider: LLM provider，用于 query 改写
        recent_messages: 最近 4-6 条对话，用于 query 改写
        session_summary: 当前会话摘要，用于 query 改写
        project_name: 当前项目名，用于 query 改写
        active_injection: True=主动注入（过滤低置信/过期），False=工具检索（仅过滤停用）
    """
    if not query or not query.strip():
        _log.warning("查询为空，返回空结果")
        return MemorySearchResult()

    _log.info("开始记忆树检索: query=%r user=%s", query[:80], user_id)

    # 若未提供 project_id 但有 conversation_id，尝试从会话获取
    if project_id is None and conversation_id is not None:
        try:
            project_id = await tree_store.get_conversation_project(conversation_id)
        except Exception as e:
            _log.warning("获取会话项目失败: %s", e)

    # a. Query 改写：生成 SearchIntent（失败降级为原始 query）
    search_query = query
    intent_domains: list[str] = []
    intent_path_hints: list[str] = []
    if settings is not None and provider is not None:
        try:
            intent = await build_search_intent(
                user_message=query,
                recent_messages=recent_messages,
                session_summary=session_summary,
                project_name=project_name,
                settings=settings,
                provider=provider,
            )
            search_query = intent.rewritten_query or query
            intent_domains = [d.value for d in intent.domains]
            intent_path_hints = list(intent.path_hints)
            _log.info(
                "Query 改写: %r -> %r (type=%s, domains=%s, path_hints=%s)",
                query[:60], search_query[:60], intent.query_type,
                intent_domains, intent_path_hints,
            )
        except Exception as e:
            _log.warning("Query 改写失败，使用原始 query: %s", e)

    # 合并路径提示：显式 path_hint + intent.path_hints
    all_path_hints: list[str] = []
    if path_hint:
        all_path_hints.append(path_hint.strip())
    all_path_hints.extend(h.strip() for h in intent_path_hints if h.strip())

    # b. 生成 query embedding（用改写后的 query）
    query_emb: list[float] | None = None
    if settings is not None:
        try:
            query_emb = await get_embedding(search_query, settings)
        except Exception as e:
            _log.warning("生成 query embedding 失败，降级为纯 FTS: %s", e)
            query_emb = None

    # c. 领域路由：优先使用显式 domains，其次 intent.domains，最后向量路由
    all_domains = await tree_store.list_nodes(
        user_id=user_id, node_type=MemoryNodeType.DOMAIN.value
    )
    if domains:
        # 使用调用方指定的 domains
        domain_set = {_enum_value(d) for d in domains}
        selected_domains = [d for d in all_domains if d["domain"] in domain_set]
        if not selected_domains:
            _log.warning("指定的 domains %s 未找到匹配节点", domain_set)
    elif intent_domains:
        # 使用 query 改写推断的 domains
        domain_set = set(intent_domains)
        selected_domains = [d for d in all_domains if d["domain"] in domain_set]
        if not selected_domains:
            _log.warning("intent domains %s 未找到匹配节点，降级向量路由", domain_set)
            selected_domains = await _route_domains(
                tree_store, search_query, query_emb, all_domains
            )
    else:
        selected_domains = await _route_domains(
            tree_store, search_query, query_emb, all_domains
        )

    if not selected_domains:
        _log.info("未选中任何 domain，返回空结果")
        return MemorySearchResult()

    _log.info(
        "选中 domain: %s",
        [d["domain"] for d in selected_domains],
    )

    # c. 节点召回：收集选中 domain 下的 topic/cluster
    candidate_nodes: list[dict[str, Any]] = []
    domain_of_node: dict[str, dict[str, Any]] = {}
    for domain_node in selected_domains:
        domain_value = domain_node["domain"]
        topics = await tree_store.list_nodes(
            user_id=user_id,
            node_type=MemoryNodeType.TOPIC.value,
            domain=domain_value,
        )
        clusters = await tree_store.list_nodes(
            user_id=user_id,
            node_type=MemoryNodeType.CLUSTER.value,
            domain=domain_value,
        )
        for n in topics + clusters:
            candidate_nodes.append(n)
            domain_of_node[n["id"]] = domain_node

    # 若提供 path_hint 或 intent.path_hints，按路径前缀缩小候选范围
    if all_path_hints and candidate_nodes:
        filtered: list[dict[str, Any]] = []
        for n in candidate_nodes:
            node_path = n.get("path", "")
            if any(node_path.startswith(h) for h in all_path_hints):
                filtered.append(n)
        if filtered:
            candidate_nodes = filtered

    if not candidate_nodes:
        _log.info("选中 domain 下无 topic/cluster 节点")
        return MemorySearchResult(
            selected_paths=[[_dict_to_memory_node(d)] for d in selected_domains],
        )

    # d. 节点召回：向量 + FTS 双通道搜索 topic/cluster
    node_vector_scores: dict[str, float] = {}
    node_keyword_scores: dict[str, float] = {}
    candidate_id_set = {n["id"] for n in candidate_nodes}
    candidate_ids = [n["id"] for n in candidate_nodes]

    if query_emb is not None:
        try:
            vec_results = await tree_store.search_node_vectors(
                query_emb, top_k=node_top_k, candidate_ids=candidate_ids
            )
            for nid, dist in vec_results:
                node_vector_scores[nid] = _distance_to_similarity(dist)
        except Exception as e:
            _log.warning("topic/cluster 向量搜索失败: %s", e)

    # FTS 搜索 topic 和 cluster
    for nt in (MemoryNodeType.TOPIC.value, MemoryNodeType.CLUSTER.value):
        try:
            fts_results = await tree_store.fts_search(
                search_query, node_type=nt, limit=node_top_k * 2
            )
            # 过滤到候选集
            fts_filtered = [
                (nid, s) for nid, s in fts_results if nid in candidate_id_set
            ]
            normalized = _normalize_fts_scores(fts_filtered)
            for nid, score in normalized.items():
                node_keyword_scores[nid] = max(node_keyword_scores.get(nid, 0.0), score)
        except Exception as e:
            _log.warning("topic/cluster FTS 搜索失败 (%s): %s", nt, e)

    # e. 子树裁剪：用 reranker 加权重排选取 top topic/cluster
    reranked_nodes = rerank_nodes(
        candidates=candidate_nodes,
        vector_scores=node_vector_scores,
        keyword_scores=node_keyword_scores,
        current_project_id=project_id,
        active_injection=active_injection,
        top_k=node_top_k,
    )
    selected_nodes = reranked_nodes

    _log.info(
        "选中 topic/cluster: %s",
        [
            (n["nodeType"], n.get("label", ""), n.get("_score", 0.0))
            for n in selected_nodes
        ],
    )

    # f. 叶子精查：在选中 topic/cluster 子树下搜索 leaf
    leaf_candidate_ids: set[str] = set()
    for node in selected_nodes:
        leaves = await _collect_leaf_ids(tree_store, node["id"])
        leaf_candidate_ids.update(leaves)

    leaf_vector_scores: dict[str, float] = {}
    leaf_keyword_scores: dict[str, float] = {}

    if query_emb is not None and leaf_candidate_ids:
        try:
            vec_results = await tree_store.search_leaf_vectors(
                query_emb,
                top_k=leaf_top_k,
                candidate_ids=list(leaf_candidate_ids),
            )
            for nid, dist in vec_results:
                leaf_vector_scores[nid] = _distance_to_similarity(dist)
        except Exception as e:
            _log.warning("leaf 向量搜索失败: %s", e)

    # FTS 搜索 leaf
    if leaf_candidate_ids:
        try:
            fts_results = await tree_store.fts_search(
                search_query,
                node_type=MemoryNodeType.LEAF.value,
                limit=leaf_top_k * 2,
            )
            fts_filtered = [
                (nid, s)
                for nid, s in fts_results
                if nid in leaf_candidate_ids
            ]
            normalized = _normalize_fts_scores(fts_filtered)
            for nid, score in normalized.items():
                leaf_keyword_scores[nid] = max(leaf_keyword_scores.get(nid, 0.0), score)
        except Exception as e:
            _log.warning("leaf FTS 搜索失败: %s", e)

    # g. 用 reranker 加权重排选取 top leaf
    # 收集候选 leaf 节点 dict（需要从 tree_store 获取完整节点信息）
    leaf_candidate_nodes: list[dict[str, Any]] = []
    for nid in leaf_candidate_ids:
        node = await tree_store.get_node(nid)
        if node:
            leaf_candidate_nodes.append(node)

    leaf_nodes = rerank_nodes(
        candidates=leaf_candidate_nodes,
        vector_scores=leaf_vector_scores,
        keyword_scores=leaf_keyword_scores,
        current_project_id=project_id,
        active_injection=active_injection,
        top_k=leaf_top_k,
    )

    # h. 更新访问统计
    for node in leaf_nodes:
        try:
            await tree_store.touch_node(node["id"])
        except Exception as e:
            _log.warning("touch_node 失败 (node=%s): %s", node.get("id"), e)

    # i. 构造选中路径
    node_id_to_node = {n["id"]: n for n in candidate_nodes}
    selected_paths = await _build_selected_paths(
        selected_nodes, node_id_to_node, domain_of_node, tree_store
    )

    # 构造 MemorySearchResult
    result = MemorySearchResult(
        selected_paths=[
            [_dict_to_memory_node(n) for n in path] for path in selected_paths
        ],
        topic_nodes=[_dict_to_memory_node(n) for n in selected_nodes],
        leaf_nodes=[_dict_to_memory_node(n) for n in leaf_nodes],
        context_text="",
    )

    _log.info(
        "记忆树检索完成: paths=%d topics=%d leaves=%d",
        len(result.selected_paths),
        len(result.topic_nodes),
        len(result.leaf_nodes),
    )
    return result


# ------------------------------------------------------------------
# 便捷检索函数
# ------------------------------------------------------------------


async def get_user_profile_memories(
    tree_store: MemoryTreeStore,
    user_id: str = DEFAULT_USER_ID,
) -> list[dict[str, Any]]:
    """从 /daily_chat 子树读取高置信叶子（confidence >= MIN_CONFIDENCE）。

    用于注入用户画像/偏好类记忆。
    """
    domain_node = await tree_store.get_domain_node(
        user_id, MemoryDomain.DAILY_CHAT.value
    )
    if not domain_node:
        _log.warning("未找到 daily_chat domain 节点")
        return []

    # 直接查询 daily_chat 域下的所有 leaf
    leaves = await tree_store.list_nodes(
        user_id=user_id,
        node_type=MemoryNodeType.LEAF.value,
        domain=MemoryDomain.DAILY_CHAT.value,
        limit=500,
    )

    # 过滤高置信叶子
    result = [
        leaf for leaf in leaves if leaf.get("confidence", 0) >= MIN_CONFIDENCE
    ]
    _log.info(
        "读取用户画像记忆: %d/%d 条高置信叶子", len(result), len(leaves)
    )
    return result


async def get_project_context_memories(
    tree_store: MemoryTreeStore,
    project_id: str,
) -> list[dict[str, Any]]:
    """读取项目路径下的 topic/cluster 摘要。

    用于注入项目上下文记忆，帮助理解当前项目背景。
    """
    topics = await tree_store.list_nodes(
        node_type=MemoryNodeType.TOPIC.value, project_id=project_id, limit=100
    )
    clusters = await tree_store.list_nodes(
        node_type=MemoryNodeType.CLUSTER.value, project_id=project_id, limit=100
    )
    _log.info(
        "读取项目上下文记忆: project=%s topics=%d clusters=%d",
        project_id,
        len(topics),
        len(clusters),
    )
    return topics + clusters
