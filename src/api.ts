import type {
  AgentArtifact,
  AgentInvokeRequest,
  AgentInvokeResponse,
  AgentTaskControlAction,
  AgentTaskCreateRequest,
  AgentTaskDetail,
  AgentTaskEvent,
  AgentTaskSummary,
  ApiChatMessage,
  AssistantVoiceInfo,
  ChatAttachment,
  CodeTaskEvent,
  CodeTaskMobileViewSnapshot,
  CodeTaskQuestionReplyRequest,
  CodeTaskStreamRequest,
  DesktopObserveRequest,
  DesktopObserveResponse,
  ChatMode,
  ConversationDetail,
  ConversationSummary,
  FileUploadResponse,
  McpServerConfig,
  ModelSettings,
  PermissionRequest,
  ProviderPreset,
  SkillPackageInfo,
  SpeechInputSettings,
  StoredSettings,
  StreamEvent,
  TaskSummaryVoiceEvent,
  UploadDevice,
  VoiceInputTranscriptionResponse,
  VisionSettings,
  VoiceSettings
} from "./types";
import { PERSONA_ID } from "./config";

const CODE_TASK_EVENT_NAMES = [
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
  attachments?: ChatAttachment[];
  mode: ChatMode;
  model: ModelSettings;
  vision: VisionSettings;
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
      attachments: options.attachments ?? [],
      personaId: PERSONA_ID,
      mode: options.mode,
      model: options.model,
      vision: options.vision,
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
  const payload: Record<string, unknown> = {
    model: settings.model,
    vision: settings.vision,
    speechInput: settings.speechInput,
    voice: settings.voice,
    desktopAssistant: settings.desktopAssistant,
    mode: settings.mode
  };
  if (settings.agent) {
    payload.agent = settings.agent;
  }
  if (settings.mcpServers) {
    payload.mcpServers = settings.mcpServers;
  }
  const response = await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `settings ${response.status}`);
  }
}

export async function observeDesktop(options: DesktopObserveRequest): Promise<DesktopObserveResponse> {
  const response = await fetch("/api/desktop/observe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      prompt: options.prompt,
      attachments: options.attachments,
      conversationId: options.conversationId || undefined,
      saveResponse: options.saveResponse ?? false,
      personaId: PERSONA_ID,
      model: options.model,
      vision: options.vision
    })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `desktop observe ${response.status}`);
  }
  return data;
}

export async function transcribeVoiceInput(options: {
  file: File;
  durationSeconds: number;
  model: ModelSettings;
  speechInput: SpeechInputSettings;
  signal?: AbortSignal;
}): Promise<VoiceInputTranscriptionResponse> {
  const form = new FormData();
  form.append("file", options.file, options.file.name);
  form.append("durationSeconds", String(options.durationSeconds));
  form.append("model", JSON.stringify(options.model));
  form.append("speechInput", JSON.stringify(options.speechInput));

  const response = await fetch("/api/voice-input/transcribe", {
    method: "POST",
    body: form,
    signal: options.signal
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `voice input ${response.status}`);
  }
  return data;
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

export function subscribeAllCodeTaskEvents(options: {
  replay?: boolean;
  onEvent: (event: CodeTaskEvent) => void;
  onError?: (message: string) => void;
}): () => void {
  const replay = options.replay === true ? "true" : "false";
  const source = new EventSource(`/api/code-tasks/events?replay=${replay}`);
  const handlers = CODE_TASK_EVENT_NAMES.map((eventName) => {
    const handler = (event: MessageEvent<string>) => {
      const payload = parseSsePayload<Record<string, unknown>>(event.data);
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
    options.onError?.("global task event stream disconnected");
  };

  return () => {
    handlers.forEach(({ eventName, handler }) => source.removeEventListener(eventName, handler));
    source.close();
  };
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

  const parsePayload = <T>(event: MessageEvent<string>): T | null => {
    return parseSsePayload<T>(event.data);
  };

  const taskHandler = (event: MessageEvent<string>) => {
    const payload = parsePayload<{ task?: unknown }>(event);
    if (payload?.task !== undefined) {
      options.onTask?.(payload.task);
    }
  };
  source.addEventListener("task", taskHandler);

  const handlers = CODE_TASK_EVENT_NAMES.map((eventName) => {
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

export async function publishCodeTaskMobileView(snapshot: CodeTaskMobileViewSnapshot): Promise<void> {
  const response = await fetch("/api/mobile/task-views", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      taskId: snapshot.taskId,
      snapshot
    })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `mobile task view ${response.status}`);
  }
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
  conversationId?: string | null;
  messageId?: string;
}): Promise<{ audioUrl: string; assistantVoice?: AssistantVoiceInfo }> {
  const response = await fetch("/api/voice/synthesize", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: options.text,
      conversationId: options.conversationId || undefined,
      messageId: options.messageId || undefined,
      personaId: PERSONA_ID,
      model: options.model,
      voice: options.voice
    })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `voice ${response.status}`);
  }
  return {
    audioUrl: String(data.audioUrl || ""),
    assistantVoice: data.assistantVoice
  };
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
        personaId: PERSONA_ID,
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
      personaId: PERSONA_ID,
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

// ---------------------------------------------------------------------------
// Complex agent / MCP / skills API
// ---------------------------------------------------------------------------

export async function fetchAgentTasks(conversationId?: string | null): Promise<AgentTaskSummary[]> {
  const query = conversationId ? `?conversationId=${encodeURIComponent(conversationId)}` : "";
  const response = await fetch(`/api/agent/tasks${query}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `agent tasks ${response.status}`);
  }
  return data.tasks ?? [];
}

export async function fetchAgentTaskDetail(id: string): Promise<AgentTaskDetail> {
  const response = await fetch(`/api/agent/tasks/${encodeURIComponent(id)}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `agent task ${response.status}`);
  }
  return data.task as AgentTaskDetail;
}

export async function controlAgentTask(
  id: string,
  action: AgentTaskControlAction,
  reason?: string
): Promise<{ ok: boolean }> {
  const response = await fetch(`/api/agent/tasks/${encodeURIComponent(id)}/control`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, reason: reason ?? "" })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `agent control ${response.status}`);
  }
  return data;
}

export function streamAgentTaskEvents(options: {
  taskId: string;
  afterSeq?: number;
  onEvent: (event: AgentTaskEvent) => void;
  onError?: (message: string) => void;
}): () => void {
  const afterSeq = options.afterSeq ?? 0;
  const url = `/api/agent/tasks/${encodeURIComponent(options.taskId)}/events?afterSeq=${afterSeq}`;
  const controller = new AbortController();

  const readerPromise = (async () => {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok || !response.body) {
      options.onError?.(`agent events ${response.status}`);
      return;
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
      buffer = drainSseBuffer<AgentTaskEvent>(buffer, options.onEvent);
    }
    buffer += decoder.decode();
    drainSseBuffer<AgentTaskEvent>(buffer, options.onEvent, true);
  })();

  readerPromise.catch((error) => {
    if (controller.signal.aborted) {
      return;
    }
    options.onError?.(error instanceof Error ? error.message : "agent events stream failed");
  });

  return () => controller.abort();
}

export async function createAgentTask(
  request: AgentTaskCreateRequest
): Promise<{ taskId: string }> {
  const response = await fetch("/api/agent/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `agent task create ${response.status}`);
  }
  return { taskId: String(data.taskId ?? data.id ?? "") };
}

export async function streamAgentTask(options: {
  request: AgentTaskCreateRequest;
  signal?: AbortSignal;
  onEvent: (event: AgentTaskEvent) => void;
  onError?: (message: string) => void;
}): Promise<string | null> {
  const response = await fetch("/api/agent/tasks/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options.request),
    signal: options.signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`agent task stream ${response.status}`);
  }

  const taskId = response.headers.get("X-Task-Id");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    buffer = drainSseBuffer<AgentTaskEvent>(buffer, options.onEvent);
  }
  buffer += decoder.decode();
  drainSseBuffer<AgentTaskEvent>(buffer, options.onEvent, true);
  return taskId;
}

// --- MCP servers ---

export async function fetchMcpServers(): Promise<McpServerConfig[]> {
  const response = await fetch("/api/mcp/servers");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `mcp servers ${response.status}`);
  }
  return data.servers ?? [];
}

export async function upsertMcpServer(server: McpServerConfig): Promise<McpServerConfig> {
  const response = await fetch("/api/mcp/servers", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ server })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `mcp upsert ${response.status}`);
  }
  return data.server as McpServerConfig;
}

export async function deleteMcpServer(id: string): Promise<void> {
  const response = await fetch(`/api/mcp/servers/${encodeURIComponent(id)}`, {
    method: "DELETE"
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `mcp delete ${response.status}`);
  }
}

export async function testMcpServer(
  server: McpServerConfig
): Promise<{ ok: boolean; error?: string; toolCount?: number; resourceCount?: number }> {
  const response = await fetch("/api/mcp/servers/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ server })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `mcp test ${response.status}`);
  }
  return data;
}

export async function connectMcpServer(id: string): Promise<{ ok: boolean; toolCount: number; resourceCount: number }> {
  const response = await fetch(`/api/mcp/servers/${encodeURIComponent(id)}/connect`, {
    method: "POST"
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `mcp connect ${response.status}`);
  }
  return data;
}

export async function disconnectMcpServer(id: string): Promise<{ ok: boolean }> {
  const response = await fetch(`/api/mcp/servers/${encodeURIComponent(id)}/disconnect`, {
    method: "POST"
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `mcp disconnect ${response.status}`);
  }
  return data;
}

// --- Skills ---

export async function fetchSkills(): Promise<SkillPackageInfo[]> {
  const response = await fetch("/api/skills");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `skills ${response.status}`);
  }
  return data.skills ?? [];
}

export async function importSkillGit(
  gitUrl: string,
  branch?: string,
  subdirectory?: string
): Promise<SkillPackageInfo> {
  const response = await fetch("/api/skills/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      gitUrl,
      branch: branch ?? "main",
      subdirectory: subdirectory ?? ""
    })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `skill import ${response.status}`);
  }
  return data.skill as SkillPackageInfo;
}

export async function importSkillZip(file: File): Promise<SkillPackageInfo> {
  const form = new FormData();
  form.append("file", file, file.name);
  const response = await fetch("/api/skills/import-zip", {
    method: "POST",
    body: form
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `skill import-zip ${response.status}`);
  }
  return data.skill as SkillPackageInfo;
}

export async function upsertSkill(id: string, skill: SkillPackageInfo): Promise<SkillPackageInfo> {
  const response = await fetch(`/api/skills/${encodeURIComponent(id)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ skill })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `skill upsert ${response.status}`);
  }
  return data.skill as SkillPackageInfo;
}

export async function deleteSkill(id: string): Promise<void> {
  const response = await fetch(`/api/skills/${encodeURIComponent(id)}`, {
    method: "DELETE"
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `skill delete ${response.status}`);
  }
}

// --- Artifacts ---

export async function fetchArtifact(id: string): Promise<AgentArtifact> {
  const response = await fetch(`/api/artifacts/${encodeURIComponent(id)}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `artifact ${response.status}`);
  }
  return data.artifact as AgentArtifact;
}

export function downloadArtifactUrl(id: string): string {
  return `/api/artifacts/${encodeURIComponent(id)}/download`;
}

// --- Permission queue ---

export async function fetchPendingPermissions(taskId: string): Promise<PermissionRequest[]> {
  const response = await fetch(`/api/agent/tasks/${encodeURIComponent(taskId)}/permissions`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `permissions ${response.status}`);
  }
  return (data.permissions ?? []) as PermissionRequest[];
}

export async function approvePermission(
  permissionId: string,
  reason?: string
): Promise<PermissionRequest> {
  const response = await fetch(`/api/agent/permissions/${encodeURIComponent(permissionId)}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason ?? "" })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `approve ${response.status}`);
  }
  return data as PermissionRequest;
}

export async function rejectPermission(
  permissionId: string,
  reason?: string
): Promise<PermissionRequest> {
  const response = await fetch(`/api/agent/permissions/${encodeURIComponent(permissionId)}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason ?? "" })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `reject ${response.status}`);
  }
  return data as PermissionRequest;
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

function parseSsePayload<T>(data: string): T | null {
  try {
    return JSON.parse(data) as T;
  } catch {
    return null;
  }
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
