"""Compatibility wrapper for retired complex-agent chat routing."""

from __future__ import annotations

from ..orchestrator.chat_routing import (
    ORCHESTRATOR_TASK_MARKER,
    ORCHESTRATOR_TASK_SYSTEM_PROMPT,
    detect_orchestrator_task_intent,
    scan_for_orchestrator_task_marker,
)


COMPLEX_TASK_MARKER = "__COMPLEX_TASK__"
COMPLEX_TASK_SYSTEM_PROMPT = ORCHESTRATOR_TASK_SYSTEM_PROMPT
detect_complex_task_intent = detect_orchestrator_task_intent


def scan_for_complex_task_marker(content: str) -> bool:
    """Return True for either the retired marker or the current orchestrator marker."""
    return COMPLEX_TASK_MARKER in content or scan_for_orchestrator_task_marker(content)
