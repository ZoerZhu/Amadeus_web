"""Compatibility wrapper for the integration tool registry.

The canonical implementation now lives in
``amadeus_app.orchestrator_integrations.tool_registry``. Keep this module as a thin
re-export for legacy complex-agent modules that still import the old path.
"""

from __future__ import annotations

from ..orchestrator_integrations.tool_registry import *  # noqa: F401,F403
