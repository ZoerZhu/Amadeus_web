from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..domain import ChatAttachment


RoutingMatchType = Literal["keyword", "regex"]
RoutingDecisionMethod = Literal["force_allow", "force_deny", "rule", "llm_rejudge"]

OrchestratorTaskStatus = Literal[
    "created",
    "running",
    "paused",
    "cancelled",
    "done",
    "failed",
    "rolled_back",
]

OrchestratorControlAction = Literal["pause", "resume", "cancel", "rollback"]

OrchestratorEventKind = Literal[
    "message",
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
    # --- Agent Loop additions ---
    "agent_thought_summary",
    "tool_call",
    "tool_result",
    "command",
    "working_set",
]

CapabilityRisk = Literal["safe", "confirm", "dangerous"]


class RoleModelOverrides(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    coordinator: dict[str, Any] = Field(default_factory=dict)
    researcher: dict[str, Any] = Field(default_factory=dict)
    coder: dict[str, Any] = Field(default_factory=dict)
    writer: dict[str, Any] = Field(default_factory=dict)
    browser: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] = Field(default_factory=dict)
    summarizer: dict[str, Any] = Field(default_factory=dict)


class OpencodeRoutingRule(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    weight: int
    keywords: list[str] = Field(default_factory=list)
    match_type: RoutingMatchType = Field(default="keyword", alias="matchType")
    description: str = ""


class OpencodeRoutingConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    allow_threshold: int = Field(default=60, alias="allowThreshold")
    ambiguous_threshold: int = Field(default=40, alias="ambiguousThreshold")
    allow_llm_rejudge: bool = Field(default=True, alias="allowLlmRejudge")
    force_allow_keywords: list[str] = Field(default_factory=list, alias="forceAllowKeywords")
    force_deny_keywords: list[str] = Field(default_factory=list, alias="forceDenyKeywords")
    rules: list[OpencodeRoutingRule] = Field(default_factory=list)


class OpencodeRoutingDecision(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    allowed: bool
    score: int
    matched_rules: list[str] = Field(default_factory=list, alias="matchedRules")
    reason: str = ""
    method: RoutingDecisionMethod = "rule"


class OrchestratorSettings(BaseModel):
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
    opencode_routing: OpencodeRoutingConfig = Field(default_factory=OpencodeRoutingConfig, alias="opencodeRouting")
    role_models: RoleModelOverrides = Field(default_factory=RoleModelOverrides, alias="roleModels")
    enabled_capabilities: list[str] = Field(default_factory=list, alias="enabledCapabilities")
    agent_loop_enabled: bool = Field(default=True, alias="agentLoopEnabled")
    task_model: dict[str, Any] = Field(default_factory=dict, alias="taskModel")


class OrchestratorTaskCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = ""
    prompt: str
    attachments: list[ChatAttachment] = Field(default_factory=list)
    workspace_path: str = Field(default="", alias="workspacePath")
    conversation_id: UUID | None = Field(default=None, alias="conversationId")
    skill_ids: list[str] = Field(default_factory=list, alias="skillIds")
    model: dict[str, Any] = Field(default_factory=dict)
    mode: str = "fast"
    trust_mode: bool | None = Field(default=None, alias="trustMode")
    max_rounds: int | None = Field(default=None, alias="maxRounds")
    max_tool_calls: int | None = Field(default=None, alias="maxToolCalls")
    max_runtime_seconds: int | None = Field(default=None, alias="maxRuntimeSeconds")
    task_model_override: dict[str, Any] = Field(default_factory=dict, alias="taskModelOverride")


class OrchestratorTaskMessageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    prompt: str
    attachments: list[ChatAttachment] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list, alias="skillIds")
    model: dict[str, Any] = Field(default_factory=dict)
    mode: str = "fast"
    trust_mode: bool | None = Field(default=None, alias="trustMode")


class OrchestratorTaskControlRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    action: OrchestratorControlAction
    reason: str = ""


class OrchestratorPermissionDecisionRequest(BaseModel):
    reason: str = ""


class CapabilityDefinition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str
    risk: CapabilityRisk = "safe"
    worker_roles: list[str] = Field(default_factory=list, alias="workerRoles")
    raw_mcp: bool = Field(default=False, alias="rawMcp")
