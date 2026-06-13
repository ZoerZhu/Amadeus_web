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

export type AgentAction = "call_tool" | "call_agent" | "query_capabilities";
export type AgentTargetType = "tool" | "agent" | "auto";

export interface AgentInvokeRequest {
  action: AgentAction;
  targetType: AgentTargetType;
  target: string;
  intent: string;
  payload: Record<string, unknown>;
  rawArguments: Record<string, unknown>;
  client: string;
  requestedAt: string;
}

export interface AgentInvokeResponse {
  ok: boolean;
  summary: string;
  data: Record<string, unknown>;
}

export type UploadDevice = "host" | "mobile";
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

export interface CodeTaskViewQuestion {
  requestId: string;
  sessionId: string;
  serverUrl?: string;
  questions: CodeTaskQuestionItem[];
  v2?: boolean;
  answered?: boolean;
  rejected?: boolean;
  answers?: string[][];
}

export interface CodeTaskViewMessage {
  id: string;
  kind: string;
  title?: string;
  text: string;
  detail?: string;
  createdAt: string;
  question?: CodeTaskViewQuestion;
}

export interface CodeTaskViewPhase {
  id: string;
  title: string;
  status: "running" | "completed" | "error";
  eventCount: number;
  hiddenCount: number;
  messages: CodeTaskViewMessage[];
}

export interface CodeTaskViewTurn {
  id: string;
  running: boolean;
  request?: CodeTaskViewMessage;
  processed: {
    title: "处理中" | "已处理";
    openByDefault: boolean;
    duration: string;
    phaseCount: number;
    eventCount: number;
    phases: CodeTaskViewPhase[];
  };
  responses: CodeTaskViewMessage[];
}

export interface CodeTaskMobileViewSnapshot {
  version: 2;
  kind: "code_task_view";
  taskId: string;
  status: string;
  running: boolean;
  renderedAt: string;
  task: {
    id: string;
    title: string;
    prompt: string;
    workspacePath: string;
    status: string;
    sessionId?: string;
    createdAt: string;
    updatedAt: string;
  };
  view: {
    source: "host";
    refreshIntervalMs: number;
    eventCount: number;
    turnCount: number;
  };
  turns: CodeTaskViewTurn[];
  pendingQuestion?: CodeTaskViewQuestion & {
    turnId: string;
    messageId: string;
  };
  summary?: string;
}

export type TaskSummaryVoiceEvent =
  | { event: "status"; payload: { phase: string } }
  | { event: "audio"; payload: { url: string; index?: number; text?: string; syncText?: boolean } }
  | { event: "voice_error"; payload: { message: string; index?: number } }
  | { event: "error"; payload: { message: string } }
  | { event: "done"; payload: { speechText: string } };
