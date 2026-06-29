"""Deprecated complex-agent runtime entrypoint.

Task execution is now owned by :mod:`amadeus_app.orchestrator`. This module is
kept only so old imports fail with a clear migration message instead of pulling
in the retired LangGraph runtime.
"""

from __future__ import annotations

from typing import Any


async def run_agent_task(**_: Any) -> None:
    """Reject direct use of the retired complex-agent runner."""
    raise RuntimeError("complex_agent runtime is retired; use /api/orchestrator/tasks instead")
