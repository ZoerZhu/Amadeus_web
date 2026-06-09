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

export interface VoiceSettings {
  ttsBackend: "local" | "cloud";
  siliconFlowApiKey: string;
  clonedVoiceUri: string;
  autoPlay: boolean;
  syncTextOutput: boolean;
  speed: number;
  gain: number;
}

export interface StoredSettings {
  model: ModelSettings;
  voice: VoiceSettings;
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

export interface UploadedFileInfo {
  device: UploadDevice;
  date: string;
  textType: string;
  originalFilename: string;
  filename: string;
  path: string;
  contentType: string;
  sizeBytes: number;
  uploadedAt: string;
  readableByFileReader: boolean;
}

export interface FileUploadResponse {
  ok: boolean;
  summary: string;
  file: UploadedFileInfo;
  data: {
    file: UploadedFileInfo;
  };
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
  | { event: "done"; payload: { text: string } };

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
