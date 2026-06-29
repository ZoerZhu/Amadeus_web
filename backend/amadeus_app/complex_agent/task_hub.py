"""Deprecated complex-agent task hub compatibility.

Live task execution moved to :mod:`amadeus_app.orchestrator.runner`; SSE
formatting moved to :mod:`amadeus_app.event_stream`. This module only preserves
old import names used by compatibility tests and external callers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..event_stream import sse


@dataclass
class TaskBudget:
    max_rounds: int = 20
    max_tool_calls: int = 80
    max_runtime_seconds: int = 1800
    max_sampling_depth: int = 3
    rounds: int = 0
    tool_calls: int = 0
    sampling_depth: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def to_dict(self) -> dict[str, Any]:
        elapsed = time.monotonic() - self.started_at
        return {
            "maxRounds": self.max_rounds,
            "maxToolCalls": self.max_tool_calls,
            "maxRuntimeSeconds": self.max_runtime_seconds,
            "maxSamplingDepth": self.max_sampling_depth,
            "rounds": self.rounds,
            "toolCalls": self.tool_calls,
            "samplingDepth": self.sampling_depth,
            "elapsedSeconds": round(elapsed, 1),
        }


@dataclass
class ManagedAgentTask:
    task_id: str
    user_id: str = ""
    title: str = ""
    prompt: str = ""
    workspace_path: str = ""
    conversation_id: str | None = None
    active_skill_ids: list[str] = field(default_factory=list)
    status: str = "created"
    error: str = ""
    budget: TaskBudget = field(default_factory=TaskBudget)
    settings: dict[str, Any] = field(default_factory=dict)


class AgentTaskHub:
    def get(self, task_id: str) -> None:
        _ = task_id
        return None

    async def create_task(self, **_: Any) -> ManagedAgentTask:
        raise RuntimeError("complex_agent task hub is retired; use /api/orchestrator/tasks instead")

    async def cancel_all(self) -> None:
        return None


agent_task_hub = AgentTaskHub()
