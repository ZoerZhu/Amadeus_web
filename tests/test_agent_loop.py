"""Tests for the Codex-style agent loop upgrade."""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from backend.amadeus_app.orchestrator.capabilities import (
    CapabilityGateway,
    capability_catalog,
)
from backend.amadeus_app.orchestrator.domain import (
    OrchestratorEventKind,
    OrchestratorSettings,
    OrchestratorTaskCreateRequest,
)


# ---------------------------------------------------------------------------
# Task 1: Domain model tests
# ---------------------------------------------------------------------------


def test_orchestrator_settings_has_agent_loop_enabled():
    settings = OrchestratorSettings()
    assert settings.agent_loop_enabled is True


def test_orchestrator_settings_has_task_model():
    settings = OrchestratorSettings()
    assert settings.task_model == {}


def test_orchestrator_event_kind_includes_new_types():
    kinds = set(OrchestratorEventKind.__args__)  # type: ignore[attr-defined]
    assert "agent_thought_summary" in kinds
    assert "tool_call" in kinds
    assert "tool_result" in kinds
    assert "command" in kinds
    assert "working_set" in kinds


def test_task_create_request_has_task_model_override():
    req = OrchestratorTaskCreateRequest(prompt="test")
    assert req.task_model_override == {}


# ---------------------------------------------------------------------------
# Task 2: Capability catalog tests
# ---------------------------------------------------------------------------


def test_capability_catalog_includes_shell_exec():
    names = {item.name for item in capability_catalog()}
    assert "shell_exec" in names


def test_capability_catalog_includes_desktop_screenshot():
    names = {item.name for item in capability_catalog()}
    assert "desktop_screenshot" in names


def test_gateway_classifies_shell_exec_as_confirm():
    gateway = CapabilityGateway(trust_mode=False)
    risk = gateway.classify("shell_exec")
    assert risk == "confirm"


def test_gateway_classifies_desktop_screenshot_as_safe():
    gateway = CapabilityGateway(trust_mode=False)
    risk = gateway.classify("desktop_screenshot")
    assert risk == "safe"
