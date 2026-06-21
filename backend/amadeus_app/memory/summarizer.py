"""记忆树摘要维护模块。

当叶子记忆新增/更新后，沿父链向上更新 cluster/topic/domain 节点的 summary，
并重建 node 向量。同时提供定期整理树结构的入口（MVP 阶段仅检测与记日志）。
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..domain import ModelProviderPreset, ModelSettings
from ..logging_config import get_logger
from ..model_adapter import build_chat_payload
from ..storage import DEFAULT_USER_ID
from .embedding import build_node_embedding_text, get_embedding
from .tree_store import MemoryTreeStore

_log = get_logger(__name__)

# 过大 cluster 的叶子数阈值（可通过环境变量覆盖）
_DEFAULT_MAX_CLUSTER_LEAVES = 40


def _max_cluster_leaves() -> int:
    """从环境变量读取 cluster 最大叶子数阈值。"""
    raw = os.getenv("AMADEUS_MEMORY_MAX_CLUSTER_LEAVES", "").strip()
    if not raw:
        return _DEFAULT_MAX_CLUSTER_LEAVES
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_MAX_CLUSTER_LEAVES


def _format_children_info(children: list[dict[str, Any]]) -> str:
    """将子节点列表格式化为 prompt 所需的素材文本。

    每行格式: "- [类型] 标签: 摘要"，若有关键词则追加。
    """
    lines: list[str] = []
    for child in children:
        node_type = child.get("nodeType", "unknown")
        label = child.get("label", "") or ""
        summary = child.get("summary", "") or ""
        keywords = child.get("keywords") or []
        if isinstance(keywords, str):
            try:
                import json as _json
                keywords = _json.loads(keywords) if keywords else []
            except (ValueError, TypeError):
                keywords = []
        line = f"- [{node_type}] {label}: {summary}".rstrip()
        if keywords:
            line += f" (关键词: {', '.join(keywords)})"
        lines.append(line)
    return "\n".join(lines)


def _build_summary_prompt(node: dict[str, Any], children_info: str) -> str:
    """构造节点摘要生成的 prompt。"""
    return (
        "你是一个记忆树摘要助手。请根据以下子节点信息，为当前节点生成一段简洁的摘要（50-150字）。\n\n"
        f"当前节点路径: {node.get('path', '')}\n"
        f"当前节点标签: {node.get('label', '')}\n"
        f"当前节点类型: {node.get('nodeType', '')}\n\n"
        "子节点信息:\n"
        f"{children_info}\n\n"
        "请回答：\n"
        "- 这个子树包含什么主题？\n"
        "- 有哪些关键事实/决策？\n"
        "- 什么时候应该进入这棵子树检索？\n\n"
        "只返回摘要文本，不要附加解释。"
    )


async def _generate_summary_via_llm(
    node: dict[str, Any],
    children: list[dict[str, Any]],
    settings: ModelSettings,
    provider: ModelProviderPreset,
) -> str | None:
    """调用 LLM 生成节点摘要，失败返回 None。"""
    children_info = _format_children_info(children)
    if not children_info.strip():
        _log.debug("节点无子节点素材，跳过摘要生成: %s", node.get("id"))
        return None

    prompt = _build_summary_prompt(node, children_info)
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": prompt},
    ]

    endpoint = f"{settings.base_url.rstrip('/')}/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    payload = build_chat_payload(settings, provider, "fast", messages, stream=False)

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
    except httpx.HTTPStatusError as error:
        _log.error(
            "节点摘要 LLM 请求失败 (HTTP %s): node=%s err=%s",
            getattr(error.response, "status_code", "?"),
            node.get("id"),
            error,
        )
        return None
    except httpx.RequestError as error:
        _log.error("节点摘要 LLM 请求异常: node=%s err=%s", node.get("id"), error)
        return None
    except Exception as error:
        _log.error("节点摘要 LLM 未知错误: node=%s err=%s", node.get("id"), error)
        return None

    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        _log.error("节点摘要 LLM 响应格式异常: node=%s", node.get("id"))
        return None

    if not content or not content.strip():
        _log.debug("节点摘要 LLM 返回空内容: node=%s", node.get("id"))
        return None

    return content.strip()


async def update_ancestor_summaries(
    leaf_node_id: str,
    tree_store: MemoryTreeStore,
    settings: ModelSettings,
    provider: ModelProviderPreset,
) -> None:
    """沿 parent_id 链从 leaf 的父节点向上更新非叶子节点的 summary 与向量。

    遍历 cluster → topic → domain，处理到 domain 节点后停止（不继续到 root）。
    任何步骤失败只记日志，不抛异常。
    """
    try:
        leaf = await tree_store.get_node(leaf_node_id)
    except Exception:
        _log.exception("获取叶子节点失败: %s", leaf_node_id)
        return

    if not leaf:
        _log.warning("叶子节点不存在，跳过摘要更新: %s", leaf_node_id)
        return

    parent_id = leaf.get("parentId")
    if not parent_id:
        _log.debug("叶子节点无父节点，跳过摘要更新: %s", leaf_node_id)
        return

    current_id: str | None = parent_id
    while current_id:
        try:
            node = await tree_store.get_node(current_id)
        except Exception:
            _log.exception("获取节点失败: %s", current_id)
            break

        if not node:
            _log.warning("父链节点不存在，停止向上遍历: %s", current_id)
            break

        node_type = node.get("nodeType", "")
        if node_type == "root":
            # 不处理 root
            break

        # 获取子节点素材
        try:
            children = await tree_store.get_children(current_id, active_only=True)
        except Exception:
            _log.exception("获取子节点失败: %s", current_id)
            children = []

        if not children:
            _log.debug("节点无活跃子节点，跳过摘要更新: %s", current_id)
            # 继续向上，但无素材无法生成摘要
            current_id = node.get("parentId")
            if node_type == "domain":
                break
            continue

        # 调用 LLM 生成新摘要
        new_summary: str | None = None
        try:
            new_summary = await _generate_summary_via_llm(
                node, children, settings, provider
            )
        except Exception:
            _log.exception("生成节点摘要异常: node=%s", current_id)

        if new_summary:
            # 写回 summary
            try:
                updated = await tree_store.update_node(
                    current_id, {"summary": new_summary}
                )
            except Exception:
                _log.exception("更新节点 summary 失败: %s", current_id)
                updated = None

            # 重建 node 向量
            if updated is not None:
                try:
                    emb = await get_embedding(
                        build_node_embedding_text(updated), settings
                    )
                    await tree_store.upsert_node_vector(current_id, emb)
                except Exception:
                    _log.exception(
                        "重建节点向量失败，跳过向量更新: %s", current_id
                    )
            else:
                # update_node 失败，用原 node 文本尝试重建向量也无意义，跳过
                _log.debug(
                    "update_node 返回 None，跳过向量重建: %s", current_id
                )
        else:
            _log.info(
                "节点摘要生成失败或为空，保留旧 summary: %s", current_id
            )

        # 到达 domain 节点，处理完后停止
        if node_type == "domain":
            break

        current_id = node.get("parentId")

    _log.info("祖先摘要更新完成: leaf=%s", leaf_node_id)


async def merge_or_split_nodes(
    tree_store: MemoryTreeStore,
    settings: ModelSettings,
    provider: ModelProviderPreset,
    user_id: str = DEFAULT_USER_ID,
) -> None:
    """定期整理树结构（MVP 阶段仅检测与记日志）。

    1. 过大 cluster 拆分检测：leaf_count 超过阈值的 cluster 节点记日志提示。
    2. 空节点清理：无子节点且非 leaf 的 topic/cluster 节点记日志提示可清理。
    """
    max_leaves = _max_cluster_leaves()

    # 1. 过大 cluster 检测
    try:
        clusters = await tree_store.list_nodes(
            user_id=user_id, node_type="cluster", active_only=True, limit=500
        )
    except Exception:
        _log.exception("列出 cluster 节点失败")
        clusters = []

    oversized = 0
    for cluster in clusters:
        leaf_count = int(cluster.get("leafCount", 0) or 0)
        if leaf_count > max_leaves:
            oversized += 1
            _log.warning(
                "检测到过大 cluster（leaf_count=%d > %d），建议拆分: id=%s label=%s path=%s",
                leaf_count,
                max_leaves,
                cluster.get("id"),
                cluster.get("label"),
                cluster.get("path"),
            )
    if oversized:
        _log.info("过大 cluster 检测完成: 共 %d 个需拆分（MVP 阶段仅标记）", oversized)

    # 2. 空节点清理检测（topic / cluster 无子节点）
    empty_nodes = 0
    for nt in ("topic", "cluster"):
        try:
            nodes = await tree_store.list_nodes(
                user_id=user_id, node_type=nt, active_only=True, limit=500
            )
        except Exception:
            _log.exception("列出 %s 节点失败", nt)
            continue

        for node in nodes:
            node_id = node.get("id")
            if not node_id:
                continue
            try:
                children = await tree_store.get_children(node_id, active_only=True)
            except Exception:
                _log.exception("获取子节点失败: %s", node_id)
                continue
            if not children:
                empty_nodes += 1
                _log.info(
                    "检测到空 %s 节点（无子节点），可清理: id=%s label=%s path=%s",
                    nt,
                    node_id,
                    node.get("label"),
                    node.get("path"),
                )

    if empty_nodes:
        _log.info("空节点检测完成: 共 %d 个可清理（MVP 阶段仅标记）", empty_nodes)

    _log.info(
        "merge_or_split_nodes 完成: oversized_clusters=%d empty_nodes=%d",
        oversized,
        empty_nodes,
    )
