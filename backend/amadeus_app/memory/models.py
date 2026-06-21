"""记忆系统 Pydantic 模型与枚举。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class MemoryNodeType(str, Enum):
    ROOT = "root"
    DOMAIN = "domain"
    TOPIC = "topic"
    CLUSTER = "cluster"
    LEAF = "leaf"


class MemoryDomain(str, Enum):
    DAILY_CHAT = "daily_chat"
    TASK = "task"
    CODE = "code"
    ENVIRONMENT = "environment"
    CREATIVE = "creative"
    KNOWLEDGE = "knowledge"


class MemoryCategory(str, Enum):
    GENERAL = "general"
    FACT = "fact"
    DECISION = "decision"
    PREFERENCE = "preference"
    SUMMARY = "summary"
    TECHNICAL = "technical"
    TASK_STATE = "task_state"
    TOOL_RESULT = "tool_result"


class MemoryNode(BaseModel):
    id: str
    parent_id: str | None = None
    node_type: MemoryNodeType
    domain: str = ""
    label: str
    path: str = ""
    depth: int = 0
    summary: str = ""
    full_content: str = ""
    category: MemoryCategory = MemoryCategory.GENERAL
    keywords: list[str] = Field(default_factory=list)
    project_id: str | None = None
    conversation_id: str | None = None
    source_message_ids: list[str] = Field(default_factory=list)
    source_summary: str = ""
    importance: float = 0.5
    confidence: float = 0.7
    access_count: int = 0
    leaf_count: int = 0
    last_accessed_at: str | None = None
    created_at: str = ""
    updated_at: str = ""
    expires_at: str | None = None
    is_active: bool = True


class ExtractedLeafMemory(BaseModel):
    """LLM 提取的叶子记忆候选。"""

    action: str = "create_leaf"  # create_leaf / update_leaf / deactivate_leaf / noop
    target_node_id: str | None = None
    domain: MemoryDomain
    path_hint: str = ""
    title: str
    summary: str
    full_content: str
    category: MemoryCategory = MemoryCategory.FACT
    keywords: list[str] = Field(default_factory=list)
    importance: float = 0.5
    confidence: float = 0.7
    source_message_ids: list[str] = Field(default_factory=list)
    source_summary: str = ""
    expires_in_days: int | None = None
    supersedes_node_ids: list[str] = Field(default_factory=list)


class MemorySearchIntent(BaseModel):
    """检索意图：Query 改写后的结构化结果。"""

    raw_user_message: str
    rewritten_query: str
    query_type: str = "general"  # project / preference / factual_recall / task_state / general
    project_hint: str | None = None
    domains: list[MemoryDomain] = Field(default_factory=list)
    path_hints: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class MemorySearchResult(BaseModel):
    """树型检索结果。"""

    selected_paths: list[list[MemoryNode]] = Field(default_factory=list)
    topic_nodes: list[MemoryNode] = Field(default_factory=list)
    leaf_nodes: list[MemoryNode] = Field(default_factory=list)
    context_text: str = ""


class MemoryLeafCreateRequest(BaseModel):
    """手动创建叶子记忆。"""

    model_config = ConfigDict(populate_by_name=True)

    parent_id: str | None = Field(default=None, alias="parentId")
    domain: MemoryDomain
    label: str
    summary: str
    full_content: str = Field(default="", alias="fullContent")
    category: MemoryCategory = MemoryCategory.FACT
    keywords: list[str] = Field(default_factory=list)
    project_id: str | None = Field(default=None, alias="projectId")
    conversation_id: str | None = Field(default=None, alias="conversationId")
    importance: float = 0.5
    confidence: float = 0.7
    node_type: MemoryNodeType = Field(default=MemoryNodeType.LEAF, alias="nodeType")


class MemoryTreeSearchRequest(BaseModel):
    """树型检索请求。"""

    model_config = ConfigDict(populate_by_name=True)

    query: str
    domains: list[MemoryDomain] = Field(default_factory=list)
    project_id: str | None = Field(default=None, alias="projectId")
    max_depth: int = 4
    node_top_k: int = Field(default=8, alias="nodeTopK")
    leaf_top_k: int = Field(default=8, alias="leafTopK")
    context_budget_chars: int = Field(default=5000, alias="contextBudgetChars")


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str = ""
    workspace_path: str = Field(default="", alias="workspacePath")
    color: str = "#8b5cf6"


class ProjectRecord(BaseModel):
    id: str
    user_id: str
    name: str
    description: str = ""
    workspace_path: str = ""
    color: str = "#8b5cf6"
    created_at: str = ""
    updated_at: str = ""
