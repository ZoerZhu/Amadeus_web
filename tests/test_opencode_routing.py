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

    @pytest.mark.asyncio
    async def test_code_write_blocked_rejects_package_json(self, tmp_path: Path):
        """Build config files (package.json) must be rejected when OpenCode
        is not allowed (P2 fix)."""
        from backend.amadeus_app.complex_agent.file_writer import make_file_writer_tool

        storage, task_id = await _make_storage_with_task()
        try:
            tool = make_file_writer_tool(
                storage=storage, task_id=task_id, workspace=tmp_path,
                artifact_root=tmp_path / "agent_artifacts",
                code_write_allowed_fn=lambda: False,
            )
            result = await tool.handler({"path": "package.json", "content": "{}"})
            assert result["ok"] is False
            assert "OpenCode routing approval" in result["error"]
        finally:
            await storage.close()

    @pytest.mark.asyncio
    async def test_code_write_blocked_rejects_pyproject_toml(self, tmp_path: Path):
        """Build config files (pyproject.toml) must be rejected when OpenCode
        is not allowed (P2 fix)."""
        from backend.amadeus_app.complex_agent.file_writer import make_file_writer_tool

        storage, task_id = await _make_storage_with_task()
        try:
            tool = make_file_writer_tool(
                storage=storage, task_id=task_id, workspace=tmp_path,
                artifact_root=tmp_path / "agent_artifacts",
                code_write_allowed_fn=lambda: False,
            )
            result = await tool.handler({"path": "pyproject.toml", "content": "[tool]"})
            assert result["ok"] is False
            assert "OpenCode routing approval" in result["error"]
        finally:
            await storage.close()

    @pytest.mark.asyncio
    async def test_code_write_blocked_rejects_tsconfig_json(self, tmp_path: Path):
        """Build config files (tsconfig.json) must be rejected when OpenCode
        is not allowed (P2 fix)."""
        from backend.amadeus_app.complex_agent.file_writer import make_file_writer_tool

        storage, task_id = await _make_storage_with_task()
        try:
            tool = make_file_writer_tool(
                storage=storage, task_id=task_id, workspace=tmp_path,
                artifact_root=tmp_path / "agent_artifacts",
                code_write_allowed_fn=lambda: False,
            )
            result = await tool.handler({"path": "tsconfig.json", "content": "{}"})
            assert result["ok"] is False
        finally:
            await storage.close()

    @pytest.mark.asyncio
    async def test_code_write_allowed_writes_package_json(self, tmp_path: Path):
        """When OpenCode IS allowed, build config files can be written."""
        from backend.amadeus_app.complex_agent.file_writer import make_file_writer_tool

        storage, task_id = await _make_storage_with_task()
        try:
            tool = make_file_writer_tool(
                storage=storage, task_id=task_id, workspace=tmp_path,
                artifact_root=tmp_path / "agent_artifacts",
                code_write_allowed_fn=lambda: True,
            )
            result = await tool.handler({"path": "package.json", "content": "{}"})
            assert result["ok"] is True
            assert (tmp_path / "package.json").read_text(encoding="utf-8") == "{}"
        finally:
            await storage.close()


# ---------------------------------------------------------------------------
# Integration: assemble_task_tools + apply_opencode_routing
# ---------------------------------------------------------------------------


class TestRoutingIntegration:
    """Test that assemble_task_tools does NOT add opencode_delegate by default,
    and that apply_opencode_routing conditionally adds it."""

    @pytest.mark.asyncio
    async def test_assemble_task_tools_no_opencode_by_default(self):
        """opencode_delegate must NOT be in task._tools after assemble_task_tools.
        It is added later by apply_opencode_routing if the decision allows."""
        from backend.amadeus_app.complex_agent.agent import assemble_task_tools
        from backend.amadeus_app.complex_agent.task_hub import ManagedAgentTask, TaskBudget

        storage, task_id = await _make_storage_with_task()
        try:
            task = ManagedAgentTask(
                task_id=task_id, user_id="u", title="t", prompt="查代码",
                workspace_path=".", conversation_id=None, active_skill_ids=[],
                budget=TaskBudget(),
            )
            settings = AgentSettings()
            settings.opencode_routing = default_opencode_routing_config()
            tools = await assemble_task_tools(
                storage=storage, task=task, settings=settings, skill_contexts=[],
            )
            tool_names = [t.name for t in tools]
            assert "opencode_delegate" not in tool_names
            assert "file_writer" in tool_names
            assert "code_search" in tool_names
        finally:
            await storage.close()

    @pytest.mark.asyncio
    async def test_assemble_task_tools_file_writer_has_protection_flag(self):
        """file_writer must have a code_write_allowed_fn that defaults to False."""
        from backend.amadeus_app.complex_agent.agent import assemble_task_tools
        from backend.amadeus_app.complex_agent.task_hub import ManagedAgentTask, TaskBudget

        storage, task_id = await _make_storage_with_task()
        try:
            task = ManagedAgentTask(
                task_id=task_id, user_id="u", title="t", prompt="test",
                workspace_path=".", conversation_id=None, active_skill_ids=[],
                budget=TaskBudget(),
            )
            settings = AgentSettings()
            settings.opencode_routing = default_opencode_routing_config()
            await assemble_task_tools(
                storage=storage, task=task, settings=settings, skill_contexts=[],
            )
            # The flag should be stored on the task, defaulting to False
            assert task.settings.get("_opencode_allowed") is False
        finally:
            await storage.close()

    @pytest.mark.asyncio
    async def test_apply_opencode_routing_allows_for_bug_fix(self):
        """After routing, opencode_delegate is added for bug-fix tasks."""
        from backend.amadeus_app.complex_agent.agent import apply_opencode_routing
        from backend.amadeus_app.complex_agent.task_hub import ManagedAgentTask, TaskBudget

        storage, task_id = await _make_storage_with_task()
        try:
            task = ManagedAgentTask(
                task_id=task_id, user_id="u", title="t", prompt="修复登录 bug",
                workspace_path=".", conversation_id=None, active_skill_ids=[],
                budget=TaskBudget(),
            )
            settings = AgentSettings()
            settings.opencode_routing = default_opencode_routing_config()
            decision = await apply_opencode_routing(
                storage=storage, task=task, settings=settings,
                prompt="修复登录 bug",
                planner_steps=[{"goal": "修复 bug"}],
                skill_info=[],
                model_settings={}, mode="fast",
            )
            assert decision.allowed is True
            assert "opencode_delegate" in [t.name for t in task._tools]
            assert task.settings.get("_opencode_allowed") is True
        finally:
            await storage.close()

    @pytest.mark.asyncio
    async def test_apply_opencode_routing_denies_for_search(self):
        """After routing, opencode_delegate is NOT added for search tasks."""
        from backend.amadeus_app.complex_agent.agent import apply_opencode_routing
        from backend.amadeus_app.complex_agent.task_hub import ManagedAgentTask, TaskBudget

        storage, task_id = await _make_storage_with_task()
        try:
            task = ManagedAgentTask(
                task_id=task_id, user_id="u", title="t", prompt="查代码",
                workspace_path=".", conversation_id=None, active_skill_ids=[],
                budget=TaskBudget(),
            )
            settings = AgentSettings()
            settings.opencode_routing = default_opencode_routing_config()
            decision = await apply_opencode_routing(
                storage=storage, task=task, settings=settings,
                prompt="查代码",
                planner_steps=[],
                skill_info=[],
                model_settings={}, mode="fast",
            )
            assert decision.allowed is False
            assert "opencode_delegate" not in [t.name for t in task._tools]
            assert task.settings.get("_opencode_allowed") is False
        finally:
            await storage.close()

    @pytest.mark.asyncio
    async def test_apply_opencode_routing_emits_event(self):
        """Routing decision must emit an opencode_routing event."""
        from backend.amadeus_app.complex_agent.agent import apply_opencode_routing
        from backend.amadeus_app.complex_agent.task_hub import (
            ManagedAgentTask,
            TaskBudget,
            agent_task_hub,
        )
        from backend.amadeus_app.complex_agent import agent_storage

        storage, task_id = await _make_storage_with_task()
        try:
            task = ManagedAgentTask(
                task_id=task_id, user_id="u", title="t", prompt="查代码",
                workspace_path=".", conversation_id=None, active_skill_ids=[],
                budget=TaskBudget(),
            )
            # Register task in hub so events are stored
            agent_task_hub._tasks[task.task_id] = task
            settings = AgentSettings()
            settings.opencode_routing = default_opencode_routing_config()
            await apply_opencode_routing(
                storage=storage, task=task, settings=settings,
                prompt="查代码",
                planner_steps=[],
                skill_info=[],
                model_settings={}, mode="fast",
            )
            events = await agent_storage.list_agent_task_events(storage, task_id=task_id, after_seq=0)
            routing_events = [e for e in events if e["kind"] == "opencode_routing"]
            assert len(routing_events) >= 1
            payload = routing_events[0]["payload"]
            assert "allowed" in payload
            assert "score" in payload
            assert "method" in payload
        finally:
            agent_task_hub._tasks.pop(task_id, None)
            await storage.close()

    @pytest.mark.asyncio
    async def test_user_config_preserved_when_rules_empty(self):
        """When the user provides no custom rules, the default rule set is used
        as a baseline — but the user's thresholds, force keywords, and
        llm_rejudge setting must be preserved (P1 fix). Previously the entire
        config was replaced by defaults, silently discarding user settings."""
        from backend.amadeus_app.complex_agent.agent import apply_opencode_routing
        from backend.amadeus_app.complex_agent.task_hub import ManagedAgentTask, TaskBudget
        from backend.amadeus_app.complex_agent.domain import OpencodeRoutingConfig

        storage, task_id = await _make_storage_with_task()
        try:
            task = ManagedAgentTask(
                task_id=task_id, user_id="u", title="t", prompt="修复 bug",
                workspace_path=".", conversation_id=None, active_skill_ids=[],
                budget=TaskBudget(),
            )
            # User config: empty rules, but custom threshold and keywords.
            settings = AgentSettings()
            settings.opencode_routing = OpencodeRoutingConfig(
                enabled=True,
                allow_threshold=95,        # very high — should deny a normal bug fix
                ambiguous_threshold=90,
                allow_llm_rejudge=False,   # user disabled LLM re-judge
                force_allow_keywords=["my-force-allow"],
                force_deny_keywords=["my-force-deny"],
                rules=[],                  # empty → default rules used as baseline
            )
            decision = await apply_opencode_routing(
                storage=storage, task=task, settings=settings,
                prompt="修复 bug",
                planner_steps=[],
                skill_info=[],
                model_settings={}, mode="fast",
            )
            # "修复 bug" matches the default fix_bug rule (weight 30).
            # Base 30 + 30 = 60, which is < user's allow_threshold 95 and
            # < ambiguous_threshold 90 → deny. If the user's threshold had been
            # overwritten by the default (60), this would wrongly allow.
            assert decision.allowed is False
            assert decision.score == 60
            # Confirm the default rules were used (fix_bug matched).
            assert "修 Bug" in decision.matched_rules
        finally:
            await storage.close()

    @pytest.mark.asyncio
    async def test_user_force_keywords_preserved_when_rules_empty(self):
        """User's force-allow keywords must work even when rules are empty
        (P1 fix — previously the whole config was replaced, dropping custom
        force keywords)."""
        from backend.amadeus_app.complex_agent.agent import apply_opencode_routing
        from backend.amadeus_app.complex_agent.task_hub import ManagedAgentTask, TaskBudget
        from backend.amadeus_app.complex_agent.domain import OpencodeRoutingConfig

        storage, task_id = await _make_storage_with_task()
        try:
            task = ManagedAgentTask(
                task_id=task_id, user_id="u", title="t", prompt="查代码",
                workspace_path=".", conversation_id=None, active_skill_ids=[],
                budget=TaskBudget(),
            )
            settings = AgentSettings()
            settings.opencode_routing = OpencodeRoutingConfig(
                enabled=True,
                allow_threshold=60,
                ambiguous_threshold=40,
                allow_llm_rejudge=False,
                force_allow_keywords=["my-custom-force-allow"],
                force_deny_keywords=[],
                rules=[],
            )
            decision = await apply_opencode_routing(
                storage=storage, task=task, settings=settings,
                prompt="查代码 my-custom-force-allow",
                planner_steps=[],
                skill_info=[],
                model_settings={}, mode="fast",
            )
            # The user's custom force-allow keyword must trigger force-allow.
            # If defaults had replaced the config, this keyword would be gone.
            assert decision.allowed is True
            assert decision.method == "force_allow"
        finally:
            await storage.close()


# ---------------------------------------------------------------------------
# Skill allowlist bypass prevention & trustMode preservation
# ---------------------------------------------------------------------------


async def _noop_event(kind, payload):
    pass


class TestSkillAllowlistBypass:
    """Even if a skill allows opencode_delegate in its toolAllowlist,
    routing must still gate it."""

    @pytest.mark.asyncio
    async def test_skill_metadata_does_not_trigger_force_allow(self):
        """A skill description containing the force-allow keyword 'opencode'
        must NOT trigger force-allow. Force-allow/force-deny keywords scan only
        the user prompt + planner steps, not skill metadata, so a skill that
        mentions 'opencode' cannot bypass routing for a read-only prompt
        (P2 fix)."""
        from backend.amadeus_app.complex_agent.agent import apply_opencode_routing
        from backend.amadeus_app.complex_agent.task_hub import ManagedAgentTask, TaskBudget

        storage, task_id = await _make_storage_with_task()
        try:
            task = ManagedAgentTask(
                task_id=task_id, user_id="u", title="t", prompt="查代码",
                workspace_path=".", conversation_id=None, active_skill_ids=[],
                budget=TaskBudget(),
            )
            settings = AgentSettings()
            settings.opencode_routing = default_opencode_routing_config()
            decision = await apply_opencode_routing(
                storage=storage, task=task, settings=settings,
                prompt="查代码",
                planner_steps=[],
                skill_info=[{"name": "code-allow-skill", "description": "allows opencode"}],
                model_settings={}, mode="fast",
            )
            # "查代码" is a read-only signal (search_code rule, weight -20).
            # The skill description "opencode" must NOT force-allow.
            assert decision.allowed is False
            assert decision.method != "force_allow"
        finally:
            await storage.close()

    @pytest.mark.asyncio
    async def test_skill_allowlist_does_not_add_opencode_when_routing_denies(self):
        """The skill allowlist intersection is computed in assemble_task_tools,
        but opencode_delegate is added by apply_opencode_routing which checks
        the routing decision, not the skill allowlist. So even if a skill's
        allowlist includes 'opencode_delegate', routing can still deny."""
        from backend.amadeus_app.complex_agent.agent import (
            assemble_task_tools,
            apply_opencode_routing,
        )
        from backend.amadeus_app.complex_agent.task_hub import ManagedAgentTask, TaskBudget
        from backend.amadeus_app.complex_agent.skills import LoadedSkillContext
        from backend.amadeus_app.complex_agent.domain import SkillManifest

        storage, task_id = await _make_storage_with_task()
        try:
            task = ManagedAgentTask(
                task_id=task_id, user_id="u", title="t",
                prompt="帮我搜索代码中的函数定义",
                workspace_path=".", conversation_id=None, active_skill_ids=[],
                budget=TaskBudget(),
            )
            # Skill that explicitly allows opencode_delegate in its allowlist.
            manifest = SkillManifest(
                id="skill1", name="test-skill",
                tool_allowlist=["opencode_delegate", "code_search", "file_reader"],
            )
            skill_ctx = LoadedSkillContext(
                skill_id="skill1", name="test-skill", description="",
                skill_md="", references={}, manifest=manifest,
            )
            settings = AgentSettings()
            settings.opencode_routing = default_opencode_routing_config()

            # assemble_task_tools does NOT add opencode_delegate.
            await assemble_task_tools(
                storage=storage, task=task, settings=settings,
                skill_contexts=[skill_ctx],
            )
            assert "opencode_delegate" not in [t.name for t in task._tools]

            # apply_opencode_routing denies because "搜索代码" is a read-only signal.
            decision = await apply_opencode_routing(
                storage=storage, task=task, settings=settings,
                prompt="帮我搜索代码中的函数定义",
                planner_steps=[],
                skill_info=[{"name": "test-skill", "description": ""}],
                model_settings={}, mode="fast",
            )
            assert decision.allowed is False
            assert "opencode_delegate" not in [t.name for t in task._tools]
        finally:
            await storage.close()


class TestTrustModePreservation:
    """trustMode behavior must be unchanged: when OpenCode is allowed,
    auto-approve still follows trustMode."""

    def test_opencode_delegate_still_uses_trust_mode(self):
        """The make_opencode_delegate_tool receives trust_mode from settings,
        and autoApprove is NOT exposed as a model-settable parameter."""
        from backend.amadeus_app.complex_agent.opencode_delegate import (
            make_opencode_delegate_tool,
        )

        # trust_mode=True
        tool = make_opencode_delegate_tool(
            task_id="t1", workspace=".", on_event=_noop_event, trust_mode=True,
        )
        props = tool.parameters.get("properties", {})
        assert "autoApprove" not in props  # model can't set it

        # trust_mode=False
        tool2 = make_opencode_delegate_tool(
            task_id="t2", workspace=".", on_event=_noop_event, trust_mode=False,
        )
        props2 = tool2.parameters.get("properties", {})
        assert "autoApprove" not in props2

    @pytest.mark.asyncio
    async def test_routing_allowed_preserves_trust_mode_in_delegate(self):
        """When routing allows OpenCode, the delegate tool still gets trust_mode
        from settings (not from routing)."""
        from backend.amadeus_app.complex_agent.agent import apply_opencode_routing
        from backend.amadeus_app.complex_agent.task_hub import ManagedAgentTask, TaskBudget
        import backend.amadeus_app.complex_agent.opencode_delegate as delegate_mod

        storage, task_id = await _make_storage_with_task()
        try:
            task = ManagedAgentTask(
                task_id=task_id, user_id="u", title="t", prompt="修复 bug",
                workspace_path=".", conversation_id=None, active_skill_ids=[],
                budget=TaskBudget(),
            )
            settings = AgentSettings()
            settings.trust_mode = False
            settings.opencode_routing = default_opencode_routing_config()

            captured: dict = {}
            original = delegate_mod.stream_opencode_task

            async def fake_stream(request):
                captured["auto_approve"] = request.auto_approve
                if False:
                    yield  # makes this an async generator function

            delegate_mod.stream_opencode_task = fake_stream
            try:
                decision = await apply_opencode_routing(
                    storage=storage, task=task, settings=settings,
                    prompt="修复 bug",
                    planner_steps=[],
                    skill_info=[],
                    model_settings={}, mode="fast",
                )
                assert decision.allowed is True
                # Find the opencode_delegate tool and call its handler.
                delegate_tool = next(
                    t for t in task._tools if t.name == "opencode_delegate"
                )
                await delegate_tool.handler({"prompt": "test"})
                # trust_mode=False → auto_approve must be False.
                assert captured["auto_approve"] is False
            finally:
                delegate_mod.stream_opencode_task = original
        finally:
            await storage.close()
