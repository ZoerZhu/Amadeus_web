"""Domain models for MCP servers and skill packages."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


McpTransport = Literal["stdio", "http"]
McpAuthType = Literal["none", "bearer", "api_key"]


class McpServerConfig(BaseModel):
    """Configuration for a single MCP server connection."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = ""
    name: str
    enabled: bool = True
    transport: McpTransport = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    cwd: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    auth_type: McpAuthType = Field(default="none", alias="authType")
    auth_token: str = Field(default="", alias="authToken")
    allowed_tools: list[str] = Field(default_factory=list, alias="allowedTools")
    allowed_resources: list[str] = Field(default_factory=list, alias="allowedResources")
    trusted: bool = False
    timeout_seconds: int = Field(default=30, alias="timeoutSeconds")


SkillSource = Literal["zip", "git", "local"]


class SkillManifest(BaseModel):
    """Manifest inside a skill package."""

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


class McpServerUpsertRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    server: McpServerConfig


class McpServerTestRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    server: McpServerConfig


class SkillImportRequest(BaseModel):
    """Request to import a skill from a Git URL."""

    model_config = ConfigDict(populate_by_name=True)

    git_url: str = Field(default="", alias="gitUrl")
    branch: str = "main"
    subdirectory: str = ""


class SkillUpsertRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    skill: SkillPackageInfo


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
