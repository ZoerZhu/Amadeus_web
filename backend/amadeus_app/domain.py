from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


ChatMode = Literal["fast", "thinking"]
ChatRole = Literal["system", "user", "assistant"]
VoiceBackend = Literal["local", "cloud"]
AgentAction = Literal["call_tool", "call_agent", "query_capabilities"]
AgentTargetType = Literal["tool", "agent", "auto"]
AttachmentMediaType = Literal["text", "document", "image", "audio"]

# Centralized default — change here to switch the default persona project-wide
DEFAULT_PERSONA_ID = "kurisu_amadeus"


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


class VisionSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    screen_capture_enabled: bool = Field(default=False, alias="screenCaptureEnabled")
    provider_name: str = Field(default="", alias="providerName")
    base_url: str = Field(default="", alias="baseUrl")
    model: str = "gpt-4.1-mini"
    api_key: str = Field(default="", alias="apiKey")
    use_remote: bool = Field(default=True, alias="useRemote")


class SpeechInputSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    enabled: bool = True
    provider_name: str = Field(default="小米 MiMo ASR", alias="providerName")
    base_url: str = Field(default="https://api.xiaomimimo.com/v1", alias="baseUrl")
    model: str = "mimo-v2.5-asr"
    api_key: str = Field(default="", alias="apiKey")
    language: str = "auto"
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


class DesktopAssistantSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    subtitle_enabled: bool = Field(default=True, alias="subtitleEnabled")
    voice_output_enabled: bool = Field(default=False, alias="voiceOutputEnabled")
    auto_voice_input_enabled: bool = Field(default=True, alias="autoVoiceInputEnabled")
    auto_screenshot_enabled: bool = Field(default=False, alias="autoScreenshotEnabled")
    screenshot_interval_seconds: int = Field(default=15, alias="screenshotIntervalSeconds")
    camera_enabled: bool = Field(default=False, alias="cameraEnabled")


class ChatAttachment(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    path: str
    original_filename: str = Field(default="", alias="originalFilename")
    filename: str = ""
    media_type: AttachmentMediaType = Field(default="text", alias="mediaType")
    content_type: str = Field(default="", alias="contentType")


class ChatStreamRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    messages: list[ChatMessage]
    attachments: list[ChatAttachment] = Field(default_factory=list)
    conversation_id: UUID | None = Field(default=None, alias="conversationId")
    persona_id: str = Field(default=DEFAULT_PERSONA_ID, alias="personaId")
    mode: ChatMode = "fast"
    model: ModelSettings = Field(default_factory=ModelSettings)
    vision: VisionSettings = Field(default_factory=VisionSettings)
    speech_input: SpeechInputSettings = Field(default_factory=SpeechInputSettings, alias="speechInput")
    voice: VoiceSettings = Field(default_factory=VoiceSettings)


class VisionUnderstandRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    prompt: str = ""
    attachments: list[ChatAttachment] = Field(default_factory=list)
    model: ModelSettings = Field(default_factory=ModelSettings)
    vision: VisionSettings = Field(default_factory=VisionSettings)


class VoiceSynthesisRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str
    conversation_id: UUID | None = Field(default=None, alias="conversationId")
    message_id: UUID | None = Field(default=None, alias="messageId")
    persona_id: str = Field(default=DEFAULT_PERSONA_ID, alias="personaId")
    model: ModelSettings = Field(default_factory=ModelSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)


class TaskSummaryVoiceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    summary: str
    persona_id: str = Field(default=DEFAULT_PERSONA_ID, alias="personaId")
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
    vision: VisionSettings = Field(default_factory=VisionSettings)
    speech_input: SpeechInputSettings = Field(default_factory=SpeechInputSettings, alias="speechInput")
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    desktop_assistant: DesktopAssistantSettings = Field(default_factory=DesktopAssistantSettings, alias="desktopAssistant")
    mode: ChatMode = "fast"
    agent: dict[str, Any] | None = None
    mcp_servers: list[dict[str, Any]] | None = Field(default=None, alias="mcpServers")


class DesktopObserveRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    prompt: str = ""
    attachments: list[ChatAttachment] = Field(default_factory=list)
    conversation_id: UUID | None = Field(default=None, alias="conversationId")
    save_response: bool = Field(default=False, alias="saveResponse")
    persona_id: str = Field(default=DEFAULT_PERSONA_ID, alias="personaId")
    mode: ChatMode = "fast"
    model: ModelSettings = Field(default_factory=ModelSettings)
    vision: VisionSettings = Field(default_factory=VisionSettings)


class ConversationCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = ""
    persona_id: str = Field(default=DEFAULT_PERSONA_ID, alias="personaId")
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
