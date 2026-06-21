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
