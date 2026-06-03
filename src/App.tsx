import { FormEvent, PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, MouseEvent as ReactMouseEvent, ReactNode } from "react";
import {
  Bell,
  BookOpenText,
  Bot,
  Brain,
  ChevronLeft,
  ChevronRight,
  CircleStop,
  Clock3,
  Copy,
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
  UserRound,
  Volume2,
  VolumeX,
  Zap
} from "lucide-react";
import {
  cloneVoice,
  createConversation,
  fetchConversation,
  fetchProviders,
  fetchSettings,
  listConversations,
  saveSettings,
  streamChat,
  synthesizeVoice
} from "./api";
import {
  WorldLineDivergenceMeter,
  type WorldLineMeterHandle
} from "./components/WorldLineDivergenceMeter";
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
  VoiceSettings
} from "./types";

const STORAGE_KEY = "amadeus-web-settings-v1";
const PERSONA_ID = "kurisu_amadeus";
const LEFT_SIDEBAR_WIDTH = 316;
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

function generateWorldLineValue(): string {
  const base = 1.048596;
  if (Math.random() > 0.82) {
    return `${Math.floor(Math.random() * 2)}.${Math.random().toString().slice(2, 8).padEnd(6, "0")}`;
  }
  return (base + (Math.random() - 0.5) * 0.00018).toFixed(6);
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
  const [loadingValue, setLoadingValue] = useState("1.048596");
  const bootMeterRef = useRef<WorldLineMeterHandle | null>(null);
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
  const bootPhase = live2dError
    ? "LIVE2D LINK FAILURE"
    : loadingReady
      ? "WORLD LINE STABILIZED"
      : live2dReady
        ? "MINIMUM BOOT SEQUENCE"
        : "SYNCHRONIZING LIVE2D MODEL";
  const bootDetail = live2dError
    ? live2dError
    : loadingReady
      ? "模型已载入，点击进入对话。"
      : live2dReady
        ? "模型已就绪，正在稳定时间线。"
        : "正在加载 Live2D 运行时与 Kurisu 模型。";

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setMinimumBootElapsed(true);
    }, MIN_BOOT_DURATION_MS);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (loadingReady) {
      setLoadingValue("1.048596");
      const timer = window.setTimeout(() => {
        bootMeterRef.current?.shiftTo("1.048596", {
          transition: "burn"
        });
      }, 80);
      return () => window.clearTimeout(timer);
    }

    const interval = window.setInterval(() => {
      setLoadingValue(generateWorldLineValue());
    }, 1800);
    return () => window.clearInterval(interval);
  }, [loadingReady]);

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
    if (event.event === "emotion") {
      setCurrentEmotion(event.payload.emotion);
      live2dRef.current?.playEmotion(event.payload.live2dEmotion);
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

    const move = (moveEvent: PointerEvent) => {
      const maxWidth = getChatMaxWidth(menuOpen, configOpen);
      const nextWidth = window.innerWidth - moveEvent.clientX - EDGE_MARGIN;
      setChatWidth(clamp(nextWidth, CHAT_MIN_WIDTH, maxWidth));
    };

    const up = () => {
      target.releasePointerCapture(pointerId);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  function startChatMouseResize(event: ReactMouseEvent<HTMLButtonElement>) {
    event.preventDefault();

    const move = (moveEvent: MouseEvent) => {
      const maxWidth = getChatMaxWidth(menuOpen, configOpen);
      const nextWidth = window.innerWidth - moveEvent.clientX - EDGE_MARGIN;
      setChatWidth(clamp(nextWidth, CHAT_MIN_WIDTH, maxWidth));
    };

    const up = () => {
      window.removeEventListener("mousemove", move);
      window.removeEventListener("mouseup", up);
    };

    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  }

  const settingsOffset = menuOpen ? EDGE_MARGIN + LEFT_SIDEBAR_WIDTH + PANEL_GAP : EDGE_MARGIN;
  const chatMaxWidth = getChatMaxWidth(menuOpen, configOpen);
  const clampedChatWidth = clamp(chatWidth, CHAT_MIN_WIDTH, chatMaxWidth);
  const currentConversation = conversations.find((conversation) => conversation.id === currentConversationId);

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
          {surface === "panel" && message.thinking && (
            <details className="thinking-block">
              <summary>thinking</summary>
              <p>{message.thinking}</p>
            </details>
          )}
          <p className="animated-text">{content}</p>
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
              <button
                className={`history-item ${conversation.id === currentConversationId ? "is-current" : ""}`}
                disabled={isStreaming}
                key={conversation.id}
                onClick={() => void openConversation(conversation.id)}
                type="button"
              >
                <span>{conversation.title}</span>
                <small>{conversation.id === currentConversationId ? status : formatHistoryTime(conversation.updatedAt)}</small>
              </button>
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
          onMouseDown={startChatMouseResize}
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

      {!enteredApp && (
        <section
          className={[
            "boot-overlay",
            loadingReady ? "is-ready" : "is-loading"
          ].join(" ")}
          aria-live="polite"
          onClick={() => {
            if (loadingReady) {
              setEnteredApp(true);
            }
          }}
        >
          <div className="boot-panel glass-panel">
            <div className="boot-meter-shell">
              <WorldLineDivergenceMeter
                ref={bootMeterRef}
                value={loadingValue}
                transition={loadingReady ? "burn" : "worldline"}
                settleDirection="right-to-left"
                duration={loadingReady ? 900 : 1900}
                stagger={105}
                scrambleRate={38}
                tubeScale={0.92}
                glow={loadingReady ? 1.25 : 1.12}
                ghostOpacity={1}
              />
            </div>
            <div className="boot-copy">
              <span className="eyebrow">Amadeus Boot Sequence</span>
              <h2>{bootPhase}</h2>
              <p>{bootDetail}</p>
            </div>
            <div className="boot-progress" aria-hidden="true">
              <span className={live2dLoadResolved ? "is-done" : ""} />
              <span className={minimumBootElapsed ? "is-done" : ""} />
              <span className={loadingReady ? "is-done" : ""} />
            </div>
            <button
              className="boot-enter-button"
              disabled={!loadingReady}
              onClick={(event) => {
                event.stopPropagation();
                setEnteredApp(true);
              }}
              type="button"
            >
              {loadingReady ? "点击进入" : "同步中"}
            </button>
          </div>
        </section>
      )}
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
