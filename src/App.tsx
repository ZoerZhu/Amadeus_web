import { FormEvent, PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import {
  Bell,
  BookOpenText,
  Bot,
  Brain,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleDashed,
  CircleStop,
  Clock3,
  Copy,
  Download,
  ExternalLink,
  KeyRound,
  Menu,
  MessageCircle,
  Mic,
  Mic2,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Search,
  Send,
  Settings,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  UserRound,
  Volume2,
  VolumeX,
  Wrench,
  XCircle,
  Zap
} from "lucide-react";
import {
  cloneVoice,
  createConversation,
  deleteConversation,
  fetchConversation,
  fetchProviders,
  fetchSettings,
  listConversations,
  saveSettings,
  streamChat,
  synthesizeVoice
} from "./api";
import { BootLoader } from "./components/BootLoader";
import { Live2DStage, type Live2DStageHandle } from "./components/Live2DStage";
import type {
  ApiChatMessage,
  ChatMessage,
  ChatMode,
  ConversationDetail,
  ConversationSummary,
  ModelSettings,
  ProviderPreset,
  StoredSettings,
  StreamEvent,
  ToolTraceEvent,
  VoiceSettings
} from "./types";

const STORAGE_KEY = "amadeus-web-settings-v1";
const PERSONA_ID = "kurisu_amadeus";
const LEFT_SIDEBAR_WIDTH = 372;
const SETTINGS_SIDEBAR_WIDTH = 388;
const PANEL_GAP = 14;
const EDGE_MARGIN = 18;
const CHAT_MIN_WIDTH = 340;
const CHAT_MAX_WIDTH = 620;
const MIN_BOOT_DURATION_MS = 2000;

const DEFAULT_MODEL_SETTINGS: ModelSettings = {
  providerName: "OpenAI",
  baseUrl: "https://api.openai.com/v1",
  model: "gpt-4.1-mini",
  apiKey: "",
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

type ConfigSection = "profile" | "model" | "voice" | "interface";
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

const TOOL_EVENT_LOG_PREFIX = "__AMADEUS_TOOL_EVENT__";
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
      voice: { ...DEFAULT_VOICE_SETTINGS, ...(parsed.voice ?? {}) },
      mode: parsed.mode === "thinking" ? "thinking" : "fast"
    };
  } catch {
    return {
      model: DEFAULT_MODEL_SETTINGS,
      voice: DEFAULT_VOICE_SETTINGS,
      mode: "fast"
    };
  }
}

function toApiMessages(messages: ChatMessage[]): ApiChatMessage[] {
  return messages
    .filter((message) => message.content.trim())
    .map((message) => ({
      role: message.role,
      content: message.content
    }));
}

function normalizeMessageContent(content: string, role: ChatMessage["role"]): string {
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

export default function App() {
  const stored = useMemo(loadStoredSettings, []);
  const [providers, setProviders] = useState<ProviderPreset[]>([]);
  const [storageOnline, setStorageOnline] = useState(false);
  const [storageError, setStorageError] = useState("");
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [modelSettings, setModelSettings] = useState<ModelSettings>(stored.model);
  const [voiceSettings, setVoiceSettings] = useState<VoiceSettings>(stored.voice);
  const [mode, setMode] = useState<ChatMode>(stored.mode);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [historySearch, setHistorySearch] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatWidth, setChatWidth] = useState(430);
  const [openSections, setOpenSections] = useState<Record<ConfigSection, boolean>>({
    profile: false,
    model: false,
    voice: false,
    interface: false
  });
  const [status, setStatus] = useState("idle");
  const [currentEmotion, setCurrentEmotion] = useState("neutral");
  const [isStreaming, setIsStreaming] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [live2dReady, setLive2dReady] = useState(false);
  const [live2dError, setLive2dError] = useState("");
  const [minimumBootElapsed, setMinimumBootElapsed] = useState(false);
  const [enteredApp, setEnteredApp] = useState(false);
  const live2dRef = useRef<Live2DStageHandle | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioQueueRef = useRef<AudioQueueItem[]>([]);
  const audioPlayingRef = useRef(false);
  const textRevealFrameRef = useRef<number | null>(null);
  const textRevealTokenRef = useRef(0);
  const floatingMessageRef = useRef<HTMLDivElement | null>(null);
  const chatMessageRef = useRef<HTMLDivElement | null>(null);
  const saveSettingsTimerRef = useRef<number | null>(null);

  const selectedProvider = providers.find((provider) => provider.name === modelSettings.providerName);
  const canSend = input.trim().length > 0 && !isStreaming;
  const visibleMessages = messages.filter((message) => message.content || message.streaming).slice(-8);
  const filteredConversations = useMemo(() => {
    const keyword = historySearch.trim().toLowerCase();
    if (!keyword) {
      return conversations;
    }
    return conversations.filter((conversation) =>
      conversation.title.toLowerCase().includes(keyword)
    );
  }, [conversations, historySearch]);
  const live2dLoadResolved = live2dReady || Boolean(live2dError);
  const loadingReady = minimumBootElapsed && live2dLoadResolved;

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setMinimumBootElapsed(true);
    }, MIN_BOOT_DURATION_MS);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    fetchProviders()
      .then((items) => {
        setProviders(items);
        const current = items.find((provider) => provider.name === modelSettings.providerName);
        if (current && (!modelSettings.baseUrl || !modelSettings.model)) {
          setModelSettings((prev) => ({
            ...prev,
            baseUrl: prev.baseUrl || current.baseUrl,
            model: prev.model || current.defaultModel
          }));
        }
      })
      .catch(() => setStatus("api offline"));
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadStoredData() {
      try {
        const [remoteSettings, conversationItems] = await Promise.all([
          fetchSettings(),
          listConversations()
        ]);
        if (cancelled) {
          return;
        }
        setStorageOnline(true);
        setStorageError("");
        if (remoteSettings) {
          setModelSettings((prev) => ({ ...prev, ...remoteSettings.model }));
          setVoiceSettings((prev) => ({ ...prev, ...remoteSettings.voice }));
          setMode(remoteSettings.mode);
        }
        setConversations(conversationItems);
        if (conversationItems.length > 0) {
          const firstConversation = await fetchConversation(conversationItems[0].id);
          if (!cancelled) {
            setCurrentConversationId(firstConversation.id);
            setMessages(toChatMessages(firstConversation.messages));
          }
        }
      } catch (error) {
        if (!cancelled) {
          setStorageOnline(false);
          setStorageError(error instanceof Error ? error.message : "storage offline");
        }
      }
    }

    void loadStoredData();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        model: modelSettings,
        voice: voiceSettings,
        mode
      })
    );

    if (!storageOnline) {
      return;
    }
    if (saveSettingsTimerRef.current !== null) {
      window.clearTimeout(saveSettingsTimerRef.current);
    }
    saveSettingsTimerRef.current = window.setTimeout(() => {
      saveSettings({
        model: modelSettings,
        voice: voiceSettings,
        mode
      }).catch((error) => {
        setStorageError(error instanceof Error ? error.message : "settings save failed");
      });
    }, 350);
  }, [modelSettings, voiceSettings, mode, storageOnline]);

  useEffect(() => {
    return () => {
      if (saveSettingsTimerRef.current !== null) {
        window.clearTimeout(saveSettingsTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    const clampChatWidth = () => {
      setChatWidth((width) => clamp(width, CHAT_MIN_WIDTH, getChatMaxWidth(menuOpen, configOpen)));
    };
    clampChatWidth();
    window.addEventListener("resize", clampChatWidth);
    return () => window.removeEventListener("resize", clampChatWidth);
  }, [menuOpen, configOpen]);

  useEffect(() => {
    floatingMessageRef.current?.scrollTo({ top: floatingMessageRef.current.scrollHeight, behavior: "smooth" });
    chatMessageRef.current?.scrollTo({ top: chatMessageRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, chatOpen]);

  function applyProvider(name: string) {
    const provider = providers.find((item) => item.name === name);
    setModelSettings((prev) => ({
      ...prev,
      providerName: name,
      baseUrl: provider?.baseUrl ?? prev.baseUrl,
      model: provider?.defaultModel ?? prev.model,
      useRemote: name !== "演示模型"
    }));
  }

  function toggleMenuFromAvatar() {
    if (configOpen) {
      setConfigOpen(false);
      return;
    }
    setMenuOpen((open) => !open);
  }

  function collapsePrimaryPanel() {
    if (configOpen) {
      setConfigOpen(false);
      return;
    }
    setMenuOpen(false);
  }

  function openConfigPanel() {
    setMenuOpen(true);
    setConfigOpen(true);
  }

  function toggleSection(section: ConfigSection) {
    setOpenSections((prev) => ({ ...prev, [section]: !prev[section] }));
  }

  async function refreshConversations() {
    if (!storageOnline) {
      return;
    }
    try {
      const items = await listConversations();
      setConversations(items);
      setStorageError("");
    } catch (error) {
      setStorageOnline(false);
      setStorageError(error instanceof Error ? error.message : "storage offline");
    }
  }

  async function openConversation(id: string) {
    if (isStreaming) {
      return;
    }
    stopAudioPlayback();
    try {
      const item = await fetchConversation(id);
      setCurrentConversationId(item.id);
      setMessages(toChatMessages(item.messages));
      setMode(item.mode);
      setChatOpen(true);
      setStorageOnline(true);
      setStorageError("");
    } catch (error) {
      setStorageError(error instanceof Error ? error.message : "conversation load failed");
    }
  }

  async function exportConversationItem(conversation: ConversationSummary) {
    try {
      const item = await fetchConversation(conversation.id);
      const stamp = new Date().toISOString().slice(0, 10);
      downloadText(`${sanitizeFilename(item.title)}-${stamp}.md`, conversationToMarkdown(item));
      setStatus("exported");
    } catch (error) {
      setStorageError(error instanceof Error ? error.message : "conversation export failed");
      setStatus("export failed");
    }
  }

  async function deleteConversationItem(conversation: ConversationSummary) {
    if (isStreaming) {
      return;
    }
    const confirmed = window.confirm(`删除聊天记录“${conversation.title}”？此操作会从数据库中删除，不能撤销。`);
    if (!confirmed) {
      return;
    }
    try {
      await deleteConversation(conversation.id);
      setConversations((prev) => prev.filter((item) => item.id !== conversation.id));
      if (conversation.id === currentConversationId) {
        stopAudioPlayback();
        setCurrentConversationId(null);
        setMessages([]);
        setInput("");
      }
      setStatus("deleted");
    } catch (error) {
      setStorageError(error instanceof Error ? error.message : "conversation delete failed");
      setStatus("delete failed");
    }
  }

  function startNewConversation() {
    abortRef.current?.abort();
    stopAudioPlayback();
    setCurrentConversationId(null);
    setMessages([]);
    setInput("");
    setStatus("idle");
    setIsStreaming(false);
    setChatOpen(true);
  }

  async function ensureConversation(text: string): Promise<string | null> {
    if (currentConversationId || !storageOnline) {
      return currentConversationId;
    }
    try {
      const conversation = await createConversation({
        title: buildConversationTitle(text),
        personaId: PERSONA_ID,
        mode
      });
      setCurrentConversationId(conversation.id);
      setConversations((prev) => [
        conversation,
        ...prev.filter((item) => item.id !== conversation.id)
      ]);
      setStorageError("");
      return conversation.id;
    } catch (error) {
      setStorageOnline(false);
      setStorageError(error instanceof Error ? error.message : "conversation create failed");
      return null;
    }
  }

  function appendAssistantDelta(id: string, delta: Partial<ChatMessage>) {
    setMessages((prev) =>
      prev.map((message) =>
        message.id === id
          ? {
              ...message,
              ...delta,
              content:
                delta.content !== undefined
                  ? normalizeMessageContent(message.content + delta.content, message.role)
                  : message.content,
              thinking: delta.thinking !== undefined ? `${message.thinking ?? ""}${delta.thinking}` : message.thinking
            }
          : message
      )
    );
  }

  function appendAssistantToolEvent(id: string, toolEvent: ToolTraceEvent) {
    setMessages((prev) =>
      prev.map((message) => {
        if (message.id !== id) {
          return message;
        }
        const existingEvents = message.toolEvents ?? [];
        const found = existingEvents.some((item) => item.id === toolEvent.id);
        return {
          ...message,
          toolEvents: found
            ? existingEvents.map((item) => (item.id === toolEvent.id ? { ...item, ...toolEvent } : item))
            : [...existingEvents, toolEvent]
        };
      })
    );
  }

  function finalizeAssistant(id: string) {
    setMessages((prev) =>
      prev.map((message) => (message.id === id ? { ...message, streaming: false } : message))
    );
  }

  function stopAudioPlayback() {
    audioQueueRef.current = [];
    audioPlayingRef.current = false;
    cancelSyncedTextReveal();
    if (audioRef.current) {
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
      audioRef.current.pause();
      audioRef.current = null;
    }
    setSpeaking(false);
  }

  function cancelSyncedTextReveal() {
    textRevealTokenRef.current += 1;
    if (textRevealFrameRef.current !== null) {
      window.cancelAnimationFrame(textRevealFrameRef.current);
      textRevealFrameRef.current = null;
    }
  }

  function startSyncedTextReveal(assistantId: string, text: string, audio: HTMLAudioElement): () => void {
    cancelSyncedTextReveal();

    const token = textRevealTokenRef.current;
    const units = Array.from(text);
    let shown = 0;
    let started = false;
    let fallbackStartedAt = performance.now();

    const appendUntil = (nextCount: number) => {
      const count = clamp(Math.floor(nextCount), shown, units.length);
      if (count <= shown) {
        return;
      }
      appendAssistantDelta(assistantId, { content: units.slice(shown, count).join("") });
      shown = count;
    };

    const revealRest = () => {
      appendUntil(units.length);
      if (textRevealFrameRef.current !== null) {
        window.cancelAnimationFrame(textRevealFrameRef.current);
        textRevealFrameRef.current = null;
      }
    };

    const step = () => {
      if (token !== textRevealTokenRef.current || audioRef.current !== audio) {
        return;
      }

      const duration = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0;
      if (duration > 0) {
        appendUntil((audio.currentTime / duration) * units.length);
      } else {
        const fallbackDuration = Math.max(1200, units.length * 95);
        appendUntil(((performance.now() - fallbackStartedAt) / fallbackDuration) * units.length);
      }

      if (shown < units.length && !audio.ended && !audio.paused) {
        textRevealFrameRef.current = window.requestAnimationFrame(step);
      } else {
        textRevealFrameRef.current = null;
      }
    };

    const start = () => {
      if (started || token !== textRevealTokenRef.current) {
        return;
      }
      started = true;
      fallbackStartedAt = performance.now();
      textRevealFrameRef.current = window.requestAnimationFrame(step);
    };

    audio.addEventListener("playing", start, { once: true });

    return () => {
      audio.removeEventListener("playing", start);
      revealRest();
    };
  }

  function enqueueAudio(item: AudioQueueItem) {
    audioQueueRef.current.push(item);
    playNextAudio();
  }

  function playNextAudio() {
    if (audioPlayingRef.current) {
      return;
    }

    const item = audioQueueRef.current.shift();
    if (!item) {
      setSpeaking(false);
      return;
    }

    audioPlayingRef.current = true;
    let finishSyncedText: (() => void) | null = null;
    let finished = false;

    const finish = () => {
      if (finished) {
        return;
      }
      finished = true;
      finishSyncedText?.();
      if (audioRef.current) {
        audioRef.current = null;
      }
      audioPlayingRef.current = false;
      if (audioQueueRef.current.length === 0) {
        setSpeaking(false);
      }
      window.setTimeout(playNextAudio, 24);
    };

    setSpeaking(true);
    const nextUrl = `${item.url}${item.url.includes("?") ? "&" : "?"}t=${Date.now()}`;
    const audio = new Audio(nextUrl);
    audioRef.current = audio;
    finishSyncedText =
      item.displayText && item.assistantId
        ? startSyncedTextReveal(item.assistantId, item.displayText, audio)
        : null;

    audio.onended = finish;
    audio.onerror = finish;
    audio.play().catch(finish);
  }

  function toggleAutoVoice() {
    const nextEnabled = !voiceSettings.autoPlay;
    setVoiceSettings((prev) => ({ ...prev, autoPlay: nextEnabled }));
    if (!nextEnabled) {
      stopAudioPlayback();
      setStatus("voice off");
      return;
    }
    setStatus("voice on");
  }

  async function copyMessageText(text: string) {
    const content = text.trim();
    if (!content) {
      return;
    }
    try {
      await navigator.clipboard.writeText(content);
      setStatus("copied");
    } catch {
      setStatus("copy failed");
    }
  }

  async function playAssistantMessage(message: ChatMessage) {
    const text = message.content.trim();
    if (!text || message.role !== "assistant") {
      return;
    }
    setVoiceBusy(true);
    setStatus("voice");
    try {
      const url = await synthesizeVoice({
        text,
        model: modelSettings,
        voice: voiceSettings
      });
      stopAudioPlayback();
      enqueueAudio({ url });
      live2dRef.current?.playEmotion("smile1");
      setStatus("done");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "voice error");
    } finally {
      setVoiceBusy(false);
    }
  }

  function handleStreamEvent(event: StreamEvent, assistantId: string) {
    if (event.event === "status") {
      setStatus(event.payload.phase);
      return;
    }
    if (event.event === "content") {
      appendAssistantDelta(assistantId, { content: event.payload.text });
      return;
    }
    if (event.event === "thinking") {
      appendAssistantDelta(assistantId, { thinking: event.payload.text });
      return;
    }
    if (event.event === "tool") {
      appendAssistantToolEvent(assistantId, event.payload);
      return;
    }
    if (event.event === "emotion") {
      setCurrentEmotion(normalizeDisplayEmotion(event.payload.emotion));
      live2dRef.current?.playEmotion(normalizeLive2dEmotion(event.payload.live2dEmotion, event.payload.emotion));
      return;
    }
    if (event.event === "audio") {
      enqueueAudio({
        url: event.payload.url,
        assistantId,
        displayText: event.payload.syncText ? event.payload.text : undefined,
        index: event.payload.index
      });
      return;
    }
    if (event.event === "voice_error") {
      setStatus(`voice: ${event.payload.message}`);
      return;
    }
    if (event.event === "error") {
      appendAssistantDelta(assistantId, { content: `请求失败：${event.payload.message}` });
      finalizeAssistant(assistantId);
      setStatus("error");
      return;
    }
    if (event.event === "done") {
      finalizeAssistant(assistantId);
      setStatus("done");
    }
  }

  async function sendMessage(event?: FormEvent) {
    event?.preventDefault();
    const text = input.trim();
    if (!text || isStreaming) {
      return;
    }

    abortRef.current?.abort();
    stopAudioPlayback();
    const controller = new AbortController();
    abortRef.current = controller;
    setIsStreaming(true);
    setStatus("connecting");
    const conversationId = await ensureConversation(text);

    const userMessage: ChatMessage = {
      id: createId(),
      role: "user",
      content: text,
      createdAt: new Date().toISOString()
    };
    const assistantMessage: ChatMessage = {
      id: createId(),
      role: "assistant",
      content: "",
      thinking: "",
      streaming: true,
      createdAt: new Date().toISOString()
    };
    setMessages((prev) => [...prev, userMessage, assistantMessage]);
    setInput("");
    setCurrentEmotion("neutral");
    live2dRef.current?.playEmotion("neutral");

    try {
      await streamChat({
        conversationId,
        messages: toApiMessages([...messages, userMessage]),
        mode,
        model: modelSettings,
        voice: voiceSettings,
        signal: controller.signal,
        onEvent: (streamEvent) => handleStreamEvent(streamEvent, assistantMessage.id)
      });
    } catch (error) {
      if (!controller.signal.aborted) {
        appendAssistantDelta(assistantMessage.id, {
          content: `请求失败：${error instanceof Error ? error.message : "未知错误"}`
        });
        setStatus("error");
      }
    } finally {
      finalizeAssistant(assistantMessage.id);
      setIsStreaming(false);
      abortRef.current = null;
      if (conversationId) {
        void refreshConversations();
      }
    }
  }

  function stopStreaming() {
    abortRef.current?.abort();
    stopAudioPlayback();
    setIsStreaming(false);
    setStatus("stopped");
    setMessages((prev) => prev.map((message) => ({ ...message, streaming: false })));
  }

  async function testVoice() {
    setVoiceBusy(true);
    setStatus("voice");
    try {
      const url = await synthesizeVoice({
        text: "これで音声のテストは完了。まったく、最初からそう言えばいいのに。",
        model: modelSettings,
        voice: voiceSettings
      });
      stopAudioPlayback();
      enqueueAudio({ url });
      live2dRef.current?.playEmotion("smile1");
      setStatus("done");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "voice error");
    } finally {
      setVoiceBusy(false);
    }
  }

  async function cloneKurisuVoice() {
    setVoiceBusy(true);
    setStatus("clone");
    try {
      const voiceUri = await cloneVoice({
        siliconFlowApiKey: voiceSettings.siliconFlowApiKey,
        customName: "siliconflow-kurisu-web"
      });
      setVoiceSettings((prev) => ({ ...prev, clonedVoiceUri: voiceUri }));
      setStatus("done");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "clone error");
    } finally {
      setVoiceBusy(false);
    }
  }

  function startChatResize(event: ReactPointerEvent<HTMLButtonElement>) {
    event.preventDefault();
    const pointerId = event.pointerId;
    const target = event.currentTarget;
    target.setPointerCapture(pointerId);
    document.documentElement.classList.add("is-chat-resizing");

    const move = (moveEvent: PointerEvent) => {
      const maxWidth = getChatMaxWidth(menuOpen, configOpen);
      const nextWidth = window.innerWidth - moveEvent.clientX - EDGE_MARGIN;
      setChatWidth(clamp(nextWidth, CHAT_MIN_WIDTH, maxWidth));
    };

    const up = () => {
      if (target.hasPointerCapture(pointerId)) {
        target.releasePointerCapture(pointerId);
      }
      document.documentElement.classList.remove("is-chat-resizing");
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }

  const settingsOffset = menuOpen ? EDGE_MARGIN + LEFT_SIDEBAR_WIDTH + PANEL_GAP : EDGE_MARGIN;
  const chatMaxWidth = getChatMaxWidth(menuOpen, configOpen);
  const clampedChatWidth = clamp(chatWidth, CHAT_MIN_WIDTH, chatMaxWidth);
  const currentConversation = conversations.find((conversation) => conversation.id === currentConversationId);

  function renderThinkingPanel(message: ChatMessage) {
    const rawThinking = (message.thinking ?? "").trim();
    const liveToolEvents = message.toolEvents ?? [];
    const parsedToolEvents = extractToolEventsFromThinking(rawThinking);
    const toolEvents = mergeToolEvents([...parsedToolEvents, ...liveToolEvents]);
    const thinking = stripToolResultTextFromThinking(stripToolEventLines(rawThinking), toolEvents);
    if (!thinking && toolEvents.length === 0) {
      return null;
    }
    return (
      <details className="thinking-card" open={message.streaming || Boolean(thinking) || toolEvents.length > 0}>
        <summary>
          <span className="thinking-summary-title">
            <span className="thinking-dot" />
            思考过程
          </span>
          <span className="thinking-summary-action">
            <span className="label-open">收起</span>
            <span className="label-closed">展开</span>
          </span>
        </summary>
        <div className="thinking-card-body">
          {thinking && <p className="thinking-text">{thinking}</p>}
          {toolEvents.length > 0 && (
            <div className="tool-trace-list" aria-label="工具调用过程">
              {toolEvents.map((item) => renderToolTraceItem(item))}
            </div>
          )}
        </div>
      </details>
    );
  }

  function renderToolTraceItem(item: ToolTraceEvent) {
    const args = formatToolArguments(item.arguments);
    const detail = item.error || item.summary || item.resultPreview || "等待工具返回结果";
    const links = extractToolResultLinks(item);
    const resultText = getToolResultText(item);
    const hasResultDetails = item.status === "completed" && (links.length > 0 || resultText.trim().length > 0);
    return (
      <div className={`tool-trace-item is-${item.status}`} key={item.id}>
        <span className="tool-trace-icon">{toolTraceIcon(item)}</span>
        <div className="tool-trace-body">
          <div className="tool-trace-head">
            <span>{toolTraceTitle(item)}</span>
            <small>{toolTraceStatus(item.status)}</small>
          </div>
          <p>{detail}</p>
          {args && <code>{args}</code>}
          {hasResultDetails && (
            <details className="tool-result-details">
              <summary>
                <span>来源与返回结果</span>
                <small>{links.length > 0 ? `${links.length} 个链接` : "返回文本"}</small>
              </summary>
              <div className="tool-result-content">
                {links.length > 0 && (
                  <div className="tool-result-links">
                    {links.map((link, index) => (
                      <a className="tool-result-link" href={link.url} target="_blank" rel="noreferrer" key={link.url}>
                        <span className="tool-result-link-index">{index + 1}</span>
                        <span className="tool-result-link-copy">
                          <strong>{link.title}</strong>
                          {link.domain && <small>{link.domain}</small>}
                          {link.snippet && <em>{link.snippet}</em>}
                        </span>
                        <ExternalLink size={13} />
                      </a>
                    ))}
                  </div>
                )}
                {resultText.trim() && <pre className="tool-result-text">{resultText.trim()}</pre>}
              </div>
            </details>
          )}
        </div>
      </div>
    );
  }

  function toolTraceIcon(item: ToolTraceEvent) {
    if (item.status === "failed") {
      return <XCircle size={14} />;
    }
    if (item.status === "completed") {
      return <CheckCircle2 size={14} />;
    }
    if (item.name.includes("search")) {
      return <Search size={14} />;
    }
    if (item.name.includes("reader") || item.name.includes("doc")) {
      return <BookOpenText size={14} />;
    }
    return item.status === "started" ? <CircleDashed size={14} /> : <Wrench size={14} />;
  }

  function toolTraceTitle(item: ToolTraceEvent): string {
    if (item.status === "started") {
      return `调用 ${item.name}`;
    }
    if (item.status === "failed") {
      return `${item.name} 调用失败`;
    }
    return `${item.name} 已完成`;
  }

  function toolTraceStatus(status: ToolTraceEvent["status"]): string {
    if (status === "started") {
      return "进行中";
    }
    if (status === "failed") {
      return "失败";
    }
    return "完成";
  }

  function formatToolArguments(value?: Record<string, unknown>): string {
    if (!value || Object.keys(value).length === 0) {
      return "";
    }
    try {
      const text = JSON.stringify(value, null, 2);
      return text.length > 360 ? `${text.slice(0, 340).trimEnd()}\n...` : text;
    } catch {
      return "";
    }
  }

  function renderMessageBubble(message: ChatMessage, surface: "floating" | "panel") {
    const isAssistant = message.role === "assistant";
    const content = message.content || (message.streaming ? "..." : "");
    const canUseText = message.content.trim().length > 0;

    return (
      <article
        key={message.id}
        className={[
          "dialog-message",
          surface === "floating" ? "is-floating" : "is-panel",
          isAssistant ? "is-assistant" : "is-user"
        ].join(" ")}
      >
        {isAssistant && <div className="bubble-agent-name">Kurisu Amadeus</div>}
        <div className={`${surface === "floating" ? "float-bubble" : "message-bubble"} ${isAssistant ? "is-assistant" : "is-user"}`}>
          {isAssistant && renderThinkingPanel(message)}
          {renderMarkdownContent(content)}
        </div>
        <div className="bubble-meta">
          <time dateTime={message.createdAt}>{formatMessageTime(message.createdAt)}</time>
          <button
            className="bubble-tool"
            disabled={!canUseText}
            onClick={() => void copyMessageText(message.content)}
            type="button"
            aria-label="复制消息"
            title="复制"
          >
            <Copy size={14} />
          </button>
          {isAssistant && (
            <button
              className="bubble-tool"
              disabled={!canUseText || voiceBusy}
              onClick={() => void playAssistantMessage(message)}
              type="button"
              aria-label="播放回复"
              title="播放"
            >
              <Volume2 size={14} />
            </button>
          )}
        </div>
      </article>
    );
  }

  return (
    <main
      className={[
        "app-shell",
        menuOpen ? "is-menu-open" : "",
        configOpen ? "is-config-open" : "",
        chatOpen ? "is-chat-open" : ""
      ].join(" ")}
      style={
        {
          "--chat-panel-width": `${clampedChatWidth}px`,
          "--settings-left": `${settingsOffset}px`
        } as CSSProperties
      }
    >
      <section className="stage-panel" aria-label="Amadeus Live2D canvas">
        <Live2DStage
          ref={live2dRef}
          speaking={speaking}
          onReady={() => {
            setLive2dReady(true);
            setLive2dError("");
          }}
          onError={(message) => {
            setLive2dError(message);
          }}
        />
      </section>

      <header className="top-bar glass-panel">
        <div className="brand-mark">
          <span className="brand-orbit">
            <Sparkles size={15} strokeWidth={1.8} />
          </span>
          <div>
            <h1>Amadeus</h1>
            <p>{compactLabel(modelSettings.model, selectedProvider?.defaultModel ?? "model")}</p>
          </div>
        </div>

        <div className="top-actions">
          <label className="model-switch">
            <Bot size={14} />
            <select value={modelSettings.providerName} onChange={(event) => applyProvider(event.target.value)}>
              {providers.map((provider) => (
                <option key={provider.name} value={provider.name}>
                  {provider.name}
                </option>
              ))}
            </select>
          </label>
          <div className={`segmented-control ${mode === "thinking" ? "is-thinking" : "is-fast"}`} aria-label="mode">
            <span className="segmented-indicator" />
            <button
              className={mode === "fast" ? "is-active" : ""}
              onClick={() => setMode("fast")}
              type="button"
            >
              <Zap size={14} />
              快速
            </button>
            <button
              className={mode === "thinking" ? "is-active" : ""}
              onClick={() => setMode("thinking")}
              type="button"
            >
              <Brain size={14} />
              思考
            </button>
          </div>
        </div>
      </header>

      <button
        className={`avatar-launcher ${menuOpen ? "is-active" : ""}`}
        onClick={toggleMenuFromAvatar}
        type="button"
        aria-label="打开侧边栏"
      >
        <UserRound size={22} />
      </button>

      <div className="emotion-chip glass-panel">
        <Bot size={15} />
        {currentEmotion}
      </div>

      <aside className={`left-dock glass-panel ${menuOpen ? "is-open" : ""}`} aria-hidden={!menuOpen}>
        <div className="dock-head">
          <div>
            <span className="eyebrow">Workspace</span>
            <h2>Amadeus</h2>
          </div>
          <button className="icon-button" onClick={collapsePrimaryPanel} type="button" aria-label="收起侧边栏">
            <ChevronLeft size={18} />
          </button>
        </div>

        <div className="dock-scroll">
          <button className="dock-action is-primary" onClick={startNewConversation} type="button">
            <Plus size={17} />
            新对话
          </button>

          <nav className="dock-nav" aria-label="main">
            <button className="dock-nav-item is-active" type="button">
              <MessageCircle size={17} />
              聊天记录
            </button>
            <button className="dock-nav-item" type="button">
              <Bell size={17} />
              任务
            </button>
            <button className="dock-nav-item" onClick={openConfigPanel} type="button">
              <Settings size={17} />
              设置
            </button>
          </nav>

          <section className="dock-section history-section">
            <label className="history-search">
              <Search size={15} />
              <input
                value={historySearch}
                onChange={(event) => setHistorySearch(event.target.value)}
                placeholder="搜索聊天记录"
                aria-label="搜索聊天记录"
              />
            </label>
            <div className="section-title">
              <Clock3 size={14} />
              最近
            </div>
            {!storageOnline && (
              <button className="history-item" type="button" disabled>
                <span>PostgreSQL 未连接</span>
                <small>{storageError || "offline"}</small>
              </button>
            )}
            {storageOnline && conversations.length === 0 && (
              <button className="history-item" type="button" disabled>
                <span>暂无历史</span>
                <small>new</small>
              </button>
            )}
            {storageOnline && conversations.length > 0 && filteredConversations.length === 0 && (
              <button className="history-item" type="button" disabled>
                <span>没有匹配记录</span>
                <small>search</small>
              </button>
            )}
            {filteredConversations.map((conversation) => (
              <div
                className={`history-row ${conversation.id === currentConversationId ? "is-current" : ""}`}
                key={conversation.id}
              >
                <button
                  className="history-item"
                  disabled={isStreaming}
                  onClick={() => void openConversation(conversation.id)}
                  type="button"
                >
                  <span>{conversation.title}</span>
                  <small>{conversation.id === currentConversationId ? status : formatHistoryTime(conversation.updatedAt)}</small>
                </button>
                <div className="history-actions" aria-label={`${conversation.title} 操作`}>
                  <button
                    className="history-icon-button"
                    onClick={() => void exportConversationItem(conversation)}
                    title="导出"
                    type="button"
                    aria-label={`导出 ${conversation.title}`}
                  >
                    <Download size={14} />
                  </button>
                  <button
                    className="history-icon-button is-danger"
                    disabled={isStreaming}
                    onClick={() => void deleteConversationItem(conversation)}
                    title="删除"
                    type="button"
                    aria-label={`删除 ${conversation.title}`}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            ))}
          </section>

          <section className="dock-section">
            <div className="section-title">
              <BookOpenText size={14} />
              任务
            </div>
            <button className="task-item" type="button">
              <span>Agent 工具注册</span>
              <small>pending</small>
            </button>
            <button className="task-item" type="button">
              <span>长期记忆接口</span>
              <small>planned</small>
            </button>
          </section>
        </div>
      </aside>

      <aside
        className={`config-dock glass-panel ${configOpen ? "is-open" : ""}`}
        style={{ left: "var(--settings-left)" }}
        aria-hidden={!configOpen}
      >
        <div className="dock-head">
          <div>
            <span className="eyebrow">Control</span>
            <h2>设置</h2>
          </div>
          <button className="icon-button" onClick={() => setConfigOpen(false)} type="button" aria-label="收起设置">
            <ChevronLeft size={18} />
          </button>
        </div>

        <div className="config-list">
          <ConfigGroup
            title="个人信息"
            icon={<UserRound size={16} />}
            open={openSections.profile}
            onToggle={() => toggleSection("profile")}
          >
            <label className="field">
              <span>用户昵称</span>
              <input value="用户" readOnly />
            </label>
            <label className="field">
              <span>人格预设</span>
              <input value="Amadeus 牧濑红莉栖" readOnly />
            </label>
          </ConfigGroup>

          <ConfigGroup
            title="基础模型"
            icon={<SlidersHorizontal size={16} />}
            open={openSections.model}
            onToggle={() => toggleSection("model")}
          >
            <label className="field">
              <span>Provider</span>
              <select value={modelSettings.providerName} onChange={(event) => applyProvider(event.target.value)}>
                {providers.map((provider) => (
                  <option key={provider.name} value={provider.name}>
                    {provider.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Base URL</span>
              <input
                value={modelSettings.baseUrl}
                onChange={(event) => setModelSettings((prev) => ({ ...prev, baseUrl: event.target.value }))}
              />
            </label>
            <label className="field">
              <span>Model</span>
              <input
                value={modelSettings.model}
                onChange={(event) => setModelSettings((prev) => ({ ...prev, model: event.target.value }))}
              />
            </label>
            <label className="field">
              <span>API Key</span>
              <input
                value={modelSettings.apiKey}
                onChange={(event) => setModelSettings((prev) => ({ ...prev, apiKey: event.target.value }))}
                type="password"
                autoComplete="off"
              />
            </label>
          </ConfigGroup>

          <ConfigGroup
            title="语音"
            icon={<Mic2 size={16} />}
            open={openSections.voice}
            onToggle={() => toggleSection("voice")}
          >
            <label className="field">
              <span>语音模型</span>
              <select
                value={voiceSettings.ttsBackend}
                onChange={(event) =>
                  setVoiceSettings((prev) => ({
                    ...prev,
                    ttsBackend: event.target.value === "cloud" ? "cloud" : "local"
                  }))
                }
              >
                <option value="local">本地 CosyVoice2</option>
                <option value="cloud">云端 SiliconFlow</option>
              </select>
            </label>
            <label className="field">
              <span>Cloud Key</span>
              <input
                value={voiceSettings.siliconFlowApiKey}
                onChange={(event) => setVoiceSettings((prev) => ({ ...prev, siliconFlowApiKey: event.target.value }))}
                type="password"
                autoComplete="off"
              />
            </label>
            <label className="field">
              <span>Cloud Voice URI</span>
              <input
                value={voiceSettings.clonedVoiceUri}
                onChange={(event) => setVoiceSettings((prev) => ({ ...prev, clonedVoiceUri: event.target.value }))}
                placeholder="speech:..."
              />
            </label>
            <div className="inline-fields">
              <label className="field">
                <span>Speed</span>
                <input
                  value={voiceSettings.speed}
                  min="0.75"
                  max="1.25"
                  step="0.05"
                  type="number"
                  onChange={(event) => setVoiceSettings((prev) => ({ ...prev, speed: Number(event.target.value) }))}
                />
              </label>
              <label className="field">
                <span>Gain</span>
                <input
                  value={voiceSettings.gain}
                  min="-6"
                  max="6"
                  step="0.5"
                  type="number"
                  onChange={(event) => setVoiceSettings((prev) => ({ ...prev, gain: Number(event.target.value) }))}
                />
              </label>
            </div>
            <div className="config-actions">
              <button className="text-icon-button" onClick={testVoice} disabled={voiceBusy} type="button">
                <Mic2 size={16} />
                试听
              </button>
              <button className="text-icon-button" onClick={cloneKurisuVoice} disabled={voiceBusy} type="button">
                <KeyRound size={16} />
                云端克隆
              </button>
            </div>
          </ConfigGroup>

          <ConfigGroup
            title="界面"
            icon={<Sparkles size={16} />}
            open={openSections.interface}
            onToggle={() => toggleSection("interface")}
          >
            <label className="switch-row">
              <span>自动语音</span>
              <input
                checked={voiceSettings.autoPlay}
                onChange={(event) => setVoiceSettings((prev) => ({ ...prev, autoPlay: event.target.checked }))}
                type="checkbox"
              />
            </label>
            <label className="switch-row">
              <span>字幕同步</span>
              <input
                checked={voiceSettings.syncTextOutput}
                onChange={(event) => setVoiceSettings((prev) => ({ ...prev, syncTextOutput: event.target.checked }))}
                type="checkbox"
              />
            </label>
          </ConfigGroup>
        </div>
      </aside>

      <section className="floating-conversation" aria-hidden={chatOpen}>
        <div className="floating-messages" ref={floatingMessageRef}>
          {visibleMessages.map((message) => renderMessageBubble(message, "floating"))}
        </div>
        <form className="floating-composer glass-panel" onSubmit={sendMessage}>
          <button className="round-tool" onClick={startNewConversation} type="button" aria-label="新建">
            <Plus size={20} />
          </button>
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void sendMessage();
              }
            }}
            placeholder="有问题，尽管问"
          />
          <button className="mode-select" type="button" onClick={() => setMode(mode === "fast" ? "thinking" : "fast")}>
            {mode === "fast" ? "Instant" : "Think"}
            <ChevronRight size={14} />
          </button>
          <button
            className={`round-tool voice-toggle ${voiceSettings.autoPlay ? "is-active" : ""}`}
            onClick={toggleAutoVoice}
            type="button"
            aria-label={voiceSettings.autoPlay ? "关闭语音" : "开启语音"}
            title={voiceSettings.autoPlay ? "关闭自动语音" : "开启自动语音"}
          >
            {voiceSettings.autoPlay ? <Volume2 size={18} /> : <Mic size={18} />}
          </button>
          {isStreaming ? (
            <button className="round-send" onClick={stopStreaming} type="button" aria-label="停止">
              <CircleStop size={19} />
            </button>
          ) : (
            <button className="round-send" disabled={!canSend} type="submit" aria-label="发送">
              <Send size={18} />
            </button>
          )}
        </form>
      </section>

      <button
        className={`chat-edge-toggle glass-panel ${chatOpen ? "is-open" : ""}`}
        onClick={() => setChatOpen((open) => !open)}
        type="button"
        aria-label={chatOpen ? "收起对话栏" : "展开对话栏"}
      >
        {chatOpen ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}
      </button>

      <aside className={`chat-panel glass-panel ${chatOpen ? "is-open" : ""}`} aria-hidden={!chatOpen}>
        <button
          className="resize-handle"
          onPointerDown={startChatResize}
          type="button"
          aria-label="调整聊天栏宽度"
        />
        <div className="chat-head">
          <div>
            <span className="eyebrow">Session</span>
            <h2>{currentConversation?.title ?? "Kurisu"}</h2>
          </div>
          <div className={`status-pill ${isStreaming ? "is-live" : ""}`}>{status}</div>
          <button className="icon-button" onClick={() => setChatOpen(false)} type="button" aria-label="收起对话栏">
            <ChevronRight size={18} />
          </button>
        </div>

        <div className="message-list" ref={chatMessageRef}>
          {messages.length === 0 && (
            <div className="empty-state">
              <MessageCircle size={24} />
              <span>待机</span>
            </div>
          )}
          {messages.map((message) => renderMessageBubble(message, "panel"))}
        </div>

        <form className="composer" onSubmit={sendMessage}>
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void sendMessage();
              }
            }}
            placeholder="输入消息"
            rows={3}
          />
          <div className="composer-actions">
            <button
              className={`text-icon-button ${voiceSettings.autoPlay ? "is-active" : ""}`}
              onClick={toggleAutoVoice}
              type="button"
            >
              {voiceSettings.autoPlay ? <Volume2 size={16} /> : <VolumeX size={16} />}
              语音
            </button>
            {isStreaming ? (
              <button className="send-button" onClick={stopStreaming} type="button">
                <CircleStop size={18} />
              </button>
            ) : (
              <button className="send-button" disabled={!canSend} type="submit">
                <Send size={18} />
              </button>
            )}
          </div>
        </form>
      </aside>

      {!enteredApp && <BootLoader ready={loadingReady} onEnter={() => setEnteredApp(true)} />}
    </main>
  );
}

function ConfigGroup({
  title,
  icon,
  open,
  onToggle,
  children
}: {
  title: string;
  icon: ReactNode;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className={`config-group ${open ? "is-open" : ""}`}>
      <button className="config-group-head" onClick={onToggle} type="button">
        <span>
          {icon}
          {title}
        </span>
        <ChevronRight size={17} />
      </button>
      <div className="config-group-body">{children}</div>
    </section>
  );
}
