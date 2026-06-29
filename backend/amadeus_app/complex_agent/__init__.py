"""Legacy complex-agent runtime.

New task execution flows through :mod:`amadeus_app.orchestrator`. MCP, skills,
tool registry, and MCP resource/prompt tools live in
:mod:`amadeus_app.orchestrator_integrations`; the similarly named modules in this
package are compatibility re-exports only.

This package is kept for old task records, legacy artifact access, and
compatibility imports while the rebuild finishes. It must not create new task
runtime state.
"""
