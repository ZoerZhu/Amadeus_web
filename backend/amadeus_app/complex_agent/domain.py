"""Compatibility domain exports for the retired complex-agent runtime.

The active task runtime lives in :mod:`amadeus_app.orchestrator`, while MCP and
skill models live in :mod:`amadeus_app.orchestrator_integrations`.  This module keeps
legacy import names as aliases so old callers do not carry a second divergent
copy of the runtime settings or request models.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..orchestrator_integrations.domain import (
    McpAuthType,
    McpElicitationRequest,
    McpSamplingRequest,
    McpServerConfig,
    McpServerTestRequest,
    McpServerUpsertRequest,
    McpTransport,
    SkillImportRequest,
    SkillManifest,
    SkillPackageInfo,
    SkillSource,
    SkillUpsertRequest,
)
from ..orchestrator.domain import (
    CapabilityRisk,
    OpencodeRoutingConfig,
    OpencodeRoutingDecision,
    OpencodeRoutingRule,
    OrchestratorControlAction,
    OrchestratorEventKind,
    OrchestratorPermissionDecisionRequest,
    OrchestratorSettings,
    OrchestratorTaskControlRequest,
    OrchestratorTaskCreateRequest,
    OrchestratorTaskStatus,
    RoutingDecisionMethod,
    RoutingMatchType,
)


# ---------------------------------------------------------------------------
# Legacy task/runtime aliases
# ---------------------------------------------------------------------------


AgentSettings = OrchestratorSettings
AgentTaskCreateRequest = OrchestratorTaskCreateRequest
AgentTaskControlRequest = OrchestratorTaskControlRequest
AgentTaskStatus = OrchestratorTaskStatus
AgentTaskControlAction = OrchestratorControlAction
AgentEventKind = OrchestratorEventKind


class AgentTaskSummary(BaseModel):
    """Summary view of an agent task (list endpoint)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    title: str
    prompt: str
    status: AgentTaskStatus
    workspace_path: str = Field(default="", alias="workspacePath")
    conversation_id: str | None = Field(default=None, alias="conversationId")
    active_skill_ids: list[str] = Field(default_factory=list, alias="activeSkillIds")
    created_at: str = Field(default="", alias="createdAt")
    updated_at: str = Field(default="", alias="updatedAt")
    finished_at: str | None = Field(default=None, alias="finishedAt")
    error: str = ""
    budget: dict[str, Any] = Field(default_factory=dict)
    artifact_count: int = Field(default=0, alias="artifactCount")
    event_count: int = Field(default=0, alias="eventCount")


class AgentArtifactRecord(BaseModel):
    """Metadata for a single artifact produced by an agent task."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    task_id: str = Field(default="", alias="taskId")
    kind: Literal["file", "screenshot", "document", "log", "diff", "snapshot"] = "file"
    name: str
    path: str
    mime_type: str = Field(default="application/octet-stream", alias="mimeType")
    size_bytes: int = Field(default=0, alias="sizeBytes")
    description: str = ""
    created_at: str = Field(default="", alias="createdAt")
    meta: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tool permission queue
# ---------------------------------------------------------------------------

PermissionRiskLevel = CapabilityRisk
PermissionRequestStatus = Literal["pending", "approved", "rejected", "timeout"]


class PermissionRequest(BaseModel):
    """A tool-execution permission request awaiting user approval."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    task_id: str = Field(alias="taskId")
    tool_name: str = Field(alias="toolName")
    arguments_preview: str = Field(default="", alias="argumentsPreview")
    risk_level: PermissionRiskLevel = Field(default="confirm", alias="riskLevel")
    status: PermissionRequestStatus = "pending"
    reason: str = ""
    created_at: str = Field(alias="createdAt")
    resolved_at: str | None = Field(default=None, alias="resolvedAt")


PermissionDecisionRequest = OrchestratorPermissionDecisionRequest
