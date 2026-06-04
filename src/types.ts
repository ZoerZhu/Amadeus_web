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
