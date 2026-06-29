"""Legacy complex-agent runner abstraction.

New task execution uses :mod:`amadeus_app.orchestrator.runner`. This module is
kept only for compatibility with old complex-agent runtime code.
"""

from __future__ import annotations

from typing import Protocol

from .domain import AgentSettings, AgentTaskCreateRequest
from .skills import LoadedSkillContext
from .task_hub import ManagedAgentTask


class AgentRunner(Protocol):
    async def run(
        self,
        *,
        storage,
        task: ManagedAgentTask,
        request: AgentTaskCreateRequest,
        agent_settings: AgentSettings,
        skill_contexts: list[LoadedSkillContext],
    ) -> None:
        """Run an agent task until it emits a terminal event."""


class DeprecatedAgentRunner:
    async def run(
        self,
        *,
        storage,
        task: ManagedAgentTask,
        request: AgentTaskCreateRequest,
        agent_settings: AgentSettings,
        skill_contexts: list[LoadedSkillContext],
    ) -> None:
        _ = (storage, task, request, agent_settings, skill_contexts)
        raise RuntimeError("complex_agent runtime is retired; use /api/orchestrator/tasks instead")


default_agent_runner: AgentRunner = DeprecatedAgentRunner()
