"""记忆去重与冲突处理模块。

当新叶子记忆与已有记忆重复或冲突时，合并、替代或标记冲突，
而不是简单跳过。extractor.py 已输出 action 字段
（create_leaf/update_leaf/deactivate_leaf）和 supersedes_node_ids，
本模块负责处理 update_leaf / deactivate_leaf / 冲突场景：

1. merge_leaf_into_existing — 将新叶子合并到已有叶子（update_leaf）
2. supersede_leaf — 新记忆替代旧记忆（deactivate_leaf / supersedes_node_ids）
3. handle_conflict — 处理冲突记忆：降低旧记忆 confidence，插入 conflicts_with 边
4. find_duplicate_leaf — 在指定父节点下查找与新叶子重复的已有叶子
5. find_similar_nodes_for_merge — 查找可合并的相似节点对（topic / cluster）

所有函数均不抛异常（非致命），失败只记日志并返回 None / 空。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from ..domain import ModelSettings
from ..logging_config import get_logger
from ..storage import DEFAULT_USER_ID
from .embedding import (
    build_leaf_embedding_text,
    build_node_embedding_text,
    get_embedding,
)
from .models import ExtractedLeafMemory
from .tree_store import MemoryTreeStore

_log = get_logger(__name__)

# ---- 去重 / 合并阈值（可通过环境变量覆盖）----
DEDUPE_SIMILARITY = float(os.getenv("AMADEUS_MEMORY_DEDUPE_SIMILARITY", "0.88"))
NODE_MERGE_SIMILARITY = float(os.getenv("AMADEUS_MEMORY_NODE_MERGE_SIMILARITY", "0.86"))

# 合并后 fullContent 的最大字符数
_MERGED_FULL_CONTENT_MAX_CHARS = 5000


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------


def _distance_to_similarity(distance: float) -> float:
    """将 sqlite-vec 的 L2 距离转换为相似度（越大越相似，上限 1.0）。"""
    return max(0.0, 1.0 - distance)


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _insert_edge(
    tree_store: MemoryTreeStore,
    parent_id: str,
    child_id: str,
    relation: str,
) -> None:
    """向 memory_edges 表插入一条边（INSERT OR IGNORE）。

    relation 取值：contains / references / supersedes / conflicts_with。
    失败时只记日志，不抛异常。
    """
    try:
        conn = tree_store._conn
        conn.execute(
            """INSERT OR IGNORE INTO memory_edges
               (parent_id, child_id, relation, weight, created_at)
               VALUES (?, ?, ?, 1.0, ?)""",
            (parent_id, child_id, relation, _now_iso()),
        )
    except Exception as e:  # noqa: BLE001
        _log.warning(
            "插入 memory_edges 失败 (parent=%s, child=%s, relation=%s): %s",
            parent_id, child_id, relation, e,
        )


# ------------------------------------------------------------------
# 1. 合并叶子
# ------------------------------------------------------------------


async def merge_leaf_into_existing(
    new_leaf: ExtractedLeafMemory,
    existing_node: dict[str, Any],
    tree_store: MemoryTreeStore,
    settings: ModelSettings,
) -> dict[str, Any] | None:
    """将新叶子记忆合并到已有叶子节点（update_leaf 场景）。

    流程：
    1. 合并 sourceMessageIds 去重
    2. 拼接 fullContent（旧 + 分隔符 + 新），截断到 5000 字
    3. 更新 summary：取 new_leaf.summary（较新优先），为空则保留旧值
    4. 合并 keywords 去重
    5. importance / confidence 取较大值
    6. 调用 tree_store.update_node
    7. 重建 leaf 向量
    8. 返回更新后的节点 dict

    失败时返回 None。
    """
    try:
        existing_id = existing_node.get("id")
        if not existing_id:
            _log.warning("merge_leaf_into_existing: existing_node 缺少 id，跳过")
            return None

        # 1. 合并 sourceMessageIds
        old_msg_ids: list[str] = list(existing_node.get("sourceMessageIds") or [])
        merged_msg_ids: list[str] = []
        seen: set[str] = set()
        for mid in [*old_msg_ids, *new_leaf.source_message_ids]:
            if mid and mid not in seen:
                seen.add(mid)
                merged_msg_ids.append(mid)

        # 2. 拼接 fullContent
        old_full = existing_node.get("fullContent") or ""
        new_full = new_leaf.full_content or ""
        if old_full and new_full:
            merged_full = f"{old_full}\n\n---\n{new_full}"
        elif new_full:
            merged_full = new_full
        else:
            merged_full = old_full
        merged_full = merged_full[:_MERGED_FULL_CONTENT_MAX_CHARS]

        # 3. summary：较新优先，为空保留旧值
        new_summary = (new_leaf.summary or "").strip()
        merged_summary = new_summary if new_summary else (existing_node.get("summary") or "")

        # 4. 合并 keywords 去重
        old_keywords: list[str] = list(existing_node.get("keywords") or [])
        merged_keywords: list[str] = []
        seen_kw: set[str] = set()
        for kw in [*old_keywords, *new_leaf.keywords]:
            if kw and kw not in seen_kw:
                seen_kw.add(kw)
                merged_keywords.append(kw)

        # 5. importance / confidence 取较大值
        old_importance = float(existing_node.get("importance") or 0.0)
        old_confidence = float(existing_node.get("confidence") or 0.0)
        merged_importance = max(old_importance, float(new_leaf.importance or 0.0))
        merged_confidence = max(old_confidence, float(new_leaf.confidence or 0.0))

        # 6. 更新节点
        updates = {
            "sourceMessageIds": merged_msg_ids,
            "fullContent": merged_full,
            "summary": merged_summary,
            "keywords": merged_keywords,
            "importance": merged_importance,
            "confidence": merged_confidence,
        }
        updated = await tree_store.update_node(existing_id, updates)
        if not updated:
            _log.warning("merge_leaf_into_existing: update_node 返回 None: %s", existing_id)
            return None

        # 7. 重建 leaf 向量
        try:
            emb_text = build_leaf_embedding_text(updated)
            emb = await get_embedding(emb_text, settings)
            await tree_store.upsert_leaf_vector(existing_id, emb)
        except Exception as e:  # noqa: BLE001
            _log.warning(
                "merge_leaf_into_existing: 重建 leaf 向量失败 (id=%s): %s",
                existing_id, e,
            )

        _log.info(
            "已合并叶子记忆到已有节点 id=%s (msg_ids=%d, keywords=%d)",
            existing_id, len(merged_msg_ids), len(merged_keywords),
        )
        return updated
    except Exception as e:  # noqa: BLE001
        _log.warning("merge_leaf_into_existing 失败: %s", e)
        return None


# ------------------------------------------------------------------
# 2. 替代叶子
# ------------------------------------------------------------------


async def supersede_leaf(
    old_node_id: str,
    tree_store: MemoryTreeStore,
    new_node_id: str | None = None,
) -> None:
    """新记忆替代旧记忆（deactivate_leaf 场景或 supersedes_node_ids 非空）。

    流程：
    1. 将旧节点标记为 inactive
    2. 在 memory_edges 表插入 supersedes 边
       （parent_id = new_node_id，child_id = old_node_id）
    3. 记日志

    参数：
        old_node_id: 被替代的旧节点 id
        tree_store: 记忆树存储
        new_node_id: 新叶子节点 id；若调用时尚未创建，可传 None（边会以空 parent 写入）
    """
    try:
        # 1. 标记旧节点 inactive
        await tree_store.update_node(old_node_id, {"isActive": False})

        # 2. 插入 supersedes 边
        parent_id = new_node_id or ""
        _insert_edge(tree_store, parent_id, old_node_id, "supersedes")

        _log.info(
            "已替代旧叶子记忆 old_id=%s new_id=%s",
            old_node_id, new_node_id or "(pending)",
        )
    except Exception as e:  # noqa: BLE001
        _log.warning("supersede_leaf 失败 (old=%s, new=%s): %s",
                     old_node_id, new_node_id, e)


# ------------------------------------------------------------------
# 3. 处理冲突
# ------------------------------------------------------------------


async def handle_conflict(
    new_leaf: ExtractedLeafMemory,
    conflict_node_id: str,
    tree_store: MemoryTreeStore,
) -> None:
    """处理冲突记忆：降低旧记忆 confidence，插入 conflicts_with 边。

    流程：
    1. 获取旧节点，降低其 confidence: max(0.1, old_confidence - 0.2)
    2. tree_store.update_node(conflict_node_id, {"confidence": lowered})
    3. 插入 conflicts_with 边（parent_id 用 new_leaf.target_node_id 或空字符串，
       child_id 用 conflict_node_id）
    4. 记日志
    """
    try:
        old_node = await tree_store.get_node(conflict_node_id)
        if not old_node:
            _log.warning("handle_conflict: 旧节点不存在 id=%s", conflict_node_id)
            return

        old_confidence = float(old_node.get("confidence") or 0.7)
        lowered = max(0.1, old_confidence - 0.2)

        # 2. 降低 confidence
        await tree_store.update_node(conflict_node_id, {"confidence": lowered})

        # 3. 插入 conflicts_with 边
        parent_id = new_leaf.target_node_id or ""
        _insert_edge(tree_store, parent_id, conflict_node_id, "conflicts_with")

        _log.info(
            "已标记冲突记忆 conflict_id=%s new_target=%s confidence %.3f -> %.3f",
            conflict_node_id, parent_id or "(pending)", old_confidence, lowered,
        )
    except Exception as e:  # noqa: BLE001
        _log.warning("handle_conflict 失败 (conflict=%s): %s", conflict_node_id, e)


# ------------------------------------------------------------------
# 4. 查找重复叶子
# ------------------------------------------------------------------


async def find_duplicate_leaf(
    leaf: ExtractedLeafMemory,
    tree_store: MemoryTreeStore,
    settings: ModelSettings,
    candidate_parent_id: str,
) -> str | None:
    """在指定父节点下查找与新叶子重复的已有叶子。

    流程：
    1. 获取 candidate_parent_id 下的所有 leaf 子节点
    2. 用 leaf.full_content 生成 embedding
    3. 在候选 leaf 向量中搜索
    4. 相似度 >= DEDUPE_SIMILARITY 返回重复节点 id
    5. embedding 不可用时降级为 FTS
    """
    try:
        candidates = await tree_store.get_children(candidate_parent_id)
        leaf_candidates = [
            c for c in candidates if c["nodeType"] == "leaf"
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
                        "find_duplicate_leaf: 命中重复 leaf=%s (similarity=%.3f)",
                        node_id, similarity,
                    )
                    return node_id
        except Exception as e:  # noqa: BLE001
            _log.warning(
                "find_duplicate_leaf: 向量搜索失败，降级到 FTS: %s", e
            )
            # FTS 降级：在候选 leaf 中取关键词匹配的 top 结果
            fts_results = await tree_store.fts_search(
                query_text, node_type="leaf", limit=10
            )
            cand_set = set(candidate_ids)
            for node_id, _score in fts_results:
                if node_id in cand_set:
                    _log.info(
                        "find_duplicate_leaf: FTS 降级命中重复 leaf=%s", node_id
                    )
                    return node_id

        return None
    except Exception as e:  # noqa: BLE001
        _log.warning("find_duplicate_leaf 失败: %s", e)
        return None


# ------------------------------------------------------------------
# 5. 查找可合并的相似节点对
# ------------------------------------------------------------------


async def find_similar_nodes_for_merge(
    tree_store: MemoryTreeStore,
    settings: ModelSettings,
    node_type: str = "cluster",
    user_id: str = DEFAULT_USER_ID,
) -> list[tuple[str, str, float]]:
    """查找可合并的相似节点对（topic 或 cluster）。

    流程：
    1. list_nodes(user_id, node_type, limit=500)
    2. 对每个节点，用其 summary 生成 embedding
    3. 在同类型节点向量中搜索相似节点
    4. 相似度 >= NODE_MERGE_SIMILARITY 的节点对加入结果
    5. 返回 [(node_id_a, node_id_b, similarity), ...]
    6. embedding 不可用时返回空列表

    为避免重复对，仅保留 node_id_a < node_id_b 的组合。
    """
    try:
        nodes = await tree_store.list_nodes(
            user_id=user_id, node_type=node_type, limit=500
        )
        if len(nodes) < 2:
            return []

        candidate_ids = [n["id"] for n in nodes]
        results: list[tuple[str, str, float]] = []
        seen_pairs: set[tuple[str, str]] = set()

        for node in nodes:
            node_id = node["id"]
            # 用 summary 生成 embedding；summary 为空则跳过
            summary = (node.get("summary") or "").strip()
            if not summary:
                # 退而求其次：用 build_node_embedding_text 构造文本
                emb_text = build_node_embedding_text(node)
                if not emb_text.strip():
                    continue
            else:
                emb_text = summary

            try:
                query_emb = await get_embedding(emb_text, settings)
            except Exception as e:  # noqa: BLE001
                _log.warning(
                    "find_similar_nodes_for_merge: embedding 不可用，终止搜索: %s", e
                )
                return []

            try:
                hits = await tree_store.search_node_vectors(
                    query_emb, top_k=8, candidate_ids=candidate_ids
                )
            except Exception as e:  # noqa: BLE001
                _log.warning(
                    "find_similar_nodes_for_merge: 节点向量搜索失败 (id=%s): %s",
                    node_id, e,
                )
                continue

            for other_id, distance in hits:
                if other_id == node_id:
                    continue
                similarity = _distance_to_similarity(distance)
                if similarity < NODE_MERGE_SIMILARITY:
                    continue
                # 规范化节点对顺序，避免重复
                pair = (node_id, other_id) if node_id < other_id else (other_id, node_id)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                results.append((pair[0], pair[1], similarity))

        # 按相似度降序
        results.sort(key=lambda x: x[2], reverse=True)
        _log.info(
            "find_similar_nodes_for_merge: node_type=%s 候选=%d 合并对=%d",
            node_type, len(nodes), len(results),
        )
        return results
    except Exception as e:  # noqa: BLE001
        _log.warning("find_similar_nodes_for_merge 失败: %s", e)
        return []
