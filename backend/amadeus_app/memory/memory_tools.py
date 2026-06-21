"""记忆系统 LLM 工具注册模块。

将 recall_memory、save_memory、browse_memory_tree 注册为 LLM function calling 工具，
供 Agent 通过 tool_registry 调用记忆树的检索、保存与浏览能力。
"""

from __future__ import annotations

from typing import Any

from .._common import get_memory_tree, get_saved_settings_payload
from ..agent_registry import ToolDefinition, tool_registry
from ..domain import ModelSettings
from ..logging_config import get_logger
from ..storage import DEFAULT_USER_ID
from .models import ExtractedLeafMemory, MemoryCategory, MemoryDomain
from .router import route_and_attach_leaf

_log = get_logger(__name__)


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------


def _resolve_model_settings(payload: dict[str, Any]) -> ModelSettings:
    """从存储的设置 payload 中解析 ModelSettings，失败时回退到默认值。"""
    model_dict = payload.get("model") or {}
    try:
        return ModelSettings(**model_dict)
    except Exception:
        _log.debug("解析 ModelSettings 失败，使用默认值")
        return ModelSettings()


def _safe_domain(value: Any) -> MemoryDomain:
    """安全地将字符串转换为 MemoryDomain 枚举，无效值回退到 DAILY_CHAT。"""
    if isinstance(value, MemoryDomain):
        return value
    try:
        return MemoryDomain(str(value))
    except ValueError:
        _log.warning("无效的 domain 值: %s，回退到 daily_chat", value)
        return MemoryDomain.DAILY_CHAT


def _safe_category(value: Any) -> MemoryCategory:
    """安全地将字符串转换为 MemoryCategory 枚举，无效值回退到 FACT。"""
    if isinstance(value, MemoryCategory):
        return value
    if not value:
        return MemoryCategory.FACT
    try:
        return MemoryCategory(str(value))
    except ValueError:
        _log.warning("无效的 category 值: %s，回退到 fact", value)
        return MemoryCategory.FACT


def _node_to_dict(node: Any) -> dict[str, Any]:
    """将 MemoryNode 或 dict 统一转换为 dict。"""
    if isinstance(node, dict):
        return node
    if hasattr(node, "model_dump"):
        return node.model_dump()
    if hasattr(node, "dict"):
        return node.dict()
    return {"value": str(node)}


# ------------------------------------------------------------------
# 工具 handler
# ------------------------------------------------------------------


async def handle_recall_memory(args: dict[str, Any]) -> dict[str, Any]:
    """检索长期记忆树。"""
    try:
        tree_store = get_memory_tree()
        if not tree_store:
            return {"ok": False, "summary": "错误: 记忆树未初始化", "data": {}}

        query = str(args.get("query") or "").strip()
        if not query:
            return {"ok": False, "summary": "错误: 缺少 query 参数", "data": {}}

        domains_raw = args.get("domains") or []
        # search_memory_tree 接受 list[str]，这里保留原始字符串
        domains = [str(d) for d in domains_raw if d]
        path_hint = str(args.get("path") or "").strip()
        _ = path_hint  # 当前 search_memory_tree 暂未支持 path 过滤，保留以备扩展

        settings_payload = await get_saved_settings_payload()
        settings = _resolve_model_settings(settings_payload)

        # 延迟导入：tree_search 模块可能尚未创建
        from .tree_search import search_memory_tree

        result = await search_memory_tree(
            query=query,
            tree_store=tree_store,
            settings=settings,
            domains=domains or None,
        )

        # 将检索结果转换为工具返回格式（兼容 Pydantic 模型与 dict）
        paths: list[list[str]] = []
        topics: list[dict[str, Any]] = []
        leaves: list[dict[str, Any]] = []

        selected_paths = getattr(result, "selected_paths", None)
        if selected_paths is None and isinstance(result, dict):
            selected_paths = result.get("selected_paths", [])
        if selected_paths:
            for path_nodes in selected_paths:
                paths.append([_node_to_dict(n).get("path", "") for n in path_nodes])

        topic_nodes = getattr(result, "topic_nodes", None)
        if topic_nodes is None and isinstance(result, dict):
            topic_nodes = result.get("topic_nodes", [])
        if topic_nodes:
            topics = [_node_to_dict(n) for n in topic_nodes]

        leaf_nodes = getattr(result, "leaf_nodes", None)
        if leaf_nodes is None and isinstance(result, dict):
            leaf_nodes = result.get("leaf_nodes", [])
        if leaf_nodes:
            leaves = [_node_to_dict(n) for n in leaf_nodes]

        summary = f"检索到 {len(topics)} 个主题节点、{len(leaves)} 个叶子记忆。"
        return {
            "ok": True,
            "summary": summary,
            "data": {
                "paths": paths,
                "topics": topics,
                "leaves": leaves,
            },
        }
    except Exception as error:
        _log.exception("recall_memory 执行失败")
        return {"ok": False, "summary": f"错误: {error}", "data": {}}


async def handle_save_memory(args: dict[str, Any]) -> dict[str, Any]:
    """保存一条叶子记忆。"""
    try:
        tree_store = get_memory_tree()
        if not tree_store:
            return {"ok": False, "summary": "错误: 记忆树未初始化", "data": {}}

        title = str(args.get("title") or "").strip()
        domain_raw = str(args.get("domain") or "").strip()
        summary = str(args.get("summary") or "").strip()
        full_content = str(args.get("full_content") or "").strip()

        if not title or not domain_raw or not summary or not full_content:
            return {
                "ok": False,
                "summary": "错误: 缺少必要参数 (title/domain/summary/full_content)",
                "data": {},
            }

        keywords = args.get("keywords") or []
        if not isinstance(keywords, list):
            keywords = [str(keywords)]
        keywords = [str(k) for k in keywords if k]

        importance = args.get("importance")
        try:
            importance = float(importance) if importance is not None else 0.5
        except (TypeError, ValueError):
            importance = 0.5

        leaf = ExtractedLeafMemory(
            action="create_leaf",
            domain=_safe_domain(domain_raw),
            title=title,
            summary=summary,
            full_content=full_content,
            category=_safe_category(args.get("category")),
            keywords=keywords,
            importance=importance,
        )

        settings_payload = await get_saved_settings_payload()
        settings = _resolve_model_settings(settings_payload)

        created = await route_and_attach_leaf(
            leaf=leaf,
            tree_store=tree_store,
            settings=settings,
            user_id=DEFAULT_USER_ID,
        )

        node_id = created.get("id", "") if isinstance(created, dict) else ""
        return {
            "ok": True,
            "summary": f"已保存记忆: {title}",
            "data": {"nodeId": node_id},
        }
    except Exception as error:
        _log.exception("save_memory 执行失败")
        return {"ok": False, "summary": f"错误: {error}", "data": {}}


async def handle_browse_memory_tree(args: dict[str, Any]) -> dict[str, Any]:
    """浏览记忆树结构。"""
    try:
        tree_store = get_memory_tree()
        if not tree_store:
            return {"ok": False, "summary": "错误: 记忆树未初始化", "data": {}}

        path = str(args.get("path") or "/").strip() or "/"
        try:
            depth = int(args.get("depth") or 2)
        except (TypeError, ValueError):
            depth = 2  # noqa: F841 - 保留用于后续深度限制扩展

        if path == "/":
            # 列出所有 domain 节点
            nodes = await tree_store.list_nodes(node_type="domain")
        else:
            # 查找指定 path 的节点，再获取其子节点
            all_nodes = await tree_store.list_nodes(limit=500)
            target = None
            for node in all_nodes:
                if node.get("path") == path:
                    target = node
                    break
            if not target:
                return {"ok": False, "summary": f"未找到路径: {path}", "data": {}}
            nodes = await tree_store.get_children(target["id"])

        return {
            "ok": True,
            "summary": f"浏览记忆树 {path}，找到 {len(nodes)} 个节点。",
            "data": {"nodes": nodes},
        }
    except Exception as error:
        _log.exception("browse_memory_tree 执行失败")
        return {"ok": False, "summary": f"错误: {error}", "data": {}}


# ------------------------------------------------------------------
# 工具注册
# ------------------------------------------------------------------


def register_memory_tools() -> None:
    """将记忆系统的 3 个工具注册到 tool_registry。"""

    tool_registry.register(
        ToolDefinition(
            name="recall_memory",
            description="检索长期记忆树。当你需要回忆之前的对话内容、用户偏好、项目细节时调用。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索查询词或自然语言问题"},
                    "domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "限定检索的 domain 列表，如 daily_chat/task/code/environment/creative/knowledge",
                    },
                    "path": {
                        "type": "string",
                        "description": "限定检索的树路径前缀，如 /daily_chat",
                    },
                },
                "required": ["query"],
            },
            handler=handle_recall_memory,
            permission="safe",
        )
    )

    tool_registry.register(
        ToolDefinition(
            name="save_memory",
            description="保存一条叶子记忆。当你认为当前对话中有值得长期记住的信息时调用。",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "记忆标题（简短）"},
                    "domain": {
                        "type": "string",
                        "description": "所属 domain: daily_chat/task/code/environment/creative/knowledge",
                    },
                    "summary": {"type": "string", "description": "记忆摘要（一句话）"},
                    "full_content": {"type": "string", "description": "记忆完整内容"},
                    "category": {
                        "type": "string",
                        "description": "记忆类别: general/fact/decision/preference/summary/technical/task_state/tool_result",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "关键词列表，便于后续检索",
                    },
                    "importance": {
                        "type": "number",
                        "description": "重要度 0.0-1.0，默认 0.5",
                    },
                },
                "required": ["title", "domain", "summary", "full_content"],
            },
            handler=handle_save_memory,
            permission="safe",
        )
    )

    tool_registry.register(
        ToolDefinition(
            name="browse_memory_tree",
            description="浏览记忆树结构。当你需要知道有哪些大类、主题或项目记忆时调用。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "树路径，默认 / 列出所有 domain",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "展开深度，默认 2",
                    },
                },
            },
            handler=handle_browse_memory_tree,
            permission="safe",
        )
    )
