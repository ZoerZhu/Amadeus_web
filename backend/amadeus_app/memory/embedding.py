"""Memory embedding provider.

默认通过 OpenAI-compatible ``/embeddings`` 调用远端或 Ollama；也支持
可选的 llama-cpp-python 直连 GGUF。本模块只负责生成向量，调用方负责
在失败时降级到 FTS。
"""

from __future__ import annotations

import asyncio
import math
import os
import site
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..domain import ModelSettings
from ..logging_config import get_logger

_log = get_logger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_LOCAL_MODEL_PATH = ROOT_DIR / "models" / "embedding" / "Qwen3-Embedding-0.6B-Q8_0.gguf"

EMBEDDING_BACKEND = os.getenv("AMADEUS_MEMORY_EMBEDDING_BACKEND", "openai").strip().lower()
EMBEDDING_MODEL = os.getenv("AMADEUS_MEMORY_EMBEDDING_MODEL", "text-embedding-3-small").strip()
EMBEDDING_DIM = int(os.getenv("AMADEUS_MEMORY_EMBEDDING_DIM", "1536"))
EMBEDDING_TIMEOUT = int(os.getenv("AMADEUS_MEMORY_EMBEDDING_TIMEOUT", "30"))
EMBEDDING_BASE_URL = os.getenv("AMADEUS_MEMORY_EMBEDDING_BASE_URL", "").strip()
EMBEDDING_API_KEY = os.getenv("AMADEUS_MEMORY_EMBEDDING_API_KEY", "").strip()
EMBEDDING_LOCAL_MODEL_PATH = os.getenv(
    "AMADEUS_MEMORY_EMBEDDING_LOCAL_MODEL_PATH",
    str(DEFAULT_LOCAL_MODEL_PATH),
).strip()
EMBEDDING_NORMALIZE = os.getenv("AMADEUS_MEMORY_EMBEDDING_NORMALIZE", "1").lower() not in ("0", "false", "no")
LLAMA_N_CTX = int(os.getenv("AMADEUS_MEMORY_LLAMA_N_CTX", "8192"))
LLAMA_N_THREADS = int(os.getenv("AMADEUS_MEMORY_LLAMA_N_THREADS", str(max(1, (os.cpu_count() or 4) // 2))))
LLAMA_N_GPU_LAYERS = int(os.getenv("AMADEUS_MEMORY_LLAMA_N_GPU_LAYERS", "0"))
REMOTE_BATCH_SIZE = int(os.getenv("AMADEUS_MEMORY_EMBEDDING_REMOTE_BATCH_SIZE", "10"))
MAX_INPUT_CHARS = 32000  # ≈ 8191 tokens

_LOCAL_LLAMA: Any | None = None
_LOCAL_LLAMA_PATH: str | None = None
_DLL_DIRECTORY_HANDLES: list[Any] = []
_DLL_DIRECTORIES_INITIALIZED = False


@dataclass(frozen=True)
class EmbeddingConfig:
    backend: str
    model: str
    dimension: int
    timeout: int
    base_url: str
    api_key: str
    local_model_path: str
    normalize: bool


def _current_config(settings: ModelSettings) -> EmbeddingConfig:
    backend = os.getenv("AMADEUS_MEMORY_EMBEDDING_BACKEND", EMBEDDING_BACKEND).strip().lower() or "openai"
    base_url = os.getenv("AMADEUS_MEMORY_EMBEDDING_BASE_URL", EMBEDDING_BASE_URL).strip() or settings.base_url
    api_key = os.getenv("AMADEUS_MEMORY_EMBEDDING_API_KEY", EMBEDDING_API_KEY).strip() or settings.api_key
    model = (
        os.getenv("AMADEUS_MEMORY_EMBEDDING_MODEL", EMBEDDING_MODEL).strip()
        or settings.model
        or "text-embedding-3-small"
    )
    return EmbeddingConfig(
        backend=backend,
        model=model,
        dimension=int(os.getenv("AMADEUS_MEMORY_EMBEDDING_DIM", str(EMBEDDING_DIM))),
        timeout=int(os.getenv("AMADEUS_MEMORY_EMBEDDING_TIMEOUT", str(EMBEDDING_TIMEOUT))),
        base_url=base_url.strip(),
        api_key=api_key.strip(),
        local_model_path=os.getenv(
            "AMADEUS_MEMORY_EMBEDDING_LOCAL_MODEL_PATH",
            EMBEDDING_LOCAL_MODEL_PATH,
        ).strip(),
        normalize=os.getenv(
            "AMADEUS_MEMORY_EMBEDDING_NORMALIZE",
            "1" if EMBEDDING_NORMALIZE else "0",
        ).lower() not in ("0", "false", "no"),
    )


async def get_embedding(text: str, settings: ModelSettings) -> list[float]:
    """返回单条文本的 embedding。

    失败时抛出异常，由调用方决定降级策略。
    """
    vectors = await get_embeddings_batch([text], settings)
    if not vectors:
        raise RuntimeError("embedding provider returned no vectors")
    return vectors[0]


async def get_embeddings_batch(texts: list[str], settings: ModelSettings) -> list[list[float]]:
    """批量获取 embedding，减少 API 调用次数。"""
    clean_texts = [t[:MAX_INPUT_CHARS] for t in texts if str(t).strip()]
    if not clean_texts:
        return []
    config = _current_config(settings)
    if config.backend in {"local", "local_gguf", "llama", "llama_cpp"}:
        return await asyncio.to_thread(_get_local_embeddings_sync, clean_texts, config)
    return await _get_openai_compatible_embeddings(clean_texts, config)


async def _get_openai_compatible_embeddings(
    texts: list[str],
    config: EmbeddingConfig,
) -> list[list[float]]:
    """调用 OpenAI-compatible embedding endpoint。

    ``backend=ollama`` 仍走 OpenAI-compatible ``/v1/embeddings``，区别只在
    默认不要求 API key。
    """
    if not config.base_url:
        raise RuntimeError("memory embedding base_url is empty")
    endpoint = f"{config.base_url.rstrip('/')}/embeddings"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    results: list[list[float]] = []
    batch_size = max(1, int(os.getenv("AMADEUS_MEMORY_EMBEDDING_REMOTE_BATCH_SIZE", str(REMOTE_BATCH_SIZE))))
    async with httpx.AsyncClient(timeout=config.timeout) as client:
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            payload: dict[str, Any] = {
                "model": config.model,
                "input": batch,
            }
            response = await client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            items = sorted(data["data"], key=lambda item: int(item.get("index", 0)))
            for item in items:
                results.append(_normalize_if_needed(list(item["embedding"]), config))

    _warn_dimension_mismatch(results, config)
    return results


def _get_local_embeddings_sync(texts: list[str], config: EmbeddingConfig) -> list[list[float]]:
    """用 llama-cpp-python 直连本地 GGUF 生成 embedding。"""
    model_path = Path(config.local_model_path).expanduser()
    if not model_path.is_absolute():
        model_path = ROOT_DIR / model_path
    if not model_path.exists():
        raise FileNotFoundError(f"local embedding model not found: {model_path}")

    llama = _load_local_llama(str(model_path))
    payload = llama.create_embedding(input=texts)
    items = sorted(payload.get("data", []), key=lambda item: int(item.get("index", 0)))
    vectors = [_normalize_if_needed(list(item["embedding"]), config) for item in items]
    _warn_dimension_mismatch(vectors, config)
    return vectors


def _load_local_llama(model_path: str) -> Any:
    """延迟加载并缓存 llama.cpp 模型。"""
    global _LOCAL_LLAMA, _LOCAL_LLAMA_PATH
    if _LOCAL_LLAMA is not None and _LOCAL_LLAMA_PATH == model_path:
        return _LOCAL_LLAMA
    _prepare_llama_cpp_windows_dlls()
    try:
        from llama_cpp import Llama
    except ImportError as exc:
        raise RuntimeError(
            "llama-cpp-python is not installed; install it or use backend=ollama/openai"
        ) from exc

    _log.info("Loading local memory embedding model: %s", model_path)
    _LOCAL_LLAMA = Llama(
        model_path=model_path,
        embedding=True,
        n_ctx=int(os.getenv("AMADEUS_MEMORY_LLAMA_N_CTX", str(LLAMA_N_CTX))),
        n_threads=int(os.getenv("AMADEUS_MEMORY_LLAMA_N_THREADS", str(LLAMA_N_THREADS))),
        n_gpu_layers=int(os.getenv("AMADEUS_MEMORY_LLAMA_N_GPU_LAYERS", str(LLAMA_N_GPU_LAYERS))),
        verbose=False,
    )
    _LOCAL_LLAMA_PATH = model_path
    return _LOCAL_LLAMA


def _prepare_llama_cpp_windows_dlls() -> None:
    """Expose llama-cpp-python and NVIDIA wheel DLLs to Windows loader."""
    global _DLL_DIRECTORIES_INITIALIZED
    if os.name != "nt" or _DLL_DIRECTORIES_INITIALIZED:
        return
    _DLL_DIRECTORIES_INITIALIZED = True

    candidate_roots: list[Path] = []
    for raw_path in [*site.getsitepackages(), site.getusersitepackages(), *sys.path]:
        if not raw_path:
            continue
        path = Path(raw_path)
        if path.exists() and path not in candidate_roots:
            candidate_roots.append(path)

    dll_dirs: list[Path] = []
    for root in candidate_roots:
        for candidate in (
            root / "bin",
            root / "llama_cpp" / "lib",
            root / "nvidia" / "cu13" / "bin" / "x86_64",
            root / "nvidia" / "cu13" / "bin",
            root / "nvidia" / "cudnn" / "bin",
        ):
            if candidate.exists() and candidate not in dll_dirs:
                dll_dirs.append(candidate)
        nvidia_root = root / "nvidia"
        if nvidia_root.exists():
            for dll_file in nvidia_root.rglob("*.dll"):
                dll_dir = dll_file.parent
                if dll_dir not in dll_dirs:
                    dll_dirs.append(dll_dir)

    if not dll_dirs:
        return

    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    for dll_dir in dll_dirs:
        dll_dir_text = str(dll_dir)
        if dll_dir_text not in path_entries:
            path_entries.insert(0, dll_dir_text)
        if hasattr(os, "add_dll_directory"):
            try:
                _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(dll_dir_text))
            except OSError as exc:
                _log.debug("Failed to add DLL directory %s: %s", dll_dir, exc)
    os.environ["PATH"] = os.pathsep.join(path_entries)


def _normalize_if_needed(vector: list[float], config: EmbeddingConfig) -> list[float]:
    if not config.normalize:
        return vector
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [value / norm for value in vector]


def _warn_dimension_mismatch(vectors: list[list[float]], config: EmbeddingConfig) -> None:
    for embedding in vectors:
        if len(embedding) != config.dimension:
            _log.warning(
                "Embedding dimension mismatch: expected %d, got %d (model=%s backend=%s)",
                config.dimension,
                len(embedding),
                config.model,
                config.backend,
            )
            return


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
