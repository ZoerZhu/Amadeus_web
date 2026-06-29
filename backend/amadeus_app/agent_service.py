"""Compatibility import for the Orchestrator invoke service.

The canonical implementation lives in
:mod:`amadeus_app.orchestrator.invoke_service`.
"""

from __future__ import annotations

from .orchestrator.invoke_service import *  # noqa: F401,F403
from .orchestrator.invoke_service import invoke_orchestrator_request


async def invoke_agent_request(request):
    """Legacy Python API wrapper for callers not yet migrated to Orchestrator."""
    return await invoke_orchestrator_request(request)
