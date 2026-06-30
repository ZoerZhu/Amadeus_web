export type ChatMode = "fast" | "thinking";
export type ChatRole = "user" | "assistant";

export interface ProviderPreset {
  name: string;
  baseUrl: string;
  defaultModel: string;
  compatible: boolean;
  note: string;
}

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  attachments?: ChatAttachment[];
  voiceInput?: VoiceInputInfo;
  assistantVoice?: AssistantVoiceInfo;
  thinking?: string;
  toolEvents?: ToolTraceEvent[];
  streaming?: boolean;
  createdAt?: string;
}

export interface ApiChatMessage {
  role: ChatRole;
  content: string;
}

export interface ModelSettings {
  providerName: string;
  baseUrl: string;
  model: string;
  apiKey: string;
  useRemote: boolean;
}

export interface VisionSettings {
  enabled: boolean;
  screenCaptureEnabled: boolean;
  providerName: string;
  baseUrl: string;
  model: string;
  apiKey: string;
  useRemote: boolean;
}

export interface SpeechInputSettings {
  enabled: boolean;
  providerName: string;
  baseUrl: string;
  model: string;
  apiKey: string;
  language: string;
  useRemote: boolean;
}

export interface VoiceSettings {
  ttsBackend: "local" | "cloud";
  siliconFlowApiKey: string;
  clonedVoiceUri: string;
  autoPlay: boolean;
  syncTextOutput: boolean;
  speed: number;
  gain: number;
}

export interface DesktopAssistantSettings {
  subtitleEnabled: boolean;
  voiceOutputEnabled: boolean;
  autoVoiceInputEnabled: boolean;
  autoScreenshotEnabled: boolean;
  screenshotIntervalSeconds: number;
  cameraEnabled: boolean;
}

export interface StoredSettings {
  model: ModelSettings;
  vision: VisionSettings;
  speechInput: SpeechInputSettings;
  voice: VoiceSettings;
  desktopAssistant: DesktopAssistantSettings;
  mode: ChatMode;
  orchestrator?: OrchestratorSettings;
  agent?: OrchestratorSettings;
  mcpServers?: McpServerConfig[];
  updatedAt?: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  personaId: string;
  mode: ChatMode;
  createdAt: string;
  updatedAt: string;
}

export interface StoredConversationMessage {
  id: string;
  role: ChatRole;
  content: string;
  thinking?: string;
  assistantVoice?: AssistantVoiceInfo;
  createdAt: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: StoredConversationMessage[];
}

export type OrchestratorInvokeAction = "call_tool" | "call_agent" | "query_capabilities";
export type OrchestratorInvokeTargetType = "tool" | "agent" | "auto";

export interface OrchestratorInvokeRequest {
  action: OrchestratorInvokeAction;
  targetType: OrchestratorInvokeTargetType;
  target: string;
  intent: string;
  payload: Record<string, unknown>;
  rawArguments: Record<string, unknown>;
  client: string;
  requestedAt: string;
}

export interface OrchestratorInvokeResponse {
  ok: boolean;
  summary: string;
  data: Record<string, unknown>;
}

export type UploadDevice = "host";
export type UploadMediaType = "text" | "document" | "image" | "audio";

export interface VoiceInputInfo {
  audioUrl: string;
  path: string;
  filename: string;
  contentType: string;
  durationSeconds: number;
  transcript: string;
  waveform: number[];
}

export interface AssistantVoiceSegment {
  index: number;
  url: string;
}

export interface AssistantVoiceInfo {
  audioUrls: string[];
  segments?: AssistantVoiceSegment[];
}

export interface UploadedFileInfo {
  device: UploadDevice;
  date: string;
  textType: string;
  mediaType: UploadMediaType;
  originalFilename: string;
  filename: string;
  path: string;
  contentType: string;
  sizeBytes: number;
  uploadedAt: string;
  readableByFileReader: boolean;
  visionReadable: boolean;
}

export interface ChatAttachment {
  path: string;
  originalFilename: string;
  filename: string;
  mediaType: UploadMediaType;
  contentType: string;
}

export interface VoiceInputTranscriptionResponse {
  ok: boolean;
  text: string;
  error: string;
  audio: {
    url: string;
    path: string;
    filename: string;
    contentType: string;
    sizeBytes: number;
    durationSeconds: number;
    uploadedAt: string;
  };
}

export interface FileUploadResponse {
  ok: boolean;
  summary: string;
  file: UploadedFileInfo;
  data: {
    file: UploadedFileInfo;
  };
}

export interface DesktopObserveRequest {
  prompt: string;
  attachments: ChatAttachment[];
  conversationId?: string | null;
  saveResponse?: boolean;
  model: ModelSettings;
  vision: VisionSettings;
}

export interface DesktopObserveResponse {
  ok: boolean;
  shouldRespond: boolean;
  response: string;
  emotion: string;
  reason: string;
  visionSummary: string;
  userMessage?: StoredConversationMessage;
  assistantMessage?: StoredConversationMessage;
}

export type ToolTraceStatus = "started" | "completed" | "failed";

export interface ToolTraceEvent {
  id: string;
  name: string;
  status: ToolTraceStatus;
  arguments?: Record<string, unknown>;
  summary?: string;
  resultPreview?: string;
  result?: unknown;
  error?: string;
}

export type StreamEvent =
  | { event: "status"; payload: { phase: string; provider?: string; model?: string } }
  | { event: "content"; payload: { text: string } }
  | { event: "thinking"; payload: { text: string } }
  | { event: "tool"; payload: ToolTraceEvent }
  | { event: "emotion"; payload: { emotion: string; live2dEmotion: string } }
  | { event: "audio"; payload: { url: string; index?: number; text?: string; syncText?: boolean } }
  | { event: "voice_error"; payload: { message: string; index?: number } }
  | { event: "error"; payload: { message: string } }
  | { event: "orchestrator_task"; payload: { taskId: string; title: string; reason: string; skillIds: string[] } }
  | { event: "done"; payload: { text: string; assistantVoice?: AssistantVoiceInfo } };

export interface CodeTaskStreamRequest {
  taskId?: string;
  title?: string;
  prompt: string;
  workspacePath: string;
  agent?: string;
  providerId?: string;
  modelId?: string;
  sessionId?: string;
  autoApprove?: boolean;
  timeoutSeconds?: number;
}

export interface CodeTaskQuestionOption {
  label: string;
  description?: string;
}

export interface CodeTaskQuestionItem {
  question: string;
  header: string;
  options: CodeTaskQuestionOption[];
  multiple?: boolean;
  custom?: boolean;
}

export interface CodeTaskQuestionReplyRequest {
  serverUrl: string;
  sessionId: string;
  requestId: string;
  answers: string[][];
  v2?: boolean;
  reject?: boolean;
}

export type CodeTaskEvent =
  | { event: "input"; payload: { text: string; source?: string; message?: string; workspacePath?: string; taskId?: string } }
  | { event: "session"; payload: { sessionId: string; serverUrl?: string; workspacePath?: string; title?: string } }
  | { event: "status"; payload: { status?: string; message?: string; workspacePath?: string; sessionId?: string } }
  | { event: "output"; payload: { text: string } }
  | { event: "log"; payload: { kind?: string; message?: string } }
  | { event: "tool"; payload: { status?: string; name?: string; message?: string; detail?: string } }
  | { event: "command"; payload: { status?: string; command?: string; message?: string; output?: string } }
  | { event: "file"; payload: { path?: string; message?: string } }
  | { event: "diff"; payload: { message?: string; diff?: unknown[] } }
  | { event: "permission"; payload: { requestId?: string; action?: string; resources?: unknown[]; message?: string; blocked?: boolean; approved?: boolean } }
  | {
      event: "question";
      payload: {
        requestId?: string;
        sessionId?: string;
        questions?: CodeTaskQuestionItem[];
        message?: string;
        tool?: Record<string, unknown> | null;
        v2?: boolean;
      };
    }
  | {
      event: "question_result";
      payload: {
        requestId?: string;
        message?: string;
        answers?: string[][];
        rejected?: boolean;
      };
    }
  | { event: "error"; payload: { message: string; requestId?: string } }
  | { event: "done"; payload: { status?: string; message?: string; sessionId?: string; workspacePath?: string } };

export type TaskSummaryVoiceEvent =
  | { event: "status"; payload: { phase: string } }
  | { event: "audio"; payload: { url: string; index?: number; text?: string; syncText?: boolean } }
  | { event: "voice_error"; payload: { message: string; index?: number } }
  | { event: "error"; payload: { message: string } }
  | { event: "done"; payload: { speechText: string } };

// ---------------------------------------------------------------------------
// Orchestrator / MCP / skills subsystem
// ---------------------------------------------------------------------------

export type RoutingMatchType = "keyword" | "regex";
export type RoutingDecisionMethod = "force_allow" | "force_deny" | "rule" | "llm_rejudge";

export interface OpencodeRoutingRule {
  id: string;
  name: string;
  weight: number;
  keywords: string[];
  matchType: RoutingMatchType;
  description: string;
}

export interface OpencodeRoutingConfig {
  enabled: boolean;
  allowThreshold: number;
  ambiguousThreshold: number;
  allowLlmRejudge: boolean;
  forceAllowKeywords: string[];
  forceDenyKeywords: string[];
  rules: OpencodeRoutingRule[];
}

export interface OrchestratorSettings {
  enabled: boolean;
  trustMode: boolean;
  defaultWorkspace: string;
  allowedExternalPaths?: string[];
  maxRounds: number;
  maxToolCalls: number;
  maxRuntimeSeconds: number;
  maxSamplingDepth: number;
  artifactRoot: string;
  rollbackEnabled: boolean;
  browserEnabled: boolean;
  opencodeEnabled: boolean;
  opencodeRouting: OpencodeRoutingConfig;
  roleModels: Record<string, Record<string, unknown>>;
  enabledCapabilities: string[];
  agentLoopEnabled?: boolean;
  taskModel?: Record<string, unknown>;
  fallbackModel?: Record<string, unknown>;
  sandboxMode?: "off" | "guard" | "strict";
}

export type McpTransport = "stdio" | "http";
export type McpAuthType = "none" | "bearer" | "api_key";

export interface McpServerConfig {
  id: string;
  name: string;
  enabled: boolean;
  transport: McpTransport;
  command: string;
  args: string[];
  cwd: string;
  env: Record<string, string>;
  url: string;
  headers: Record<string, string>;
  authType: McpAuthType;
  authToken: string;
  allowedTools: string[];
  allowedResources: string[];
  trusted: boolean;
  timeoutSeconds: number;
  connected?: boolean;
}

export type SkillSource = "zip" | "git" | "local";

export interface SkillPackageInfo {
  id: string;
  name: string;
  version: string;
  enabled: boolean;
  source: SkillSource;
  path: string;
  description: string;
  triggers: string[];
  requiredMcpServers: string[];
  toolAllowlist: string[];
  roles: string[];
  budgets: Record<string, unknown>;
  manifest?: Record<string, unknown>;
  createdAt?: string;
  updatedAt?: string;
}

export type OrchestratorTaskStatus =
  | "created"
  | "running"
  | "paused"
  | "cancelled"
  | "done"
  | "failed"
  | "rolled_back";

export type OrchestratorTaskControlAction = "pause" | "resume" | "cancel" | "rollback";

export type OrchestratorEventKind =
  | "message"
  | "status"
  | "plan"
  | "step"
  | "tool"
  | "mcp"
  | "browser"
  | "artifact"
  | "question"
  | "sampling"
  | "opencode_routing"
  | "error"
  | "done"
  | "agent_thought_summary"
  | "tool_call"
  | "tool_result"
  | "command"
  | "working_set";

export interface OrchestratorTaskBudget {
  maxRounds: number;
  maxToolCalls: number;
  maxRuntimeSeconds: number;
  maxSamplingDepth: number;
  rounds: number;
  toolCalls: number;
  samplingDepth: number;
  elapsedSeconds: number;
  remainingRounds: number;
  remainingToolCalls: number;
  remainingSeconds: number;
}

export interface OrchestratorTaskSummary {
  id: string;
  userId?: string;
  title: string;
  prompt: string;
  status: OrchestratorTaskStatus;
  workspacePath: string;
  conversationId: string | null;
  activeSkillIds: string[];
  createdAt: string;
  updatedAt: string;
  finishedAt: string | null;
  error: string;
  budget: OrchestratorTaskBudget | Record<string, unknown>;
  artifactCount?: number;
  eventCount?: number;
  settings?: Record<string, unknown>;
  context?: Record<string, unknown>;
}

export type OrchestratorArtifactKind =
  | "file"
  | "screenshot"
  | "document"
  | "log"
  | "diff"
  | "snapshot";

export interface OrchestratorArtifact {
  id: string;
  taskId: string;
  kind: OrchestratorArtifactKind;
  name: string;
  path: string;
  mimeType: string;
  sizeBytes: number;
  description: string;
  meta: Record<string, unknown>;
  createdAt: string;
}

export interface OrchestratorTaskEvent {
  taskId: string;
  seq: number;
  kind: OrchestratorEventKind;
  role: string;
  name: string;
  status: string;
  summary: string;
  payload: Record<string, unknown>;
  artifactIds: string[];
  timestamp: string;
}

export interface FileChange {
  id: string;
  taskId: string;
  seq: number;
  relativePath: string;
  changeType: "modified" | "created" | "deleted";
  diffText: string;
  oldSnapshot: string;
  source: string;
  createdAt: string;
}

export interface OrchestratorTaskDetail {
  task: OrchestratorTaskSummary;
  events: OrchestratorTaskEvent[];
  artifacts: OrchestratorArtifact[];
}

export interface OrchestratorTaskCreateRequest {
  title?: string;
  prompt: string;
  attachments?: ChatAttachment[];
  workspacePath?: string;
  conversationId?: string | null;
  skillIds?: string[];
  model?: Record<string, unknown>;
  mode?: string;
  trustMode?: boolean | null;
  maxRounds?: number | null;
  maxToolCalls?: number | null;
  maxRuntimeSeconds?: number | null;
}

export interface OrchestratorTaskMessageRequest {
  prompt: string;
  attachments?: ChatAttachment[];
  skillIds?: string[];
  model?: Record<string, unknown>;
  mode?: string;
  trustMode?: boolean | null;
}

export type PermissionRiskLevel = "safe" | "confirm" | "dangerous";
export type PermissionRequestStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "timeout";

export interface OrchestratorPermissionRequest {
  id: string;
  taskId: string;
  toolName: string;
  argumentsPreview: string;
  riskLevel: PermissionRiskLevel;
  status: PermissionRequestStatus;
  reason: string;
  createdAt: string;
  resolvedAt: string | null;
  questionType?: string;
  payload?: {
    toolName?: string;
    toolArguments?: Record<string, unknown>;
    toolArgumentsPreview?: string;
    riskLevel?: PermissionRiskLevel;
    autoApproved?: boolean;
    reason?: string;
    commandPreview?: string;
    workspacePath?: string;
    round?: number;
    questionType?: string;
    question?: string;
    context?: string;
    options?: string[];
    answer?: string;
    // File-op extensions
    operation?: string;
    paths?: string[];
    outsideWorkspace?: boolean;
    diff?: string;
    requiresHash?: boolean;
    backupPreview?: boolean;
  };
}

export type PermissionRequest = OrchestratorPermissionRequest;

export interface McpPreset {
  id: string;
  name: string;
  description: string;
  transport: "stdio" | "http";
  url?: string;
  command?: string;
  args?: string[];
  cwd?: string;
  env?: Record<string, string>;
  authType?: string;
  headers?: Record<string, string>;
  trusted: boolean;
  allowedTools: string[];
  allowedResources: string[];
  enabled: boolean;
  timeoutSeconds: number;
  _preset?: boolean;
  _category?: string;
}

export interface McpCapability {
  tools: Array<{ name: string; description?: string }>;
  resources: Array<{ uri: string; name?: string; description?: string }>;
  prompts: Array<{ name: string; description?: string }>;
  lastConnectedAt: string | null;
  lastError: string | null;
}

export interface BuiltinSkillInfo {
  id: string;
  name: string;
  version: string;
  description: string;
  triggers: string[];
  roles: string[];
  toolAllowlist: string[];
  mcpServers: string[];
  builtin: boolean;
}

export interface TaskLedger {
  currentStepIndex?: number;
  completedSteps?: number[];
  openQuestions?: string[];
  risks?: string[];
}

// ---------------------------------------------------------------------------
// Memory subsystem
// ---------------------------------------------------------------------------

export type MemoryNodeType = "root" | "domain" | "topic" | "cluster" | "leaf";
export type MemoryDomain =
  | "daily_chat"
  | "task"
  | "code"
  | "environment"
  | "creative"
  | "knowledge";
export type MemoryCategory =
  | "general"
  | "fact"
  | "decision"
  | "preference"
  | "summary"
  | "technical"
  | "task_state"
  | "tool_result";

export interface MemoryNode {
  id: string;
  parentId: string | null;
  nodeType: MemoryNodeType;
  domain: string;
  label: string;
  path: string;
  depth: number;
  summary: string;
  fullContent: string;
  category: MemoryCategory;
  keywords: string[];
  projectId: string | null;
  conversationId: string | null;
  sourceMessageIds: string[];
  sourceSummary: string;
  importance: number;
  confidence: number;
  accessCount: number;
  leafCount: number;
  lastAccessedAt: string | null;
  createdAt: string;
  updatedAt: string;
  expiresAt: string | null;
  isActive: boolean;
  children?: MemoryNode[];
}

export interface MemoryTreeResponse {
  tree: MemoryNode[];
  nodes: MemoryNode[];
}

export interface MemorySearchResult {
  selectedPaths: MemoryNode[][];
  topicNodes: MemoryNode[];
  leafNodes: MemoryNode[];
  contextText: string;
}

export interface MemoryIngestRequest {
  text: string;
  domain: MemoryDomain;
  title?: string;
  projectId?: string | null;
  conversationId?: string | null;
}

export interface MemoryProject {
  id: string;
  name: string;
  description: string;
  workspacePath: string;
  color: string;
  createdAt: string;
  updatedAt: string;
}
