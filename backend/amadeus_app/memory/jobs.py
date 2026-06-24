"""记忆系统异步任务 worker。

从 memory_jobs 表拉取任务并执行，支持 ingest / summarize_node / cleanup 三种任务类型。
作为 asyncio 后台任务运行，不阻塞主事件循环。
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from ..domain import ModelProviderPreset, ModelSettings
from ..logging_config import get_logger
from ..providers import get_provider
from ..runtime_config import effective_model_settings
from ..storage import DEFAULT_USER_ID, SQLiteStorage
from .tree_store import MemoryTreeStore

_log = get_logger(__name__)

# 任务拉取间隔（秒）
JOB_POLL_INTERVAL = 5.0
# 单次拉取任务上限
JOB_BATCH_LIMIT = 5
# 最大重试次数（含首次执行）
DEFAULT_MAX_ATTEMPTS = 3


def _max_attempts() -> int:
    """从环境变量读取最大重试次数。"""
    raw = os.getenv("AMADEUS_MEMORY_JOB_MAX_ATTEMPTS", "").strip()
    if not raw:
        return DEFAULT_MAX_ATTEMPTS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_ATTEMPTS


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_payload(raw: Any) -> dict[str, Any]:
    """解析 payload 字段为 dict。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


class MemoryJobWorker:
    """记忆系统异步任务 worker。

    作为 asyncio 后台任务运行，定期从 memory_jobs 表拉取 pending 任务并执行。
    """

    def __init__(self, storage: SQLiteStorage, tree_store: MemoryTreeStore) -> None:
        self._storage = storage
        self._tree_store = tree_store
        self._task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        """启动后台循环。"""
        if self._task is not None:
            return
        # 启动前确保默认树结构存在
        try:
            await self._tree_store.ensure_default_tree()
        except Exception:
            _log.exception("ensure_default_tree 失败，worker 仍将启动")
        self._stopping = False
        self._task = asyncio.create_task(self._run_loop(), name="memory-job-worker")
        _log.info("MemoryJobWorker 已启动")

    async def stop(self) -> None:
        """停止循环。"""
        self._stopping = True
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception:
            _log.exception("停止 MemoryJobWorker 时发生异常")
        self._task = None
        _log.info("MemoryJobWorker 已停止")

    async def _run_loop(self) -> None:
        """主循环，每隔 5 秒拉取任务。"""
        _log.info("记忆任务循环开始，间隔 %.1fs", JOB_POLL_INTERVAL)
        while not self._stopping:
            try:
                jobs = await self._tree_store.claim_pending_jobs(JOB_BATCH_LIMIT)
                if jobs:
                    _log.debug("拉取到 %d 个记忆任务", len(jobs))
                    # 每批次只加载一次设置
                    settings, provider = await self._resolve_settings()
                    for job in jobs:
                        if self._stopping:
                            break
                        await self._process_job(job, settings, provider)
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("记忆任务循环异常")
            try:
                await asyncio.sleep(JOB_POLL_INTERVAL)
            except asyncio.CancelledError:
                raise

    async def _resolve_settings(self) -> tuple[ModelSettings, ModelProviderPreset]:
        """加载当前用户的模型设置与 provider。"""
        try:
            raw = await self._storage.get_settings(DEFAULT_USER_ID)
        except Exception:
            _log.exception("加载设置失败")
            raw = None
        if not raw or not isinstance(raw, dict):
            return ModelSettings(providerName="演示模型"), get_provider("演示模型")
        model_dict = raw.get("model") or {}
        try:
            model_settings = ModelSettings(**model_dict)
        except Exception:
            _log.exception("解析 ModelSettings 失败，使用默认值")
            return ModelSettings(providerName="演示模型"), get_provider("演示模型")
        provider = get_provider(model_settings.provider_name or "演示模型")
        settings = effective_model_settings(model_settings, provider)
        provider = get_provider(settings.provider_name)
        settings = effective_model_settings(settings, provider)
        return settings, provider

    async def _process_job(
        self, job: dict, settings: ModelSettings, provider: ModelProviderPreset
    ) -> None:
        """处理单个任务，失败时记录错误并按需重试。"""
        job_id = job.get("id", "")
        job_type = job.get("job_type", "")
        try:
            if job_type == "ingest":
                await self._handle_ingest(job, settings, provider)
            elif job_type == "summarize_node":
                await self._handle_summarize_node(job, settings, provider)
            elif job_type == "cleanup":
                await self._handle_cleanup(job)
            else:
                _log.warning("未知任务类型: %s (job=%s)", job_type, job_id)
            await self._tree_store.complete_job(job_id)
            _log.info("记忆任务完成: %s type=%s", job_id, job_type)
        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}"
            _log.warning("记忆任务失败: %s type=%s err=%s", job_id, job_type, err_msg)
            attempts = int(job.get("attempts", 0)) + 1
            max_attempts = _max_attempts()
            if attempts >= max_attempts:
                # 超过最大重试次数，标记为 failed
                await self._tree_store.complete_job(job_id, error=err_msg)
                _log.warning(
                    "记忆任务超过最大重试次数，标记为 failed: %s attempts=%d/%d",
                    job_id, attempts, max_attempts,
                )
            else:
                # 重新入队等待重试
                await self._tree_store.requeue_job(job_id, error=err_msg)
                _log.info(
                    "记忆任务重新入队等待重试: %s attempts=%d/%d",
                    job_id, attempts, max_attempts,
                )

    # ================================================================
    # 任务处理器
    # ================================================================

    async def _handle_ingest(
        self, job: dict, settings: ModelSettings, provider: ModelProviderPreset
    ) -> None:
        """处理 ingest 任务：从对话加载消息，提取叶子记忆并路由挂载。

        支持两种模式：
        1. 手动文本导入：payload 包含 text 字段，直接创建叶子记忆（无需 conversationId）
        2. 对话提取：payload 包含 conversationId，从对话消息中 LLM 提取叶子记忆
        """
        payload = _decode_payload(job.get("payload"))
        user_id = job.get("user_id", DEFAULT_USER_ID)
        project_id = payload.get("projectId")

        # ---- 模式 1：手动文本导入 ----
        raw_text = payload.get("text")
        if raw_text and isinstance(raw_text, str) and raw_text.strip():
            await self._handle_ingest_text(
                text=raw_text.strip(),
                domain=payload.get("domain", "knowledge"),
                title=payload.get("title", ""),
                project_id=project_id,
                conversation_id=payload.get("conversationId"),
                user_id=user_id,
                settings=settings,
            )
            return

        # ---- 模式 2：对话提取（原有流程）----
        # 检查模型配置是否可用（如 API key 为空则跳过）
        if not settings.api_key.strip() or not settings.model.strip():
            _log.warning(
                "模型配置不完整（api_key 或 model 为空），跳过 ingest 任务: job=%s",
                job.get("id"),
            )
            return

        conversation_id = payload.get("conversationId") or job.get("conversation_id")
        message_ids = payload.get("messageIds") or []
        extract_window = payload.get("extractWindow")  # 仅处理最近 N 条消息

        if not conversation_id:
            raise RuntimeError("ingest 任务缺少 conversationId 或 text")

        # 加载对话消息
        try:
            conv_uuid = UUID(str(conversation_id))
        except (ValueError, AttributeError, TypeError) as exc:
            raise RuntimeError(f"无效的 conversation_id: {conversation_id}") from exc

        conversation = await self._storage.get_conversation(
            user_id=user_id, conversation_id=conv_uuid
        )
        if not conversation:
            raise RuntimeError(f"未找到对话: {conversation_id}")

        messages = conversation.get("messages") or []
        if not messages:
            _log.info("对话无消息，跳过 ingest: %s", conversation_id)
            return

        # 按 message_ids 过滤（若提供）
        if message_ids:
            id_set = {str(mid) for mid in message_ids}
            messages = [m for m in messages if str(m.get("id")) in id_set]
            if not messages:
                _log.info("message_ids 过滤后无消息，跳过 ingest: %s", conversation_id)
                return

        # 按 extractWindow 截取最近 N 条消息（避免重复扫描全会话）
        if extract_window and isinstance(extract_window, int) and extract_window > 0:
            if len(messages) > extract_window:
                _log.info(
                    "按 extractWindow=%d 截取消息: 总 %d 条 → 最近 %d 条",
                    extract_window, len(messages), extract_window,
                )
                messages = messages[-extract_window:]

        # 延迟导入：extractor / router 尚未创建，避免模块加载时报错
        from .extractor import extract_leaf_memories

        leaves = await extract_leaf_memories(messages, settings, provider)
        if not leaves:
            _log.info("未提取到叶子记忆: conversation=%s", conversation_id)
            return

        # 路由并挂载每个叶子
        from .router import route_and_attach_leaf

        attached = 0
        for leaf in leaves:
            try:
                await route_and_attach_leaf(
                    leaf,
                    self._tree_store,
                    settings,
                    user_id=user_id,
                    conversation_id=str(conversation_id),
                    project_id=project_id,
                )
                attached += 1
            except Exception:
                leaf_title = getattr(leaf, "title", None) or str(leaf)
                _log.exception("挂载叶子记忆失败: %s", leaf_title)
        _log.info(
            "ingest 完成: conversation=%s 提取=%d 挂载=%d",
            conversation_id, len(leaves), attached,
        )

        # 生成/更新会话滚动摘要（替代硬截断 12 条）
        try:
            from .extractor import generate_session_summary

            old_summary = await self._tree_store.get_session_summary(str(conversation_id))
            new_summary = await generate_session_summary(
                old_summary=old_summary or "",
                messages=messages,
                settings=settings,
                provider=provider,
            )
            if new_summary and new_summary.strip():
                await self._tree_store.save_session_summary(
                    str(conversation_id), new_summary.strip(), user_id=user_id
                )
                _log.info("会话摘要已更新: conversation=%s len=%d", conversation_id, len(new_summary))
        except Exception:
            _log.exception("生成会话摘要失败（非致命）")

    async def _handle_ingest_text(
        self,
        *,
        text: str,
        domain: str,
        title: str,
        project_id: str | None,
        conversation_id: str | None,
        user_id: str,
        settings: ModelSettings,
    ) -> None:
        """手动文本导入：直接从文本创建叶子记忆，跳过 LLM 提取。

        构造 ExtractedLeafMemory 并调用 route_and_attach_leaf，
        复用路由 / 去重 / 向量写入逻辑。
        """
        from .models import ExtractedLeafMemory, MemoryDomain, MemoryCategory
        from .router import route_and_attach_leaf

        # domain 字符串 → 枚举（容错：无效值降级为 knowledge）
        try:
            domain_enum = MemoryDomain(domain)
        except ValueError:
            domain_enum = MemoryDomain.KNOWLEDGE

        # title 为空时从 text 截取前 60 字
        leaf_title = (title or "").strip() or text[:60]

        leaf = ExtractedLeafMemory(
            action="create_leaf",
            domain=domain_enum,
            title=leaf_title,
            summary=text[:200] if len(text) > 200 else text,
            full_content=text,
            category=MemoryCategory.FACT,
            keywords=[],
            importance=0.6,
            confidence=0.8,
        )

        try:
            created = await route_and_attach_leaf(
                leaf,
                self._tree_store,
                settings,
                user_id=user_id,
                conversation_id=conversation_id,
                project_id=project_id,
            )
            if created:
                _log.info(
                    "手动文本导入完成: leaf=%s domain=%s title=%s",
                    created.get("id"), domain, leaf_title,
                )
        except Exception:
            _log.exception("手动文本导入失败: title=%s", leaf_title)

    async def _handle_summarize_node(
        self, job: dict, settings: ModelSettings, provider: ModelProviderPreset
    ) -> None:
        """处理节点摘要更新任务：沿父链更新 cluster/topic/domain summary + 重建 node 向量。"""
        payload = _decode_payload(job.get("payload"))
        node_id = payload.get("node_id")
        if not node_id:
            _log.warning("summarize_node 任务缺少 node_id: job=%s", job.get("id"))
            return

        # 检查模型配置是否可用
        if not settings.api_key.strip() or not settings.model.strip():
            _log.warning(
                "模型配置不完整，跳过 summarize_node 任务: job=%s node=%s",
                job.get("id"), node_id,
            )
            return

        from .summarizer import update_ancestor_summaries

        await update_ancestor_summaries(
            leaf_node_id=node_id,
            tree_store=self._tree_store,
            settings=settings,
            provider=provider,
        )
        _log.info("summarize_node 完成: job=%s node=%s", job.get("id"), node_id)

    async def _handle_cleanup(self, job: dict) -> None:
        """清理过期记忆：将已过期的叶子节点标记为 inactive。"""
        user_id = job.get("user_id", DEFAULT_USER_ID)
        now_iso = _now_iso()
        # 列出所有叶子节点（含 inactive），筛选已过期且仍 active 的
        nodes = await self._tree_store.list_nodes(
            user_id=user_id, node_type="leaf", active_only=False, limit=500,
        )
        cleaned = 0
        for node in nodes:
            expires_at = node.get("expiresAt")
            if not expires_at:
                continue
            # 仅处理仍 active 且已过期的节点
            if not node.get("isActive"):
                continue
            if expires_at <= now_iso:
                try:
                    await self._tree_store.update_node(node["id"], {"isActive": False})
                    cleaned += 1
                except Exception:
                    _log.exception("清理过期节点失败: %s", node.get("id"))
        _log.info("cleanup 完成: 清理过期节点 %d 个 (user=%s)", cleaned, user_id)


async def start_memory_worker(
    storage: SQLiteStorage, tree_store: MemoryTreeStore
) -> MemoryJobWorker:
    """便捷启动函数：创建并启动 MemoryJobWorker。"""
    worker = MemoryJobWorker(storage, tree_store)
    await worker.start()
    return worker
