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

    当 candidate_ids 非空时，直接按主键精确取回候选向量，在 Python 中
    计算 L2 距离后排序取 top_k。这避免了 sqlite-vec 全局 KNN 把子树内
    候选截掉的问题（overfetch-then-filter 在候选数 > fetch_limit 时会漏召回）。
    """
    if candidate_ids is not None and len(candidate_ids) == 0:
        return []

    # --- candidate-aware path: exact lookup + Python L2 ---
    if candidate_ids is not None:
        return _search_candidates(conn, table, query_embedding, top_k, candidate_ids)

    # --- global KNN path ---
    query_sql = f"""
        SELECT node_id, distance
        FROM {table}
        WHERE embedding MATCH ?
        ORDER BY distance
        LIMIT ?
    """
    rows = conn.execute(query_sql, [_serialize_vec(query_embedding), top_k]).fetchall()
    return [(row[0], row[1]) for row in rows]


def _search_candidates(
    conn: sqlite3.Connection,
    table: str,
    query_embedding: list[float],
    top_k: int,
    candidate_ids: list[str],
) -> list[tuple[str, float]]:
    """对 candidate_ids 精确取向量，Python 计算 L2 距离后排序。

    使用 ``WHERE node_id IN (?, ?, ...)`` 分批查询以减少 SQLite 往返。
    每批最多 500 个 ID，对典型 domain 子树（几十到几百个节点）只需 1 次查询。
    """
    results: list[tuple[str, float]] = []
    batch_size = 500
    for start in range(0, len(candidate_ids), batch_size):
        batch = candidate_ids[start : start + batch_size]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT node_id, embedding FROM {table} WHERE node_id IN ({placeholders})",
            batch,
        ).fetchall()
        for row in rows:
            emb = _deserialize_vec(row[1])
            if emb is None:
                continue
            dist = _l2_distance(query_embedding, emb)
            results.append((row[0], dist))
    results.sort(key=lambda x: x[1])
    return results[:top_k]


def _l2_distance(a: list[float], b: list[float]) -> float:
    """计算两个向量的欧氏距离。"""
    import math

    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _deserialize_vec(raw: bytes) -> list[float] | None:
    """将 sqlite-vec 存储的 bytes 反序列化为 float 列表。

    返回 None 表示数据损坏或维度不匹配，调用方应跳过。
    """
    import struct

    if not raw or len(raw) % 4 != 0:
        _log.warning("skip corrupted embedding bytes: len=%d", len(raw))
        return None
    count = len(raw) // 4
    try:
        return list(struct.unpack(f"<{count}f", raw))
    except struct.error:
        _log.warning("skip embedding bytes: struct.unpack failed, len=%d", len(raw))
        return None


def _serialize_vec(embedding: list[float]) -> bytes:
    """将 float 列表序列化为 sqlite-vec 需要的 bytes 格式。"""
    import struct

    return struct.pack(f"<{len(embedding)}f", *embedding)
