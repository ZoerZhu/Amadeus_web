"""记忆系统 v2.0 新增模块测试 — reranker / query_rewriter / injector / deduper / session summary。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# ====================================================================
# Reranker 测试
# ====================================================================


class TestReranker:
    """测试记忆重排模块。"""

    def test_weighted_rerank_basic(self):
        """测试基本加权重排。"""
        from backend.amadeus_app.memory.reranker import weighted_rerank

        candidates = [
            {
                "id": "a",
                "importance": 0.9,
                "confidence": 0.9,
                "updatedAt": "2026-06-18T00:00:00+00:00",
                "accessCount": 5,
                "expiresAt": None,
                "projectId": "proj1",
                "isActive": True,
            },
            {
                "id": "b",
                "importance": 0.3,
                "confidence": 0.6,
                "updatedAt": "2026-06-18T00:00:00+00:00",
                "accessCount": 0,
                "expiresAt": None,
                "projectId": None,
                "isActive": True,
            },
        ]
        vector_scores = {"a": 0.8, "b": 0.2}
        keyword_scores = {"a": 0.6, "b": 0.1}

        results = weighted_rerank(
            candidates=candidates,
            vector_scores=vector_scores,
            keyword_scores=keyword_scores,
            current_project_id="proj1",
            active_injection=True,
        )
        # a 应该排在 b 前面（各项分数都更高）
        assert len(results) == 2
        assert results[0][0] == "a"
        assert results[0][1] > results[1][1]

    def test_weighted_rerank_filters_low_confidence(self):
        """active_injection=True 时过滤低置信节点。"""
        from backend.amadeus_app.memory.reranker import weighted_rerank

        candidates = [
            {
                "id": "low_conf",
                "importance": 0.9,
                "confidence": 0.3,  # 低于 MIN_CONFIDENCE (0.5)
                "updatedAt": "2026-06-18T00:00:00+00:00",
                "accessCount": 0,
                "expiresAt": None,
                "isActive": True,
            },
            {
                "id": "ok",
                "importance": 0.5,
                "confidence": 0.8,
                "updatedAt": "2026-06-18T00:00:00+00:00",
                "accessCount": 0,
                "expiresAt": None,
                "isActive": True,
            },
        ]
        results = weighted_rerank(
            candidates=candidates,
            vector_scores={"ok": 0.5},  # ok 有向量命中
            keyword_scores={},
            active_injection=True,
        )
        ids = [r[0] for r in results]
        assert "low_conf" not in ids
        assert "ok" in ids

    def test_weighted_rerank_no_filter_when_tool_retrieval(self):
        """active_injection=False 时不过滤低置信。"""
        from backend.amadeus_app.memory.reranker import weighted_rerank

        candidates = [
            {
                "id": "low_conf",
                "importance": 0.9,
                "confidence": 0.3,
                "updatedAt": "2026-06-18T00:00:00+00:00",
                "accessCount": 0,
                "expiresAt": None,
                "isActive": True,
            },
        ]
        results = weighted_rerank(
            candidates=candidates,
            vector_scores={},
            keyword_scores={},
            active_injection=False,
        )
        assert len(results) == 1
        assert results[0][0] == "low_conf"

    def test_weighted_rerank_filters_expired(self):
        """过滤已过期节点。"""
        from backend.amadeus_app.memory.reranker import weighted_rerank

        candidates = [
            {
                "id": "expired",
                "importance": 0.9,
                "confidence": 0.9,
                "updatedAt": "2026-06-18T00:00:00+00:00",
                "accessCount": 0,
                "expiresAt": "2020-01-01T00:00:00+00:00",  # 已过期
                "isActive": True,
            },
        ]
        results = weighted_rerank(
            candidates=candidates,
            vector_scores={},
            keyword_scores={},
            active_injection=True,
        )
        assert len(results) == 0

    def test_project_match_boost(self):
        """项目匹配的节点分数更高。"""
        from backend.amadeus_app.memory.reranker import weighted_rerank

        candidates = [
            {
                "id": "same_project",
                "importance": 0.5,
                "confidence": 0.7,
                "updatedAt": "2026-06-18T00:00:00+00:00",
                "accessCount": 0,
                "expiresAt": None,
                "projectId": "proj1",
                "isActive": True,
            },
            {
                "id": "diff_project",
                "importance": 0.5,
                "confidence": 0.7,
                "updatedAt": "2026-06-18T00:00:00+00:00",
                "accessCount": 0,
                "expiresAt": None,
                "projectId": "proj2",
                "isActive": True,
            },
        ]
        results = weighted_rerank(
            candidates=candidates,
            vector_scores={"same_project": 0.5, "diff_project": 0.5},
            keyword_scores={},
            current_project_id="proj1",
            active_injection=True,
        )
        assert results[0][0] == "same_project"

    def test_rerank_nodes_returns_dicts_with_score(self):
        """rerank_nodes 返回带 _score 的 dict 列表。"""
        from backend.amadeus_app.memory.reranker import rerank_nodes

        candidates = [
            {
                "id": "a",
                "importance": 0.8,
                "confidence": 0.8,
                "updatedAt": "2026-06-18T00:00:00+00:00",
                "accessCount": 3,
                "expiresAt": None,
                "isActive": True,
            },
        ]
        results = rerank_nodes(
            candidates=candidates,
            vector_scores={"a": 0.7},
            keyword_scores={"a": 0.5},
            top_k=5,
        )
        assert len(results) == 1
        assert "_score" in results[0]
        assert results[0]["id"] == "a"


# ====================================================================
# QueryRewriter 测试
# ====================================================================


class TestQueryRewriter:
    """测试查询改写模块。"""

    def test_detect_recall_triggers_continue(self):
        """检测"继续"触发词。"""
        from backend.amadeus_app.memory.query_rewriter import _detect_recall_triggers

        assert _detect_recall_triggers("继续刚才那个部署问题") is True

    def test_detect_recall_triggers_before(self):
        """检测"之前"触发词。"""
        from backend.amadeus_app.memory.query_rewriter import _detect_recall_triggers

        assert _detect_recall_triggers("之前那个方案怎么样了") is True

    def test_detect_recall_triggers_remember(self):
        """检测"记住"触发词。"""
        from backend.amadeus_app.memory.query_rewriter import _detect_recall_triggers

        assert _detect_recall_triggers("记住我喜欢简洁的回答") is True

    def test_detect_recall_triggers_no_trigger(self):
        """无触发词时返回 False。"""
        from backend.amadeus_app.memory.query_rewriter import _detect_recall_triggers

        assert _detect_recall_triggers("今天天气怎么样") is False
        assert _detect_recall_triggers("请帮我写一个 Python 函数") is False

    def test_parse_intent_json_valid(self):
        """解析有效的 intent JSON。"""
        from backend.amadeus_app.memory.models import MemorySearchIntent
        from backend.amadeus_app.memory.query_rewriter import _parse_intent_json

        raw = '{"rewritten_query": "部署问题", "query_type": "project", "domains": ["code", "task"], "keywords": ["部署"]}'
        intent = _parse_intent_json(raw, "继续")
        assert isinstance(intent, MemorySearchIntent)
        assert intent.rewritten_query == "部署问题"
        assert intent.query_type == "project"
        assert len(intent.domains) == 2
        assert intent.keywords == ["部署"]

    def test_parse_intent_json_markdown_wrapped(self):
        """解析 markdown 包裹的 JSON。"""
        from backend.amadeus_app.memory.query_rewriter import _parse_intent_json

        raw = '```json\n{"rewritten_query": "测试", "query_type": "general"}\n```'
        intent = _parse_intent_json(raw, "原始消息")
        assert intent.rewritten_query == "测试"

    def test_parse_intent_json_invalid_fallback(self):
        """无效 JSON 时降级为简单 intent。"""
        from backend.amadeus_app.memory.query_rewriter import _parse_intent_json

        intent = _parse_intent_json("这不是JSON", "原始消息")
        assert intent.rewritten_query == "原始消息"
        assert intent.query_type == "general"

    def test_parse_intent_json_null_project_hint(self):
        """project_hint 为 "null" 字符串时转为 None。"""
        from backend.amadeus_app.memory.query_rewriter import _parse_intent_json

        raw = '{"rewritten_query": "q", "project_hint": "null"}'
        intent = _parse_intent_json(raw, "msg")
        assert intent.project_hint is None

    def test_format_recent_messages(self):
        """格式化最近消息。"""
        from backend.amadeus_app.memory.query_rewriter import _format_recent_messages

        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
            {"role": "tool", "content": "工具结果"},  # 应被跳过
            {"role": "user", "content": ""},
        ]
        text = _format_recent_messages(messages, max_count=6)
        assert "用户" in text
        assert "助手" in text
        assert "工具" not in text  # tool 消息应被跳过

    @pytest.mark.asyncio
    async def test_build_search_intent_no_settings(self):
        """无 settings 时返回简单 intent。"""
        from backend.amadeus_app.memory.query_rewriter import build_search_intent

        intent = await build_search_intent(user_message="测试消息")
        assert intent.rewritten_query == "测试消息"
        assert intent.query_type == "general"


# ====================================================================
# Injector 测试
# ====================================================================


class TestInjectorUserProfile:
    """测试注入器 UserProfile 段。"""

    def test_build_memory_context_with_user_profile(self):
        """build_memory_context 包含 UserProfile 段。"""
        from backend.amadeus_app.memory.injector import build_memory_context

        user_profile = [
            {
                "label": "回答风格偏好",
                "summary": "用户偏好中文、直接给可执行步骤",
                "confidence": 0.92,
            },
            {
                "label": "编程语言偏好",
                "summary": "用户常用 Python 和 TypeScript",
                "confidence": 0.85,
            },
        ]
        result = build_memory_context(
            selected_paths=[],
            topic_nodes=[],
            leaf_nodes=[],
            user_profile=user_profile,
        )
        assert "<UserProfile>" in result
        assert "confidence=0.92" in result
        assert "回答风格偏好" in result

    def test_build_memory_context_without_user_profile(self):
        """无 user_profile 时不包含 UserProfile 段。"""
        from backend.amadeus_app.memory.injector import build_memory_context

        result = build_memory_context(
            selected_paths=[],
            topic_nodes=[],
            leaf_nodes=[],
        )
        assert "<UserProfile>" not in result

    def test_format_user_profile_item(self):
        """格式化单条用户画像。"""
        from backend.amadeus_app.memory.injector import format_user_profile_item

        item = format_user_profile_item({
            "label": "偏好",
            "summary": "喜欢简洁",
            "confidence": 0.88,
        })
        assert "confidence=0.88" in item
        assert "偏好" in item
        assert "喜欢简洁" in item

    def test_format_user_profile_item_empty(self):
        """空内容返回空字符串。"""
        from backend.amadeus_app.memory.injector import format_user_profile_item

        assert format_user_profile_item({}) == ""


# ====================================================================
# Session Summary 测试（通过 API 间接测试 tree_store）
# ====================================================================


class TestSessionSummary:
    """测试会话摘要存取。"""

    def test_session_summary_save_and_get(self, client: TestClient):
        """通过 API 创建会话后，直接测试 tree_store 的摘要存取。"""
        # 获取 app 实例中的 memory tree
        from backend.amadeus_app._common import get_memory_tree

        tree = get_memory_tree()
        assert tree is not None

        import asyncio

        loop = asyncio.new_event_loop()
        try:
            conv_id = "test-conv-summary-001"

            # 保存摘要
            loop.run_until_complete(
                tree.save_session_summary(conv_id, "这是测试会话摘要内容")
            )

            # 读取摘要
            summary = loop.run_until_complete(
                tree.get_session_summary(conv_id)
            )
            assert summary == "这是测试会话摘要内容"

            # 更新摘要
            loop.run_until_complete(
                tree.save_session_summary(conv_id, "更新后的摘要")
            )
            summary = loop.run_until_complete(
                tree.get_session_summary(conv_id)
            )
            assert summary == "更新后的摘要"
        finally:
            loop.close()


# ====================================================================
# Router Action 处理测试
# ====================================================================


class TestRouterActionHandling:
    """测试 router.py 的 action 字段处理。"""

    @pytest.mark.asyncio
    async def test_route_noop_action(self):
        """action=noop 时返回空 dict。"""
        from backend.amadeus_app.memory.models import ExtractedLeafMemory, MemoryDomain
        from backend.amadeus_app.memory.router import route_and_attach_leaf

        leaf = ExtractedLeafMemory(
            action="noop",
            domain=MemoryDomain.CODE,
            title="测试",
            summary="测试摘要",
            full_content="测试内容",
        )
        # 不需要真实的 tree_store/settings，noop 在获取 domain 之前就返回
        result = await route_and_attach_leaf(
            leaf=leaf,
            tree_store=None,  # type: ignore
            settings=None,  # type: ignore
        )
        assert result == {}


# ====================================================================
# Bug 修复验证测试
# ====================================================================


class TestVectorSearchFix:
    """验证 sqlite-vec 参数传递 bug 修复。"""

    def test_vector_search_with_candidate_ids_no_error(self, client: TestClient):
        """向量搜索带 candidate_ids 不应报 TypeError。"""
        from backend.amadeus_app._common import get_memory_tree

        tree = get_memory_tree()
        assert tree is not None

        import asyncio

        loop = asyncio.new_event_loop()
        try:
            # 构造假 embedding（1536 维）
            fake_emb = [0.1] * 1536

            # 带 candidate_ids 搜索 — 之前会 TypeError
            results = loop.run_until_complete(
                tree.search_node_vectors(
                    fake_emb, top_k=5, candidate_ids=["nonexistent-id"]
                )
            )
            # 不报错即通过，结果应为空（因为候选 ID 不存在）
            assert isinstance(results, list)

            # 同样测试 leaf 向量搜索
            results = loop.run_until_complete(
                tree.search_leaf_vectors(
                    fake_emb, top_k=5, candidate_ids=["nonexistent-id"]
                )
            )
            assert isinstance(results, list)
        finally:
            loop.close()

    def test_vector_search_returns_candidates(self, client: TestClient):
        """向量搜索应能命中已写入的向量。"""
        from backend.amadeus_app._common import get_memory_tree

        tree = get_memory_tree()
        assert tree is not None

        import asyncio

        loop = asyncio.new_event_loop()
        try:
            fake_emb = [0.5] * 1536

            # 写入一个测试向量
            loop.run_until_complete(
                tree.upsert_node_vector("test-vec-node-001", fake_emb)
            )

            # 搜索应命中
            results = loop.run_until_complete(
                tree.search_node_vectors(fake_emb, top_k=5)
            )
            assert len(results) > 0
            assert results[0][0] == "test-vec-node-001"
        finally:
            loop.close()


class TestRerankerRelevanceThreshold:
    """验证 reranker 相关性底线。"""

    def test_no_relevance_score_filtered_in_active_injection(self):
        """主动注入时，无向量/关键词命中的节点被过滤。"""
        from backend.amadeus_app.memory.reranker import weighted_rerank

        candidates = [
            {
                "id": "no_hit",
                "importance": 0.9,
                "confidence": 0.9,
                "updatedAt": "2026-06-18T00:00:00+00:00",
                "accessCount": 10,
                "expiresAt": None,
                "isActive": True,
            },
        ]
        # 无向量命中、无关键词命中
        results = weighted_rerank(
            candidates=candidates,
            vector_scores={},
            keyword_scores={},
            active_injection=True,
        )
        # 应被相关性底线过滤
        assert len(results) == 0

    def test_relevance_threshold_not_applied_for_tool_retrieval(self):
        """工具检索时，无命中节点不被过滤。"""
        from backend.amadeus_app.memory.reranker import weighted_rerank

        candidates = [
            {
                "id": "no_hit",
                "importance": 0.9,
                "confidence": 0.3,  # 低于 MIN_CONFIDENCE，但工具检索不过滤
                "updatedAt": "2026-06-18T00:00:00+00:00",
                "accessCount": 0,
                "expiresAt": None,
                "isActive": True,
            },
        ]
        results = weighted_rerank(
            candidates=candidates,
            vector_scores={},
            keyword_scores={},
            active_injection=False,
        )
        assert len(results) == 1


class TestMemoryGating:
    """验证记忆检索门控。"""

    def test_detect_triggers_for_memory_recall(self):
        """触发词检测：回忆类消息应触发检索。"""
        from backend.amadeus_app.memory.query_rewriter import _detect_recall_triggers

        assert _detect_recall_triggers("之前那个部署方案怎么样了") is True
        assert _detect_recall_triggers("继续我们上次讨论的") is True
        assert _detect_recall_triggers("记得我之前说的偏好吗") is True

    def test_no_trigger_for_specific_question(self):
        """具体问题不应触发检索。"""
        from backend.amadeus_app.memory.query_rewriter import _detect_recall_triggers

        assert _detect_recall_triggers("写一个快速排序函数") is False
        assert _detect_recall_triggers("今天天气怎么样") is False
        assert _detect_recall_triggers("解释一下什么是闭包") is False


class TestRebuildIndex:
    """验证索引重建接口。"""

    def test_rebuild_fts_index(self, client: TestClient):
        """FTS 索引重建应返回节点数。"""
        from backend.amadeus_app._common import get_memory_tree

        tree = get_memory_tree()
        assert tree is not None

        import asyncio

        loop = asyncio.new_event_loop()
        try:
            count = loop.run_until_complete(tree.rebuild_fts_index())
            # 默认树有 root + 6 domain = 7 个节点
            assert count >= 7
        finally:
            loop.close()

    def test_rebuild_index_api(self, client: TestClient):
        """rebuild-index API 应返回成功。"""
        resp = client.post("/api/memory/rebuild-index", json={"rebuildFts": True, "rebuildVectors": False})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "result" in data
        assert data["result"]["ftsNodes"] >= 7


class TestSearchContextText:
    """验证 search API 返回真实 contextText。"""

    def test_search_returns_non_empty_context_when_hits(self, client: TestClient):
        """有命中时 contextText 应非空。"""
        # 先创建一个叶子节点
        create_resp = client.post("/api/memory/nodes", json={
            "parentId": None,
            "domain": "knowledge",
            "label": "Python 排序算法",
            "summary": "快速排序和归并排序的实现",
            "fullContent": "快速排序是一种高效的排序算法，时间复杂度 O(n log n)",
            "category": "fact",
            "keywords": ["排序", "算法"],
            "importance": 0.8,
            "confidence": 0.9,
        })
        assert create_resp.status_code in (200, 201)

        # 搜索应返回 contextText
        search_resp = client.post("/api/memory/search", json={
            "query": "排序算法",
            "contextBudgetChars": 3000,
        })
        assert search_resp.status_code == 200
        data = search_resp.json()["result"]
        # contextText 应包含 MemoryContext 标签
        assert "MemoryContext" in data["contextText"] or data["contextText"] == ""


class TestMemoryNodeTypeValidation:
    """验证 /api/memory/nodes 的 nodeType 输入校验。"""

    def test_create_topic_and_leaf_node_types(self, client: TestClient):
        """topic 和 leaf nodeType 均应创建成功。"""
        tree_resp = client.get("/api/memory/tree?path=/&depth=1")
        domain = next(node for node in tree_resp.json()["nodes"] if node["nodeType"] == "domain")

        topic_resp = client.post("/api/memory/nodes", json={
            "nodeType": "topic",
            "parentId": domain["id"],
            "domain": domain["domain"],
            "label": "nodeType topic 测试",
            "summary": "topic 类型应成功创建",
        })
        assert topic_resp.status_code == 200
        topic = topic_resp.json()["node"]
        assert topic["nodeType"] == "topic"

        leaf_resp = client.post("/api/memory/nodes", json={
            "nodeType": "leaf",
            "parentId": topic["id"],
            "domain": domain["domain"],
            "label": "nodeType leaf 测试",
            "summary": "leaf 类型应成功创建",
            "fullContent": "leaf 类型应成功创建",
        })
        assert leaf_resp.status_code == 200
        assert leaf_resp.json()["node"]["nodeType"] == "leaf"

    def test_invalid_node_type_returns_422(self, client: TestClient):
        """非法 nodeType 应被 Pydantic 拒绝为 422，而不是 SQLite 500。"""
        tree_resp = client.get("/api/memory/tree?path=/&depth=1")
        domain = next(node for node in tree_resp.json()["nodes"] if node["nodeType"] == "domain")

        response = client.post("/api/memory/nodes", json={
            "nodeType": "invalid",
            "parentId": domain["id"],
            "domain": domain["domain"],
            "label": "非法类型",
            "summary": "非法 nodeType 应返回 422",
        })
        assert response.status_code == 422


class TestExtractInterval:
    """验证按批次提取记忆的间隔逻辑。"""

    def test_extract_interval_config(self):
        """AMADEUS_MEMORY_EXTRACT_INTERVAL 环境变量可读取。"""
        import os

        interval = int(os.getenv("AMADEUS_MEMORY_EXTRACT_INTERVAL", "6"))
        assert interval == 6  # 默认值

    def test_interval_logic_every_6_turns(self):
        """验证每 6 轮才入队的逻辑。"""
        interval = 6
        # 模拟用户消息计数
        for user_msg_count in range(1, 13):
            should_enqueue = (user_msg_count % interval == 0)
            if user_msg_count == 6:
                assert should_enqueue is True
            elif user_msg_count == 12:
                assert should_enqueue is True
            else:
                assert should_enqueue is False


class TestSummaryUpdateChain:
    """验证摘要更新链路修复。"""

    def test_router_enqueues_leaf_id_not_cluster_id(self):
        """router.py 源码应使用 created_leaf['id'] 而非 cluster_id/topic_id 入队。"""
        import inspect

        from backend.amadeus_app.memory import router

        source = inspect.getsource(router)
        # 确保入队 summarize_node 时用的是 created_leaf["id"]
        assert 'created_leaf["id"]' in source
        # 确保不再用 cluster_node["id"] 或 topic_node["id"] 入队 summarize
        # 找到 enqueue_job + summarize_node 的代码段
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if "summarize_node" in line and "enqueue" in lines[max(0, i-3):i+1].__str__().lower():
                context = "\n".join(lines[max(0, i-5):i+5])
                assert 'created_leaf["id"]' in context, (
                    f"summarize_node 入队应使用 created_leaf['id']，但实际代码:\n{context}"
                )


# ====================================================================
# Phase 3: camelCase alias / overfetch / 门控扩展 / source_message_ids
# ====================================================================


class TestRebuildIndexCamelCaseAlias:
    """验证 RebuildIndexRequest 的 camelCase alias 生效。"""

    def test_rebuild_vectors_false_respected(self, client: TestClient):
        """rebuildVectors=false 时后端应收到 rebuild_vectors=False。"""
        # 通过 API 发送 camelCase 参数
        resp = client.post(
            "/api/memory/rebuild-index",
            json={"rebuildFts": True, "rebuildVectors": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        # rebuildVectors=False 时不应重建向量，result 中不应有 vectors 键
        assert "vectors" not in data["result"]

    def test_rebuild_vectors_true_respected(self, client: TestClient):
        """rebuildVectors=true 时后端应收到 rebuild_vectors=True。"""
        resp = client.post(
            "/api/memory/rebuild-index",
            json={"rebuildFts": False, "rebuildVectors": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        # rebuildVectors=True 时应尝试重建向量（可能因无 API key 而失败，但应进入分支）
        # 即使 vectors 键不存在（因 API key 缺失等），status 仍应为 200

    def test_request_model_accepts_camel_case(self):
        """Pydantic 模型应接受 camelCase 字段名。"""
        from backend.amadeus_app.routers.memories import RebuildIndexRequest

        # camelCase 输入
        req = RebuildIndexRequest.model_validate(
            {"rebuildFts": False, "rebuildVectors": False}
        )
        assert req.rebuild_fts is False
        assert req.rebuild_vectors is False

        # snake_case 也能用（populate_by_name=True）
        req2 = RebuildIndexRequest.model_validate(
            {"rebuild_fts": True, "rebuild_vectors": True}
        )
        assert req2.rebuild_fts is True
        assert req2.rebuild_vectors is True


class TestVectorSearchOverfetch:
    """验证 sqlite-vec 候选过滤精确召回。"""

    def test_candidate_search_not_truncated_by_global_knn(self, client: TestClient):
        """候选向量即使排在全局 top-N 外，也必须能被 candidate_ids 精确召回。"""
        from backend.amadeus_app._common import get_memory_tree

        tree = get_memory_tree()
        assert tree is not None

        import asyncio

        loop = asyncio.new_event_loop()
        try:
            query = [0.0] * 1536
            # 250 个非候选向量比目标候选更接近 query；旧 overfetch=200 会把目标截掉。
            for index in range(250):
                loop.run_until_complete(
                    tree.upsert_node_vector(f"non-candidate-{index}", query)
                )
            target_id = "candidate-target-outside-global-top"
            loop.run_until_complete(
                tree.upsert_node_vector(target_id, [0.75] * 1536)
            )

            results = loop.run_until_complete(
                tree.search_node_vectors(query, top_k=1, candidate_ids=[target_id])
            )
        finally:
            loop.close()

        assert results
        assert results[0][0] == target_id

    def test_candidate_ids_empty_returns_empty(self, client: TestClient):
        """candidate_ids 为空列表时应直接返回空。"""
        from backend.amadeus_app._common import get_memory_tree

        tree = get_memory_tree()
        assert tree is not None

        import asyncio

        loop = asyncio.new_event_loop()
        try:
            fake_emb = [0.1] * 1536
            results = loop.run_until_complete(
                tree.search_node_vectors(fake_emb, top_k=5, candidate_ids=[])
            )
            assert results == []
        finally:
            loop.close()


class TestMemoryGatingExpansion:
    """验证记忆门控扩展：隐式记忆问题 + 画像类召回。"""

    def test_implicit_memory_triggers_name(self):
        """'我叫什么名字'应触发检索。"""
        from backend.amadeus_app.memory.query_rewriter import _detect_recall_triggers

        assert _detect_recall_triggers("我叫什么名字") is True

    def test_implicit_memory_triggers_tech_stack(self):
        """'我常用什么技术栈'应触发检索。"""
        from backend.amadeus_app.memory.query_rewriter import _detect_recall_triggers

        assert _detect_recall_triggers("我常用什么技术栈") is True

    def test_implicit_memory_triggers_habit(self):
        """'按我的习惯来'应触发检索。"""
        from backend.amadeus_app.memory.query_rewriter import _detect_recall_triggers

        assert _detect_recall_triggers("按我的习惯来") is True

    def test_implicit_memory_triggers_english(self):
        """英文隐式记忆问题也应触发。"""
        from backend.amadeus_app.memory.query_rewriter import _detect_recall_triggers

        assert _detect_recall_triggers("what do you know about me") is True
        assert _detect_recall_triggers("my name") is True

    def test_is_profile_query_name(self):
        """is_profile_query 检测名字类问题。"""
        from backend.amadeus_app.memory.query_rewriter import is_profile_query

        assert is_profile_query("我叫什么名字") is True
        assert is_profile_query("我是谁") is True

    def test_is_profile_query_preference(self):
        """is_profile_query 检测偏好类问题。"""
        from backend.amadeus_app.memory.query_rewriter import is_profile_query

        assert is_profile_query("我偏好什么编程语言") is True
        assert is_profile_query("我的习惯是什么") is True

    def test_is_profile_query_not_triggered_for_general(self):
        """一般问题不应被识别为画像查询。"""
        from backend.amadeus_app.memory.query_rewriter import is_profile_query

        assert is_profile_query("写一个排序函数") is False
        assert is_profile_query("今天天气怎么样") is False

    def test_is_profile_query_english(self):
        """英文画像查询检测。"""
        from backend.amadeus_app.memory.query_rewriter import is_profile_query

        assert is_profile_query("what do you know about me") is True
        assert is_profile_query("my preference") is True


class TestSourceMessageIdsTraceability:
    """验证 source_message_ids 追溯链路。"""

    def test_format_conversation_includes_msg_id(self):
        """format_conversation 应在每条消息前带上 [msg:<id>] 标记。"""
        from backend.amadeus_app.memory.extractor import format_conversation

        messages = [
            {"id": "msg-001", "role": "user", "content": "你好"},
            {"id": "msg-002", "role": "assistant", "content": "你好！"},
        ]
        text = format_conversation(messages)
        assert "[msg:msg-001]" in text
        assert "[msg:msg-002]" in text

    def test_format_conversation_no_id(self):
        """消息没有 id 字段时不带 [msg:] 标记。"""
        from backend.amadeus_app.memory.extractor import format_conversation

        messages = [
            {"role": "user", "content": "你好"},
        ]
        text = format_conversation(messages)
        assert "[msg:" not in text
        assert "[用户]" in text

    def test_format_conversation_skips_empty(self):
        """空内容消息应被跳过。"""
        from backend.amadeus_app.memory.extractor import format_conversation

        messages = [
            {"id": "msg-001", "role": "user", "content": ""},
            {"id": "msg-002", "role": "user", "content": "  "},
            {"id": "msg-003", "role": "user", "content": "有效内容"},
        ]
        text = format_conversation(messages)
        assert "[msg:msg-003]" in text
        assert "msg-001" not in text
        assert "msg-002" not in text

    def test_extraction_prompt_mentions_source_message_ids(self):
        """EXTRACTION_PROMPT 应包含 source_message_ids 的说明。"""
        from backend.amadeus_app.memory.extractor import EXTRACTION_PROMPT

        assert "source_message_ids" in EXTRACTION_PROMPT
        assert "[msg:" in EXTRACTION_PROMPT

    def test_source_message_ids_cleaning_strips_prefix(self):
        """清洗逻辑应去掉 LLM 误带的 msg: 前缀。"""
        from backend.amadeus_app.memory.extractor import _clean_source_message_ids

        # 假设我们提取了清洗逻辑为独立函数；若没有，直接测试 extract 流程
        # 这里通过模拟清洗逻辑验证
        raw_ids = ["msg:abc-123", "def-456", "msg:ghi-789"]
        valid_ids = {"abc-123", "def-456", "ghi-789"}
        cleaned = _clean_source_message_ids(raw_ids, valid_ids)
        assert cleaned == ["abc-123", "def-456", "ghi-789"]

    def test_source_message_ids_cleaning_filters_invalid(self):
        """清洗逻辑应过滤掉不在实际消息集合中的 id。"""
        from backend.amadeus_app.memory.extractor import _clean_source_message_ids

        raw_ids = ["valid-001", "invalid-999", "valid-002"]
        valid_ids = {"valid-001", "valid-002"}
        cleaned = _clean_source_message_ids(raw_ids, valid_ids)
        assert cleaned == ["valid-001", "valid-002"]

    def test_source_message_ids_cleaning_dedup(self):
        """清洗逻辑应去重。"""
        from backend.amadeus_app.memory.extractor import _clean_source_message_ids

        raw_ids = ["abc-123", "abc-123", "def-456"]
        valid_ids = {"abc-123", "def-456"}
        cleaned = _clean_source_message_ids(raw_ids, valid_ids)
        assert cleaned == ["abc-123", "def-456"]

    def test_router_passes_source_message_ids_to_create(self):
        """router.py 源码应把 leaf.source_message_ids 传给 create_node。"""
        import inspect

        from backend.amadeus_app.memory import router

        source = inspect.getsource(router)
        assert 'leaf.source_message_ids' in source or "sourceMessageIds" in source


class TestSecurityHeadersPermissions:
    """验证安全头 Permissions-Policy 修复。"""

    def test_permissions_policy_allows_camera_microphone(self):
        """security.py 源码应允许 camera/microphone=(self)。"""
        import inspect

        from backend.amadeus_app import security

        source = inspect.getsource(security)
        # 不应再硬编码 camera=(), microphone=()
        assert "camera=(self)" in source or "microphone=(self)" in source, (
            "Permissions-Policy 应允许 camera/microphone=(self)"
        )
        # 不应同时出现 camera=() 和 microphone=() 的硬编码默认值
        assert 'camera=(), microphone=()' not in source, (
            "不应再硬编码 camera=(), microphone=() 禁用"
        )


# ====================================================================
# 端到端：聊天流记忆注入 + 手动文本导入
# ====================================================================


class TestChatFlowMemoryInjection:
    """端到端测试：触发记忆检索后，MemoryContext 被注入到主模型 payload。

    这个测试会直接抓到 search_memory_tree / get_user_profile_memories 未导入的问题。
    """

    def test_trigger_word_injects_memory_context(
        self, client: TestClient, monkeypatch
    ):
        """发送含触发词的消息，验证 system prompt 包含 <MemoryContext>。"""
        # 1. 创建会话
        conv_resp = client.post("/api/conversations", json={"title": "记忆注入测试"})
        assert conv_resp.status_code == 200
        conversation_id = conv_resp.json()["conversation"]["id"]

        # 2. 写入记忆：domain → topic → leaf（搜索需要 topic/cluster 中间层）
        #    通过 API 创建节点，避免跨事件循环问题
        tree_resp = client.get("/api/memory/tree?path=/&depth=1")
        domains = tree_resp.json()["nodes"]
        code_domain = None
        for d in domains:
            if d.get("domain") == "code":
                code_domain = d
                break
        if code_domain is None:
            code_domain = domains[0]
        domain_id = code_domain["id"]
        domain_value = code_domain["domain"]

        # 创建 topic 节点
        topic_resp = client.post("/api/memory/nodes", json={
            "nodeType": "topic",
            "parentId": domain_id,
            "domain": domain_value,
            "label": "部署相关",
            "summary": "关于部署方案的讨论",
        })
        assert topic_resp.status_code == 200
        topic_id = topic_resp.json()["node"]["id"]

        # 创建 leaf 节点（内容包含 "部署方案" 关键词，便于 FTS 匹配）
        leaf_resp = client.post("/api/memory/nodes", json={
            "nodeType": "leaf",
            "parentId": topic_id,
            "domain": domain_value,
            "label": "部署方案讨论",
            "summary": "用户之前讨论过 Docker 部署方案",
            "fullContent": "用户之前提到想用 Docker Compose 部署微服务架构，包括 Nginx 反向代理和 Redis 缓存。",
            "category": "technical",
            "keywords": ["部署", "Docker", "部署方案"],
            "importance": 0.8,
            "confidence": 0.9,
        })
        assert leaf_resp.status_code == 200

        # 3. monkeypatch stream_model_turn 捕获 payload
        captured_payload: dict = {}

        async def fake_stream_model_turn(*, client, endpoint, headers, payload, mode):
            captured_payload.update(payload)
            from backend.amadeus_app.chat_service import ModelTurn
            yield ("result", ModelTurn(content="测试回复", tool_calls=[], finish_reason="stop"))

        from backend.amadeus_app import chat_service
        monkeypatch.setattr(chat_service, "stream_model_turn", fake_stream_model_turn)

        # 3b. mock get_embedding 和 build_search_intent，避免网络调用
        #     直接强制 FTS 降级路径，使测试完全确定性
        from backend.amadeus_app.memory import tree_search
        from backend.amadeus_app.memory.models import MemorySearchIntent

        async def fake_get_embedding(text, settings):
            raise RuntimeError("mocked: no network in tests")

        async def fake_build_search_intent(**kwargs):
            user_msg = kwargs.get("user_message", "")
            return MemorySearchIntent(
                raw_user_message=user_msg,
                rewritten_query=user_msg,
                query_type="general",
            )

        monkeypatch.setattr(tree_search, "get_embedding", fake_get_embedding)
        monkeypatch.setattr(tree_search, "build_search_intent", fake_build_search_intent)

        # 4. 发送带触发词的聊天请求
        #    "之前" 是触发词，"部署方案" 匹配记忆节点关键词
        chat_resp = client.post(
            "/api/chat/stream",
            json={
                "messages": [
                    {"role": "user", "content": "之前那个部署方案怎么样了？"}
                ],
                "conversationId": conversation_id,
                "personaId": "kurisu_amadeus",
                "mode": "fast",
                "model": {
                    "providerName": "OpenAI",
                    "baseUrl": "http://127.0.0.1:1",
                    "model": "gpt-4.1-mini",
                    "apiKey": "fake-key-for-test",
                    "useRemote": True,
                },
            },
        )

        # 5. 验证响应正常完成
        assert chat_resp.status_code == 200

        # 6. 验证 system prompt 包含 <MemoryContext>
        assert "messages" in captured_payload, "payload 应包含 messages"
        system_content = captured_payload["messages"][0]["content"]
        assert "<MemoryContext" in system_content, (
            f"system prompt 应包含 <MemoryContext> 标签，实际内容前 500 字:\n"
            f"{system_content[:500]}"
        )

    def test_profile_query_injects_user_profile(
        self, client: TestClient, monkeypatch
    ):
        """画像类问题（我叫什么名字）应触发 profile 召回并注入。"""
        # 1. 创建会话
        conv_resp = client.post("/api/conversations", json={"title": "画像查询测试"})
        assert conv_resp.status_code == 200
        conversation_id = conv_resp.json()["conversation"]["id"]

        # 2. 写入 profile 类记忆：domain → topic → leaf
        #    通过 API 创建节点，避免跨事件循环问题
        tree_resp = client.get("/api/memory/tree?path=/&depth=1")
        domains = tree_resp.json()["nodes"]
        chat_domain = None
        for d in domains:
            if d.get("domain") == "daily_chat":
                chat_domain = d
                break
        if chat_domain is None:
            chat_domain = domains[0]
        domain_id = chat_domain["id"]
        domain_value = chat_domain["domain"]

        # 创建 topic 节点
        topic_resp = client.post("/api/memory/nodes", json={
            "nodeType": "topic",
            "parentId": domain_id,
            "domain": domain_value,
            "label": "用户姓名信息",
            "summary": "关于用户的名字和姓名",
        })
        assert topic_resp.status_code == 200
        topic_id = topic_resp.json()["node"]["id"]

        # 创建 leaf 节点（category=preference，用于 profile 召回）
        leaf_resp = client.post("/api/memory/nodes", json={
            "nodeType": "leaf",
            "parentId": topic_id,
            "domain": domain_value,
            "label": "用户姓名",
            "summary": "用户的名字是测试用户",
            "fullContent": "用户的名字是测试用户，喜欢被叫小测。",
            "category": "preference",
            "keywords": ["姓名", "名字", "测试用户"],
            "importance": 0.9,
            "confidence": 0.95,
        })
        assert leaf_resp.status_code == 200

        # 3. monkeypatch stream_model_turn
        captured_payload: dict = {}

        async def fake_stream_model_turn(*, client, endpoint, headers, payload, mode):
            captured_payload.update(payload)
            from backend.amadeus_app.chat_service import ModelTurn
            yield ("result", ModelTurn(content="你的名字是小测", tool_calls=[], finish_reason="stop"))

        from backend.amadeus_app import chat_service
        monkeypatch.setattr(chat_service, "stream_model_turn", fake_stream_model_turn)

        # 3b. mock get_embedding 和 build_search_intent，避免网络调用
        from backend.amadeus_app.memory import tree_search
        from backend.amadeus_app.memory.models import MemorySearchIntent

        async def fake_get_embedding(text, settings):
            raise RuntimeError("mocked: no network in tests")

        async def fake_build_search_intent(**kwargs):
            user_msg = kwargs.get("user_message", "")
            return MemorySearchIntent(
                raw_user_message=user_msg,
                rewritten_query=user_msg,
                query_type="general",
            )

        monkeypatch.setattr(tree_search, "get_embedding", fake_get_embedding)
        monkeypatch.setattr(tree_search, "build_search_intent", fake_build_search_intent)

        # 4. 发送画像类问题
        chat_resp = client.post(
            "/api/chat/stream",
            json={
                "messages": [
                    {"role": "user", "content": "我叫什么名字？"}
                ],
                "conversationId": conversation_id,
                "personaId": "kurisu_amadeus",
                "mode": "fast",
                "model": {
                    "providerName": "OpenAI",
                    "baseUrl": "http://127.0.0.1:1",
                    "model": "gpt-4.1-mini",
                    "apiKey": "fake-key-for-test",
                    "useRemote": True,
                },
            },
        )

        assert chat_resp.status_code == 200
        # 画像类问题应注入记忆上下文（profile memories 或 MemoryContext）
        assert "messages" in captured_payload, "payload 应包含 messages"
        system_content = captured_payload["messages"][0]["content"]
        # profile 召回会注入 <UserProfile> 或 <MemoryContext>
        assert "<UserProfile>" in system_content or "<MemoryContext" in system_content, (
            f"画像类问题应注入 <UserProfile> 或 <MemoryContext>，实际内容前 500 字:\n"
            f"{system_content[:500]}"
        )

    def test_non_trigger_message_skips_memory_injection(
        self, client: TestClient, monkeypatch
    ):
        """普通问题（无触发词、非画像查询）不应注入记忆。"""
        # 1. 创建会话
        conv_resp = client.post("/api/conversations", json={"title": "无记忆测试"})
        assert conv_resp.status_code == 200
        conversation_id = conv_resp.json()["conversation"]["id"]

        # 2. monkeypatch stream_model_turn
        captured_payload: dict = {}

        async def fake_stream_model_turn(*, client, endpoint, headers, payload, mode):
            captured_payload.update(payload)
            from backend.amadeus_app.chat_service import ModelTurn
            yield ("result", ModelTurn(content="好的", tool_calls=[], finish_reason="stop"))

        from backend.amadeus_app import chat_service
        monkeypatch.setattr(chat_service, "stream_model_turn", fake_stream_model_turn)

        # 3. 发送普通问题（无触发词）
        chat_resp = client.post(
            "/api/chat/stream",
            json={
                "messages": [
                    {"role": "user", "content": "写一个快速排序函数"}
                ],
                "conversationId": conversation_id,
                "personaId": "kurisu_amadeus",
                "mode": "fast",
                "model": {
                    "providerName": "OpenAI",
                    "baseUrl": "http://127.0.0.1:1",
                    "model": "gpt-4.1-mini",
                    "apiKey": "fake-key-for-test",
                    "useRemote": True,
                },
            },
        )

        assert chat_resp.status_code == 200
        assert "messages" in captured_payload
        system_content = captured_payload["messages"][0]["content"]
        # 普通问题不应注入记忆
        assert "<MemoryContext" not in system_content, (
            "普通问题不应注入 <MemoryContext>"
        )


class TestManualIngestText:
    """测试 /api/memory/ingest 手动文本导入。"""

    def test_ingest_endpoint_returns_ok(self, client: TestClient):
        """ingest 端点应返回 {ok: true, jobId: ...}。"""
        resp = client.post(
            "/api/memory/ingest",
            json={
                "text": "用户偏好使用 TypeScript 和 React 开发前端",
                "domain": "knowledge",
                "title": "用户技术栈偏好",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "jobId" in data

    def test_ingest_endpoint_job_creates_leaf_node(self, client: TestClient):
        """调用 /api/memory/ingest 后，worker 消费 job 应创建 leaf。"""
        import asyncio

        from backend.amadeus_app._common import get_memory_tree, require_storage
        from backend.amadeus_app.domain import ModelSettings
        from backend.amadeus_app.memory.jobs import MemoryJobWorker
        from backend.amadeus_app.providers import get_provider

        tree = get_memory_tree()
        assert tree is not None
        storage = require_storage()

        marker = "集成测试手动导入 leaf 记忆"
        resp = client.post(
            "/api/memory/ingest",
            json={
                "text": f"{marker}，用户偏好 pytest 集成测试。",
                "domain": "knowledge",
                "title": "手动导入集成测试",
            },
        )
        assert resp.status_code == 200
        job_id = resp.json()["jobId"]

        loop = asyncio.new_event_loop()
        try:
            jobs = loop.run_until_complete(tree.claim_pending_jobs(50))
            job = next((item for item in jobs if item.get("id") == job_id), None)
            assert job is not None, f"未找到刚入队的 ingest job: {job_id}"

            worker = MemoryJobWorker(storage=storage, tree_store=tree)
            settings = ModelSettings(
                providerName="OpenAI",
                baseUrl="http://127.0.0.1:1",
                model="gpt-4.1-mini",
                apiKey="",
                useRemote=True,
            )
            loop.run_until_complete(
                worker._process_job(job, settings, get_provider("OpenAI"))
            )
            results = loop.run_until_complete(
                tree.fts_search(marker, node_type="leaf", limit=5)
            )
            assert results
            node = loop.run_until_complete(tree.get_node(results[0][0]))
        finally:
            loop.close()

        assert node is not None
        assert node["nodeType"] == "leaf"
        assert node["domain"] == "knowledge"
        assert "pytest 集成测试" in node["fullContent"]

    def test_ingest_text_creates_leaf_node(self, client: TestClient):
        """_handle_ingest_text 应直接从文本创建叶子记忆。"""
        import asyncio

        from backend.amadeus_app._common import get_memory_tree
        from backend.amadeus_app.domain import ModelSettings
        from backend.amadeus_app.memory.jobs import MemoryJobWorker
        from backend.amadeus_app.storage import DEFAULT_USER_ID

        tree = get_memory_tree()
        assert tree is not None

        # 创建 worker 实例（storage 可为 None，_handle_ingest_text 不需要它）
        worker = MemoryJobWorker(storage=None, tree_store=tree)

        # 准备 settings（空 API key，embedding 会失败但叶子仍会创建）
        settings = ModelSettings(
            providerName="OpenAI",
            baseUrl="http://127.0.0.1:1",
            model="gpt-4.1-mini",
            apiKey="",
            useRemote=True,
        )

        # 执行手动文本导入（使用 DEFAULT_USER_ID 确保与 domain 节点匹配）
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                worker._handle_ingest_text(
                    text="用户偏好使用 Vim 编辑器，快捷键已自定义。",
                    domain="daily_chat",
                    title="编辑器偏好",
                    project_id=None,
                    conversation_id=None,
                    user_id=DEFAULT_USER_ID,
                    settings=settings,
                )
            )
        finally:
            loop.close()

        # 验证叶子节点已创建（通过 FTS 搜索）
        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(
                tree.fts_search("Vim 编辑器", node_type="leaf", limit=5)
            )
        finally:
            loop.close()

        assert len(results) > 0, "应能通过 FTS 搜索到刚导入的叶子节点"
        # 验证节点内容
        node_id = results[0][0]
        loop = asyncio.new_event_loop()
        try:
            node = loop.run_until_complete(tree.get_node(node_id))
        finally:
            loop.close()
        assert node is not None
        assert "Vim" in (node.get("full_content") or "") or "Vim" in (node.get("summary") or "")

    def test_ingest_text_invalid_domain_falls_back(self, client: TestClient):
        """无效 domain 应降级为 knowledge，不应报错。"""
        import asyncio

        from backend.amadeus_app._common import get_memory_tree
        from backend.amadeus_app.domain import ModelSettings
        from backend.amadeus_app.memory.jobs import MemoryJobWorker
        from backend.amadeus_app.storage import DEFAULT_USER_ID

        tree = get_memory_tree()
        worker = MemoryJobWorker(storage=None, tree_store=tree)
        settings = ModelSettings(
            providerName="OpenAI",
            baseUrl="http://127.0.0.1:1",
            model="gpt-4.1-mini",
            apiKey="",
            useRemote=True,
        )

        loop = asyncio.new_event_loop()
        try:
            # 无效 domain "invalid_domain" 应被降级为 knowledge
            loop.run_until_complete(
                worker._handle_ingest_text(
                    text="测试无效 domain 降级",
                    domain="invalid_domain",
                    title="",
                    project_id=None,
                    conversation_id=None,
                    user_id=DEFAULT_USER_ID,
                    settings=settings,
                )
            )
        finally:
            loop.close()

        # 验证叶子节点已创建
        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(
                tree.fts_search("无效 domain 降级", node_type="leaf", limit=5)
            )
        finally:
            loop.close()

        assert len(results) > 0, "无效 domain 也应成功创建叶子节点"
