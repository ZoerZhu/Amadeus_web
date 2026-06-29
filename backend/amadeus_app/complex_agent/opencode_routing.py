"""Compatibility wrapper for OpenCode routing.

The active implementation lives in :mod:`amadeus_app.orchestrator.opencode_routing`.
The legacy async API is preserved for old imports, but it delegates to the same
deterministic decision engine used by the orchestrator planner.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..orchestrator.opencode_routing import (
    default_opencode_routing_config,
    effective_opencode_routing_config,
    evaluate_opencode_routing,
)
from .domain import OpencodeRoutingConfig, OpencodeRoutingDecision


async def compute_opencode_routing_decision(
    *,
    prompt: str,
    planner_steps: list[dict[str, Any]],
    skill_info: list[dict[str, Any]],
    config: OpencodeRoutingConfig,
    opencode_enabled: bool,
    llm_rejudge_fn: Callable[[str, list[dict[str, Any]], OpencodeRoutingConfig], Awaitable[bool]] | None = None,
) -> OpencodeRoutingDecision:
    """Return the shared orchestrator routing decision.

    ``llm_rejudge_fn`` is accepted for signature compatibility. The rebuilt
    orchestrator currently uses deterministic routing only.
    """
    _ = llm_rejudge_fn
    return evaluate_opencode_routing(
        prompt=prompt,
        config=config,
        opencode_enabled=opencode_enabled,
        heuristic_requires_code_agent=False,
        planner_steps=planner_steps,
        skill_info=skill_info,
    )


def build_llm_rejudge_fn(call_llm_fn: Any, model_settings: dict[str, Any], mode: str):
    """Return a conservative no-op rejudge callback for legacy callers."""
    _ = (call_llm_fn, model_settings, mode)

    async def rejudge(prompt: str, steps: list[dict[str, Any]], config: OpencodeRoutingConfig) -> bool:
        _ = (prompt, steps, config)
        return False

    return rejudge
