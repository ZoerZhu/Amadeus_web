"""OpenAI 兼容的 Embedding 服务。

复用现有 provider 配置，走 {base_url}/embeddings 端点。
支持 OpenAI、小米 MiMo、通义千问等兼容 provider。
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..domain import ModelSettings
from ..logging_config import get_logger

_log = get_logger(__name__)

EMBEDDING_MODEL = os.getenv("AMADEUS_MEMORY_EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = int(os.getenv("AMADEUS_MEMORY_EMBEDDING_DIM", "1536"))
EMBEDDING_TIMEOUT = int(os.getenv("AMADEUS_MEMORY_EMBEDDING_TIMEOUT", "30"))
MAX_INPUT_CHARS = 32000  # ≈ 8191 tokens


async def get_embedding(text: str, settings: ModelSettings) -> list[float]:
    """调用 OpenAI 兼容的 embedding API，返回浮点向量。

    失败时抛出异常，由调用方决定降级策略。
    """
    text = text[:MAX_INPUT_CHARS]
    endpoint = f"{settings.base_url.rstrip('/')}/embeddings"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    payload: dict[str, Any] = {
        "model": EMBEDDING_MODEL,
        "input": text,
    }

    async with httpx.AsyncClient(timeout=EMBEDDING_TIMEOUT) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    embedding = data["data"][0]["embedding"]
    if len(embedding) != EMBEDDING_DIM:
        _log.warning(
            "Embedding dimension mismatch: expected %d, got %d",
            EMBEDDING_DIM,
            len(embedding),
        )
    return embedding


async def get_embeddings_batch(texts: list[str], settings: ModelSettings) -> list[list[float]]:
    """批量获取 embedding，减少 API 调用次数。"""
    texts = [t[:MAX_INPUT_CHARS] for t in texts]
    endpoint = f"{settings.base_url.rstrip('/')}/embeddings"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    payload: dict[str, Any] = {
        "model": EMBEDDING_MODEL,
        "input": texts,
    }

    async with httpx.AsyncClient(timeout=EMBEDDING_TIMEOUT) as client:
        response = await client.post(endpoint, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    # API 返回按 index 排序
    results = [None] * len(texts)
    for item in data["data"]:
        results[item["index"]] = item["embedding"]
    return [r for r in results if r is not None]


def build_node_embedding_text(node: dict[str, Any]) -> str:
    """构造节点摘要的 embedding 输入文本。"""
    parts = [node.get("path", ""), node.get("label", "")]
    if node.get("summary"):
        parts.append(node["summary"])
    keywords = node.get("keywords", "[]")
    if isinstance(keywords, str):
        parts.append(keywords)
    elif isinstance(keywords, list):
        parts.append(" ".join(keywords))
    return "\n".join(p for p in parts if p)


def build_leaf_embedding_text(node: dict[str, Any]) -> str:
    """构造叶子内容的 embedding 输入文本。"""
    parts = [node.get("path", ""), node.get("label", ""), node.get("summary", "")]
    if node.get("full_content"):
        parts.append(node["full_content"])
    keywords = node.get("keywords", "[]")
    if isinstance(keywords, str):
        parts.append(keywords)
    elif isinstance(keywords, list):
        parts.append(" ".join(keywords))
    return "\n".join(p for p in parts if p)
