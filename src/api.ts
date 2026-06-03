import type {
  AgentInvokeRequest,
  AgentInvokeResponse,
  ApiChatMessage,
  ChatMode,
  ConversationDetail,
  ConversationSummary,
  ModelSettings,
  ProviderPreset,
  StoredSettings,
  StreamEvent,
  VoiceSettings
} from "./types";

export async function fetchProviders(): Promise<ProviderPreset[]> {
  const response = await fetch("/api/providers");
  if (!response.ok) {
    throw new Error(`providers ${response.status}`);
  }
  const data = await response.json();
  return data.providers ?? [];
}

export async function streamChat(options: {
  conversationId?: string | null;
  messages: ApiChatMessage[];
  mode: ChatMode;
  model: ModelSettings;
  voice: VoiceSettings;
  signal?: AbortSignal;
  onEvent: (event: StreamEvent) => void;
}): Promise<void> {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversationId: options.conversationId || undefined,
      messages: options.messages,
      personaId: "kurisu_amadeus",
      mode: options.mode,
      model: options.model,
      voice: options.voice
    }),
    signal: options.signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`chat ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    buffer = drainSseBuffer(buffer, options.onEvent);
  }
  buffer += decoder.decode();
  drainSseBuffer(buffer, options.onEvent, true);
}

export async function fetchSettings(): Promise<StoredSettings | null> {
  const response = await fetch("/api/settings");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `settings ${response.status}`);
  }
  return data.settings ?? null;
}

export async function saveSettings(settings: StoredSettings): Promise<void> {
  const response = await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: settings.model,
      voice: settings.voice,
      mode: settings.mode
    })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `settings ${response.status}`);
  }
}

export async function listConversations(): Promise<ConversationSummary[]> {
  const response = await fetch("/api/conversations");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `conversations ${response.status}`);
  }
  return data.conversations ?? [];
}

export async function createConversation(options: {
  title: string;
  personaId: string;
  mode: ChatMode;
}): Promise<ConversationSummary> {
  const response = await fetch("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options)
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `conversation ${response.status}`);
  }
  return data.conversation;
}

export async function fetchConversation(id: string): Promise<ConversationDetail> {
  const response = await fetch(`/api/conversations/${encodeURIComponent(id)}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `conversation ${response.status}`);
  }
  return data.conversation;
}

export async function fetchAgentCapabilities(): Promise<AgentInvokeResponse> {
  const response = await fetch("/api/agent/capabilities");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `agent capabilities ${response.status}`);
  }
  return data;
}

export async function invokeAgent(request: Partial<AgentInvokeRequest>): Promise<AgentInvokeResponse> {
  const response = await fetch("/api/agent/invoke", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      action: request.action ?? "call_agent",
      targetType: request.targetType ?? "auto",
      target: request.target ?? "",
      intent: request.intent ?? "",
      payload: request.payload ?? {},
      rawArguments: request.rawArguments ?? {},
      client: request.client ?? "Amadeus Web",
      requestedAt: request.requestedAt ?? new Date().toISOString()
    })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `agent ${response.status}`);
  }
  return data;
}

export async function synthesizeVoice(options: {
  text: string;
  model: ModelSettings;
  voice: VoiceSettings;
}): Promise<string> {
  const response = await fetch("/api/voice/synthesize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: options.text,
      personaId: "kurisu_amadeus",
      model: options.model,
      voice: options.voice
    })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `voice ${response.status}`);
  }
  return data.audioUrl;
}

export async function cloneVoice(options: {
  siliconFlowApiKey: string;
  customName: string;
  referenceText?: string;
}): Promise<string> {
  const response = await fetch("/api/voice/clone", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options)
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `clone ${response.status}`);
  }
  return data.voiceUri;
}

function drainSseBuffer(
  input: string,
  onEvent: (event: StreamEvent) => void,
  flush = false
): string {
  let buffer = input.replace(/\r\n/g, "\n");
  while (true) {
    const boundary = buffer.indexOf("\n\n");
    if (boundary < 0) {
      break;
    }
    const block = buffer.slice(0, boundary);
    buffer = buffer.slice(boundary + 2);
    emitSseBlock(block, onEvent);
  }
  if (flush && buffer.trim()) {
    emitSseBlock(buffer, onEvent);
    return "";
  }
  return buffer;
}

function emitSseBlock(block: string, onEvent: (event: StreamEvent) => void): void {
  const lines = block.split("\n");
  const eventLine = lines.find((line) => line.startsWith("event:"));
  const dataLines = lines
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart());

  if (!eventLine || dataLines.length === 0) {
    return;
  }
  const event = eventLine.slice(6).trim();
  try {
    onEvent({ event, payload: JSON.parse(dataLines.join("\n")) } as StreamEvent);
  } catch {
    return;
  }
}
