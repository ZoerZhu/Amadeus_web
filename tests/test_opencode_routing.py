"""Tests for OpenCode routing domain models and scoring engine."""
from __future__ import annotations

import pytest
from backend.amadeus_app.complex_agent.domain import (
    AgentSettings,
    OpencodeRoutingConfig,
    OpencodeRoutingDecision,
    OpencodeRoutingRule,
)


class TestRoutingDomainModels:
    def test_opencode_routing_rule_defaults(self):
        rule = OpencodeRoutingRule(id="r1", name="test", weight=10, keywords=["foo"])
        assert rule.match_type == "keyword"
        assert rule.description == ""

    def test_opencode_routing_config_defaults(self):
        config = OpencodeRoutingConfig()
        assert config.enabled is True
        assert config.allow_threshold == 60
        assert config.ambiguous_threshold == 40
        assert config.allow_llm_rejudge is True
        assert config.force_allow_keywords == []
        assert config.force_deny_keywords == []
        assert config.rules == []

    def test_opencode_routing_decision_fields(self):
        decision = OpencodeRoutingDecision(
            allowed=True, score=80, matched_rules=["fix_bug"],
            reason="score above threshold", method="rule",
        )
        assert decision.allowed is True
        assert decision.score == 80
        assert decision.method == "rule"

    def test_agent_settings_has_opencode_routing(self):
        settings = AgentSettings()
        assert hasattr(settings, "opencode_routing")
        assert isinstance(settings.opencode_routing, OpencodeRoutingConfig)

    def test_agent_settings_opencode_routing_serializes_camel_case(self):
        settings = AgentSettings()
        data = settings.model_dump(by_alias=True)
        assert "opencodeRouting" in data
        assert data["opencodeRouting"]["allowThreshold"] == 60
        assert data["opencodeRouting"]["ambiguousThreshold"] == 40

    def test_agent_settings_opencode_routing_round_trip(self):
        settings = AgentSettings()
        settings.opencode_routing.allow_threshold = 70
        data = settings.model_dump(by_alias=True)
        restored = AgentSettings.model_validate(data)
        assert restored.opencode_routing.allow_threshold == 70


from backend.amadeus_app.complex_agent.opencode_routing import (
    compute_opencode_routing_decision,
    default_opencode_routing_config,
)


class TestRoutingScoring:
    def _config(self):
        return default_opencode_routing_config()

    @pytest.mark.asyncio
    async def test_opencode_disabled_hard_off(self):
        decision = await compute_opencode_routing_decision(
            prompt="重构整个项目并跑测试",
            planner_steps=[],
            skill_info=[],
            config=self._config(),
            opencode_enabled=False,
        )
        assert decision.allowed is False
        assert decision.method == "force_deny"

    @pytest.mark.asyncio
    async def test_force_allow_keyword(self):
        decision = await compute_opencode_routing_decision(
            prompt="请用 opencode 修改这个文件",
            planner_steps=[],
            skill_info=[],
            config=self._config(),
            opencode_enabled=True,
        )
        assert decision.allowed is True
        assert decision.method == "force_allow"

    @pytest.mark.asyncio
    async def test_force_deny_keyword_overrides_everything(self):
        config = self._config()
        config.force_deny_keywords = ["nocode"]
        decision = await compute_opencode_routing_decision(
            prompt="用 opencode 重构 nocode 项目",
            planner_steps=[],
            skill_info=[],
            config=config,
            opencode_enabled=True,
        )
        assert decision.allowed is False
        assert decision.method == "force_deny"

    @pytest.mark.asyncio
    async def test_readonly_search_code_denies(self):
        decision = await compute_opencode_routing_decision(
            prompt="帮我查代码里哪里调用了 foo 函数",
            planner_steps=[{"goal": "搜索 foo 的调用位置"}],
            skill_info=[],
            config=self._config(),
            opencode_enabled=True,
        )
        assert decision.allowed is False
        assert decision.score < 40

    @pytest.mark.asyncio
    async def test_explain_implementation_denies(self):
        decision = await compute_opencode_routing_decision(
            prompt="解释一下这个模块是怎么实现的",
            planner_steps=[],
            skill_info=[],
            config=self._config(),
            opencode_enabled=True,
        )
        assert decision.allowed is False

    @pytest.mark.asyncio
    async def test_fix_bug_allows(self):
        decision = await compute_opencode_routing_decision(
            prompt="修复登录页面的 bug，用户点击按钮没反应",
            planner_steps=[{"goal": "修复 bug"}],
            skill_info=[],
            config=self._config(),
            opencode_enabled=True,
        )
        assert decision.allowed is True
        assert decision.score >= 60

    @pytest.mark.asyncio
    async def test_run_tests_allows(self):
        decision = await compute_opencode_routing_decision(
            prompt="帮我跑一下测试",
            planner_steps=[],
            skill_info=[],
            config=self._config(),
            opencode_enabled=True,
        )
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_multi_file_edit_allows(self):
        decision = await compute_opencode_routing_decision(
            prompt="需要跨文件修改，把所有 API 调用都加上错误处理",
            planner_steps=[],
            skill_info=[],
            config=self._config(),
            opencode_enabled=True,
        )
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_ambiguous_zone_no_llm_denies(self):
        config = self._config()
        config.allow_llm_rejudge = False
        # "实现功能" gives +15, base 30 → 45, which is in ambiguous zone (40-59)
        decision = await compute_opencode_routing_decision(
            prompt="实现一个简单的功能",
            planner_steps=[],
            skill_info=[],
            config=config,
            opencode_enabled=True,
        )
        assert decision.allowed is False
        assert config.ambiguous_threshold <= decision.score < config.allow_threshold

    @pytest.mark.asyncio
    async def test_ambiguous_zone_llm_rejudge_allows(self):
        async def fake_llm(prompt, steps, config):
            return True

        config = self._config()
        decision = await compute_opencode_routing_decision(
            prompt="实现一个简单的功能",
            planner_steps=[],
            skill_info=[],
            config=config,
            opencode_enabled=True,
            llm_rejudge_fn=fake_llm,
        )
        assert decision.allowed is True
        assert decision.method == "llm_rejudge"

    @pytest.mark.asyncio
    async def test_ambiguous_zone_llm_rejudge_denies(self):
        async def fake_llm(prompt, steps, config):
            return False

        config = self._config()
        decision = await compute_opencode_routing_decision(
            prompt="实现一个简单的功能",
            planner_steps=[],
            skill_info=[],
            config=config,
            opencode_enabled=True,
            llm_rejudge_fn=fake_llm,
        )
        assert decision.allowed is False
        assert decision.method == "llm_rejudge"

    @pytest.mark.asyncio
    async def test_llm_rejudge_failure_denies(self):
        async def failing_llm(prompt, steps, config):
            raise RuntimeError("LLM unavailable")

        config = self._config()
        decision = await compute_opencode_routing_decision(
            prompt="实现一个简单的功能",
            planner_steps=[],
            skill_info=[],
            config=config,
            opencode_enabled=True,
            llm_rejudge_fn=failing_llm,
        )
        assert decision.allowed is False
        assert decision.method == "llm_rejudge"

    @pytest.mark.asyncio
    async def test_routing_disabled_falls_back_to_allow(self):
        config = self._config()
        config.enabled = False
        decision = await compute_opencode_routing_decision(
            prompt="查代码",
            planner_steps=[],
            skill_info=[],
            config=config,
            opencode_enabled=True,
        )
        assert decision.allowed is True

    @pytest.mark.asyncio
    async def test_planner_steps_contribute_to_text(self):
        decision = await compute_opencode_routing_decision(
            prompt="帮我处理一下",
            planner_steps=[{"goal": "修复 bug 并跑测试"}],
            skill_info=[],
            config=self._config(),
            opencode_enabled=True,
        )
        assert decision.allowed is True
        assert "修 Bug" in decision.matched_rules or "跑测试" in decision.matched_rules

    @pytest.mark.asyncio
    async def test_skill_info_contributes_to_text(self):
        decision = await compute_opencode_routing_decision(
            prompt="帮我处理一下",
            planner_steps=[],
            skill_info=[{"name": "codebase-navigation", "description": "搜索代码"}],
            config=self._config(),
            opencode_enabled=True,
        )
        # "搜索代码" matches "查代码" rule → negative score
        assert decision.score < 40


# ---------------------------------------------------------------------------
# file_writer code-file protection
# ---------------------------------------------------------------------------

import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from backend.amadeus_app.complex_agent.file_writer import (  # noqa: E402
    CODE_FILE_EXTENSIONS,
    make_file_writer_tool,
)
from backend.amadeus_app.storage import SQLiteStorage  # noqa: E402


async def _make_storage_with_task():
    """Return (storage, task_id) with a real task record for FK constraints."""
    from backend.amadeus_app.complex_agent import agent_storage
    s = SQLiteStorage(":memory:")
    await s.connect()
    task = await agent_storage.create_agent_task(
        s,
        user_id="u",
        title="t",
        prompt="p",
        workspace_path=".",
        conversation_id=None,
        active_skill_ids=[],
        settings={"trustMode": True},
        budget={"rounds": 0},
    )
    return s, task["id"]


class TestFileWriterCodeProtection:
    @pytest.mark.asyncio
    async def test_code_write_allowed_writes_py(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage, task_id = await _make_storage_with_task()
            try:
                tool = make_file_writer_tool(
                    storage=storage, task_id=task_id, workspace=tmp,
                    artifact_root=tmp, code_write_allowed_fn=lambda: True,
                )
                result = await tool.handler({"path": "foo.py", "content": "print(1)"})
                assert result["ok"] is True
                assert (Path(tmp) / "foo.py").exists()
            finally:
                await storage.close()

    @pytest.mark.asyncio
    async def test_code_write_blocked_rejects_py(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage, task_id = await _make_storage_with_task()
            try:
                tool = make_file_writer_tool(
                    storage=storage, task_id=task_id, workspace=tmp,
                    artifact_root=tmp, code_write_allowed_fn=lambda: False,
                )
                result = await tool.handler({"path": "foo.py", "content": "print(1)"})
                assert result["ok"] is False
                assert "OpenCode" in result["error"] or "opencode" in result["error"].lower()
                assert not (Path(tmp) / "foo.py").exists()
            finally:
                await storage.close()

    @pytest.mark.asyncio
    async def test_code_write_blocked_allows_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage, task_id = await _make_storage_with_task()
            try:
                tool = make_file_writer_tool(
                    storage=storage, task_id=task_id, workspace=tmp,
                    artifact_root=tmp, code_write_allowed_fn=lambda: False,
                )
                result = await tool.handler({"path": "report.md", "content": "# Hello"})
                assert result["ok"] is True
                assert (Path(tmp) / "report.md").exists()
            finally:
                await storage.close()

    @pytest.mark.asyncio
    async def test_code_write_blocked_allows_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage, task_id = await _make_storage_with_task()
            try:
                tool = make_file_writer_tool(
                    storage=storage, task_id=task_id, workspace=tmp,
                    artifact_root=tmp, code_write_allowed_fn=lambda: False,
                )
                result = await tool.handler({"path": "data.json", "content": "{}"})
                assert result["ok"] is True
            finally:
                await storage.close()

    @pytest.mark.asyncio
    async def test_code_write_blocked_rejects_tsx(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage, task_id = await _make_storage_with_task()
            try:
                tool = make_file_writer_tool(
                    storage=storage, task_id=task_id, workspace=tmp,
                    artifact_root=tmp, code_write_allowed_fn=lambda: False,
                )
                result = await tool.handler({"path": "comp.tsx", "content": "export const X = () => null"})
                assert result["ok"] is False
            finally:
                await storage.close()

    @pytest.mark.asyncio
    async def test_default_code_write_allowed_is_true(self):
        """When code_write_allowed_fn is not passed, default is True (backward compat)."""
        with tempfile.TemporaryDirectory() as tmp:
            storage, task_id = await _make_storage_with_task()
            try:
                tool = make_file_writer_tool(
                    storage=storage, task_id=task_id, workspace=tmp, artifact_root=tmp,
                )
                result = await tool.handler({"path": "foo.py", "content": "print(1)"})
                assert result["ok"] is True
            finally:
                await storage.close()

    def test_code_file_extensions_includes_common_types(self):
        for ext in [".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".vue", ".svelte"]:
            assert ext in CODE_FILE_EXTENSIONS
