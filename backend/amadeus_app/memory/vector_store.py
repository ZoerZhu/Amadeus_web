"""sqlite-vec 向量存储封装。

提供 upsert / delete / search / rebuild 接口，
后续可替换为 ChromaDB 等后端。
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

import sqlite_vec

from ..logging_config import get_logger

_log = get_logger(__name__)

EMBEDDING_DIM = int(os.getenv("AMADEUS_MEMORY_EMBEDDING_DIM", "1536"))


def load_vec_extension(conn: sqlite3.Connection) -> None:
    """向 SQLite 连接加载 sqlite-vec 扩展。"""
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def ensure_vec_tables(conn: sqlite3.Connection) -> None:
    """创建向量虚拟表（如果不存在）。

    必须在 load_vec_extension 之后调用。
    """
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_node_vectors
            USING vec0(
                node_id TEXT PRIMARY KEY,
                embedding FLOAT[{EMBEDDING_DIM}]
            )
        """
    )
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_leaf_vectors
            USING vec0(
                node_id TEXT PRIMARY KEY,
                embedding FLOAT[{EMBEDDING_DIM}]
            )
        """
    )


def upsert_node_vector(conn: sqlite3.Connection, node_id: str, embedding: list[float]) -> None:
    """插入或替换节点摘要向量。"""
    # vec0 不支持 UPSERT，先删后插
    conn.execute("DELETE FROM memory_node_vectors WHERE node_id = ?", (node_id,))
    conn.execute(
        "INSERT INTO memory_node_vectors (node_id, embedding) VALUES (?, ?)",
        (node_id, _serialize_vec(embedding)),
    )


def upsert_leaf_vector(conn: sqlite3.Connection, node_id: str, embedding: list[float]) -> None:
    """插入或替换叶子内容向量。"""
    conn.execute("DELETE FROM memory_leaf_vectors WHERE node_id = ?", (node_id,))
    conn.execute(
        "INSERT INTO memory_leaf_vectors (node_id, embedding) VALUES (?, ?)",
        (node_id, _serialize_vec(embedding)),
    )


def delete_node_vector(conn: sqlite3.Connection, node_id: str) -> None:
    conn.execute("DELETE FROM memory_node_vectors WHERE node_id = ?", (node_id,))


def delete_leaf_vector(conn: sqlite3.Connection, node_id: str) -> None:
    conn.execute("DELETE FROM memory_leaf_vectors WHERE node_id = ?", (node_id,))


def search_node_vectors(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    top_k: int = 8,
    *,
    candidate_ids: list[str] | None = None,
) -> list[tuple[str, float]]:
    """在节点向量中搜索最相似的 top_k 个节点。

    返回 [(node_id, distance)]，distance 越小越相似。
    candidate_ids 非空时只在这些 ID 中搜索（用于限定 domain 子树）。
    """
    return _search_vec_table(
        conn, "memory_node_vectors", query_embedding, top_k, candidate_ids
    )


def search_leaf_vectors(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    top_k: int = 8,
    *,
    candidate_ids: list[str] | None = None,
) -> list[tuple[str, float]]:
    """在叶子向量中搜索最相似的 top_k 个叶子。"""
    return _search_vec_table(
        conn, "memory_leaf_vectors", query_embedding, top_k, candidate_ids
    )


def _search_vec_table(
    conn: sqlite3.Connection,
    table: str,
    query_embedding: list[float],
    top_k: int,
    candidate_ids: list[str] | None,
) -> list[tuple[str, float]]:
    """通用 vec0 KNN 搜索。

    当 candidate_ids 非空时，sqlite-vec 不支持 WHERE IN 过滤，
    需先 overfetch 再在应用层过滤。为避免全局 top_k 截断掉子树内候选，
    overfetch 数量取 max(top_k * 10, len(candidate_ids) * 2, 200)。
    """
    if candidate_ids is not None and len(candidate_ids) == 0:
        return []

    # candidate_ids 场景下扩大 LIMIT，避免全局 top_k 截断子树候选
    if candidate_ids is not None:
        fetch_limit = max(top_k * 10, len(candidate_ids) * 2, 200)
    else:
        fetch_limit = top_k

    query_sql = f"""
        SELECT node_id, distance
        FROM {table}
        WHERE embedding MATCH ?
        ORDER BY distance
        LIMIT ?
    """
    params: list[Any] = [_serialize_vec(query_embedding), fetch_limit]

    rows = conn.execute(query_sql, params).fetchall()

    if candidate_ids is not None:
        id_set = set(candidate_ids)
        filtered = [(row[0], row[1]) for row in rows if row[0] in id_set]
        # 过滤后取 top_k
        return filtered[:top_k]

    return [(row[0], row[1]) for row in rows]


def _serialize_vec(embedding: list[float]) -> bytes:
    """将 float 列表序列化为 sqlite-vec 需要的 bytes 格式。"""
    import struct

    return struct.pack(f"<{len(embedding)}f", *embedding)
