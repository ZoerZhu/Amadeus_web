"""记忆树存储层 — SQLite 事务封装。

负责 memory_nodes / memory_edges / memory_fts 的 CRUD，
以及与 vector_store 的协调。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..logging_config import get_logger
from ..storage import DEFAULT_USER_ID, SQLiteStorage
from . import vector_store

_log = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_json_array(raw: str | None) -> list:
    """安全解析 JSON 数组字符串，失败返回空列表。"""
    if not raw:
        return []
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# FTS5 查询中需要过滤的中文停用字（高频但无区分度的单字）
_FTS_STOP_CHARS: set[str] = {
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "们",
    "那", "这", "个", "么", "怎", "样", "前", "后", "就", "都",
    "也", "还", "又", "把", "被", "让", "给", "到", "和", "与",
    "或", "及", "以", "于", "对", "为", "从", "向", "里", "外",
    "上", "下", "中", "间", "时", "地", "得", "着", "过", "吧",
    "呢", "啊", "哦", "嗯", "哈", "嘛", "呀", "哇",
}


def _build_fts_or_query(query: str) -> str:
    """将用户查询转换为 FTS5 OR 查询，提升中文召回。

    unicode61 分词器会把中文拆成单字，FTS5 默认 AND 逻辑要求文档包含所有字，
    导致"之前那个部署方案怎么样了"无法匹配"关于部署方案的讨论"。
    改为 OR 查询后，只要文档包含查询中的任一字即可匹配，bm25 排序保证相关性。
    """
    import re

    # 拆分为 token：CJK 单字 + Latin/数字单词
    tokens = re.findall(r"[\u4e00-\u9fff]|[a-zA-Z0-9]+", query)

    # 过滤停用字和太短的 Latin token
    filtered: list[str] = []
    for t in tokens:
        if t in _FTS_STOP_CHARS:
            continue
        if len(t) == 1 and t.isascii() and not t.isdigit():
            # 单个英文字母无区分度，跳过
            continue
        filtered.append(t)

    if not filtered:
        # 降级：用原始查询（可能匹配不到，但不会报错）
        return query

    # 用 OR 连接，FTS5 语法：token1 OR token2 OR ...
    return " OR ".join(filtered)


def _space_separate_cjk(text: str) -> str:
    """在 CJK 字符之间插入空格，让 unicode61 分词器把每个汉字作为独立 token。

    FTS5 的 unicode61 分词器会把连续的 CJK 字符视为一个 token，
    导致"关于部署方案的讨论"是一个 token，无法匹配"部署方案"。
    插入空格后，每个汉字成为独立 token，配合 OR 查询可实现单字级召回。
    """
    if not text:
        return ""
    import re
    # 在每个 CJK 字符前后加空格，再压缩多余空格
    result = re.sub(r"([\u4e00-\u9fff])", r" \1 ", text)
    result = re.sub(r"\s+", " ", result).strip()
    return result


def _serialize_node(row: sqlite3.Row) -> dict[str, Any]:
    """将 memory_nodes 行序列化为前端友好的 dict。"""
    return {
        "id": row["id"],
        "parentId": row["parent_id"],
        "nodeType": row["node_type"],
        "domain": row["domain"],
        "label": row["label"],
        "path": row["path"],
        "depth": row["depth"],
        "summary": row["summary"],
        "fullContent": row["full_content"],
        "category": row["category"],
        "keywords": json.loads(row["keywords"]) if row["keywords"] else [],
        "projectId": row["project_id"],
        "conversationId": row["conversation_id"],
        "sourceMessageIds": json.loads(row["source_message_ids"]) if row["source_message_ids"] else [],
        "sourceSummary": row["source_summary"],
        "importance": row["importance"],
        "confidence": row["confidence"],
        "accessCount": row["access_count"],
        "leafCount": row["leaf_count"],
        "lastAccessedAt": row["last_accessed_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "expiresAt": row["expires_at"],
        "isActive": bool(row["is_active"]),
    }


# ---- 默认一级领域 ----

DEFAULT_DOMAINS = [
    ("daily_chat", "日常聊天"),
    ("task", "任务相关"),
    ("code", "代码相关"),
    ("environment", "环境与工具"),
    ("creative", "创意与写作"),
    ("knowledge", "知识库"),
]


class MemoryTreeStore:
    """记忆树存储层，封装所有 SQLite 操作。"""

    def __init__(self, storage: SQLiteStorage) -> None:
        self._storage = storage
        self._lock = asyncio.Lock()

    @property
    def _conn(self) -> sqlite3.Connection:
        return self._storage.require_connection()

    # ================================================================
    # 初始化默认树结构
    # ================================================================

    async def ensure_default_tree(self, user_id: str = DEFAULT_USER_ID) -> None:
        """确保 root + 6 个 domain 节点存在。"""
        async with self._lock:
            await asyncio.to_thread(self._ensure_default_tree_sync, user_id)

    def _ensure_default_tree_sync(self, user_id: str) -> None:
        conn = self._conn
        # root
        root = conn.execute(
            "SELECT id FROM memory_nodes WHERE user_id = ? AND node_type = 'root'",
            (user_id,),
        ).fetchone()
        if not root:
            root_id = str(uuid4())
            conn.execute(
                """INSERT INTO memory_nodes
                   (id, user_id, parent_id, node_type, domain, label, path, depth,
                    summary, full_content, category, keywords, importance, confidence,
                    created_at, updated_at, is_active)
                   VALUES (?, ?, NULL, 'root', '', 'Memory Root', '/', 0,
                           '', '', 'general', '[]', 0.5, 0.7, ?, ?, 1)""",
                (root_id, user_id, _now_iso(), _now_iso()),
            )
        else:
            root_id = root["id"]

        # domains
        for domain_key, domain_label in DEFAULT_DOMAINS:
            existing = conn.execute(
                "SELECT id FROM memory_nodes WHERE user_id = ? AND node_type = 'domain' AND domain = ?",
                (user_id, domain_key),
            ).fetchone()
            if not existing:
                domain_id = str(uuid4())
                path = f"/{domain_key}"
                conn.execute(
                    """INSERT INTO memory_nodes
                       (id, user_id, parent_id, node_type, domain, label, path, depth,
                        summary, full_content, category, keywords, importance, confidence,
                        created_at, updated_at, is_active)
                       VALUES (?, ?, ?, 'domain', ?, ?, ?, 1,
                               '', '', 'general', '[]', 0.5, 0.7, ?, ?, 1)""",
                    (domain_id, user_id, root_id, domain_key, domain_label, path,
                     _now_iso(), _now_iso()),
                )
                conn.execute(
                    """INSERT INTO memory_edges (parent_id, child_id, relation, weight, created_at)
                       VALUES (?, ?, 'contains', 1.0, ?)""",
                    (root_id, domain_id, _now_iso()),
                )

    # ================================================================
    # 节点 CRUD
    # ================================================================

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_node_sync, node_id)

    def _get_node_sync(self, node_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM memory_nodes WHERE id = ?", (node_id,)
        ).fetchone()
        return _serialize_node(row) if row else None

    async def get_children(
        self, parent_id: str, *, active_only: bool = True
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._get_children_sync, parent_id, active_only)

    def _get_children_sync(self, parent_id: str, active_only: bool) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memory_nodes WHERE parent_id = ?"
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY node_type DESC, updated_at DESC"
        rows = self._conn.execute(sql, (parent_id,)).fetchall()
        return [_serialize_node(r) for r in rows]

    async def get_domain_node(
        self, user_id: str, domain: str
    ) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_domain_node_sync, user_id, domain)

    def _get_domain_node_sync(self, user_id: str, domain: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM memory_nodes WHERE user_id = ? AND node_type = 'domain' AND domain = ?",
            (user_id, domain),
        ).fetchone()
        return _serialize_node(row) if row else None

    async def list_nodes(
        self,
        *,
        user_id: str = DEFAULT_USER_ID,
        node_type: str | None = None,
        domain: str | None = None,
        project_id: str | None = None,
        conversation_id: str | None = None,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(
                self._list_nodes_sync,
                user_id, node_type, domain, project_id, conversation_id,
                active_only, limit,
            )

    def _list_nodes_sync(
        self, user_id, node_type, domain, project_id, conversation_id,
        active_only, limit,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memory_nodes WHERE user_id = ?"
        params: list[Any] = [user_id]
        if node_type:
            sql += " AND node_type = ?"
            params.append(node_type)
        if domain:
            sql += " AND domain = ?"
            params.append(domain)
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        if conversation_id:
            sql += " AND conversation_id = ?"
            params.append(conversation_id)
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [_serialize_node(r) for r in rows]

    async def create_node(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建任意类型节点。"""
        async with self._lock:
            return await asyncio.to_thread(self._create_node_sync, data)

    def _create_node_sync(self, data: dict[str, Any]) -> dict[str, Any]:
        conn = self._conn
        node_id = data.get("id") or str(uuid4())
        now = _now_iso()

        parent_id = data.get("parentId")
        node_type = data["nodeType"]
        domain = data.get("domain", "")
        label = data["label"]
        parent_path = ""

        if parent_id:
            parent = conn.execute(
                "SELECT path, depth FROM memory_nodes WHERE id = ?", (parent_id,)
            ).fetchone()
            if parent:
                parent_path = parent["path"]
                depth = parent["depth"] + 1
            else:
                depth = 1
        else:
            depth = 0

        path = data.get("path") or f"{parent_path}/{label}" if parent_path else f"/{label}"
        if not path.startswith("/"):
            path = "/" + path

        keywords = data.get("keywords", [])
        source_msg_ids = data.get("sourceMessageIds", [])

        conn.execute(
            """INSERT INTO memory_nodes
               (id, user_id, parent_id, node_type, domain, label, path, depth,
                summary, full_content, category, keywords, project_id, conversation_id,
                source_message_ids, source_summary, importance, confidence,
                access_count, leaf_count, last_accessed_at,
                created_at, updated_at, expires_at, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, NULL, ?, ?, ?, 1)""",
            (
                node_id,
                data.get("userId", DEFAULT_USER_ID),
                parent_id,
                node_type,
                domain,
                label,
                path,
                depth,
                data.get("summary", ""),
                data.get("fullContent", ""),
                data.get("category", "general"),
                json.dumps(keywords, ensure_ascii=False),
                data.get("projectId"),
                data.get("conversationId"),
                json.dumps(source_msg_ids, ensure_ascii=False),
                data.get("sourceSummary", ""),
                data.get("importance", 0.5),
                data.get("confidence", 0.7),
                now,
                now,
                data.get("expiresAt"),
            ),
        )

        # 父子边
        if parent_id:
            conn.execute(
                """INSERT OR IGNORE INTO memory_edges (parent_id, child_id, relation, weight, created_at)
                   VALUES (?, ?, 'contains', 1.0, ?)""",
                (parent_id, node_id, now),
            )
            # 更新父节点 leaf_count
            if node_type == "leaf":
                conn.execute(
                    "UPDATE memory_nodes SET leaf_count = leaf_count + 1, updated_at = ? WHERE id = ?",
                    (now, parent_id),
                )

        # FTS 索引
        self._upsert_fts_sync(node_id, node_type, path, label,
                               data.get("summary", ""), data.get("fullContent", ""),
                               keywords)

        row = conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()
        return _serialize_node(row)

    async def update_node(self, node_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        async with self._lock:
            return await asyncio.to_thread(self._update_node_sync, node_id, updates)

    def _update_node_sync(self, node_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        conn = self._conn
        row = conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()
        if not row:
            return None

        now = _now_iso()
        set_clauses: list[str] = ["updated_at = ?"]
        params: list[Any] = [now]

        field_map = {
            "label": "label",
            "summary": "summary",
            "fullContent": "full_content",
            "category": "category",
            "importance": "importance",
            "confidence": "confidence",
            "projectId": "project_id",
            "conversationId": "conversation_id",
            "sourceSummary": "source_summary",
            "expiresAt": "expires_at",
            "isActive": "is_active",
        }
        for key, col in field_map.items():
            if key in updates:
                set_clauses.append(f"{col} = ?")
                if key == "isActive":
                    params.append(1 if updates[key] else 0)
                else:
                    params.append(updates[key])

        if "keywords" in updates:
            set_clauses.append("keywords = ?")
            params.append(json.dumps(updates["keywords"], ensure_ascii=False))

        if "sourceMessageIds" in updates:
            set_clauses.append("source_message_ids = ?")
            params.append(json.dumps(updates["sourceMessageIds"], ensure_ascii=False))

        params.append(node_id)
        conn.execute(
            f"UPDATE memory_nodes SET {', '.join(set_clauses)} WHERE id = ?",
            params,
        )

        # 更新 FTS
        updated_row = conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()
        if updated_row:
            self._upsert_fts_sync(
                node_id,
                updated_row["node_type"],
                updated_row["path"],
                updated_row["label"],
                updated_row["summary"],
                updated_row["full_content"],
                json.loads(updated_row["keywords"]) if updated_row["keywords"] else [],
            )

        return _serialize_node(updated_row)

    async def delete_node(self, node_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._delete_node_sync, node_id)

    def _delete_node_sync(self, node_id: str) -> bool:
        conn = self._conn
        row = conn.execute(
            "SELECT node_type, parent_id FROM memory_nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if not row:
            return False

        # 级联删除由外键处理，但向量需手动删
        self._delete_subtree_vectors_sync(node_id)

        conn.execute("DELETE FROM memory_nodes WHERE id = ?", (node_id,))
        conn.execute("DELETE FROM memory_fts WHERE node_id = ?", (node_id,))

        # 更新父节点 leaf_count
        if row["node_type"] == "leaf" and row["parent_id"]:
            conn.execute(
                "UPDATE memory_nodes SET leaf_count = MAX(0, leaf_count - 1), updated_at = ? WHERE id = ?",
                (_now_iso(), row["parent_id"]),
            )
        return True

    def _delete_subtree_vectors_sync(self, node_id: str) -> None:
        conn = self._conn
        # 删除自身向量
        vector_store.delete_node_vector(conn, node_id)
        vector_store.delete_leaf_vector(conn, node_id)
        # 递归删除子节点向量
        children = conn.execute(
            "SELECT id FROM memory_nodes WHERE parent_id = ?", (node_id,)
        ).fetchall()
        for child in children:
            self._delete_subtree_vectors_sync(child["id"])

    # ================================================================
    # FTS 索引
    # ================================================================

    def _upsert_fts_sync(
        self, node_id: str, node_type: str, path: str, label: str,
        summary: str, full_content: str, keywords: list[str],
    ) -> None:
        conn = self._conn
        conn.execute("DELETE FROM memory_fts WHERE node_id = ?", (node_id,))
        # CJK 字符用空格分隔，让 unicode61 分词器把每个汉字作为独立 token
        # 这样 FTS 查询可以用 OR 语法匹配单字，大幅提升中文召回
        conn.execute(
            """INSERT INTO memory_fts (node_id, node_type, path, label, summary, full_content, keywords)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (node_id, node_type, _space_separate_cjk(path), _space_separate_cjk(label),
             _space_separate_cjk(summary), _space_separate_cjk(full_content),
             _space_separate_cjk(" ".join(keywords))),
        )

    async def fts_search(
        self, query: str, *, node_type: str | None = None,
        limit: int = 16,
    ) -> list[tuple[str, float]]:
        """FTS5 关键词检索，返回 [(node_id, rank_score)]。"""
        async with self._lock:
            return await asyncio.to_thread(self._fts_search_sync, query, node_type, limit)

    def _fts_search_sync(
        self, query: str, node_type: str | None, limit: int,
    ) -> list[tuple[str, float]]:
        conn = self._conn
        # FTS5 MATCH 查询，用 bm25 排序
        # 将查询转为 OR 语法，提升中文召回（unicode61 分词器会把中文拆成单字，
        # 默认 AND 逻辑要求文档包含所有字，导致漏召回）
        fts_query = _build_fts_or_query(query)
        sql = """
            SELECT f.node_id, bm25(memory_fts) AS rank
            FROM memory_fts f
            WHERE memory_fts MATCH ?
        """
        params: list[Any] = [fts_query]
        if node_type:
            sql += " AND f.node_type = ?"
            params.append(node_type)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        try:
            rows = conn.execute(sql, params).fetchall()
            # bm25 返回负值，越小越相关，转为正分
            return [(row["node_id"], -row["rank"]) for row in rows]
        except sqlite3.OperationalError:
            return []

    # ================================================================
    # 向量操作
    # ================================================================

    async def upsert_node_vector(self, node_id: str, embedding: list[float]) -> None:
        async with self._lock:
            await asyncio.to_thread(vector_store.upsert_node_vector, self._conn, node_id, embedding)

    async def upsert_leaf_vector(self, node_id: str, embedding: list[float]) -> None:
        async with self._lock:
            await asyncio.to_thread(vector_store.upsert_leaf_vector, self._conn, node_id, embedding)

    async def search_node_vectors(
        self, query_embedding: list[float], top_k: int = 8,
        *, candidate_ids: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        async with self._lock:
            return await asyncio.to_thread(
                vector_store.search_node_vectors, self._conn,
                query_embedding, top_k, candidate_ids=candidate_ids,
            )

    async def search_leaf_vectors(
        self, query_embedding: list[float], top_k: int = 8,
        *, candidate_ids: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        async with self._lock:
            return await asyncio.to_thread(
                vector_store.search_leaf_vectors, self._conn,
                query_embedding, top_k, candidate_ids=candidate_ids,
            )

    # ================================================================
    # 访问统计
    # ================================================================

    async def touch_node(self, node_id: str) -> None:
        """更新节点访问时间和计数。"""
        async with self._lock:
            await asyncio.to_thread(self._touch_node_sync, node_id)

    def _touch_node_sync(self, node_id: str) -> None:
        conn = self._conn
        now = _now_iso()
        conn.execute(
            "UPDATE memory_nodes SET access_count = access_count + 1, last_accessed_at = ? WHERE id = ?",
            (now, node_id),
        )
        # 反向提升父节点
        parent = conn.execute(
            "SELECT parent_id FROM memory_nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if parent and parent["parent_id"]:
            conn.execute(
                "UPDATE memory_nodes SET last_accessed_at = ? WHERE id = ?",
                (now, parent["parent_id"]),
            )

    # ================================================================
    # 会话摘要
    # ================================================================

    async def get_session_summary(self, conversation_id: str) -> str | None:
        """获取指定会话的滚动摘要（category=summary 的叶子节点）。

        返回最新一条摘要的 full_content，无则 None。
        """
        async with self._lock:
            return await asyncio.to_thread(self._get_session_summary_sync, conversation_id)

    def _get_session_summary_sync(self, conversation_id: str) -> str | None:
        row = self._conn.execute(
            """SELECT full_content FROM memory_nodes
               WHERE conversation_id = ? AND category = 'summary' AND is_active = 1
               ORDER BY updated_at DESC LIMIT 1""",
            (conversation_id,),
        ).fetchone()
        return row["full_content"] if row else None

    async def save_session_summary(
        self, conversation_id: str, summary: str, user_id: str = DEFAULT_USER_ID
    ) -> None:
        """保存/更新会话滚动摘要。

        若已存在该会话的 summary 叶子则更新，否则新建（挂到 daily_chat domain 下）。
        """
        async with self._lock:
            await asyncio.to_thread(
                self._save_session_summary_sync, conversation_id, summary, user_id
            )

    def _save_session_summary_sync(
        self, conversation_id: str, summary: str, user_id: str
    ) -> None:
        conn = self._conn
        now = _now_iso()
        # 查找已有 summary 叶子
        existing = conn.execute(
            """SELECT id FROM memory_nodes
               WHERE conversation_id = ? AND category = 'summary' AND is_active = 1
               ORDER BY updated_at DESC LIMIT 1""",
            (conversation_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE memory_nodes SET full_content = ?, summary = ?, updated_at = ? WHERE id = ?",
                (summary, summary[:200], now, existing["id"]),
            )
            return
        # 新建：挂到 daily_chat domain 下
        domain_row = conn.execute(
            "SELECT id, path FROM memory_nodes WHERE user_id = ? AND node_type = 'domain' AND domain = 'daily_chat'",
            (user_id,),
        ).fetchone()
        parent_id = domain_row["id"] if domain_row else None
        parent_path = domain_row["path"] if domain_row else ""
        node_id = str(uuid4())
        path = f"{parent_path}/会话摘要" if parent_path else "/会话摘要"
        conn.execute(
            """INSERT INTO memory_nodes
               (id, user_id, parent_id, node_type, domain, label, path, depth,
                summary, full_content, category, keywords, conversation_id,
                source_message_ids, importance, confidence, is_active,
                created_at, updated_at)
               VALUES (?, ?, ?, 'leaf', 'daily_chat', ?, ?, 1, ?, ?, 'summary', '[]', ?, '[]', 0.6, 0.9, 1, ?, ?)""",
            (node_id, user_id, parent_id, f"会话摘要 {conversation_id[:8]}", path,
             summary[:200], summary, conversation_id, now, now),
        )
        if parent_id:
            conn.execute(
                """INSERT OR IGNORE INTO memory_edges (parent_id, child_id, relation, weight, created_at)
                   VALUES (?, ?, 'contains', 1.0, ?)""",
                (parent_id, node_id, now),
            )
        # FTS 索引
        self._upsert_fts_sync(node_id, "leaf", path, f"会话摘要 {conversation_id[:8]}",
                               summary[:200], summary, [])

    # ================================================================
    # 索引重建
    # ================================================================

    async def rebuild_fts_index(self) -> int:
        """重建 FTS5 索引。返回重新索引的节点数。"""
        async with self._lock:
            return await asyncio.to_thread(self._rebuild_fts_index_sync)

    def _rebuild_fts_index_sync(self) -> int:
        conn = self._conn
        # 清空 FTS 表
        conn.execute("DELETE FROM memory_fts")
        # 重新索引所有活跃节点
        rows = conn.execute(
            """SELECT id, node_type, path, label, summary, full_content, keywords
               FROM memory_nodes WHERE is_active = 1"""
        ).fetchall()
        count = 0
        for row in rows:
            keywords = _parse_json_array(row["keywords"])
            self._upsert_fts_sync(
                row["id"], row["node_type"], row["path"], row["label"],
                row["summary"], row["full_content"], keywords,
            )
            count += 1
        conn.commit()
        return count

    async def rebuild_vectors(
        self, settings: ModelSettings | None, batch_size: int = 50
    ) -> dict[str, int]:
        """重建所有节点和叶子的向量索引。

        Args:
            settings: 模型配置（用于生成 embedding）。为 None 时只清空不重建。
            batch_size: 批量 embedding 的批次大小

        Returns:
            {"nodes": count, "leaves": count}
        """
        from .embedding import (
            build_leaf_embedding_text,
            build_node_embedding_text,
            get_embeddings_batch,
        )

        async with self._lock:
            # 清空向量表
            self._conn.execute("DELETE FROM memory_node_vectors")
            self._conn.execute("DELETE FROM memory_leaf_vectors")
            self._conn.commit()

            if settings is None:
                return {"nodes": 0, "leaves": 0}

            # 重建非叶子节点向量
            node_rows = self._conn.execute(
                """SELECT id, node_type, path, label, summary, keywords, domain
                   FROM memory_nodes WHERE is_active = 1 AND node_type != 'leaf'"""
            ).fetchall()
            node_count = 0
            for i in range(0, len(node_rows), batch_size):
                batch = node_rows[i : i + batch_size]
                texts = [
                    build_node_embedding_text({
                        "path": r["path"], "label": r["label"],
                        "summary": r["summary"],
                        "keywords": _parse_json_array(r["keywords"]),
                    })
                    for r in batch
                ]
                try:
                    embeddings = await get_embeddings_batch(texts, settings)
                    for r, emb in zip(batch, embeddings):
                        vector_store.upsert_node_vector(self._conn, r["id"], emb)
                        node_count += 1
                except Exception as e:
                    _log.warning("批量重建 node 向量失败 (batch %d): %s", i, e)

            # 重建叶子向量
            leaf_rows = self._conn.execute(
                """SELECT id, path, label, summary, full_content, keywords, domain
                   FROM memory_nodes WHERE is_active = 1 AND node_type = 'leaf'"""
            ).fetchall()
            leaf_count = 0
            for i in range(0, len(leaf_rows), batch_size):
                batch = leaf_rows[i : i + batch_size]
                texts = [
                    build_leaf_embedding_text({
                        "path": r["path"], "label": r["label"],
                        "summary": r["summary"],
                        "fullContent": r["full_content"],
                        "keywords": _parse_json_array(r["keywords"]),
                    })
                    for r in batch
                ]
                try:
                    embeddings = await get_embeddings_batch(texts, settings)
                    for r, emb in zip(batch, embeddings):
                        vector_store.upsert_leaf_vector(self._conn, r["id"], emb)
                        leaf_count += 1
                except Exception as e:
                    _log.warning("批量重建 leaf 向量失败 (batch %d): %s", i, e)

            self._conn.commit()
            return {"nodes": node_count, "leaves": leaf_count}

    # ================================================================
    # 项目 CRUD
    # ================================================================

    async def create_project(self, user_id: str, name: str, description: str = "",
                             workspace_path: str = "", color: str = "#8b5cf6") -> dict[str, Any]:
        async with self._lock:
            return await asyncio.to_thread(
                self._create_project_sync, user_id, name, description, workspace_path, color,
            )

    def _create_project_sync(self, user_id, name, description, workspace_path, color) -> dict[str, Any]:
        conn = self._conn
        project_id = str(uuid4())
        now = _now_iso()
        conn.execute(
            """INSERT INTO projects (id, user_id, name, description, workspace_path, color, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, user_id, name, description, workspace_path, color, now, now),
        )
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return dict(row)

    async def list_projects(self, user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._list_projects_sync, user_id)

    def _list_projects_sync(self, user_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM projects WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    async def link_conversation_project(self, conversation_id: str, project_id: str) -> None:
        async with self._lock:
            await asyncio.to_thread(self._link_conversation_project_sync, conversation_id, project_id)

    def _link_conversation_project_sync(self, conversation_id: str, project_id: str) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO conversation_projects (conversation_id, project_id)
               VALUES (?, ?)""",
            (conversation_id, project_id),
        )

    async def get_conversation_project(self, conversation_id: str) -> str | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_conversation_project_sync, conversation_id)

    def _get_conversation_project_sync(self, conversation_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT project_id FROM conversation_projects WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return row["project_id"] if row else None

    # ================================================================
    # memory_jobs
    # ================================================================

    async def enqueue_job(
        self, user_id: str, job_type: str, conversation_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        async with self._lock:
            return await asyncio.to_thread(
                self._enqueue_job_sync, user_id, job_type, conversation_id, payload,
            )

    def _enqueue_job_sync(self, user_id, job_type, conversation_id, payload) -> str:
        conn = self._conn
        job_id = str(uuid4())
        now = _now_iso()
        conn.execute(
            """INSERT INTO memory_jobs (id, user_id, conversation_id, job_type, status, payload, attempts, last_error, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending', ?, 0, '', ?, ?)""",
            (job_id, user_id, conversation_id, job_type,
             json.dumps(payload or {}, ensure_ascii=False), now, now),
        )
        return job_id

    async def claim_pending_jobs(self, limit: int = 5) -> list[dict[str, Any]]:
        """拉取 pending 任务并标记为 running。"""
        async with self._lock:
            return await asyncio.to_thread(self._claim_pending_jobs_sync, limit)

    def _claim_pending_jobs_sync(self, limit: int) -> list[dict[str, Any]]:
        conn = self._conn
        rows = conn.execute(
            "SELECT * FROM memory_jobs WHERE status = 'pending' ORDER BY updated_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        now = _now_iso()
        result = []
        for row in rows:
            conn.execute(
                "UPDATE memory_jobs SET status = 'running', updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            result.append(dict(row))
        return result

    async def complete_job(self, job_id: str, error: str = "") -> None:
        async with self._lock:
            await asyncio.to_thread(self._complete_job_sync, job_id, error)

    def _complete_job_sync(self, job_id: str, error: str) -> None:
        conn = self._conn
        now = _now_iso()
        if error:
            conn.execute(
                """UPDATE memory_jobs
                   SET status = 'failed', attempts = attempts + 1, last_error = ?, updated_at = ?
                   WHERE id = ?""",
                (error[:2000], now, job_id),
            )
        else:
            conn.execute(
                "UPDATE memory_jobs SET status = 'done', updated_at = ? WHERE id = ?",
                (now, job_id),
            )

    async def requeue_job(self, job_id: str, error: str = "") -> None:
        """将失败任务重新入队（status=pending）以便后续重试。"""
        async with self._lock:
            await asyncio.to_thread(self._requeue_job_sync, job_id, error)

    def _requeue_job_sync(self, job_id: str, error: str) -> None:
        conn = self._conn
        now = _now_iso()
        conn.execute(
            """UPDATE memory_jobs
               SET status = 'pending', attempts = attempts + 1, last_error = ?, updated_at = ?
               WHERE id = ?""",
            (error[:2000], now, job_id),
        )
