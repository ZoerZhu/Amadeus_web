"""记忆系统 REST API 端点。

提供记忆树、节点 CRUD、树型检索、手动导入，
以及项目（Project）与 会话-项目关联的管理接口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .._common import get_memory_tree, get_saved_settings_payload, require_storage
from ..domain import ModelSettings
from ..logging_config import get_logger
from ..memory.models import (
    MemoryLeafCreateRequest,
    MemoryTreeSearchRequest,
    ProjectCreateRequest,
)
from ..memory.tree_search import search_memory_tree
from ..memory.tree_store import MemoryTreeStore
from ..storage import DEFAULT_USER_ID

_log = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["memory"])


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------


def _require_tree() -> MemoryTreeStore:
    """获取全局 MemoryTreeStore，未初始化时抛 503。"""
    tree = get_memory_tree()
    if tree is None:
        raise HTTPException(status_code=503, detail="记忆树未初始化，请检查存储连接。")
    return tree


async def _load_model_settings() -> ModelSettings:
    """从存储加载用户模型设置，失败时返回默认值。"""
    payload = await get_saved_settings_payload()
    model_dict = payload.get("model") or {}
    try:
        return ModelSettings(**model_dict)
    except Exception:
        _log.exception("解析 ModelSettings 失败，使用默认值")
        return ModelSettings()


def _build_tree(nodes: list[dict[str, Any]], root_path: str, max_depth: int) -> list[dict[str, Any]]:
    """根据扁平节点列表构建嵌套树结构。

    以 path 等于 root_path 的节点为根，向下递归到 max_depth 层。
    """
    # 按 parentId 索引子节点
    children_map: dict[str | None, list[dict[str, Any]]] = {}
    for node in nodes:
        parent_id = node.get("parentId")
        children_map.setdefault(parent_id, []).append(node)

    # 找到根节点：path 匹配 root_path 的节点
    root_nodes = [n for n in nodes if n.get("path", "") == root_path]
    if not root_nodes:
        # 退化到 node_type == 'root' 的节点
        root_nodes = [n for n in nodes if n.get("nodeType") == "root"]
    if not root_nodes:
        return []

    def _attach(node: dict[str, Any], current_depth: int) -> dict[str, Any]:
        """递归挂载 children，直到达到 max_depth。"""
        result = dict(node)
        if current_depth >= max_depth:
            result["children"] = []
            return result
        kids = children_map.get(node["id"], [])
        result["children"] = [_attach(k, current_depth + 1) for k in kids]
        return result

    return [_attach(rn, 0) for rn in root_nodes]


# ------------------------------------------------------------------
# 记忆树与节点
# ------------------------------------------------------------------


@router.get("/memory/tree")
async def get_tree(path: str = "/", depth: int = 2) -> dict:
    """获取记忆树（按 path 与 depth 过滤）。"""
    tree_store = _require_tree()
    try:
        all_nodes = await tree_store.list_nodes(user_id=DEFAULT_USER_ID, active_only=False, limit=2000)
        tree = _build_tree(all_nodes, path, depth)
        return {"tree": tree, "nodes": all_nodes}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("获取记忆树失败")
        raise HTTPException(status_code=500, detail=f"获取记忆树失败: {e}") from e


@router.get("/memory/nodes/{node_id}")
async def get_node(node_id: str) -> dict:
    """获取节点详情。"""
    tree_store = _require_tree()
    try:
        node = await tree_store.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail="节点不存在")
        return {"node": node}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("获取节点失败: node_id=%s", node_id)
        raise HTTPException(status_code=500, detail=f"获取节点失败: {e}") from e


@router.post("/memory/nodes")
async def create_node(request: MemoryLeafCreateRequest) -> dict:
    """创建节点（默认 leaf，可通过 nodeType 指定 topic/cluster）。"""
    tree_store = _require_tree()
    try:
        data: dict[str, Any] = {
            "nodeType": request.node_type.value,
            "parentId": request.parent_id,
            "domain": request.domain.value if hasattr(request.domain, "value") else str(request.domain),
            "label": request.label,
            "summary": request.summary,
            "fullContent": request.full_content,
            "category": request.category.value if hasattr(request.category, "value") else str(request.category),
            "keywords": list(request.keywords),
            "projectId": request.project_id,
            "conversationId": request.conversation_id,
            "importance": request.importance,
            "confidence": request.confidence,
        }
        node = await tree_store.create_node(data)
        return {"node": node}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("创建节点失败")
        raise HTTPException(status_code=500, detail=f"创建节点失败: {e}") from e


@router.put("/memory/nodes/{node_id}")
async def update_node(node_id: str, updates: dict) -> dict:
    """更新节点（部分字段）。"""
    tree_store = _require_tree()
    try:
        existing = await tree_store.get_node(node_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="节点不存在")
        updated = await tree_store.update_node(node_id, updates)
        if updated is None:
            raise HTTPException(status_code=404, detail="节点不存在")
        return {"node": updated}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("更新节点失败: node_id=%s", node_id)
        raise HTTPException(status_code=500, detail=f"更新节点失败: {e}") from e


@router.delete("/memory/nodes/{node_id}")
async def delete_node(node_id: str) -> dict:
    """删除/归档节点。"""
    tree_store = _require_tree()
    try:
        existing = await tree_store.get_node(node_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="节点不存在")
        # 优先归档（置为 inactive），若已是 inactive 则真正删除
        if existing.get("isActive"):
            await tree_store.update_node(node_id, {"isActive": False})
            _log.info("节点已归档: %s", node_id)
        else:
            await tree_store.delete_node(node_id)
            _log.info("节点已删除: %s", node_id)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("删除节点失败: node_id=%s", node_id)
        raise HTTPException(status_code=500, detail=f"删除节点失败: {e}") from e


# ------------------------------------------------------------------
# 树型检索
# ------------------------------------------------------------------


@router.post("/memory/search")
async def search_memory(request: MemoryTreeSearchRequest) -> dict:
    """树型检索：根据 query 返回 selectedPaths / topicNodes / leafNodes / contextText。"""
    tree_store = _require_tree()
    try:
        settings = await _load_model_settings()
        # 将 MemoryDomain 枚举转为字符串
        domains: list[str] | None = None
        if request.domains:
            domains = [d.value if hasattr(d, "value") else str(d) for d in request.domains]
        result = await search_memory_tree(
            request.query,
            tree_store,
            settings,
            user_id=DEFAULT_USER_ID,
            project_id=request.project_id,
            domains=domains,
            node_top_k=request.node_top_k,
            leaf_top_k=request.leaf_top_k,
        )

        # 用 build_memory_context 生成真实 contextText
        from ..memory.injector import build_memory_context
        result_dict = result.model_dump() if hasattr(result, "model_dump") else {}
        context_text = build_memory_context(
            selected_paths=result_dict.get("selected_paths", []),
            topic_nodes=result_dict.get("topic_nodes", []),
            leaf_nodes=result_dict.get("leaf_nodes", []),
            budget_chars=request.context_budget_chars,
        )

        # 转为前端友好的 camelCase 字典
        return {
            "result": {
                "selectedPaths": [
                    [n.model_dump() if hasattr(n, "model_dump") else n for n in path]
                    for path in (result.selected_paths or [])
                ],
                "topicNodes": [n.model_dump() if hasattr(n, "model_dump") else n for n in (result.topic_nodes or [])],
                "leafNodes": [n.model_dump() if hasattr(n, "model_dump") else n for n in (result.leaf_nodes or [])],
                "contextText": context_text,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("记忆检索失败: query=%s", request.query)
        raise HTTPException(status_code=500, detail=f"记忆检索失败: {e}") from e


# ------------------------------------------------------------------
# 索引重建
# ------------------------------------------------------------------


class RebuildIndexRequest(BaseModel):
    """索引重建请求体。"""

    model_config = ConfigDict(populate_by_name=True)

    rebuild_fts: bool = Field(default=True, alias="rebuildFts")
    rebuild_vectors: bool = Field(default=True, alias="rebuildVectors")


@router.post("/memory/rebuild-index")
async def rebuild_index(request: RebuildIndexRequest) -> dict:
    """重建记忆索引（FTS5 和/或向量索引）。"""
    tree_store = _require_tree()
    try:
        result: dict[str, Any] = {}

        if request.rebuild_fts:
            fts_count = await tree_store.rebuild_fts_index()
            result["ftsNodes"] = fts_count
            _log.info("FTS 索引重建完成: %d 个节点", fts_count)

        if request.rebuild_vectors:
            settings = await _load_model_settings()
            vec_result = await tree_store.rebuild_vectors(settings)
            result["vectors"] = vec_result
            _log.info("向量索引重建完成: %s", vec_result)

        return {"ok": True, "result": result}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("索引重建失败")
        raise HTTPException(status_code=500, detail=f"索引重建失败: {e}") from e


# ------------------------------------------------------------------
# 手动导入
# ------------------------------------------------------------------


class MemoryIngestRequest(BaseModel):
    """手动导入文本为叶子记忆的请求体。"""

    text: str
    domain: str = "knowledge"
    title: str = ""
    project_id: str | None = None
    conversation_id: str | None = None


@router.post("/memory/ingest")
async def ingest_memory(request: MemoryIngestRequest) -> dict:
    """手动导入文本为叶子记忆，入队 ingest job。"""
    tree_store = _require_tree()
    try:
        payload: dict[str, Any] = {
            "text": request.text,
            "domain": request.domain,
            "title": request.title,
            "projectId": request.project_id,
            "conversationId": request.conversation_id,
        }
        job_id = await tree_store.enqueue_job(
            user_id=DEFAULT_USER_ID,
            job_type="ingest",
            conversation_id=request.conversation_id,
            payload=payload,
        )
        _log.info(
            "已入队 ingest 任务: job=%s domain=%s title=%s",
            job_id,
            request.domain,
            request.title,
        )
        return {"ok": True, "jobId": job_id}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("入队 ingest 任务失败")
        raise HTTPException(status_code=500, detail=f"入队 ingest 任务失败: {e}") from e


# ------------------------------------------------------------------
# 项目（Project）
# ------------------------------------------------------------------


@router.get("/projects")
async def list_projects() -> dict:
    """列出当前用户的所有项目。"""
    tree_store = _require_tree()
    try:
        projects = await tree_store.list_projects(user_id=DEFAULT_USER_ID)
        return {"projects": projects}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("列出项目失败")
        raise HTTPException(status_code=500, detail=f"列出项目失败: {e}") from e


@router.post("/projects")
async def create_project(request: ProjectCreateRequest) -> dict:
    """创建项目。"""
    tree_store = _require_tree()
    try:
        project = await tree_store.create_project(
            user_id=DEFAULT_USER_ID,
            name=request.name,
            description=request.description,
            workspace_path=request.workspace_path,
            color=request.color,
        )
        _log.info("已创建项目: id=%s name=%s", project.get("id"), request.name)
        return {"project": project}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("创建项目失败")
        raise HTTPException(status_code=500, detail=f"创建项目失败: {e}") from e


@router.put("/projects/{project_id}")
async def update_project(project_id: str, updates: dict) -> dict:
    """更新项目（简化为更新名称/描述）。"""
    _require_tree()  # 确保记忆树已初始化，否则返回 503
    try:
        # tree_store 暂未暴露 update_project，直接走 SQL
        storage = require_storage()
        conn = storage.require_connection()
        existing = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="项目不存在")

        set_clauses: list[str] = []
        params: list[Any] = []
        for key, col in (
            ("name", "name"),
            ("description", "description"),
            ("workspacePath", "workspace_path"),
            ("color", "color"),
        ):
            if key in updates:
                set_clauses.append(f"{col} = ?")
                params.append(updates[key])
        if not set_clauses:
            return {"project": dict(existing)}

        from datetime import datetime, timezone

        set_clauses.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
        params.append(project_id)
        conn.execute(
            f"UPDATE projects SET {', '.join(set_clauses)} WHERE id = ?",
            params,
        )
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        _log.info("已更新项目: %s", project_id)
        return {"project": dict(row)}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("更新项目失败: project_id=%s", project_id)
        raise HTTPException(status_code=500, detail=f"更新项目失败: {e}") from e


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str) -> dict:
    """删除项目（同时清理会话关联）。"""
    _require_tree()  # 确保记忆树已初始化，否则返回 503
    try:
        storage = require_storage()
        conn = storage.require_connection()
        existing = conn.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="项目不存在")

        # 清理会话-项目关联
        conn.execute("DELETE FROM conversation_projects WHERE project_id = ?", (project_id,))
        # 删除项目本身
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        _log.info("已删除项目: %s", project_id)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("删除项目失败: project_id=%s", project_id)
        raise HTTPException(status_code=500, detail=f"删除项目失败: {e}") from e


# ------------------------------------------------------------------
# 会话-项目关联
# ------------------------------------------------------------------


class ConversationProjectLinkRequest(BaseModel):
    """会话关联项目的请求体。"""

    projectId: str


@router.post("/conversations/{conversation_id}/project")
async def link_conversation_project(conversation_id: str, request: ConversationProjectLinkRequest) -> dict:
    """关联会话到项目。"""
    tree_store = _require_tree()
    try:
        await tree_store.link_conversation_project(conversation_id, request.projectId)
        _log.info(
            "已关联会话到项目: conversation=%s project=%s",
            conversation_id,
            request.projectId,
        )
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("关联会话到项目失败: conversation=%s", conversation_id)
        raise HTTPException(status_code=500, detail=f"关联会话到项目失败: {e}") from e
