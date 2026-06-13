import type { ReactNode } from "react";
import type {
  ApiChatMessage,
  AssistantVoiceInfo,
  ChatAttachment,
  ChatMessage,
  ConversationDetail,
  CodeTaskMobileViewSnapshot,
  CodeTaskQuestionItem,
  DesktopAssistantSettings,
  ModelSettings,
  StoredSettings,
  ToolTraceEvent,
  UploadedFileInfo,
  SpeechInputSettings,
  VoiceInputInfo,
  VisionSettings,
  VoiceSettings
} from "../types";
const STORAGE_KEY = "amadeus-web-settings-v1";
const PERSONA_ID = "kurisu_amadeus";
const LEFT_SIDEBAR_WIDTH = 372;
const SETTINGS_SIDEBAR_WIDTH = 388;
const PANEL_GAP = 14;
const EDGE_MARGIN = 18;
const CHAT_MIN_WIDTH = 340;
const CHAT_MAX_WIDTH = 620;
const MIN_BOOT_DURATION_MS = 2000;
const LIVE2D_ACTIVE_STORAGE_KEY = "amadeus-live2d-active-model-v1";
const LIVE2D_LOCK_STORAGE_KEY = "amadeus-live2d-transform-locked-v1";
const LIVE2D_IMPORT_INPUT_ID = "amadeus-live2d-folder-input";
const DEFAULT_CODE_TASK_WORKSPACE = "";

const DEFAULT_MODEL_SETTINGS: ModelSettings = {
  providerName: "OpenAI",
  baseUrl: "https://api.openai.com/v1",
  model: "gpt-4.1-mini",
  apiKey: "",
  useRemote: true
};

const DEFAULT_VISION_SETTINGS: VisionSettings = {
  enabled: true,
  screenCaptureEnabled: false,
  providerName: "",
  baseUrl: "",
  model: "gpt-4.1-mini",
  apiKey: "",
  useRemote: true
};

const DEFAULT_SPEECH_INPUT_SETTINGS: SpeechInputSettings = {
  enabled: true,
  providerName: "小米 MiMo ASR",
  baseUrl: "https://api.xiaomimimo.com/v1",
  model: "mimo-v2.5-asr",
  apiKey: "",
  language: "auto",
  useRemote: true
};

const DEFAULT_VOICE_SETTINGS: VoiceSettings = {
  ttsBackend: "local",
  siliconFlowApiKey: "",
  clonedVoiceUri: "",
  autoPlay: false,
  syncTextOutput: true,
  speed: 1,
  gain: 0
};

const DEFAULT_DESKTOP_ASSISTANT_SETTINGS: DesktopAssistantSettings = {
  subtitleEnabled: true,
  voiceOutputEnabled: false,
  autoScreenshotEnabled: false,
  screenshotIntervalSeconds: 15,
  cameraEnabled: false
};

function normalizeDesktopAssistantSettings(value?: Partial<DesktopAssistantSettings> | null): DesktopAssistantSettings {
  const merged = { ...DEFAULT_DESKTOP_ASSISTANT_SETTINGS, ...(value ?? {}) };
  return {
    subtitleEnabled: Boolean(merged.subtitleEnabled),
    voiceOutputEnabled: Boolean(merged.voiceOutputEnabled),
    autoScreenshotEnabled: Boolean(merged.autoScreenshotEnabled),
    screenshotIntervalSeconds: clamp(Number(merged.screenshotIntervalSeconds || 15), 5, 120),
    cameraEnabled: Boolean(merged.cameraEnabled)
  };
}

function normalizeSpeechInputSettings(value?: Partial<SpeechInputSettings> | null): SpeechInputSettings {
  const merged = { ...DEFAULT_SPEECH_INPUT_SETTINGS, ...(value ?? {}) };
  if (!merged.providerName.trim() && !merged.baseUrl.trim() && (!merged.model.trim() || merged.model === "whisper-1")) {
    return DEFAULT_SPEECH_INPUT_SETTINGS;
  }
  if (
    merged.providerName === "SiliconFlow ASR" &&
    merged.baseUrl === "https://api.siliconflow.cn/v1" &&
    merged.model === "FunAudioLLM/SenseVoiceSmall"
  ) {
    return DEFAULT_SPEECH_INPUT_SETTINGS;
  }
  return merged;
}
const ACCEPTED_UPLOAD_TYPES = [
  ".txt",
  ".md",
  ".markdown",
  ".json",
  ".jsonl",
  ".yaml",
  ".yml",
  ".toml",
  ".ini",
  ".cfg",
  ".conf",
  ".csv",
  ".tsv",
  ".py",
  ".pyi",
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".css",
  ".html",
  ".xml",
  ".sql",
  ".sh",
  ".ps1",
  ".bat",
  ".pdf",
  ".doc",
  ".docx",
  ".xls",
  ".xlsx",
  ".png",
  ".jpg",
  ".jpeg",
  ".webp",
  ".gif",
  "text/*",
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
  "application/json",
  "application/pdf",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
].join(",");

type ConfigSection = "profile" | "model" | "vision" | "speechInput" | "voice" | "live2d" | "interface";
type AudioQueueItem = {
  url: string;
  assistantId?: string;
  displayText?: string;
  index?: number;
};
type ToolResultLink = {
  title: string;
  url: string;
  domain?: string;
  snippet?: string;
};
type MarkdownBlock =
  | { type: "paragraph"; lines: string[] }
  | { type: "heading"; level: number; text: string }
  | { type: "orderedList" | "unorderedList"; items: string[] }
  | { type: "code"; text: string };
type RightPanelTab = "chat" | "tasks";
type CodeTaskMessageKind =
  | "user"
  | "assistant"
  | "status"
  | "tool"
  | "command"
  | "file"
  | "permission"
  | "question"
  | "error"
  | "done";
type CodeTaskQuestionData = {
  requestId: string;
  sessionId: string;
  serverUrl?: string;
  questions: CodeTaskQuestionItem[];
  v2?: boolean;
  answered?: boolean;
  rejected?: boolean;
  answers?: string[][];
};
type CodeTaskMessage = {
  id: string;
  kind: CodeTaskMessageKind;
  title?: string;
  text: string;
  detail?: string;
  question?: CodeTaskQuestionData;
  createdAt: string;
};
type CodeTaskRecord = {
  id: string;
  title: string;
  prompt: string;
  workspacePath: string;
  status: string;
  sessionId?: string;
  messages: CodeTaskMessage[];
  summary?: string;
  summarySpeechText?: string;
  summaryAudioUrl?: string;
  createdAt: string;
  updatedAt: string;
};
type CodeTaskPhase = {
  id: string;
  title: string;
  status: "running" | "completed" | "error";
  messages: CodeTaskMessage[];
};

declare global {
  interface Window {
    amadeusDesktop?: {
      selectFolder?: () => Promise<string | null>;
      captureScreen?: () => Promise<{ dataUrl: string; filename: string } | null>;
      setDesktopAssistantMode?: (enabled: boolean) => Promise<{ ok: boolean; reason?: string }>;
      moveAssistantWindow?: (delta: { dx: number; dy: number }) => Promise<{ ok: boolean; reason?: string }>;
      getCameraAvailability?: () => Promise<{ available: boolean; reason?: string; source?: string }>;
      windowCommand?: (command: "minimize" | "toggle-maximize" | "close") => Promise<{ ok: boolean; reason?: string }>;
    };
  }
}

const TOOL_EVENT_LOG_PREFIX = "__AMADEUS_TOOL_EVENT__";
const CODE_TASK_HISTORY_STORAGE_KEY = "amadeus-code-task-history-v1";
const CODE_TASK_FLUSH_INTERVAL_MS = 260;
const CODE_TASK_MAX_HISTORY = 48;
const CODE_TASK_MAX_MESSAGES = 160;
const CODE_TASK_MAX_ASSISTANT_TEXT = 80000;
const CODE_TASK_MAX_DETAIL_TEXT = 8000;
const CODE_TASK_SUMMARY_TEXT_LIMIT = 6000;
const CODE_TASK_TRUNCATED_SUFFIX = "\n\n[OpenCode 输出较长，后续内容已省略；完整结果请查看项目文件或 OpenCode 会话。]";
const DISPLAY_EMOTIONS = new Set(["neutral", "anger", "joy", "sadness", "shy", "smile", "surprise", "unhappy"]);
const LIVE2D_EMOTIONS = new Set([
  "neutral",
  "anger",
  "joy",
  "sadness",
  "shy",
  "shy2",
  "smile1",
  "smile2",
  "surprise",
  "unhappy"
]);
const LIVE2D_EMOTION_ALIASES: Record<string, string> = {
  smile: "smile1"
};

function createId(): string {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function loadStoredSettings(): StoredSettings {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "");
    return {
      model: { ...DEFAULT_MODEL_SETTINGS, ...(parsed.model ?? {}) },
      vision: { ...DEFAULT_VISION_SETTINGS, ...(parsed.vision ?? {}) },
      speechInput: normalizeSpeechInputSettings(parsed.speechInput),
      voice: { ...DEFAULT_VOICE_SETTINGS, ...(parsed.voice ?? {}) },
      desktopAssistant: normalizeDesktopAssistantSettings(parsed.desktopAssistant),
      mode: parsed.mode === "thinking" ? "thinking" : "fast"
    };
  } catch {
    return {
      model: DEFAULT_MODEL_SETTINGS,
      vision: DEFAULT_VISION_SETTINGS,
      speechInput: DEFAULT_SPEECH_INPUT_SETTINGS,
      voice: DEFAULT_VOICE_SETTINGS,
      desktopAssistant: DEFAULT_DESKTOP_ASSISTANT_SETTINGS,
      mode: "fast"
    };
  }
}

function toApiMessages(messages: ChatMessage[]): ApiChatMessage[] {
  return messages
    .filter((message) => message.content.trim())
    .map((message) => ({
      role: message.role,
      content: message.role === "user" ? withUserVoiceInputContext(message.content, message.voiceInput) : message.content
    }));
}

function withUserVoiceInputContext(content: string, voiceInput?: VoiceInputInfo): string {
  if (!voiceInput) {
    return content;
  }
  return `${content.trim()}\n\n<用户语音输入>${JSON.stringify(voiceInput)}</用户语音输入>`;
}

function imageAttachmentFromParts(originalFilename: string, path: string): ChatAttachment {
  const filename = path.split("/").pop() || originalFilename || "image";
  return {
    path,
    originalFilename: originalFilename || filename,
    filename,
    mediaType: "image",
    contentType: imageContentType(filename)
  };
}

function imageContentType(filename: string): string {
  const suffix = filename.split(".").pop()?.toLowerCase() ?? "";
  if (suffix === "jpg" || suffix === "jpeg") {
    return "image/jpeg";
  }
  if (suffix === "webp") {
    return "image/webp";
  }
  if (suffix === "gif") {
    return "image/gif";
  }
  return "image/png";
}

function extractImageAttachmentsFromContent(content: string): ChatAttachment[] {
  const attachments = new Map<string, ChatAttachment>();
  const add = (originalFilename: string, path: string) => {
    const cleanPath = path.trim();
    if (!cleanPath || attachments.has(cleanPath)) {
      return;
    }
    attachments.set(cleanPath, imageAttachmentFromParts(originalFilename.trim(), cleanPath));
  };

  for (const match of content.matchAll(/已附加图片：([^\n]+)\n路径：([^\n]+)\n类型：image/g)) {
    add(match[1], match[2]);
  }
  for (const match of content.matchAll(/^- ([^:\n]+):\s*(agent_uploads\/[^\s\n]+\.(?:png|jpe?g|webp|gif))$/gim)) {
    add(match[1], match[2]);
  }
  return Array.from(attachments.values());
}

function extractVoiceInputFromContent(content: string): VoiceInputInfo | undefined {
  const match = /<用户语音输入>([\s\S]*?)<\/用户语音输入>/.exec(content);
  if (!match) {
    return undefined;
  }
  try {
    const value = JSON.parse(match[1]);
    if (!value || typeof value !== "object") {
      return undefined;
    }
    const record = value as Partial<VoiceInputInfo>;
    if (!record.audioUrl || !record.path) {
      return undefined;
    }
    return {
      audioUrl: String(record.audioUrl),
      path: String(record.path),
      filename: String(record.filename || "voice.webm"),
      contentType: String(record.contentType || "audio/webm"),
      durationSeconds: Number(record.durationSeconds || 0),
      transcript: String(record.transcript || ""),
      waveform: Array.isArray(record.waveform)
        ? record.waveform.map((item) => Number(item)).filter((item) => Number.isFinite(item)).slice(0, 32)
        : []
    };
  } catch {
    return undefined;
  }
}

function normalizeAssistantVoice(value: unknown): AssistantVoiceInfo | undefined {
  if (!value || typeof value !== "object") {
    return undefined;
  }
  const record = value as Partial<AssistantVoiceInfo>;
  const segments = Array.isArray(record.segments)
    ? record.segments
        .map((segment, fallbackIndex) => ({
          index: Number(segment?.index ?? fallbackIndex),
          url: String(segment?.url || "")
        }))
        .filter((segment) => Number.isFinite(segment.index) && segment.url)
        .sort((a, b) => a.index - b.index)
    : [];
  const audioUrls = (Array.isArray(record.audioUrls) ? record.audioUrls : [])
    .map((url) => String(url || ""))
    .filter(Boolean);
  const urls = segments.length > 0 ? segments.map((segment) => segment.url) : audioUrls;
  if (urls.length === 0) {
    return undefined;
  }
  return {
    audioUrls: urls,
    segments: segments.length > 0 ? segments : urls.map((url, index) => ({ index, url }))
  };
}

function stripUserVisionContext(content: string): string {
  return content
    .replace(/\n*<视觉理解摘要>[\s\S]*?<\/视觉理解摘要>\s*/g, "\n")
    .replace(/\n*<用户语音输入>[\s\S]*?<\/用户语音输入>\s*/g, "\n")
    .replace(
      /(?:^|\n+)已附加图片：[^\n]+\n路径：[^\n]+\n类型：image\n请先根据视觉理解摘要回答，不要把图片当作普通文本文件读取。\s*/g,
      "\n"
    )
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function normalizeMessageContent(content: string, role: ChatMessage["role"]): string {
  if (role === "user") {
    return stripUserVisionContext(content);
  }
  if (role !== "assistant") {
    return content;
  }
  return content.replace(/^[\r\n]+/, "");
}

function toChatMessages(messages: ConversationDetail["messages"]): ChatMessage[] {
  return messages.map((message) => ({
    id: message.id,
    role: message.role,
    content: normalizeMessageContent(message.content, message.role),
    attachments: message.role === "user" ? extractImageAttachmentsFromContent(message.content) : undefined,
    voiceInput: message.role === "user" ? extractVoiceInputFromContent(message.content) : undefined,
    assistantVoice: message.role === "assistant" ? normalizeAssistantVoice(message.assistantVoice) : undefined,
    thinking: message.thinking || "",
    createdAt: message.createdAt
  }));
}

function buildConversationTitle(text: string): string {
  const title = text.replace(/\s+/g, " ").trim();
  if (!title) {
    return "新对话";
  }
  return title.length > 32 ? `${title.slice(0, 32)}...` : title;
}

function sanitizeFilename(value: string): string {
  const name = value.replace(/[\\/:*?"<>|]/g, " ").replace(/\s+/g, " ").trim();
  return (name || "conversation").slice(0, 80);
}

function formatExportTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function conversationToMarkdown(conversation: ConversationDetail): string {
  const lines = [
    `# ${conversation.title}`,
    "",
    `- Persona: ${conversation.personaId}`,
    `- Mode: ${conversation.mode}`,
    `- Created: ${formatExportTime(conversation.createdAt)}`,
    `- Updated: ${formatExportTime(conversation.updatedAt)}`,
    ""
  ];

  for (const message of conversation.messages) {
    const role = message.role === "assistant" ? "Kurisu Amadeus" : "User";
    lines.push(`## ${role} · ${formatExportTime(message.createdAt)}`, "");
    if (message.thinking?.trim()) {
      lines.push("### Thinking", "", message.thinking.trim(), "");
    }
    lines.push(message.content.trim() || "(empty)", "");
  }
  return lines.join("\n").trimEnd() + "\n";
}

function downloadText(filename: string, text: string, mimeType = "text/markdown;charset=utf-8") {
  const url = URL.createObjectURL(new Blob([text], { type: mimeType }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function uploadedFilePromptLine(file: { originalFilename: string; path: string; textType: string; mediaType?: string }): string {
  if (file.mediaType === "image") {
    return `已附加图片：${file.originalFilename}\n路径：${file.path}\n类型：image\n请先根据视觉理解摘要回答，不要把图片当作普通文本文件读取。`;
  }
  return `已上传文件：${file.originalFilename}\n路径：${file.path}\n类型：${file.textType}\n请在需要时调用 file_reader 读取并参考这个文件。`;
}

function composeMessageText(text: string, files: UploadedFileInfo[]): string {
  const content = text.trim();
  const nonImageFiles = files.filter((file) => file.mediaType !== "image" && !file.visionReadable);
  const hasImages = files.length > nonImageFiles.length;
  const fileText = nonImageFiles.map(uploadedFilePromptLine).join("\n\n");
  if (content && fileText) {
    return `${content}\n\n${fileText}`;
  }
  return content || fileText || (hasImages ? "请理解这些图片。" : "");
}

function uploadTypeLabel(file: UploadedFileInfo): string {
  if (file.mediaType === "image" || file.visionReadable) {
    return "图片";
  }
  const suffix = file.filename.split(".").pop()?.toLowerCase() ?? "";
  if (["pdf", "doc", "docx", "xls", "xlsx"].includes(suffix)) {
    return "文档";
  }
  if (file.textType.includes("code")) {
    return "代码";
  }
  if (file.textType.includes("csv") || file.textType.includes("excel")) {
    return "表格";
  }
  return "文档";
}

function parseMarkdownBlocks(text: string): MarkdownBlock[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: MarkdownBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    if (line.trimStart().startsWith("```")) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].trimStart().startsWith("```")) {
        code.push(lines[index]);
        index += 1;
      }
      index += 1;
      blocks.push({ type: "code", text: code.join("\n") });
      continue;
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(line.trim());
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2] });
      index += 1;
      continue;
    }

    const ordered = /^\s*\d+[.)]\s+(.+)$/.exec(line);
    const unordered = /^\s*[-*]\s+(.+)$/.exec(line);
    if (ordered || unordered) {
      const type = ordered ? "orderedList" : "unorderedList";
      const marker = ordered ? /^\s*\d+[.)]\s+(.+)$/ : /^\s*[-*]\s+(.+)$/;
      const items: string[] = [];
      while (index < lines.length) {
        const item = marker.exec(lines[index]);
        if (!item) {
          if (lines[index].trim() && /^\s+/.test(lines[index]) && items.length > 0) {
            items[items.length - 1] += `\n${lines[index].trim()}`;
            index += 1;
            continue;
          }
          break;
        }
        items.push(item[1].trim());
        index += 1;
      }
      blocks.push({ type, items });
      continue;
    }

    const paragraph: string[] = [];
    while (index < lines.length && lines[index].trim()) {
      if (/^(#{1,3})\s+/.test(lines[index].trim()) || /^\s*(?:\d+[.)]|[-*])\s+/.test(lines[index])) {
        break;
      }
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push({ type: "paragraph", lines: paragraph });
  }

  return blocks;
}

function renderInlineMarkdown(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /\[([^\]]+)]\((https?:\/\/[^\s)]+)\)|`([^`]+)`|\*\*([^*]+)\*\*/g;
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(text.slice(cursor, match.index));
    }
    if (match[1] && match[2]) {
      nodes.push(
        <a href={match[2]} key={`${keyPrefix}-link-${match.index}`} rel="noreferrer" target="_blank">
          {match[1]}
        </a>
      );
    } else if (match[3]) {
      nodes.push(<code key={`${keyPrefix}-code-${match.index}`}>{match[3]}</code>);
    } else if (match[4]) {
      nodes.push(<strong key={`${keyPrefix}-strong-${match.index}`}>{match[4]}</strong>);
    }
    cursor = match.index + match[0].length;
  }

  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }
  return nodes;
}

function renderMarkdownText(text: string, keyPrefix: string): ReactNode[] {
  return text.split("\n").flatMap((line, index) => {
    const nodes = renderInlineMarkdown(line, `${keyPrefix}-${index}`);
    return index === 0 ? nodes : [<br key={`${keyPrefix}-br-${index}`} />, ...nodes];
  });
}

function renderMarkdownContent(text: string): ReactNode {
  const blocks = parseMarkdownBlocks(text);
  if (blocks.length === 0) {
    return <div className="animated-text markdown-content">{text}</div>;
  }
  return (
    <div className="animated-text markdown-content">
      {blocks.map((block, index) => {
        const key = `md-${index}`;
        if (block.type === "heading") {
          const Tag = (`h${Math.min(block.level + 2, 4)}` as keyof JSX.IntrinsicElements);
          return <Tag key={key}>{renderMarkdownText(block.text, key)}</Tag>;
        }
        if (block.type === "orderedList") {
          return (
            <ol key={key}>
              {block.items.map((item, itemIndex) => (
                <li key={`${key}-${itemIndex}`}>{renderMarkdownText(item, `${key}-${itemIndex}`)}</li>
              ))}
            </ol>
          );
        }
        if (block.type === "unorderedList") {
          return (
            <ul key={key}>
              {block.items.map((item, itemIndex) => (
                <li key={`${key}-${itemIndex}`}>{renderMarkdownText(item, `${key}-${itemIndex}`)}</li>
              ))}
            </ul>
          );
        }
        if (block.type === "code") {
          return <pre key={key}>{block.text}</pre>;
        }
        if (block.type === "paragraph") {
          return <p key={key}>{renderMarkdownText(block.lines.join("\n"), key)}</p>;
        }
        return null;
      })}
    </div>
  );
}

function formatHistoryTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

function formatMessageTime(value?: string): string {
  const date = value ? new Date(value) : new Date();
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit"
  });
}

function normalizeDisplayEmotion(value?: string): string {
  const emotion = value?.trim().toLowerCase() ?? "";
  if (emotion === "smile1" || emotion === "smile2") {
    return "smile";
  }
  return DISPLAY_EMOTIONS.has(emotion) ? emotion : "neutral";
}

function normalizeLive2dEmotion(...values: Array<string | undefined>): string {
  for (const value of values) {
    const emotion = value?.trim().toLowerCase() ?? "";
    const aliased = LIVE2D_EMOTION_ALIASES[emotion] ?? emotion;
    if (LIVE2D_EMOTIONS.has(aliased)) {
      return aliased;
    }
  }
  return "neutral";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function parseJsonValue(value?: string): unknown {
  if (!value?.trim()) {
    return undefined;
  }
  try {
    return JSON.parse(value);
  } catch {
    return undefined;
  }
}

function normalizeToolTraceEvent(value: unknown): ToolTraceEvent | null {
  if (!isRecord(value)) {
    return null;
  }
  const status = value.status;
  if (status !== "started" && status !== "completed" && status !== "failed") {
    return null;
  }
  const name = asString(value.name);
  if (!name) {
    return null;
  }
  const event: ToolTraceEvent = {
    id: asString(value.id) || `stored-${name}`,
    name,
    status
  };
  if (isRecord(value.arguments)) {
    event.arguments = value.arguments;
  }
  if (typeof value.summary === "string") {
    event.summary = value.summary;
  }
  if (typeof value.resultPreview === "string") {
    event.resultPreview = value.resultPreview;
  }
  if ("result" in value) {
    event.result = value.result;
  }
  if (typeof value.error === "string") {
    event.error = value.error;
  }
  return event;
}

function parseStoredToolEvent(line: string): ToolTraceEvent | null {
  const text = line.trim();
  if (!text.startsWith(TOOL_EVENT_LOG_PREFIX)) {
    return null;
  }
  return normalizeToolTraceEvent(parseJsonValue(text.slice(TOOL_EVENT_LOG_PREFIX.length).trim()));
}

function mergeToolEvents(events: ToolTraceEvent[]): ToolTraceEvent[] {
  const merged = new Map<string, ToolTraceEvent>();
  for (const event of events) {
    const key = event.id || event.name;
    merged.set(key, { ...(merged.get(key) ?? {}), ...event });
  }
  return Array.from(merged.values());
}

function extractToolEventsFromThinking(text: string): ToolTraceEvent[] {
  const storedEvents = text
    .split(/\r?\n/)
    .map((line) => parseStoredToolEvent(line))
    .filter((event): event is ToolTraceEvent => Boolean(event));
  if (storedEvents.length > 0) {
    return mergeToolEvents(storedEvents);
  }

  const eventsByName = new Map<string, ToolTraceEvent>();
  for (const match of text.matchAll(/调用工具\s+([\w.-]+)。/g)) {
    const name = match[1];
    eventsByName.set(name, {
      id: `thinking-${name}`,
      name,
      status: "started",
      summary: "工具调用已记录在思考过程中。"
    });
  }
  for (const match of text.matchAll(/工具\s+([\w.-]+)\s+完成：([^。\n]+)。+/g)) {
    const name = match[1];
    eventsByName.set(name, {
      id: `thinking-${name}`,
      name,
      status: "completed",
      summary: match[2]
    });
  }
  for (const match of text.matchAll(/工具\s+([\w.-]+)\s+失败：([^。\n]+)。+/g)) {
    const name = match[1];
    eventsByName.set(name, {
      id: `thinking-${name}`,
      name,
      status: "failed",
      error: match[2]
    });
  }
  return Array.from(eventsByName.values());
}

function stripToolEventLines(text: string): string {
  return text
    .replace(new RegExp(`^\\s*${TOOL_EVENT_LOG_PREFIX}\\s+.*(?:\\n|$)`, "gm"), "")
    .replace(/调用工具\s+[\w.-]+。/g, "")
    .replace(/工具\s+[\w.-]+\s+完成：[^。\n]+。+/g, "")
    .replace(/工具\s+[\w.-]+\s+失败：[^。\n]+。+/g, "")
    .replace(/^\s*(?:em\s*otion|emotion)\s*:\s*[\w.-]+\s*$/gim, "")
    .replace(/\[emotion:\w+]\s*/gi, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function normalizeThinkingText(text: string): string {
  let normalized = text
    .replace(/\r\n/g, "\n")
    .replace(/([^\n])\n(?!\n)/g, "$1 ")
    .replace(/([A-Za-z0-9])\s+([_.:/-])\s*([A-Za-z0-9])/g, "$1$2$3")
    .replace(/([A-Za-z0-9])([_.:/-])\s+([A-Za-z0-9])/g, "$1$2$3")
    .replace(/([A-Za-z0-9_])\s+(_[A-Za-z0-9])/g, "$1$2")
    .replace(/(\d)\s+(\d)/g, "$1$2")
    .replace(/(\d)\s*:\s*(\d)/g, "$1:$2")
    .replace(/([“‘（《])\s+/g, "$1")
    .replace(/\s+([”’）》])/g, "$1")
    .replace(/[ \t]{2,}/g, " ");

  for (let index = 0; index < 4; index += 1) {
    normalized = normalized
      .replace(/([\u3400-\u9fff])\s+([\u3400-\u9fff])/g, "$1$2")
      .replace(/([\u3400-\u9fff])\s+(\d)/g, "$1$2")
      .replace(/(\d)\s+([\u3400-\u9fff])/g, "$1$2")
      .replace(/([\u3400-\u9fff])\s+([，。！？；：、])/g, "$1$2")
      .replace(/([，。！？；：、])\s+([\u3400-\u9fff])/g, "$1$2");
  }

  return normalized.trim();
}

function getToolResultRoot(item: ToolTraceEvent): unknown {
  if (item.result !== undefined) {
    return item.result;
  }
  return parseJsonValue(item.resultPreview) ?? item.resultPreview;
}

function getToolResultData(item: ToolTraceEvent): unknown {
  const root = getToolResultRoot(item);
  if (isRecord(root) && "data" in root) {
    return root.data;
  }
  return root;
}

function getToolResultText(item: ToolTraceEvent): string {
  const root = getToolResultRoot(item);
  const data = getToolResultData(item);
  if (isRecord(data)) {
    const answer = asString(data.answer);
    if (answer) {
      return answer;
    }
    const text = asString(data.text) || asString(data.content) || asString(data.result);
    if (text) {
      return text;
    }
  }
  if (isRecord(root)) {
    const summary = asString(root.summary) || asString(root.error);
    if (summary) {
      return summary;
    }
    try {
      return JSON.stringify(root, null, 2);
    } catch {
      return "";
    }
  }
  return asString(root) || item.resultPreview || "";
}

function extractToolResultLinks(item: ToolTraceEvent): ToolResultLink[] {
  const root = getToolResultRoot(item);
  const data = getToolResultData(item);
  const links: ToolResultLink[] = [];
  const seen = new Set<string>();

  const addLink = (value: unknown) => {
    if (!isRecord(value)) {
      return;
    }
    const url = asString(value.url) || asString(value.link) || asString(value.href);
    if (!/^https?:\/\//i.test(url) || seen.has(url)) {
      return;
    }
    seen.add(url);
    const evidence = Array.isArray(value.evidence) && isRecord(value.evidence[0]) ? asString(value.evidence[0].quote) : "";
    links.push({
      title: asString(value.title) || asString(value.name) || asString(value.displayUrl) || url,
      url,
      domain: asString(value.domain) || asString(value.displayUrl),
      snippet: asString(value.snippet) || asString(value.contentSummary) || evidence
    });
  };

  const visitList = (value: unknown) => {
    if (Array.isArray(value)) {
      value.forEach(addLink);
    }
  };

  if (isRecord(data)) {
    visitList(data.results);
    visitList(data.sources);
    visitList(data.candidates);
    visitList(data.links);
  }
  if (isRecord(root)) {
    visitList(root.results);
    visitList(root.sources);
    visitList(root.links);
  }
  if (Array.isArray(data)) {
    data.forEach(addLink);
  }
  return links;
}

function stripToolResultTextFromThinking(text: string, events: ToolTraceEvent[]): string {
  let output = normalizeThinkingText(text);
  for (const event of events) {
    const resultText = normalizeThinkingText(getToolResultText(event));
    if (resultText.length < 48) {
      continue;
    }
    output = output.split(resultText).join("");
  }
  return output.replace(/\s{3,}/g, " ").trim();
}

function compactLabel(value: string, fallback: string): string {
  return value.trim() || fallback;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function limitCodeTaskText(text: string, limit = CODE_TASK_MAX_DETAIL_TEXT): string {
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, limit).trimEnd()}\n\n[内容过长，已截断显示。]`;
}

function limitCodeTaskSummaryText(text: string): string {
  if (text.length <= CODE_TASK_SUMMARY_TEXT_LIMIT) {
    return text;
  }
  const head = text.slice(0, CODE_TASK_SUMMARY_TEXT_LIMIT).trimEnd();
  const boundaryPatterns = [/\n\n/g, /\n/g, /[。！？.!?]\s*/g, /[,，；;]\s*/g, /\s+/g];
  let cutIndex = -1;
  for (const pattern of boundaryPatterns) {
    for (const match of head.matchAll(pattern)) {
      const nextIndex = match.index === undefined ? -1 : match.index + match[0].length;
      if (nextIndex > CODE_TASK_SUMMARY_TEXT_LIMIT * 0.65) {
        cutIndex = Math.max(cutIndex, nextIndex);
      }
    }
    if (cutIndex > 0) {
      break;
    }
  }
  const visible = (cutIndex > 0 ? head.slice(0, cutIndex) : head).trimEnd();
  return `${visible}\n\n[内容较长，已省略后续文本；完整过程可在“已处理”中查看。]`;
}

function appendLimitedCodeTaskText(current: string, delta: string): string {
  if (!delta || current.includes(CODE_TASK_TRUNCATED_SUFFIX)) {
    return current;
  }
  const combined = `${current}${delta}`;
  if (combined.length <= CODE_TASK_MAX_ASSISTANT_TEXT) {
    return combined;
  }
  return `${combined.slice(0, CODE_TASK_MAX_ASSISTANT_TEXT).trimEnd()}${CODE_TASK_TRUNCATED_SUFFIX}`;
}

function trimCodeTaskMessages(messages: CodeTaskMessage[]): CodeTaskMessage[] {
  if (messages.length <= CODE_TASK_MAX_MESSAGES) {
    return messages;
  }
  const firstUser = messages.find((message) => message.kind === "user");
  const tail = messages.slice(-(CODE_TASK_MAX_MESSAGES - 1));
  if (firstUser && !tail.some((message) => message.id === firstUser.id)) {
    return [firstUser, ...tail];
  }
  return messages.slice(-CODE_TASK_MAX_MESSAGES);
}

function normalizeStoredQuestionItems(value: unknown): CodeTaskQuestionItem[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item): CodeTaskQuestionItem | null => {
      if (!isRecord(item)) {
        return null;
      }
      const question = typeof item.question === "string" ? item.question : "";
      const header = typeof item.header === "string" ? item.header : "";
      const options = Array.isArray(item.options)
        ? item.options
            .map((option): CodeTaskQuestionItem["options"][number] | null => {
              if (!isRecord(option) || typeof option.label !== "string" || !option.label.trim()) {
                return null;
              }
              return {
                label: option.label,
                description: typeof option.description === "string" ? option.description : undefined
              };
            })
            .filter((option): option is CodeTaskQuestionItem["options"][number] => Boolean(option))
        : [];
      if (!question && !header) {
        return null;
      }
      return {
        question: question || header,
        header: header || question.slice(0, 30),
        options,
        multiple: Boolean(item.multiple),
        custom: Boolean(item.custom)
      };
    })
    .filter((item): item is CodeTaskQuestionItem => Boolean(item));
}

function normalizeStoredQuestionAnswers(value: unknown): string[][] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  return value.map((answer) =>
    Array.isArray(answer)
      ? answer.map((label) => String(label).trim()).filter(Boolean)
      : String(answer).trim()
        ? [String(answer).trim()]
        : []
  );
}

function normalizeStoredCodeTaskQuestion(value: unknown): CodeTaskQuestionData | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  const requestId = typeof value.requestId === "string" ? value.requestId : "";
  const sessionId = typeof value.sessionId === "string" ? value.sessionId : "";
  const questions = normalizeStoredQuestionItems(value.questions);
  if (!requestId || questions.length === 0) {
    return undefined;
  }
  return {
    requestId,
    sessionId,
    serverUrl: typeof value.serverUrl === "string" ? value.serverUrl : undefined,
    questions,
    v2: Boolean(value.v2),
    answered: Boolean(value.answered),
    rejected: Boolean(value.rejected),
    answers: normalizeStoredQuestionAnswers(value.answers)
  };
}

function normalizeStoredCodeTaskMessage(value: unknown): CodeTaskMessage | null {
  if (!isRecord(value)) {
    return null;
  }
  const kind = typeof value.kind === "string" ? value.kind : "";
  const allowedKinds: CodeTaskMessageKind[] = [
    "user",
    "assistant",
    "status",
    "tool",
    "command",
    "file",
    "permission",
    "question",
    "error",
    "done"
  ];
  if (!allowedKinds.includes(kind as CodeTaskMessageKind)) {
    return null;
  }
  return {
    id: typeof value.id === "string" ? value.id : createId(),
    kind: kind as CodeTaskMessageKind,
    title: typeof value.title === "string" ? value.title : undefined,
    text: typeof value.text === "string" ? value.text : "",
    detail: typeof value.detail === "string" ? value.detail : undefined,
    question: normalizeStoredCodeTaskQuestion(value.question),
    createdAt: typeof value.createdAt === "string" ? value.createdAt : new Date().toISOString()
  };
}

function loadStoredCodeTasks(): CodeTaskRecord[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(CODE_TASK_HISTORY_STORAGE_KEY) ?? "[]");
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed
      .map((item): CodeTaskRecord | null => {
        if (!isRecord(item)) {
          return null;
        }
        const messages = Array.isArray(item.messages)
          ? item.messages.map(normalizeStoredCodeTaskMessage).filter((message): message is CodeTaskMessage => Boolean(message))
          : [];
        const now = new Date().toISOString();
        const title = typeof item.title === "string" && item.title.trim() ? item.title : "OpenCode 任务";
        return {
          id: typeof item.id === "string" ? item.id : createId(),
          title,
          prompt: typeof item.prompt === "string" ? item.prompt : title,
          workspacePath: typeof item.workspacePath === "string" ? item.workspacePath : "",
          status: typeof item.status === "string" ? item.status : "idle",
          sessionId: typeof item.sessionId === "string" ? item.sessionId : undefined,
          messages: trimCodeTaskMessages(messages),
          summary: typeof item.summary === "string" ? item.summary : undefined,
          summarySpeechText: typeof item.summarySpeechText === "string" ? item.summarySpeechText : undefined,
          summaryAudioUrl: typeof item.summaryAudioUrl === "string" ? item.summaryAudioUrl : undefined,
          createdAt: typeof item.createdAt === "string" ? item.createdAt : now,
          updatedAt: typeof item.updatedAt === "string" ? item.updatedAt : now
        };
      })
      .filter((task): task is CodeTaskRecord => Boolean(task))
      .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
      .slice(0, CODE_TASK_MAX_HISTORY);
  } catch {
    return [];
  }
}

function codeTaskToMarkdown(task: CodeTaskRecord): string {
  const lines = [
    `# ${task.title}`,
    "",
    `- Status: ${task.status}`,
    `- Workspace: ${task.workspacePath || "backend workspace"}`,
    `- Session: ${task.sessionId || "none"}`,
    `- Created: ${formatExportTime(task.createdAt)}`,
    `- Updated: ${formatExportTime(task.updatedAt)}`,
    "",
    "## Prompt",
    "",
    task.prompt.trim() || "(empty)",
    ""
  ];
  if (task.summary?.trim()) {
    lines.push("## Summary", "", task.summary.trim(), "");
  }
  if (task.summarySpeechText?.trim()) {
    lines.push("## Voice Script", "", task.summarySpeechText.trim(), "");
  }
  lines.push("## Timeline", "");
  for (const message of task.messages) {
    lines.push(`### ${message.title || message.kind} · ${formatExportTime(message.createdAt)}`, "");
    lines.push(message.text.trim() || "(empty)", "");
    if (message.detail?.trim()) {
      lines.push("```text", message.detail.trim(), "```", "");
    }
  }
  return lines.join("\n").trimEnd() + "\n";
}

function buildCodeTaskPhases(messages: CodeTaskMessage[], running: boolean): CodeTaskPhase[] {
  const phaseSpecs: Array<{ id: string; title: string; kinds: CodeTaskMessageKind[] }> = [
    { id: "request", title: "任务请求", kinds: ["user"] },
    { id: "setup", title: "启动与会话", kinds: ["status"] },
    { id: "thinking", title: "思考与输出", kinds: ["assistant"] },
    { id: "tools", title: "工具调用", kinds: ["tool"] },
    { id: "commands", title: "命令执行", kinds: ["command"] },
    { id: "files", title: "文件与 Diff", kinds: ["file"] },
    { id: "permissions", title: "权限处理", kinds: ["permission"] },
    { id: "questions", title: "交互确认", kinds: ["question"] },
    { id: "result", title: "任务结果", kinds: ["done", "error"] }
  ];
  const lastMessage = messages[messages.length - 1];
  const activePhaseId =
    running && lastMessage
      ? phaseSpecs.find((phase) => phase.kinds.includes(lastMessage.kind))?.id ?? "setup"
      : "";

  return phaseSpecs
    .map((phase) => {
      const phaseMessages = messages.filter((message) => phase.kinds.includes(message.kind));
      if (phaseMessages.length === 0) {
        return null;
      }
      const hasError = phaseMessages.some((message) => message.kind === "error");
      return {
        id: phase.id,
        title: phase.title,
        status: hasError ? "error" : activePhaseId === phase.id ? "running" : "completed",
        messages: phaseMessages
      } satisfies CodeTaskPhase;
    })
    .filter((phase): phase is CodeTaskPhase => Boolean(phase));
}

function isFinalCodeTaskSummaryMessage(message: CodeTaskMessage): boolean {
  return message.kind === "done" && message.title === "任务总结";
}

function isPendingCodeTaskQuestionMessage(message: CodeTaskMessage): boolean {
  return message.kind === "question" && Boolean(message.question) && !message.question?.answered && !message.question?.rejected;
}

function formatCodeTaskViewDuration(messages: CodeTaskMessage[]): string {
  if (messages.length < 2) {
    return "";
  }
  const start = new Date(messages[0].createdAt).getTime();
  const end = new Date(messages[messages.length - 1].createdAt).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return "";
  }
  const totalSeconds = Math.max(1, Math.round((end - start) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}h ${minutes}m ${seconds}s`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds}s`;
  }
  return `${seconds}s`;
}

function buildCodeTaskViewTurns(messages: CodeTaskMessage[], running: boolean) {
  const turns: Array<{ id: string; messages: CodeTaskMessage[]; running: boolean }> = [];
  let current: { id: string; messages: CodeTaskMessage[]; running: boolean } | null = null;

  for (const message of messages) {
    if (message.kind === "user" || current === null) {
      current = {
        id: message.kind === "user" ? message.id : `task-orphan-${message.id}`,
        messages: [message],
        running: false
      };
      turns.push(current);
      continue;
    }
    current.messages.push(message);
  }

  return turns.map((turn, index) => ({
    ...turn,
    running: running && index === turns.length - 1
  }));
}

function codeTaskViewMessage(message: CodeTaskMessage): CodeTaskMobileViewSnapshot["turns"][number]["responses"][number] {
  return {
    id: message.id,
    kind: message.kind,
    title: message.title,
    text: limitCodeTaskText(message.text, message.kind === "assistant" ? 4200 : 1400),
    detail: message.detail ? limitCodeTaskText(message.detail, 2200) : undefined,
    createdAt: message.createdAt,
    question: message.question
      ? {
          requestId: message.question.requestId,
          sessionId: message.question.sessionId,
          serverUrl: message.question.serverUrl,
          questions: message.question.questions,
          v2: message.question.v2,
          answered: message.question.answered,
          rejected: message.question.rejected,
          answers: message.question.answers
        }
      : undefined
  };
}

function buildCodeTaskMobileViewSnapshot(
  task: CodeTaskRecord,
  messages: CodeTaskMessage[],
  running: boolean
): CodeTaskMobileViewSnapshot {
  const turns = buildCodeTaskViewTurns(messages, running);
  const viewTurns = turns.map((turn) => {
    const phases = buildCodeTaskPhases(turn.messages, turn.running)
      .filter((phase) => phase.id !== "request")
      .map((phase) => ({
        ...phase,
        messages: phase.messages.filter((message) => !isFinalCodeTaskSummaryMessage(message))
      }))
      .filter((phase) => phase.messages.length > 0);
    const pendingQuestion = turn.messages.some(isPendingCodeTaskQuestionMessage);
    const hasError = phases.some((phase) => phase.status === "error");
    const eventCount = phases.reduce((total, phase) => total + phase.messages.length, 0);
    const responseMessages = turn.messages.filter(
      (message) => message.kind === "error" || isFinalCodeTaskSummaryMessage(message)
    );
    const fallback = !turn.running && responseMessages.length === 0
      ? [...turn.messages].reverse().find((message) => message.kind === "done")
      : undefined;
    const responses = fallback ? [fallback] : responseMessages;

    return {
      id: turn.id,
      running: turn.running,
      request: turn.messages.find((message) => message.kind === "user")
        ? codeTaskViewMessage(turn.messages.find((message) => message.kind === "user") as CodeTaskMessage)
        : undefined,
      processed: {
        title: turn.running ? "处理中" as const : "已处理" as const,
        openByDefault: turn.running || pendingQuestion || hasError,
        duration: formatCodeTaskViewDuration(turn.messages),
        phaseCount: phases.length,
        eventCount,
        phases: phases.map((phase) => {
          const visibleMessages = phase.messages.slice(-10);
          return {
            id: phase.id,
            title: phase.title,
            status: phase.status,
            eventCount: phase.messages.length,
            hiddenCount: Math.max(0, phase.messages.length - visibleMessages.length),
            messages: visibleMessages.map(codeTaskViewMessage)
          };
        })
      },
      responses: responses.map(codeTaskViewMessage)
    };
  });
  const pendingQuestionMessage = [...messages].reverse().find(isPendingCodeTaskQuestionMessage);
  const pendingQuestionTurn = pendingQuestionMessage
    ? viewTurns.find((turn) =>
        turn.processed.phases.some((phase) =>
          phase.messages.some((message) => message.id === pendingQuestionMessage.id)
        )
      )
    : undefined;
  const finalSummary = task.summary || [...messages].reverse().find(isFinalCodeTaskSummaryMessage)?.text;

  return {
    version: 2,
    kind: "code_task_view",
    taskId: task.id,
    status: task.status,
    running,
    renderedAt: new Date().toISOString(),
    task: {
      id: task.id,
      title: task.title,
      prompt: task.prompt,
      workspacePath: task.workspacePath,
      status: task.status,
      sessionId: task.sessionId,
      createdAt: task.createdAt,
      updatedAt: task.updatedAt
    },
    view: {
      source: "host",
      refreshIntervalMs: 1000,
      eventCount: viewTurns.reduce((total, turn) => total + turn.processed.eventCount, 0),
      turnCount: viewTurns.length
    },
    turns: viewTurns,
    pendingQuestion: pendingQuestionMessage?.question
      ? {
          turnId: pendingQuestionTurn?.id ?? "",
          messageId: pendingQuestionMessage.id,
          requestId: pendingQuestionMessage.question.requestId,
          sessionId: pendingQuestionMessage.question.sessionId,
          serverUrl: pendingQuestionMessage.question.serverUrl,
          questions: pendingQuestionMessage.question.questions,
          v2: pendingQuestionMessage.question.v2,
          answered: pendingQuestionMessage.question.answered,
          rejected: pendingQuestionMessage.question.rejected,
          answers: pendingQuestionMessage.question.answers
        }
      : undefined,
    summary: finalSummary
  };
}

function buildCodeTaskSummaryText(task: CodeTaskRecord, messages: CodeTaskMessage[]): string {
  const error = [...messages].reverse().find((message) => message.kind === "error");
  const prompt = messages.find((message) => message.kind === "user")?.text.trim() || task.prompt || task.title;
  const taskLabel = buildConversationTitle(prompt || task.title);
  const finalOutput = limitCodeTaskSummaryText(extractCodeTaskFinalAnswer(messages));
  const parts = [
    `OpenCode 已完成「${taskLabel}」。`,
    error ? `需要注意：${error.text}` : "",
    finalOutput ? `结论：${finalOutput}` : "没有收到可用于总结的最终文本。"
  ];
  return parts.filter(Boolean).join("\n\n");
}

function buildCodeTaskVoiceSummarySource(task: CodeTaskRecord, messages: CodeTaskMessage[]): string {
  const prompt = messages.find((message) => message.kind === "user")?.text.trim() || task.prompt || task.title;
  const finalAnswer = extractCodeTaskFinalAnswer(messages);
  const files = messages
    .filter((message) => message.kind === "file")
    .map((message) => message.text.replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .slice(-3);
  const lines = [
    `任务标题：${task.title}`,
    `用户需求：${prompt}`,
    files.length > 0 ? `相关文件变化：${files.join("；")}` : "",
    "OpenCode 最终回答：",
    finalAnswer || "OpenCode 没有返回可总结的最终回答。"
  ];
  return lines.filter(Boolean).join("\n").slice(0, 5000);
}

function extractCodeTaskFinalAnswer(messages: CodeTaskMessage[]): string {
  const assistantText = messages
    .filter((message) => message.kind === "assistant")
    .map((message) => message.text)
    .join("\n")
    .trim();
  if (!assistantText) {
    return "";
  }
  return cleanCodeTaskFinalAnswer(assistantText);
}

function cleanCodeTaskFinalAnswer(text: string): string {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const filtered = lines.filter((line) => {
    if (/^(用户想要|用户说|用户要求|我需要|让我|我已经|现在我|我来|接下来|这有点|可能|也许)/.test(line)) {
      return false;
    }
    if (/^(首先|然后|最后)?我(需要|会|已经|来)/.test(line)) {
      return false;
    }
    return true;
  });
  const normalized = (filtered.length > 0 ? filtered : lines).join("\n");
  return normalized.replace(/\n{3,}/g, "\n\n").trim();
}

function getLeftOccupiedWidth(menuOpen: boolean, configOpen: boolean): number {
  if (!menuOpen) {
    return EDGE_MARGIN;
  }
  return (
    EDGE_MARGIN +
    LEFT_SIDEBAR_WIDTH +
    (configOpen ? PANEL_GAP + SETTINGS_SIDEBAR_WIDTH : 0) +
    PANEL_GAP
  );
}

function getChatMaxWidth(menuOpen: boolean, configOpen: boolean): number {
  if (typeof window === "undefined") {
    return CHAT_MAX_WIDTH;
  }
  const available = window.innerWidth - getLeftOccupiedWidth(menuOpen, configOpen) - EDGE_MARGIN;
  return clamp(available, CHAT_MIN_WIDTH, CHAT_MAX_WIDTH);
}

export {
  ACCEPTED_UPLOAD_TYPES,
  CHAT_MAX_WIDTH,
  CHAT_MIN_WIDTH,
  CODE_TASK_HISTORY_STORAGE_KEY,
  CODE_TASK_FLUSH_INTERVAL_MS,
  CODE_TASK_MAX_ASSISTANT_TEXT,
  CODE_TASK_MAX_DETAIL_TEXT,
  CODE_TASK_MAX_HISTORY,
  DEFAULT_CODE_TASK_WORKSPACE,
  DEFAULT_DESKTOP_ASSISTANT_SETTINGS,
  DEFAULT_MODEL_SETTINGS,
  DEFAULT_SPEECH_INPUT_SETTINGS,
  DEFAULT_VISION_SETTINGS,
  DEFAULT_VOICE_SETTINGS,
  EDGE_MARGIN,
  LIVE2D_ACTIVE_STORAGE_KEY,
  LIVE2D_IMPORT_INPUT_ID,
  LIVE2D_LOCK_STORAGE_KEY,
  LEFT_SIDEBAR_WIDTH,
  MIN_BOOT_DURATION_MS,
  PANEL_GAP,
  PERSONA_ID,
  STORAGE_KEY,
  appendLimitedCodeTaskText,
  buildCodeTaskPhases,
  buildCodeTaskMobileViewSnapshot,
  buildCodeTaskSummaryText,
  buildCodeTaskVoiceSummarySource,
  buildConversationTitle,
  clamp,
  codeTaskToMarkdown,
  compactLabel,
  composeMessageText,
  conversationToMarkdown,
  createId,
  downloadText,
  extractToolEventsFromThinking,
  extractToolResultLinks,
  formatExportTime,
  formatHistoryTime,
  formatMessageTime,
  getChatMaxWidth,
  getToolResultText,
  limitCodeTaskText,
  loadStoredCodeTasks,
  loadStoredSettings,
  mergeToolEvents,
  normalizeDisplayEmotion,
  normalizeAssistantVoice,
  normalizeDesktopAssistantSettings,
  normalizeLive2dEmotion,
  normalizeMessageContent,
  normalizeSpeechInputSettings,
  normalizeThinkingText,
  renderMarkdownContent,
  sanitizeFilename,
  stripToolEventLines,
  stripToolResultTextFromThinking,
  toApiMessages,
  toChatMessages,
  trimCodeTaskMessages,
  uploadTypeLabel
};

export type {
  AudioQueueItem,
  CodeTaskMessage,
  CodeTaskMessageKind,
  CodeTaskPhase,
  CodeTaskQuestionData,
  CodeTaskRecord,
  ConfigSection,
  RightPanelTab,
  ToolResultLink
};
