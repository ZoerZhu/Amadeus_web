"""Agent runner abstraction.

The current implementation is LangGraph-based, but callers depend on this
small interface so future runner engines can replace it without touching chat
routing, REST routes, or task lifecycle code.
"""

from __future__ import annotations

from typing import Protocol

from .agent import run_agent_task
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


class LangGraphAgentRunner:
    async def run(
        self,
        *,
        storage,
        task: ManagedAgentTask,
        request: AgentTaskCreateRequest,
        agent_settings: AgentSettings,
        skill_contexts: list[LoadedSkillContext],
    ) -> None:
        await run_agent_task(
            storage=storage,
            task=task,
            request=request,
            agent_settings=agent_settings,
            skill_contexts=skill_contexts,
        )


default_agent_runner: AgentRunner = LangGraphAgentRunner()
