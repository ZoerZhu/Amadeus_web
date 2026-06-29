import type {
  ApiChatMessage,
  AssistantVoiceInfo,
  BuiltinSkillInfo,
  ChatAttachment,
  CodeTaskEvent,
  CodeTaskQuestionReplyRequest,
  CodeTaskStreamRequest,
  DesktopObserveRequest,
  DesktopObserveResponse,
  ChatMode,
  ConversationDetail,
  ConversationSummary,
  FileUploadResponse,
  McpCapability,
  McpPreset,
  McpServerConfig,
  MemoryDomain,
  MemoryIngestRequest,
  MemoryNode,
  MemoryProject,
  MemorySearchResult,
  MemoryTreeResponse,
  ModelSettings,
  OrchestratorArtifact,
  OrchestratorInvokeRequest,
  OrchestratorInvokeResponse,
  OrchestratorPermissionRequest,
  OrchestratorTaskControlAction,
  OrchestratorTaskCreateRequest,
  OrchestratorTaskDetail,
  OrchestratorTaskEvent,
  OrchestratorTaskMessageRequest,
  OrchestratorTaskSummary,
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
  if (settings.orchestrator) {
    payload.orchestrator = settings.orchestrator;
  } else if (settings.agent) {
    payload.orchestrator = settings.agent;
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

export async function fetchOrchestratorCapabilities(): Promise<OrchestratorInvokeResponse> {
  const response = await fetch("/api/orchestrator/capabilities");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `orchestrator capabilities ${response.status}`);
  }
  return data;
}

export async function invokeOrchestrator(request: Partial<OrchestratorInvokeRequest>): Promise<OrchestratorInvokeResponse> {
  const response = await fetch("/api/orchestrator/invoke", {
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
    throw new Error(data.detail || `orchestrator invoke ${response.status}`);
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
  const source = new EventSource(`/api/code-tasks/events?replay=${replay}`);

  const parsePayload = <T>(event: MessageEvent<string>): T | null => {
    return parseSsePayload<T>(event.data);
  };

  const handlers = CODE_TASK_EVENT_NAMES.map((eventName) => {
    const handler = (event: MessageEvent<string>) => {
      const payload = parsePayload<Record<string, unknown>>(event);
      if (!payload) {
        options.onError?.(`task event parse failed: ${eventName}`);
        return;
      }
      if (payload.taskId && payload.taskId !== options.taskId) {
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

export async function uploadWorkspaceFile(options: {
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
// Orchestrator / MCP / skills API
// ---------------------------------------------------------------------------

export async function fetchOrchestratorTasks(
  conversationId?: string | null,
  includeLegacy = false
): Promise<OrchestratorTaskSummary[]> {
  const params = new URLSearchParams();
  if (conversationId) {
    params.set("conversationId", conversationId);
  }
  params.set("includeLegacy", includeLegacy ? "true" : "false");
  const query = params.toString() ? `?${params.toString()}` : "";
  const response = await fetch(`/api/orchestrator/tasks${query}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `orchestrator tasks ${response.status}`);
  }
  return data.tasks ?? [];
}

export async function fetchOrchestratorTaskDetail(id: string): Promise<OrchestratorTaskDetail> {
  const response = await fetch(`/api/orchestrator/tasks/${encodeURIComponent(id)}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `orchestrator task ${response.status}`);
  }
  return data.task as OrchestratorTaskDetail;
}

export async function controlOrchestratorTask(
  id: string,
  action: OrchestratorTaskControlAction,
  reason?: string
): Promise<{ ok: boolean }> {
  const response = await fetch(`/api/orchestrator/tasks/${encodeURIComponent(id)}/control`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, reason: reason ?? "" })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `orchestrator control ${response.status}`);
  }
  return data;
}

export function streamOrchestratorTaskEvents(options: {
  taskId: string;
  afterSeq?: number;
  onEvent: (event: OrchestratorTaskEvent) => void;
  onError?: (message: string) => void;
}): () => void {
  const afterSeq = options.afterSeq ?? 0;
  const url = `/api/orchestrator/tasks/${encodeURIComponent(options.taskId)}/events?afterSeq=${afterSeq}`;
  const controller = new AbortController();

  const readerPromise = (async () => {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok || !response.body) {
      options.onError?.(`orchestrator events ${response.status}`);
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
      buffer = drainSseBuffer<OrchestratorTaskEvent>(buffer, options.onEvent);
    }
    buffer += decoder.decode();
    drainSseBuffer<OrchestratorTaskEvent>(buffer, options.onEvent, true);
  })();

  readerPromise.catch((error) => {
    if (controller.signal.aborted) {
      return;
    }
    options.onError?.(error instanceof Error ? error.message : "orchestrator events stream failed");
  });

  return () => controller.abort();
}

export async function createOrchestratorTask(
  request: OrchestratorTaskCreateRequest
): Promise<{ taskId: string }> {
  const response = await fetch("/api/orchestrator/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `orchestrator task create ${response.status}`);
  }
  return { taskId: String(data.taskId ?? data.id ?? "") };
}

export async function appendOrchestratorTaskMessage(
  taskId: string,
  request: OrchestratorTaskMessageRequest
): Promise<{ taskId: string }> {
  const response = await fetch(`/api/orchestrator/tasks/${encodeURIComponent(taskId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `orchestrator task message ${response.status}`);
  }
  return { taskId: String(data.taskId ?? data.id ?? taskId) };
}

export async function streamOrchestratorTask(options: {
  request: OrchestratorTaskCreateRequest;
  signal?: AbortSignal;
  onEvent: (event: OrchestratorTaskEvent) => void;
  onError?: (message: string) => void;
}): Promise<string | null> {
  const response = await fetch("/api/orchestrator/tasks/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options.request),
    signal: options.signal
  });

  if (!response.ok || !response.body) {
    throw new Error(`orchestrator task stream ${response.status}`);
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
    buffer = drainSseBuffer<OrchestratorTaskEvent>(buffer, options.onEvent);
  }
  buffer += decoder.decode();
  drainSseBuffer<OrchestratorTaskEvent>(buffer, options.onEvent, true);
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

export async function fetchArtifact(id: string): Promise<OrchestratorArtifact> {
  const response = await fetch(`/api/orchestrator/artifacts/${encodeURIComponent(id)}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `artifact ${response.status}`);
  }
  return data.artifact as OrchestratorArtifact;
}

export function downloadArtifactUrl(id: string): string {
  return `/api/orchestrator/artifacts/${encodeURIComponent(id)}/download`;
}

// --- Permission queue ---

export async function fetchPendingPermissions(taskId: string): Promise<OrchestratorPermissionRequest[]> {
  const response = await fetch(`/api/orchestrator/tasks/${encodeURIComponent(taskId)}/permissions`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `permissions ${response.status}`);
  }
  return (data.permissions ?? []) as OrchestratorPermissionRequest[];
}

export async function approvePermission(
  permissionId: string,
  reason?: string
): Promise<OrchestratorPermissionRequest> {
  const response = await fetch(`/api/orchestrator/permissions/${encodeURIComponent(permissionId)}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason ?? "" })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `approve ${response.status}`);
  }
  return data as OrchestratorPermissionRequest;
}

export async function rejectPermission(
  permissionId: string,
  reason?: string
): Promise<OrchestratorPermissionRequest> {
  const response = await fetch(`/api/orchestrator/permissions/${encodeURIComponent(permissionId)}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason ?? "" })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `reject ${response.status}`);
  }
  return data as OrchestratorPermissionRequest;
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

// --- MCP presets and capabilities ---

export async function fetchMcpPresets(): Promise<McpPreset[]> {
  const response = await fetch("/api/mcp/presets");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `presets ${response.status}`);
  }
  return (data.presets ?? []) as McpPreset[];
}

export async function fetchMcpCapabilities(): Promise<Record<string, McpCapability>> {
  const response = await fetch("/api/mcp/capabilities");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `capabilities ${response.status}`);
  }
  return (data.capabilities ?? {}) as Record<string, McpCapability>;
}

// --- Builtin skills ---

export async function fetchBuiltinSkills(): Promise<BuiltinSkillInfo[]> {
  const response = await fetch("/api/skills/builtin");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `builtin skills ${response.status}`);
  }
  return (data.skills ?? []) as BuiltinSkillInfo[];
}

export async function installBuiltinSkills(): Promise<{ installed: string[] }> {
  const response = await fetch("/api/skills/install-builtin", { method: "POST" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `install ${response.status}`);
  }
  return data as { installed: string[] };
}

// --- Memory ---

export async function fetchMemoryTree(options?: {
  path?: string;
  depth?: number;
}): Promise<MemoryTreeResponse> {
  const params = new URLSearchParams();
  params.set("path", options?.path ?? "/");
  params.set("depth", String(options?.depth ?? 3));
  const response = await fetch(`/api/memory/tree?${params.toString()}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `memory tree ${response.status}`);
  }
  return {
    tree: (data.tree ?? []).map(normalizeMemoryNode),
    nodes: (data.nodes ?? []).map(normalizeMemoryNode)
  };
}

export async function searchMemory(options: {
  query: string;
  domains?: MemoryDomain[];
  projectId?: string | null;
  nodeTopK?: number;
  leafTopK?: number;
  contextBudgetChars?: number;
}): Promise<MemorySearchResult> {
  const response = await fetch("/api/memory/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: options.query,
      domains: options.domains ?? [],
      projectId: options.projectId || undefined,
      nodeTopK: options.nodeTopK ?? 8,
      leafTopK: options.leafTopK ?? 8,
      contextBudgetChars: options.contextBudgetChars ?? 5000
    })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `memory search ${response.status}`);
  }
  return normalizeMemorySearchResult(data.result ?? {
    selectedPaths: [],
    topicNodes: [],
    leafNodes: [],
    contextText: ""
  });
}

export async function ingestMemory(request: MemoryIngestRequest): Promise<{ ok: boolean; jobId: string }> {
  const response = await fetch("/api/memory/ingest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `memory ingest ${response.status}`);
  }
  return { ok: Boolean(data.ok), jobId: String(data.jobId ?? "") };
}

export async function rebuildMemoryIndex(options?: {
  rebuildFts?: boolean;
  rebuildVectors?: boolean;
}): Promise<Record<string, unknown>> {
  const response = await fetch("/api/memory/rebuild-index", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      rebuildFts: options?.rebuildFts ?? true,
      rebuildVectors: options?.rebuildVectors ?? false
    })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `memory rebuild ${response.status}`);
  }
  return (data.result ?? {}) as Record<string, unknown>;
}

export async function fetchMemoryProjects(): Promise<MemoryProject[]> {
  const response = await fetch("/api/projects");
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `projects ${response.status}`);
  }
  return (data.projects ?? []).map(normalizeMemoryProject);
}

export async function createMemoryProject(options: {
  name: string;
  description?: string;
  workspacePath?: string;
  color?: string;
}): Promise<MemoryProject> {
  const response = await fetch("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: options.name,
      description: options.description ?? "",
      workspacePath: options.workspacePath ?? "",
      color: options.color ?? "#8b5cf6"
    })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `project create ${response.status}`);
  }
  return normalizeMemoryProject(data.project ?? {});
}

export async function linkConversationProject(options: {
  conversationId: string;
  projectId: string;
}): Promise<void> {
  const response = await fetch(`/api/conversations/${encodeURIComponent(options.conversationId)}/project`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ projectId: options.projectId })
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `conversation project ${response.status}`);
  }
}

function normalizeMemoryProject(raw: Record<string, unknown>): MemoryProject {
  return {
    id: String(raw.id ?? ""),
    name: String(raw.name ?? ""),
    description: String(raw.description ?? ""),
    workspacePath: String(raw.workspacePath ?? raw.workspace_path ?? ""),
    color: String(raw.color ?? "#8b5cf6"),
    createdAt: String(raw.createdAt ?? raw.created_at ?? ""),
    updatedAt: String(raw.updatedAt ?? raw.updated_at ?? "")
  };
}

function normalizeMemorySearchResult(raw: Record<string, unknown>): MemorySearchResult {
  const selectedPaths = Array.isArray(raw.selectedPaths ?? raw.selected_paths)
    ? (raw.selectedPaths ?? raw.selected_paths) as unknown[]
    : [];
  const topicNodes = Array.isArray(raw.topicNodes ?? raw.topic_nodes)
    ? (raw.topicNodes ?? raw.topic_nodes) as unknown[]
    : [];
  const leafNodes = Array.isArray(raw.leafNodes ?? raw.leaf_nodes)
    ? (raw.leafNodes ?? raw.leaf_nodes) as unknown[]
    : [];
  return {
    selectedPaths: selectedPaths.map((path) =>
      Array.isArray(path) ? path.map((item) => normalizeMemoryNode(item as Record<string, unknown>)) : []
    ),
    topicNodes: topicNodes.map((item) => normalizeMemoryNode(item as Record<string, unknown>)),
    leafNodes: leafNodes.map((item) => normalizeMemoryNode(item as Record<string, unknown>)),
    contextText: String(raw.contextText ?? raw.context_text ?? "")
  };
}

function normalizeMemoryNode(raw: Record<string, unknown>): MemoryNode {
  const children = Array.isArray(raw.children) ? raw.children : [];
  return {
    id: String(raw.id ?? ""),
    parentId: optionalString(raw.parentId ?? raw.parent_id),
    nodeType: String(raw.nodeType ?? raw.node_type ?? "leaf") as MemoryNode["nodeType"],
    domain: String(raw.domain ?? ""),
    label: String(raw.label ?? ""),
    path: String(raw.path ?? ""),
    depth: Number(raw.depth ?? 0),
    summary: String(raw.summary ?? ""),
    fullContent: String(raw.fullContent ?? raw.full_content ?? ""),
    category: String(raw.category ?? "general") as MemoryNode["category"],
    keywords: Array.isArray(raw.keywords) ? raw.keywords.map(String) : [],
    projectId: optionalString(raw.projectId ?? raw.project_id),
    conversationId: optionalString(raw.conversationId ?? raw.conversation_id),
    sourceMessageIds: Array.isArray(raw.sourceMessageIds ?? raw.source_message_ids)
      ? ((raw.sourceMessageIds ?? raw.source_message_ids) as unknown[]).map(String)
      : [],
    sourceSummary: String(raw.sourceSummary ?? raw.source_summary ?? ""),
    importance: Number(raw.importance ?? 0.5),
    confidence: Number(raw.confidence ?? 0.7),
    accessCount: Number(raw.accessCount ?? raw.access_count ?? 0),
    leafCount: Number(raw.leafCount ?? raw.leaf_count ?? 0),
    lastAccessedAt: optionalString(raw.lastAccessedAt ?? raw.last_accessed_at),
    createdAt: String(raw.createdAt ?? raw.created_at ?? ""),
    updatedAt: String(raw.updatedAt ?? raw.updated_at ?? ""),
    expiresAt: optionalString(raw.expiresAt ?? raw.expires_at),
    isActive: Boolean(raw.isActive ?? raw.is_active ?? true),
    children: children.map((item) => normalizeMemoryNode(item as Record<string, unknown>))
  };
}

function optionalString(value: unknown): string | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  return String(value);
}
