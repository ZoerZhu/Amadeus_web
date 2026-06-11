from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


ChatMode = Literal["fast", "thinking"]
ChatRole = Literal["system", "user", "assistant"]
VoiceBackend = Literal["local", "cloud"]
AgentAction = Literal["call_tool", "call_agent", "query_capabilities"]
AgentTargetType = Literal["tool", "agent", "auto"]


class ModelProviderPreset(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    base_url: str = Field(alias="baseUrl")
    default_model: str = Field(alias="defaultModel")
    compatible: bool
    note: str


class ChatMessage(BaseModel):
    role: ChatRole
    content: str


class ModelSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider_name: str = Field(default="OpenAI", alias="providerName")
    base_url: str = Field(default="", alias="baseUrl")
    model: str = ""
    api_key: str = Field(default="", alias="apiKey")
    use_remote: bool = Field(default=True, alias="useRemote")


class VoiceSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tts_backend: VoiceBackend = Field(default="local", alias="ttsBackend")
    silicon_flow_api_key: str = Field(default="", alias="siliconFlowApiKey")
    cloned_voice_uri: str = Field(default="", alias="clonedVoiceUri")
    auto_play: bool = Field(default=False, alias="autoPlay")
    sync_text_output: bool = Field(default=True, alias="syncTextOutput")
    speed: float = 1.0
    gain: float = 0.0


class ChatStreamRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    messages: list[ChatMessage]
    conversation_id: UUID | None = Field(default=None, alias="conversationId")
    persona_id: str = Field(default="kurisu_amadeus", alias="personaId")
    mode: ChatMode = "fast"
    model: ModelSettings = Field(default_factory=ModelSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)


class VoiceSynthesisRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str
    persona_id: str = Field(default="kurisu_amadeus", alias="personaId")
    model: ModelSettings = Field(default_factory=ModelSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)


class TaskSummaryVoiceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    summary: str
    persona_id: str = Field(default="kurisu_amadeus", alias="personaId")
    model: ModelSettings = Field(default_factory=ModelSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)


class VoiceCloneRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    silicon_flow_api_key: str = Field(default="", alias="siliconFlowApiKey")
    reference_text: str = Field(default="", alias="referenceText")
    custom_name: str = Field(default="siliconflow-kurisu-web", alias="customName")


class SettingsSaveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    model: ModelSettings = Field(default_factory=ModelSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    mode: ChatMode = "fast"


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = ""
    persona_id: str = Field(default="kurisu_amadeus", alias="personaId")
    mode: ChatMode = "fast"


class AgentInvokeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    action: AgentAction = "call_agent"
    target_type: AgentTargetType = Field(default="auto", alias="targetType")
    target: str = ""
    intent: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    raw_arguments: dict[str, Any] = Field(default_factory=dict, alias="rawArguments")
    client: str = "Amadeus Web"
    requested_at: str = Field(default="", alias="requestedAt")


class CodeTaskStreamRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(default="", alias="taskId")
    title: str = ""
    prompt: str
    workspace_path: str = Field(default="", alias="workspacePath")
    agent: str = ""
    provider_id: str = Field(default="", alias="providerId")
    model_id: str = Field(default="", alias="modelId")
    session_id: str = Field(default="", alias="sessionId")
    source: str = "host"
    auto_approve: bool = Field(default=False, alias="autoApprove")
    timeout_seconds: int = Field(default=1800, alias="timeoutSeconds")


class CodeTaskQuestionReplyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    server_url: str = Field(alias="serverUrl")
    session_id: str = Field(alias="sessionId")
    request_id: str = Field(alias="requestId")
    answers: list[list[str]] = Field(default_factory=list)
    v2: bool = True
    reject: bool = False


class CodeTaskRemoteMessageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    prompt: str
    title: str = ""
    agent: str = ""
    provider_id: str = Field(default="", alias="providerId")
    model_id: str = Field(default="", alias="modelId")
    auto_approve: bool = Field(default=False, alias="autoApprove")
    timeout_seconds: int = Field(default=1800, alias="timeoutSeconds")


class CodeTaskMobileQuestionReplyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(alias="requestId")
    answers: list[list[str]] = Field(default_factory=list)
    v2: bool = True
    reject: bool = False
    wait_for_result: bool = Field(default=False, alias="waitForResult")
    after_seq: int = Field(default=0, alias="afterSeq")
    timeout_seconds: int = Field(default=180, alias="timeoutSeconds")


class CodeTaskSnapshot(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str
    title: str = ""
    prompt: str = ""
    workspace_path: str = Field(default="", alias="workspacePath")
    status: str = "idle"
    session_id: str = Field(default="", alias="sessionId")
    created_at: str = Field(default="", alias="createdAt")
    updated_at: str = Field(default="", alias="updatedAt")
    messages: list[dict[str, Any]] = Field(default_factory=list)


class CodeTaskSyncRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tasks: list[CodeTaskSnapshot] = Field(default_factory=list)


class CodeTaskViewPublishRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(default="", alias="taskId")
    snapshot: dict[str, Any] = Field(default_factory=dict)
