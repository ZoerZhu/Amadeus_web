"""Domain models for the complex agent / MCP / skills subsystem.

All Pydantic models use camelCase aliases so they can be serialized
directly to the frontend without manual conversion.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# OpenCode routing
# ---------------------------------------------------------------------------

RoutingMatchType = Literal["keyword", "regex"]
RoutingDecisionMethod = Literal["force_allow", "force_deny", "rule", "llm_rejudge"]


class OpencodeRoutingRule(BaseModel):
    """A single scoring rule for OpenCode routing."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    weight: int  # positive = allow signal, negative = deny signal
    keywords: list[str] = Field(default_factory=list)
    match_type: RoutingMatchType = Field(default="keyword", alias="matchType")
    description: str = ""


class OpencodeRoutingConfig(BaseModel):
    """Configuration for OpenCode complexity-based routing."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    allow_threshold: int = Field(default=60, alias="allowThreshold")
    ambiguous_threshold: int = Field(default=40, alias="ambiguousThreshold")
    allow_llm_rejudge: bool = Field(default=True, alias="allowLlmRejudge")
    force_allow_keywords: list[str] = Field(default_factory=list, alias="forceAllowKeywords")
    force_deny_keywords: list[str] = Field(default_factory=list, alias="forceDenyKeywords")
    rules: list[OpencodeRoutingRule] = Field(default_factory=list)


class OpencodeRoutingDecision(BaseModel):
    """The result of an OpenCode routing evaluation."""

    model_config = ConfigDict(populate_by_name=True)

    allowed: bool
    score: int
    matched_rules: list[str] = Field(default_factory=list, alias="matchedRules")
    reason: str = ""
    method: RoutingDecisionMethod = "rule"


# ---------------------------------------------------------------------------
# Agent settings
# ---------------------------------------------------------------------------


class AgentSettings(BaseModel):
    """Top-level complex-agent configuration stored in app_settings."""

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    trust_mode: bool = Field(default=True, alias="trustMode")
    default_workspace: str = Field(default="", alias="defaultWorkspace")
    max_rounds: int = Field(default=20, alias="maxRounds")
    max_tool_calls: int = Field(default=80, alias="maxToolCalls")
    max_runtime_seconds: int = Field(default=1800, alias="maxRuntimeSeconds")
    max_sampling_depth: int = Field(default=3, alias="maxSamplingDepth")
    artifact_root: str = Field(default="generated_docs/agent_artifacts", alias="artifactRoot")
    rollback_enabled: bool = Field(default=True, alias="rollbackEnabled")
    browser_enabled: bool = Field(default=False, alias="browserEnabled")
    opencode_enabled: bool = Field(default=True, alias="opencodeEnabled")
    opencode_routing: OpencodeRoutingConfig = Field(
        default_factory=OpencodeRoutingConfig, alias="opencodeRouting"
    )


# ---------------------------------------------------------------------------
# MCP server configuration
# ---------------------------------------------------------------------------


McpTransport = Literal["stdio", "http"]
McpAuthType = Literal["none", "bearer", "api_key"]


class McpServerConfig(BaseModel):
    """Configuration for a single MCP server connection."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = ""
    name: str
    enabled: bool = True
    transport: McpTransport = "stdio"
    # stdio transport
    command: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    # http transport
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    auth_type: McpAuthType = Field(default="none", alias="authType")
    auth_token: str = Field(default="", alias="authToken")
    # access control
    allowed_tools: list[str] = Field(default_factory=list, alias="allowedTools")
    allowed_resources: list[str] = Field(default_factory=list, alias="allowedResources")
    trusted: bool = False
    timeout_seconds: int = Field(default=30, alias="timeoutSeconds")


# ---------------------------------------------------------------------------
# Skill packages
# ---------------------------------------------------------------------------


SkillSource = Literal["zip", "git", "local"]


class SkillManifest(BaseModel):
    """Manifest inside a skill package (manifest.json)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    triggers: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    tool_allowlist: list[str] = Field(default_factory=list, alias="toolAllowlist")
    mcp_servers: list[str] = Field(default_factory=list, alias="mcpServers")
    references: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)
    budgets: dict[str, Any] = Field(default_factory=dict)


class SkillPackageInfo(BaseModel):
    """Persisted metadata about an installed skill package."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    version: str = "0.1.0"
    enabled: bool = True
    source: SkillSource = "local"
    path: str = ""
    description: str = ""
    triggers: list[str] = Field(default_factory=list)
    required_mcp_servers: list[str] = Field(default_factory=list, alias="requiredMcpServers")
    tool_allowlist: list[str] = Field(default_factory=list, alias="toolAllowlist")
    roles: list[str] = Field(default_factory=list)
    budgets: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Agent tasks
# ---------------------------------------------------------------------------


AgentTaskStatus = Literal[
    "created",
    "running",
    "paused",
    "cancelled",
    "done",
    "failed",
    "rolled_back",
]

AgentTaskControlAction = Literal["pause", "resume", "cancel", "rollback"]

AgentEventKind = Literal[
    "status",
    "plan",
    "step",
    "tool",
    "mcp",
    "browser",
    "artifact",
    "question",
    "sampling",
    "opencode_routing",
    "error",
    "done",
]


class AgentTaskCreateRequest(BaseModel):
    """Request body for POST /api/agent/tasks/stream."""

    model_config = ConfigDict(populate_by_name=True)

    title: str = ""
    prompt: str
    workspace_path: str = Field(default="", alias="workspacePath")
    conversation_id: UUID | None = Field(default=None, alias="conversationId")
    skill_ids: list[str] = Field(default_factory=list, alias="skillIds")
    model: dict[str, Any] = Field(default_factory=dict)
    mode: str = "fast"
    trust_mode: bool | None = Field(default=None, alias="trustMode")
    max_rounds: int | None = Field(default=None, alias="maxRounds")
    max_tool_calls: int | None = Field(default=None, alias="maxToolCalls")
    max_runtime_seconds: int | None = Field(default=None, alias="maxRuntimeSeconds")


class AgentTaskControlRequest(BaseModel):
    """Request body for POST /api/agent/tasks/{id}/control."""

    model_config = ConfigDict(populate_by_name=True)

    action: AgentTaskControlAction
    reason: str = ""


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
# MCP / Skills API request models
# ---------------------------------------------------------------------------


class McpServerUpsertRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    server: McpServerConfig


class McpServerTestRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    server: McpServerConfig


class SkillImportRequest(BaseModel):
    """Request to import a skill from a Git URL (ZIP uses multipart upload)."""

    model_config = ConfigDict(populate_by_name=True)

    git_url: str = Field(default="", alias="gitUrl")
    branch: str = "main"
    subdirectory: str = ""


class SkillUpsertRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    skill: SkillPackageInfo


# ---------------------------------------------------------------------------
# Sampling / elicitation (MCP server -> host callbacks)
# ---------------------------------------------------------------------------


class McpSamplingRequest(BaseModel):
    """MCP server requests the host to perform an LLM sampling call."""

    model_config = ConfigDict(populate_by_name=True)

    server_id: str = Field(alias="serverId")
    messages: list[dict[str, Any]]
    system_prompt: str = Field(default="", alias="systemPrompt")
    max_tokens: int = Field(default=1024, alias="maxTokens")
    temperature: float | None = None


class McpElicitationRequest(BaseModel):
    """MCP server requests user input via the host UI."""

    model_config = ConfigDict(populate_by_name=True)

    server_id: str = Field(alias="serverId")
    message: str
    requested_schema: dict[str, Any] | None = Field(default=None, alias="requestedSchema")


# ---------------------------------------------------------------------------
# Tool permission queue
# ---------------------------------------------------------------------------

PermissionRiskLevel = Literal["safe", "confirm", "dangerous"]
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


class PermissionDecisionRequest(BaseModel):
    """Request body for approve/reject a permission request."""

    model_config = ConfigDict(populate_by_name=True)

    reason: str = ""
