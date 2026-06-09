import type {
  AgentInvokeRequest,
  AgentInvokeResponse,
  ApiChatMessage,
  CodeTaskEvent,
  CodeTaskQuestionReplyRequest,
  CodeTaskStreamRequest,
  ChatMode,
  ConversationDetail,
  ConversationSummary,
  FileUploadResponse,
  ModelSettings,
  ProviderPreset,
  StoredSettings,
  StreamEvent,
  TaskSummaryVoiceEvent,
  UploadDevice,
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

export async function deleteConversation(id: string): Promise<void> {
  const response = await fetch(`/api/conversations/${encodeURIComponent(id)}`, {
    method: "DELETE"
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `conversation delete ${response.status}`);
  }
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

export async function streamCodeTask(options: CodeTaskStreamRequest & {
  signal?: AbortSignal;
  onEvent: (event: CodeTaskEvent) => void;
}): Promise<void> {
  const response = await fetch("/api/code-tasks/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      taskId: options.taskId ?? "",
      title: options.title ?? "",
      prompt: options.prompt,
      workspacePath: options.workspacePath,
      agent: options.agent ?? "",
      providerId: options.providerId ?? "",
      modelId: options.modelId ?? "",
      sessionId: options.sessionId ?? "",
      autoApprove: options.autoApprove ?? false,
      timeoutSeconds: options.timeoutSeconds ?? 1800
    }),
    signal: options.signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`code task ${response.status}`);
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
    buffer = drainSseBuffer<CodeTaskEvent>(buffer, options.onEvent);
  }
  buffer += decoder.decode();
  drainSseBuffer<CodeTaskEvent>(buffer, options.onEvent, true);
}

export async function syncCodeTaskHistory(tasks: unknown[]): Promise<void> {
  const response = await fetch("/api/code-tasks/sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tasks })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `code task sync ${response.status}`);
  }
}

export function subscribeCodeTaskEvents(options: {
  taskId: string;
  replay?: boolean;
  onEvent: (event: CodeTaskEvent) => void;
  onTask?: (task: unknown) => void;
  onError?: (message: string) => void;
}): () => void {
  const replay = options.replay === true ? "true" : "false";
  const source = new EventSource(`/api/mobile/code-tasks/${encodeURIComponent(options.taskId)}/events?replay=${replay}`);
  const eventNames = [
    "input",
    "session",
    "status",
    "output",
    "log",
    "tool",
    "command",
    "file",
    "diff",
    "permission",
    "question",
    "question_result",
    "error",
    "done"
  ];

  const parsePayload = <T>(event: MessageEvent<string>): T | null => {
    try {
      return JSON.parse(event.data) as T;
    } catch {
      return null;
    }
  };

  const taskHandler = (event: MessageEvent<string>) => {
    const payload = parsePayload<{ task?: unknown }>(event);
    if (payload?.task !== undefined) {
      options.onTask?.(payload.task);
    }
  };
  source.addEventListener("task", taskHandler);

  const handlers = eventNames.map((eventName) => {
    const handler = (event: MessageEvent<string>) => {
      const payload = parsePayload<Record<string, unknown>>(event);
      if (!payload) {
        options.onError?.(`task event parse failed: ${eventName}`);
        return;
      }
      options.onEvent({ event: eventName, payload } as CodeTaskEvent);
    };
    source.addEventListener(eventName, handler);
    return { eventName, handler };
  });

  source.onerror = () => {
    options.onError?.("task event stream disconnected");
  };

  return () => {
    source.removeEventListener("task", taskHandler);
    handlers.forEach(({ eventName, handler }) => source.removeEventListener(eventName, handler));
    source.close();
  };
}

export async function replyCodeTaskQuestion(options: CodeTaskQuestionReplyRequest): Promise<void> {
  const response = await fetch("/api/code-tasks/question/reply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      serverUrl: options.serverUrl,
      sessionId: options.sessionId,
      requestId: options.requestId,
      answers: options.answers,
      v2: options.v2 ?? true,
      reject: options.reject ?? false
    })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `question reply ${response.status}`);
  }
}

export async function uploadAgentFile(options: {
  file: File;
  device?: UploadDevice;
  overwrite?: boolean;
  signal?: AbortSignal;
}): Promise<FileUploadResponse> {
  const form = new FormData();
  form.append("file", options.file, options.file.name);
  form.append("device", options.device ?? "host");
  form.append("overwrite", String(options.overwrite ?? false));

  const response = await fetch("/api/files/upload", {
    method: "POST",
    body: form,
    signal: options.signal
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `file upload ${response.status}`);
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

export async function synthesizeTaskSummaryVoice(options: {
  summary: string;
  model: ModelSettings;
  voice: VoiceSettings;
}): Promise<{ audioUrl: string; speechText: string }> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 75000);
  try {
    const response = await fetch("/api/voice/task-summary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        summary: options.summary,
        personaId: "kurisu_amadeus",
        model: options.model,
        voice: options.voice
      }),
      signal: controller.signal
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || `task summary voice ${response.status}`);
    }
    return {
      audioUrl: String(data.audioUrl || ""),
      speechText: String(data.speechText || "")
    };
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("任务总结语音生成超时");
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

export async function streamTaskSummaryVoice(options: {
  summary: string;
  model: ModelSettings;
  voice: VoiceSettings;
  signal?: AbortSignal;
  onEvent: (event: TaskSummaryVoiceEvent) => void;
}): Promise<void> {
  const response = await fetch("/api/voice/task-summary/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      summary: options.summary,
      personaId: "kurisu_amadeus",
      model: options.model,
      voice: options.voice
    }),
    signal: options.signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`task summary voice stream ${response.status}`);
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
    buffer = drainSseBuffer<TaskSummaryVoiceEvent>(buffer, options.onEvent);
  }
  buffer += decoder.decode();
  drainSseBuffer<TaskSummaryVoiceEvent>(buffer, options.onEvent, true);
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

function drainSseBuffer<TEvent>(
  input: string,
  onEvent: (event: TEvent) => void,
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
    emitSseBlock<TEvent>(block, onEvent);
  }
  if (flush && buffer.trim()) {
    emitSseBlock<TEvent>(buffer, onEvent);
    return "";
  }
  return buffer;
}

function emitSseBlock<TEvent>(block: string, onEvent: (event: TEvent) => void): void {
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
    onEvent({ event, payload: JSON.parse(dataLines.join("\n")) } as TEvent);
  } catch {
    return;
  }
}
