import { PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, CSSProperties, DragEvent as ReactDragEvent, FormEvent } from "react";
import {
  Bell,
  ChevronLeft,
  ChevronRight,
  CircleStop,
  FolderOpen,
  KeyRound,
  Menu,
  MessageCircle,
  Mic,
  Mic2,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Play,
  Send,
  SlidersHorizontal,
  RotateCcw,
  Sparkles,
  UserRound,
  Volume2,
  VolumeX
} from "lucide-react";
import {
  cloneVoice,
  createConversation,
  deleteConversation,
  fetchConversation,
  fetchProviders,
  fetchSettings,
  listConversations,
  replyCodeTaskQuestion,
  saveSettings,
  subscribeCodeTaskEvents,
  streamCodeTask,
  streamChat,
  streamTaskSummaryVoice,
  syncCodeTaskHistory,
  synthesizeVoice,
  uploadAgentFile
} from "./api";
import {
  DEFAULT_LIVE2D_MODEL,
  activateLive2DModel,
  deleteImportedLive2DModel,
  importLive2DModelFromFiles,
  listLive2DModelHistory,
  releaseLive2DModelRuntime,
  updateLive2DModelTransform,
  type Live2DControlOption,
  type Live2DModelRecord,
  type Live2DModelTransform
} from "./agents/live2dImportAgent";
import { BootLoader } from "./components/BootLoader";
import { ChatMessageBubble } from "./components/ChatMessageBubble";
import { ConfigGroup } from "./components/ConfigGroup";
import { LeftDock } from "./components/LeftDock";
import { Live2DStage, type Live2DStageHandle } from "./components/Live2DStage";
import { Live2DModelHistory, Live2DQuickControls } from "./components/Live2DControls";
import { TaskPanel } from "./components/TaskPanel";
import { TopBar } from "./components/TopBar";
import { UploadAttachmentTray, UploadPopover } from "./components/UploadControls";
import type {
  ApiChatMessage,
  ChatMessage,
  ChatMode,
  CodeTaskEvent,
  ConversationDetail,
  ConversationSummary,
  ModelSettings,
  ProviderPreset,
  StoredSettings,
  StreamEvent,
  ToolTraceEvent,
  UploadedFileInfo,
  VoiceSettings
} from "./types";

import {
  CHAT_MIN_WIDTH,
  CODE_TASK_FLUSH_INTERVAL_MS,
  CODE_TASK_HISTORY_STORAGE_KEY,
  CODE_TASK_MAX_ASSISTANT_TEXT,
  CODE_TASK_MAX_DETAIL_TEXT,
  CODE_TASK_MAX_HISTORY,
  DEFAULT_CODE_TASK_WORKSPACE,
  DEFAULT_MODEL_SETTINGS,
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
  buildCodeTaskSummaryText,
  buildCodeTaskVoiceSummarySource,
  buildConversationTitle,
  clamp,
  codeTaskToMarkdown,
  composeMessageText,
  conversationToMarkdown,
  createId,
  downloadText,
  formatMessageTime,
  getChatMaxWidth,
  limitCodeTaskText,
  loadStoredCodeTasks,
  loadStoredSettings,
  normalizeDisplayEmotion,
  normalizeLive2dEmotion,
  normalizeMessageContent,
  normalizeThinkingText,
  sanitizeFilename,
  toApiMessages,
  toChatMessages,
  trimCodeTaskMessages,
  type AudioQueueItem,
  type CodeTaskMessage,
  type CodeTaskMessageKind,
  type CodeTaskRecord,
  type ConfigSection,
  type RightPanelTab,
  type ToolResultLink
} from "./app/appSupport";
export default function App() {
  const stored = useMemo(loadStoredSettings, []);
  const storedCodeTasks = useMemo(loadStoredCodeTasks, []);
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
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFileInfo[]>([]);
  const [historySearch, setHistorySearch] = useState("");
  const [uploadPanelOpen, setUploadPanelOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatWidth, setChatWidth] = useState(430);
  const [rightPanelTab, setRightPanelTab] = useState<RightPanelTab>("chat");
  const [codeTasks, setCodeTasks] = useState<CodeTaskRecord[]>(storedCodeTasks);
  const [activeCodeTaskId, setActiveCodeTaskId] = useState<string | null>(storedCodeTasks[0]?.id ?? null);
  const [codeTaskWorkspace, setCodeTaskWorkspace] = useState(storedCodeTasks[0]?.workspacePath ?? DEFAULT_CODE_TASK_WORKSPACE);
  const [codeTaskInput, setCodeTaskInput] = useState("");
  const [codeTaskAutoApprove, setCodeTaskAutoApprove] = useState(false);
  const [codeTaskRunning, setCodeTaskRunning] = useState(false);
  const [codeTaskStatus, setCodeTaskStatus] = useState(storedCodeTasks[0]?.status ?? "idle");
  const [codeTaskMessages, setCodeTaskMessages] = useState<CodeTaskMessage[]>(storedCodeTasks[0]?.messages ?? []);
  const [codeTaskQuestionBusyIds, setCodeTaskQuestionBusyIds] = useState<string[]>([]);
  const [openSections, setOpenSections] = useState<Record<ConfigSection, boolean>>({
    profile: false,
    model: false,
    voice: false,
    live2d: false,
    interface: false
  });
  const [status, setStatus] = useState("idle");
  const [currentEmotion, setCurrentEmotion] = useState("neutral");
  const [live2dModels, setLive2dModels] = useState<Live2DModelRecord[]>([DEFAULT_LIVE2D_MODEL]);
  const [activeLive2DModel, setActiveLive2DModel] = useState<Live2DModelRecord>(DEFAULT_LIVE2D_MODEL);
  const [live2dImportBusy, setLive2dImportBusy] = useState(false);
  const [live2dImportStatus, setLive2dImportStatus] = useState("");
  const [live2dControlsOpen, setLive2dControlsOpen] = useState(false);
  const [live2dControlTab, setLive2dControlTab] = useState<"expressions" | "motions">("expressions");
  const [live2dTransformLocked, setLive2dTransformLocked] = useState(() => {
    return localStorage.getItem(LIVE2D_LOCK_STORAGE_KEY) !== "false";
  });
  const [isStreaming, setIsStreaming] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [live2dReady, setLive2dReady] = useState(false);
  const [live2dError, setLive2dError] = useState("");
  const [minimumBootElapsed, setMinimumBootElapsed] = useState(false);
  const [enteredApp, setEnteredApp] = useState(false);
  const live2dRef = useRef<Live2DStageHandle | null>(null);
  const activeLive2DModelRef = useRef<Live2DModelRecord>(DEFAULT_LIVE2D_MODEL);
  const abortRef = useRef<AbortController | null>(null);
  const codeTaskAbortRef = useRef<AbortController | null>(null);
  const codeTaskAssistantMessageIdRef = useRef<string | null>(null);
  const codeTaskFlushTimerRef = useRef<number | null>(null);
  const pendingCodeTaskAssistantDeltaRef = useRef("");
  const pendingCodeTaskMessagesRef = useRef<Array<Omit<CodeTaskMessage, "id" | "createdAt">>>([]);
  const lastCodeTaskStatusMessageRef = useRef("");
  const codeTasksRef = useRef<CodeTaskRecord[]>(storedCodeTasks);
  const codeTaskMessagesRef = useRef<CodeTaskMessage[]>(storedCodeTasks[0]?.messages ?? []);
  const codeTaskCompletedRef = useRef(false);
  const codeTaskFinalStatusRef = useRef("");
  const codeTaskServerUrlRef = useRef("");
  const codeTaskPersistTimerRef = useRef<number | null>(null);
  const codeTaskHistorySyncTimerRef = useRef<number | null>(null);
  const handledCodeTaskEventKeysRef = useRef<Set<string>>(new Set());
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioQueueRef = useRef<AudioQueueItem[]>([]);
  const audioPlayingRef = useRef(false);
  const textRevealFrameRef = useRef<number | null>(null);
  const textRevealTokenRef = useRef(0);
  const floatingMessageRef = useRef<HTMLDivElement | null>(null);
  const chatMessageRef = useRef<HTMLDivElement | null>(null);
  const taskMessageRef = useRef<HTMLDivElement | null>(null);
  const saveSettingsTimerRef = useRef<number | null>(null);

  const selectedProvider = providers.find((provider) => provider.name === modelSettings.providerName);
  const canSend = (input.trim().length > 0 || uploadedFiles.length > 0) && !isStreaming;
  const canStartCodeTask = codeTaskInput.trim().length > 0 && !codeTaskRunning;
  const visibleMessages = messages.filter((message) => message.content || message.streaming).slice(-8);
  const activeCodeTask = codeTasks.find((task) => task.id === activeCodeTaskId) ?? null;
  const visibleCodeTasks = useMemo(
    () => [...codeTasks].sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()),
    [codeTasks]
  );
  const codeTaskPhases = useMemo(
    () => buildCodeTaskPhases(codeTaskMessages, codeTaskRunning),
    [codeTaskMessages, codeTaskRunning]
  );
  const codeTaskMessageCount = codeTaskMessages.length;
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
    let cancelled = false;

    async function loadLive2DHistory() {
      try {
        const records = await listLive2DModelHistory();
        if (cancelled) {
          return;
        }
        setLive2dModels(records);
        const storedId = localStorage.getItem(LIVE2D_ACTIVE_STORAGE_KEY) || DEFAULT_LIVE2D_MODEL.id;
        const target = records.find((record) => record.id === storedId) ?? DEFAULT_LIVE2D_MODEL;
        const activated = await activateLive2DModel(target);
        if (cancelled) {
          releaseLive2DModelRuntime(activated);
          return;
        }
        applyLive2DRecord(activated, { persist: false });
      } catch (error) {
        setLive2dImportStatus(error instanceof Error ? error.message : "Live2D history failed");
      }
    }

    void loadLive2DHistory();
    return () => {
      cancelled = true;
      releaseLive2DModelRuntime(activeLive2DModelRef.current);
    };
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
    const payload = codeTasks.slice(0, CODE_TASK_MAX_HISTORY);
    codeTasksRef.current = payload;
    localStorage.setItem(CODE_TASK_HISTORY_STORAGE_KEY, JSON.stringify(payload));

    if (codeTaskHistorySyncTimerRef.current !== null) {
      window.clearTimeout(codeTaskHistorySyncTimerRef.current);
    }
    codeTaskHistorySyncTimerRef.current = window.setTimeout(() => {
      codeTaskHistorySyncTimerRef.current = null;
      syncCodeTaskHistory(payload).catch(() => undefined);
    }, 500);
  }, [codeTasks]);

  useEffect(() => {
    const syncCurrentCodeTasks = () => {
      const payload = codeTasksRef.current.slice(0, CODE_TASK_MAX_HISTORY);
      syncCodeTaskHistory(payload).catch(() => undefined);
    };
    const syncTimer = window.setInterval(syncCurrentCodeTasks, 30000);
    window.addEventListener("focus", syncCurrentCodeTasks);
    document.addEventListener("visibilitychange", syncCurrentCodeTasks);
    syncCurrentCodeTasks();
    return () => {
      window.clearInterval(syncTimer);
      window.removeEventListener("focus", syncCurrentCodeTasks);
      document.removeEventListener("visibilitychange", syncCurrentCodeTasks);
    };
  }, []);

  useEffect(() => {
    codeTaskMessagesRef.current = codeTaskMessages;
  }, [codeTaskMessages]);

  useEffect(() => {
    if (!activeCodeTaskId) {
      return;
    }
    const taskId = activeCodeTaskId;
    handledCodeTaskEventKeysRef.current.clear();
    return subscribeCodeTaskEvents({
      taskId,
      replay: false,
      onEvent: (event) => handleCodeTaskEventOnce(event, taskId),
      onError: (message) => {
        if (rightPanelTab === "tasks") {
          setCodeTaskStatus(message);
        }
      }
    });
  }, [activeCodeTaskId, rightPanelTab]);

  useEffect(() => {
    if (!activeCodeTaskId) {
      if (codeTaskPersistTimerRef.current !== null) {
        window.clearTimeout(codeTaskPersistTimerRef.current);
        codeTaskPersistTimerRef.current = null;
      }
      return;
    }
    if (codeTaskPersistTimerRef.current !== null) {
      window.clearTimeout(codeTaskPersistTimerRef.current);
    }
    codeTaskPersistTimerRef.current = window.setTimeout(
      () => {
        codeTaskPersistTimerRef.current = null;
        const now = new Date().toISOString();
        setCodeTasks((prev) =>
          prev
            .map((task) =>
              task.id === activeCodeTaskId
                ? {
                    ...task,
                    workspacePath: codeTaskWorkspace,
                    status: codeTaskStatus,
                    messages: trimCodeTaskMessages(codeTaskMessages),
                    updatedAt: now
                  }
                : task
            )
            .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
            .slice(0, CODE_TASK_MAX_HISTORY)
        );
      },
      codeTaskRunning ? 700 : 0
    );
  }, [activeCodeTaskId, codeTaskMessages, codeTaskStatus, codeTaskWorkspace, codeTaskRunning]);

  useEffect(() => {
    return () => {
      if (saveSettingsTimerRef.current !== null) {
        window.clearTimeout(saveSettingsTimerRef.current);
      }
      if (codeTaskFlushTimerRef.current !== null) {
        window.clearTimeout(codeTaskFlushTimerRef.current);
      }
      if (codeTaskPersistTimerRef.current !== null) {
        window.clearTimeout(codeTaskPersistTimerRef.current);
      }
      if (codeTaskHistorySyncTimerRef.current !== null) {
        window.clearTimeout(codeTaskHistorySyncTimerRef.current);
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

  useEffect(() => {
    taskMessageRef.current?.scrollTo({ top: taskMessageRef.current.scrollHeight, behavior: "auto" });
  }, [codeTaskMessageCount, rightPanelTab, chatOpen]);

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

  function toggleLive2DTransformLock() {
    setLive2dTransformLocked((locked) => {
      const nextLocked = !locked;
      localStorage.setItem(LIVE2D_LOCK_STORAGE_KEY, String(nextLocked));
      setStatus(nextLocked ? "live2d locked" : "live2d unlocked");
      return nextLocked;
    });
  }

  function applyLive2DRecord(record: Live2DModelRecord, options: { persist?: boolean } = {}) {
    const previous = activeLive2DModelRef.current;
    activeLive2DModelRef.current = record;
    setActiveLive2DModel(record);
    setLive2dReady(false);
    setLive2dError("");
    setCurrentEmotion("neutral");
    setLive2dControlsOpen(false);
    if (options.persist !== false) {
      localStorage.setItem(LIVE2D_ACTIVE_STORAGE_KEY, record.id);
    }
    if (previous.modelUrl !== record.modelUrl) {
      window.setTimeout(() => releaseLive2DModelRuntime(previous), 1200);
    }
  }

  function attachLive2DFolderInput(node: HTMLInputElement | null) {
    if (node) {
      node.setAttribute("webkitdirectory", "");
      node.setAttribute("directory", "");
    }
  }

  async function refreshLive2DHistory() {
    const records = await listLive2DModelHistory();
    setLive2dModels(records);
    return records;
  }

  async function handleLive2DFolderSelection(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.currentTarget.files ?? []);
    event.currentTarget.value = "";
    await importLive2DFolderFiles(files);
  }

  async function importLive2DFolderFiles(files: File[]) {
    if (files.length === 0 || live2dImportBusy) {
      return;
    }
    setLive2dImportBusy(true);
    setLive2dImportStatus("解析 Live2D 目录");
    setStatus("live2d import");
    try {
      const result = await importLive2DModelFromFiles(files);
      applyLive2DRecord(result.record);
      await refreshLive2DHistory();
      setLive2dImportStatus(
        `已导入 ${result.record.name}：${result.record.expressions.length} 表情 / ${result.record.motions.length} 动作`
      );
      setStatus("live2d ready");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Live2D import failed";
      setLive2dImportStatus(message);
      setStatus("live2d error");
    } finally {
      setLive2dImportBusy(false);
    }
  }

  async function switchLive2DModel(record: Live2DModelRecord) {
    if (live2dImportBusy || record.id === activeLive2DModel.id) {
      return;
    }
    setLive2dImportStatus(`切换到 ${record.name}`);
    setStatus("live2d switch");
    try {
      const activated = await activateLive2DModel(record);
      applyLive2DRecord(activated);
      setLive2dImportStatus(`当前形象：${activated.name}`);
      setStatus("live2d ready");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Live2D switch failed";
      setLive2dImportStatus(message);
      setStatus("live2d error");
    }
  }

  async function removeLive2DModel(record: Live2DModelRecord) {
    if (record.source === "default" || live2dImportBusy) {
      return;
    }
    setLive2dImportBusy(true);
    try {
      await deleteImportedLive2DModel(record.id);
      const records = await refreshLive2DHistory();
      if (record.id === activeLive2DModel.id) {
        const fallback = records.find((item) => item.id === DEFAULT_LIVE2D_MODEL.id) ?? DEFAULT_LIVE2D_MODEL;
        const activated = await activateLive2DModel(fallback);
        applyLive2DRecord(activated);
      }
      setLive2dImportStatus(`已删除 ${record.name}`);
    } catch (error) {
      setLive2dImportStatus(error instanceof Error ? error.message : "Live2D delete failed");
    } finally {
      setLive2dImportBusy(false);
    }
  }

  function handleLive2DTransformChange(transform: Live2DModelTransform) {
    const current = activeLive2DModelRef.current;
    if (current.source === "default") {
      return;
    }
    const nextActive = {
      ...current,
      transform
    };
    activeLive2DModelRef.current = nextActive;
    setActiveLive2DModel(nextActive);
    setLive2dModels((prev) =>
      prev.map((record) => (record.id === current.id ? { ...record, transform } : record))
    );
    void updateLive2DModelTransform(current.id, transform).catch((error) => {
      setLive2dImportStatus(error instanceof Error ? error.message : "Live2D transform save failed");
    });
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
        setUploadedFiles([]);
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
    setUploadedFiles([]);
    setStatus("idle");
    setIsStreaming(false);
    setRightPanelTab("chat");
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

  async function uploadFiles(files: File[]) {
    if (files.length === 0) {
      return;
    }

    setUploadBusy(true);
    try {
      const uploaded: UploadedFileInfo[] = [];
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index];
        setStatus(files.length > 1 ? `upload ${index + 1}/${files.length}` : "upload");
        const response = await uploadAgentFile({
          file,
          device: "host",
          overwrite: true
        });
        uploaded.push(response.file);
      }
      setUploadedFiles((prev) => {
        const byPath = new Map(prev.map((file) => [file.path, file]));
        uploaded.forEach((file) => byPath.set(file.path, file));
        return Array.from(byPath.values());
      });
      setUploadPanelOpen(false);
      setStatus(files.length > 1 ? `uploaded ${files.length}` : "uploaded");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "upload failed");
    } finally {
      setUploadBusy(false);
    }
  }

  async function handleFileSelection(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.currentTarget.files ?? []);
    event.currentTarget.value = "";
    await uploadFiles(files);
  }

  function handleUploadDrop(event: ReactDragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (uploadBusy) {
      return;
    }
    void uploadFiles(Array.from(event.dataTransfer.files ?? []));
  }

  function toggleUploadPanel() {
    if (uploadBusy) {
      return;
    }
    setUploadPanelOpen((open) => {
      const nextOpen = !open;
      setStatus(nextOpen ? "select file" : "idle");
      return nextOpen;
    });
  }

  function removeUploadedFile(path: string) {
    setUploadedFiles((prev) => prev.filter((file) => file.path !== path));
  }

  function renderUploadedFiles() {
    return <UploadAttachmentTray files={uploadedFiles} onRemove={removeUploadedFile} />;
  }

  function renderUploadPanel() {
    if (!uploadPanelOpen) {
      return null;
    }
    return <UploadPopover busy={uploadBusy} onDrop={handleUploadDrop} onFileChange={handleFileSelection} />;
  }

  function live2DRuntimeLabel(record: Live2DModelRecord): string {
    return record.runtime === "cubism3" ? "moc3" : "moc";
  }

  function playLive2DExpression(option: Live2DControlOption) {
    setCurrentEmotion(option.label);
    live2dRef.current?.playExpression(option.name);
  }

  function playLive2DMotion(option: Live2DControlOption) {
    setCurrentEmotion(option.label);
    live2dRef.current?.playMotion(option.group ?? option.name, option.index ?? 0);
  }

  function renderLive2DQuickControls() {
    return (
      <Live2DQuickControls
        activeModel={activeLive2DModel}
        currentEmotion={currentEmotion}
        controlsOpen={live2dControlsOpen}
        transformLocked={live2dTransformLocked}
        tab={live2dControlTab}
        onToggleControls={() => setLive2dControlsOpen((open) => !open)}
        onToggleLock={toggleLive2DTransformLock}
        onTabChange={setLive2dControlTab}
        onExpression={playLive2DExpression}
        onMotion={playLive2DMotion}
      />
    );
  }

  function renderLive2DHistory() {
    return (
      <Live2DModelHistory
        models={live2dModels}
        activeModelId={activeLive2DModel.id}
        busy={live2dImportBusy}
        onSwitch={switchLive2DModel}
        onRemove={removeLive2DModel}
      />
    );
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
    const text = composeMessageText(input, uploadedFiles);
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
    setUploadedFiles([]);
    setUploadPanelOpen(false);
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

  function openTaskPanel() {
    setRightPanelTab("tasks");
    setChatOpen(true);
  }

  function updateCodeTaskRecord(taskId: string, updater: (task: CodeTaskRecord) => CodeTaskRecord) {
    setCodeTasks((prev) =>
      prev
        .map((task) => (task.id === taskId ? updater(task) : task))
        .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())
        .slice(0, CODE_TASK_MAX_HISTORY)
    );
  }

  function openCodeTaskRecord(task: CodeTaskRecord) {
    if (codeTaskRunning && task.id !== activeCodeTaskId) {
      return;
    }
    clearPendingCodeTaskFlush();
    setActiveCodeTaskId(task.id);
    setCodeTaskWorkspace(task.workspacePath);
    setCodeTaskStatus(task.status);
    setCodeTaskMessages(task.messages);
    codeTaskMessagesRef.current = task.messages;
    setCodeTaskInput("");
    codeTaskAssistantMessageIdRef.current = null;
    codeTaskServerUrlRef.current = "";
    setCodeTaskQuestionBusyIds([]);
    openTaskPanel();
  }

  function startNewCodeTaskDraft() {
    codeTaskAbortRef.current?.abort();
    clearPendingCodeTaskFlush();
    codeTaskAssistantMessageIdRef.current = null;
    codeTaskServerUrlRef.current = "";
    setCodeTaskQuestionBusyIds([]);
    setActiveCodeTaskId(null);
    setCodeTaskMessages([]);
    codeTaskMessagesRef.current = [];
    setCodeTaskInput("");
    setCodeTaskStatus("idle");
    setCodeTaskRunning(false);
    openTaskPanel();
  }

  function exportCodeTaskRecord(task: CodeTaskRecord) {
    const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
    downloadText(`${sanitizeFilename(task.title)}-${stamp}.md`, codeTaskToMarkdown(task));
  }

  function deleteCodeTaskRecord(task: CodeTaskRecord) {
    if (codeTaskRunning && task.id === activeCodeTaskId) {
      setCodeTaskStatus("请先停止当前任务");
      return;
    }
    const confirmed = window.confirm(`删除任务「${task.title}」？`);
    if (!confirmed) {
      return;
    }
    setCodeTasks((prev) => prev.filter((item) => item.id !== task.id));
    if (task.id === activeCodeTaskId) {
      const nextTask = codeTasks.find((item) => item.id !== task.id) ?? null;
      setActiveCodeTaskId(nextTask?.id ?? null);
      setCodeTaskMessages(nextTask?.messages ?? []);
      codeTaskMessagesRef.current = nextTask?.messages ?? [];
      setCodeTaskWorkspace(nextTask?.workspacePath ?? DEFAULT_CODE_TASK_WORKSPACE);
      setCodeTaskStatus(nextTask?.status ?? "idle");
      codeTaskServerUrlRef.current = "";
      setCodeTaskQuestionBusyIds([]);
    }
  }

  async function chooseCodeTaskFolder() {
    try {
      const selected = await window.amadeusDesktop?.selectFolder?.();
      if (selected) {
        setCodeTaskWorkspace(selected);
        return;
      }
    } catch (error) {
      setCodeTaskStatus(error instanceof Error ? error.message : "folder picker failed");
      return;
    }
    const manualPath = window.prompt("输入要交给 OpenCode 的文件夹路径", codeTaskWorkspace);
    if (manualPath !== null) {
      setCodeTaskWorkspace(manualPath.trim());
    }
  }

  function appendCodeTaskMessage(message: Omit<CodeTaskMessage, "id" | "createdAt">) {
    pendingCodeTaskMessagesRef.current.push({
      ...message,
      text: limitCodeTaskText(message.text, message.kind === "user" ? CODE_TASK_MAX_ASSISTANT_TEXT : CODE_TASK_MAX_DETAIL_TEXT),
      detail: message.detail ? limitCodeTaskText(message.detail) : undefined
    });
    scheduleCodeTaskFlush();
  }

  function appendCodeTaskAssistantDelta(text: string) {
    if (!text) {
      return;
    }
    pendingCodeTaskAssistantDeltaRef.current += text;
    scheduleCodeTaskFlush();
  }

  function clearPendingCodeTaskFlush() {
    pendingCodeTaskAssistantDeltaRef.current = "";
    pendingCodeTaskMessagesRef.current = [];
    lastCodeTaskStatusMessageRef.current = "";
    if (codeTaskFlushTimerRef.current !== null) {
      window.clearTimeout(codeTaskFlushTimerRef.current);
      codeTaskFlushTimerRef.current = null;
    }
  }

  function scheduleCodeTaskFlush() {
    if (codeTaskFlushTimerRef.current !== null) {
      return;
    }
    codeTaskFlushTimerRef.current = window.setTimeout(flushCodeTaskUpdates, CODE_TASK_FLUSH_INTERVAL_MS);
  }

  function flushCodeTaskUpdates() {
    codeTaskFlushTimerRef.current = null;
    const assistantDelta = pendingCodeTaskAssistantDeltaRef.current;
    const queuedMessages = pendingCodeTaskMessagesRef.current;
    pendingCodeTaskAssistantDeltaRef.current = "";
    pendingCodeTaskMessagesRef.current = [];
    if (!assistantDelta && queuedMessages.length === 0) {
      return;
    }
    let next = codeTaskMessagesRef.current;
    const targetId = codeTaskAssistantMessageIdRef.current;
    if (assistantDelta) {
      if (targetId && next.some((message) => message.id === targetId)) {
        next = next.map((message) =>
          message.id === targetId
            ? {
                ...message,
                text: appendLimitedCodeTaskText(message.text, assistantDelta)
              }
            : message
        );
      } else {
        const id = createId();
        codeTaskAssistantMessageIdRef.current = id;
        next = [
          ...next,
          {
            id,
            kind: "assistant",
            title: "OpenCode",
            text: appendLimitedCodeTaskText("", assistantDelta),
            createdAt: new Date().toISOString()
          }
        ];
      }
    }
    if (queuedMessages.length > 0) {
      const createdAt = new Date().toISOString();
      next = [
        ...next,
        ...queuedMessages.map((message) => ({
          ...message,
          id: createId(),
          createdAt
        }))
      ];
    }
    const trimmed = trimCodeTaskMessages(next);
    codeTaskMessagesRef.current = trimmed;
    setCodeTaskMessages(trimmed);
  }

  function updateCodeTaskMessage(messageId: string, updater: (message: CodeTaskMessage) => CodeTaskMessage) {
    flushCodeTaskUpdates();
    const next = codeTaskMessagesRef.current.map((message) => (message.id === messageId ? updater(message) : message));
    codeTaskMessagesRef.current = next;
    setCodeTaskMessages(next);
  }

  function handleCodeTaskEventOnce(event: CodeTaskEvent, taskId = activeCodeTaskId) {
    const payload = event.payload as Record<string, unknown>;
    const eventId = payload.eventId;
    const key = taskId && (typeof eventId === "number" || typeof eventId === "string")
      ? `${taskId}:${event.event}:${eventId}`
      : "";
    if (key) {
      if (handledCodeTaskEventKeysRef.current.has(key)) {
        return;
      }
      handledCodeTaskEventKeysRef.current.add(key);
      if (handledCodeTaskEventKeysRef.current.size > 1200) {
        handledCodeTaskEventKeysRef.current.clear();
      }
    }
    handleCodeTaskEvent(event, taskId);
  }

  function handleCodeTaskEvent(event: CodeTaskEvent, taskId = activeCodeTaskId) {
    if (event.event === "input") {
      setCodeTaskRunning(true);
      setCodeTaskStatus("running");
      if (event.payload.source === "mobile") {
        flushCodeTaskUpdates();
        codeTaskAssistantMessageIdRef.current = null;
        appendCodeTaskMessage({
          kind: "user",
          title: "手机端指令",
          text: event.payload.text || event.payload.message || "",
          detail: event.payload.workspacePath
        });
      }
      return;
    }
    if (event.event === "output") {
      appendCodeTaskAssistantDelta(event.payload.text);
      return;
    }
    if (event.event === "status") {
      setCodeTaskRunning(true);
      const nextStatus = event.payload.status || event.payload.message || "running";
      const statusMessage = event.payload.message || `OpenCode 状态：${nextStatus}`;
      setCodeTaskStatus(nextStatus);
      if (statusMessage !== lastCodeTaskStatusMessageRef.current) {
        lastCodeTaskStatusMessageRef.current = statusMessage;
        appendCodeTaskMessage({ kind: "status", title: "状态", text: statusMessage });
      }
      return;
    }
    if (event.event === "session") {
      setCodeTaskRunning(true);
      setCodeTaskStatus("session");
      codeTaskServerUrlRef.current = event.payload.serverUrl || "";
      if (taskId) {
        updateCodeTaskRecord(taskId, (task) => ({
          ...task,
          sessionId: event.payload.sessionId,
          workspacePath: event.payload.workspacePath || task.workspacePath,
          updatedAt: new Date().toISOString()
        }));
      }
      appendCodeTaskMessage({
        kind: "status",
        title: "会话",
        text: `OpenCode 会话已创建：${event.payload.sessionId}`,
        detail: event.payload.workspacePath
      });
      return;
    }
    if (event.event === "question") {
      const requestId = event.payload.requestId || "";
      const questions = event.payload.questions ?? [];
      setCodeTaskStatus("waiting_input");
      setCodeTaskRunning(true);
      appendCodeTaskMessage({
        kind: "question",
        title: "需要选择",
        text: event.payload.message || "OpenCode 请求你选择或补充信息。",
        detail: event.payload.tool ? JSON.stringify(event.payload.tool, null, 2) : undefined,
        question: {
          requestId,
          sessionId: event.payload.sessionId || activeCodeTask?.sessionId || "",
          serverUrl: codeTaskServerUrlRef.current,
          questions,
          v2: event.payload.v2 ?? true
        }
      });
      return;
    }
    if (event.event === "question_result") {
      const requestId = event.payload.requestId || "";
      if (requestId) {
        flushCodeTaskUpdates();
        const next = codeTaskMessagesRef.current.map((message) =>
          message.question?.requestId === requestId
            ? {
                ...message,
                question: {
                  ...message.question,
                  answered: !event.payload.rejected,
                  rejected: Boolean(event.payload.rejected),
                  answers: event.payload.answers ?? message.question.answers
                }
              }
            : message
        );
        codeTaskMessagesRef.current = next;
        setCodeTaskMessages(next);
      }
      appendCodeTaskMessage({
        kind: "status",
        title: event.payload.rejected ? "已拒绝选择" : "已提交选择",
        text: event.payload.message || (event.payload.rejected ? "已拒绝 OpenCode 的选择请求。" : "OpenCode 已收到选择。")
      });
      setCodeTaskRunning(true);
      return;
    }
    if (event.event === "tool") {
      appendCodeTaskMessage({
        kind: "tool",
        title: event.payload.name || "工具",
        text: event.payload.message || `工具状态：${event.payload.status || "running"}`,
        detail: event.payload.detail
      });
      return;
    }
    if (event.event === "command") {
      appendCodeTaskMessage({
        kind: "command",
        title: event.payload.status === "started" ? "命令开始" : "命令完成",
        text: event.payload.message || event.payload.command || "命令事件",
        detail: event.payload.output
      });
      return;
    }
    if (event.event === "file") {
      appendCodeTaskMessage({
        kind: "file",
        title: "文件",
        text: event.payload.message || event.payload.path || "文件变更"
      });
      return;
    }
    if (event.event === "permission") {
      appendCodeTaskMessage({
        kind: "permission",
        title: event.payload.approved ? "权限已批准" : "权限请求",
        text: event.payload.message || event.payload.action || "OpenCode 请求权限",
        detail: Array.isArray(event.payload.resources) ? event.payload.resources.map(String).join("\n") : undefined
      });
      return;
    }
    if (event.event === "diff") {
      appendCodeTaskMessage({
        kind: "file",
        title: "Diff",
        text: event.payload.message || "OpenCode 生成了文件差异。",
        detail: event.payload.diff ? JSON.stringify(event.payload.diff, null, 2) : undefined
      });
      return;
    }
    if (event.event === "log") {
      appendCodeTaskMessage({
        kind: "status",
        title: event.payload.kind || "日志",
        text: event.payload.message || ""
      });
      return;
    }
    if (event.event === "error") {
      setCodeTaskStatus("error");
      setCodeTaskRunning(false);
      appendCodeTaskMessage({ kind: "error", title: "错误", text: event.payload.message });
      return;
    }
    if (event.event === "done") {
      codeTaskCompletedRef.current = true;
      codeTaskFinalStatusRef.current = event.payload.status || "done";
      setCodeTaskRunning(false);
      if (taskId && event.payload.sessionId) {
        updateCodeTaskRecord(taskId, (task) => ({
          ...task,
          sessionId: event.payload.sessionId,
          workspacePath: event.payload.workspacePath || task.workspacePath,
          updatedAt: new Date().toISOString()
        }));
      }
      setCodeTaskStatus(event.payload.status || "done");
      appendCodeTaskMessage({
        kind: "done",
        title: "完成",
        text: event.payload.message || "OpenCode 任务已结束。"
      });
    }
  }

  async function answerCodeTaskQuestion(message: CodeTaskMessage, answers: string[][], reject = false) {
    const question = message.question;
    if (!question) {
      return;
    }
    const busyId = question.requestId || message.id;
    if (codeTaskQuestionBusyIds.includes(busyId)) {
      return;
    }
    const serverUrl = codeTaskServerUrlRef.current || question.serverUrl;
    const sessionId = question.sessionId || activeCodeTask?.sessionId || "";
    if (!serverUrl || !sessionId || !question.requestId) {
      appendCodeTaskMessage({
        kind: "error",
        title: "选择提交失败",
        text: "OpenCode question 缺少 serverUrl、sessionId 或 requestId，无法提交选择。"
      });
      return;
    }
    setCodeTaskQuestionBusyIds((prev) => (prev.includes(busyId) ? prev : [...prev, busyId]));
    try {
      await replyCodeTaskQuestion({
        serverUrl,
        sessionId,
        requestId: question.requestId,
        answers,
        v2: question.v2 ?? true,
        reject
      });
      updateCodeTaskMessage(message.id, (current) => ({
        ...current,
        question: current.question
          ? {
              ...current.question,
              sessionId,
              serverUrl,
              answered: !reject,
              rejected: reject,
              answers: reject ? current.question.answers : answers
            }
          : current.question
      }));
      setCodeTaskStatus("running");
      appendCodeTaskMessage({
        kind: "status",
        title: reject ? "已拒绝选择" : "已提交选择",
        text: reject
          ? "已拒绝 OpenCode 的选择请求。"
          : `已提交选择：${answers.map((items) => items.join(", ")).filter(Boolean).join("；") || "已回答"}`
      });
    } catch (error) {
      appendCodeTaskMessage({
        kind: "error",
        title: "选择提交失败",
        text: error instanceof Error ? error.message : "OpenCode question reply failed"
      });
    } finally {
      setCodeTaskQuestionBusyIds((prev) => prev.filter((item) => item !== busyId));
    }
  }

  function latestCodeTaskTurnMessages(messages: CodeTaskMessage[]): CodeTaskMessage[] {
    const lastUserIndex = messages.reduce(
      (latestIndex, message, index) => (message.kind === "user" ? index : latestIndex),
      -1
    );
    return lastUserIndex >= 0 ? messages.slice(lastUserIndex) : messages;
  }

  async function finalizeCodeTaskSummary(taskId: string, finalStatus: string) {
    flushCodeTaskUpdates();
    const messages = codeTaskMessagesRef.current;
    const turnMessages = latestCodeTaskTurnMessages(messages);
    const turnPrompt = turnMessages.find((message) => message.kind === "user")?.text ?? "";
    const task =
      codeTasks.find((item) => item.id === taskId) ??
      ({
        id: taskId,
        title: buildConversationTitle(turnPrompt || "OpenCode 任务"),
        prompt: turnPrompt,
        workspacePath: codeTaskWorkspace,
        status: finalStatus,
        messages: turnMessages,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString()
      } satisfies CodeTaskRecord);
    const summaryTask = {
      ...task,
      prompt: turnPrompt || task.prompt,
      status: finalStatus || task.status,
      messages: turnMessages
    };
    const summary = buildCodeTaskSummaryText(summaryTask, turnMessages);
    const voiceSummarySource = buildCodeTaskVoiceSummarySource(summaryTask, turnMessages);
    const createdAt = new Date().toISOString();
    const summaryMessage: CodeTaskMessage = {
      id: createId(),
      kind: "done",
      title: "任务总结",
      text: summary,
      createdAt
    };
    const nextMessages = trimCodeTaskMessages([...messages, summaryMessage]);
    codeTaskMessagesRef.current = nextMessages;
    setCodeTaskMessages(nextMessages);
    updateCodeTaskRecord(taskId, (current) => ({
      ...current,
      status: finalStatus || current.status,
      messages: nextMessages,
      summary,
      updatedAt: createdAt
    }));

    try {
      const audioUrls: string[] = [];
      const voiceErrors: string[] = [];
      let speechText = "";
      await streamTaskSummaryVoice({
        summary: voiceSummarySource,
        model: modelSettings,
        voice: voiceSettings,
        onEvent: (event) => {
          if (event.event === "audio") {
            audioUrls.push(event.payload.url);
            enqueueAudio({
              url: event.payload.url,
              displayText: event.payload.text,
              index: event.payload.index
            });
            return;
          }
          if (event.event === "voice_error") {
            voiceErrors.push(event.payload.message);
            return;
          }
          if (event.event === "error") {
            voiceErrors.push(event.payload.message);
            return;
          }
          if (event.event === "done") {
            speechText = event.payload.speechText;
          }
        }
      });
      if (audioUrls.length === 0 && voiceErrors.length > 0) {
        throw new Error(voiceErrors[0]);
      }
      updateCodeTaskRecord(taskId, (current) => ({
        ...current,
        summarySpeechText: speechText || current.summarySpeechText,
        summaryAudioUrl: audioUrls[0] || current.summaryAudioUrl,
        updatedAt: new Date().toISOString()
      }));
    } catch (error) {
      appendCodeTaskMessage({
        kind: "status",
        title: "语音",
        text: `任务总结语音生成失败：${error instanceof Error ? error.message : "voice error"}`
      });
    }
  }

  async function sendCodeTask(event?: FormEvent) {
    event?.preventDefault();
    const prompt = codeTaskInput.trim();
    if (!prompt || codeTaskRunning) {
      return;
    }

    const existingTask = activeCodeTaskId ? codeTasks.find((task) => task.id === activeCodeTaskId) ?? null : null;
    const shouldCreateTask = !existingTask || codeTaskMessages.length === 0;
    const taskId = shouldCreateTask ? createId() : existingTask.id;
    const title = shouldCreateTask ? buildConversationTitle(prompt) : existingTask.title;
    const sessionId = shouldCreateTask ? undefined : existingTask.sessionId;
    const workspacePath = codeTaskWorkspace.trim();
    const now = new Date().toISOString();

    codeTaskAbortRef.current?.abort();
    clearPendingCodeTaskFlush();
    codeTaskAssistantMessageIdRef.current = null;
    codeTaskCompletedRef.current = false;
    codeTaskFinalStatusRef.current = "";
    codeTaskServerUrlRef.current = "";
    setCodeTaskQuestionBusyIds([]);
    const controller = new AbortController();
    codeTaskAbortRef.current = controller;
    if (shouldCreateTask) {
      const newTask: CodeTaskRecord = {
        id: taskId,
        title,
        prompt,
        workspacePath,
        status: "connecting",
        messages: [],
        createdAt: now,
        updatedAt: now
      };
      setCodeTasks((prev) => [newTask, ...prev].slice(0, CODE_TASK_MAX_HISTORY));
      setCodeTaskMessages([]);
      codeTaskMessagesRef.current = [];
    } else {
      updateCodeTaskRecord(taskId, (task) => ({
        ...task,
        workspacePath,
        status: "connecting",
        updatedAt: now
      }));
    }
    setActiveCodeTaskId(taskId);
    setCodeTaskRunning(true);
    setCodeTaskStatus("connecting");
    openTaskPanel();
    appendCodeTaskMessage({
      kind: "user",
      title: "任务",
      text: prompt,
      detail: codeTaskWorkspace.trim() || "当前后端工作区"
    });
    setCodeTaskInput("");

    try {
      await streamCodeTask({
        taskId,
        title,
        prompt,
        workspacePath,
        sessionId,
        autoApprove: codeTaskAutoApprove,
        timeoutSeconds: 1800,
        signal: controller.signal,
        onEvent: (streamEvent) => handleCodeTaskEventOnce(streamEvent, taskId)
      });
    } catch (error) {
      if (!controller.signal.aborted) {
        appendCodeTaskMessage({
          kind: "error",
          title: "错误",
          text: error instanceof Error ? error.message : "OpenCode task failed"
        });
        setCodeTaskStatus("error");
      }
    } finally {
      flushCodeTaskUpdates();
      setCodeTaskRunning(false);
      codeTaskAbortRef.current = null;
      codeTaskAssistantMessageIdRef.current = null;
      if (codeTaskCompletedRef.current && !controller.signal.aborted) {
        await finalizeCodeTaskSummary(taskId, codeTaskFinalStatusRef.current || "done");
      }
      codeTaskCompletedRef.current = false;
      codeTaskFinalStatusRef.current = "";
    }
  }

  function stopCodeTask() {
    codeTaskAbortRef.current?.abort();
    setCodeTaskRunning(false);
    setCodeTaskStatus("stopped");
    appendCodeTaskMessage({ kind: "status", title: "停止", text: "已停止等待 OpenCode 任务。" });
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

  function renderMessageBubble(message: ChatMessage, surface: "floating" | "panel") {
    return (
      <ChatMessageBubble
        key={message.id}
        message={message}
        surface={surface}
        voiceBusy={voiceBusy}
        onCopy={copyMessageText}
        onPlay={playAssistantMessage}
      />
    );
  }

  function renderTaskPanel() {
    return (
      <TaskPanel
        activeTask={activeCodeTask ?? undefined}
        workspace={codeTaskWorkspace}
        autoApprove={codeTaskAutoApprove}
        messagesCount={codeTaskMessages.length}
        messages={codeTaskMessages}
        phases={codeTaskPhases}
        messageRef={taskMessageRef}
        questionBusyIds={codeTaskQuestionBusyIds}
        input={codeTaskInput}
        running={codeTaskRunning}
        status={codeTaskStatus}
        canStart={canStartCodeTask}
        onWorkspaceChange={setCodeTaskWorkspace}
        onChooseFolder={chooseCodeTaskFolder}
        onAutoApproveChange={setCodeTaskAutoApprove}
        onAnswerQuestion={answerCodeTaskQuestion}
        onInputChange={setCodeTaskInput}
        onSend={sendCodeTask}
        onNewDraft={startNewCodeTaskDraft}
        onStop={stopCodeTask}
      />
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
          model={activeLive2DModel}
          transformLocked={live2dTransformLocked}
          speaking={speaking}
          onTransformChange={handleLive2DTransformChange}
          onReady={() => {
            setLive2dReady(true);
            setLive2dError("");
          }}
          onError={(message) => {
            setLive2dError(message);
          }}
        />
      </section>

      <TopBar
        modelSettings={modelSettings}
        providers={providers}
        selectedProvider={selectedProvider}
        mode={mode}
        onProviderChange={applyProvider}
        onModeChange={setMode}
      />

      <button
        className={`avatar-launcher ${menuOpen ? "is-active" : ""}`}
        onClick={toggleMenuFromAvatar}
        type="button"
        aria-label="打开侧边栏"
      >
        <UserRound size={22} />
      </button>

      {renderLive2DQuickControls()}

      <LeftDock
        open={menuOpen}
        storageOnline={storageOnline}
        storageError={storageError}
        conversations={conversations}
        filteredConversations={filteredConversations}
        currentConversationId={currentConversationId}
        chatStatus={status}
        streaming={isStreaming}
        historySearch={historySearch}
        visibleCodeTasks={visibleCodeTasks}
        activeCodeTaskId={activeCodeTaskId}
        codeTaskRunning={codeTaskRunning}
        codeTaskStatus={codeTaskStatus}
        onCollapse={collapsePrimaryPanel}
        onNewConversation={startNewConversation}
        onOpenTaskPanel={openTaskPanel}
        onOpenConfigPanel={openConfigPanel}
        onHistorySearchChange={setHistorySearch}
        onOpenConversation={openConversation}
        onExportConversation={exportConversationItem}
        onDeleteConversation={deleteConversationItem}
        onNewCodeTask={startNewCodeTaskDraft}
        onOpenCodeTask={openCodeTaskRecord}
        onExportCodeTask={exportCodeTaskRecord}
        onDeleteCodeTask={deleteCodeTaskRecord}
      />

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
            title="Live2D"
            icon={<FolderOpen size={16} />}
            open={openSections.live2d}
            onToggle={() => toggleSection("live2d")}
          >
            <div className="live2d-current">
              <strong title={activeLive2DModel.name}>{activeLive2DModel.name}</strong>
              <span>
                {live2DRuntimeLabel(activeLive2DModel)} · {activeLive2DModel.expressions.length} 表情 · {activeLive2DModel.motions.length} 动作
              </span>
            </div>
            <input
              className="live2d-folder-input"
              id={LIVE2D_IMPORT_INPUT_ID}
              ref={attachLive2DFolderInput}
              type="file"
              multiple
              disabled={live2dImportBusy}
              onChange={handleLive2DFolderSelection}
            />
            <div className="config-actions live2d-import-actions">
              <label
                className={`text-icon-button live2d-import-label ${live2dImportBusy ? "is-disabled" : ""}`}
                htmlFor={LIVE2D_IMPORT_INPUT_ID}
              >
                <FolderOpen size={16} />
                导入模型
              </label>
              <button
                className="text-icon-button"
                disabled={live2dImportBusy || activeLive2DModel.source === "default"}
                onClick={() => void switchLive2DModel(DEFAULT_LIVE2D_MODEL)}
                type="button"
              >
                <RotateCcw size={16} />
                默认
              </button>
            </div>
            {live2dImportStatus && <div className="live2d-import-status">{live2dImportStatus}</div>}
            {renderLive2DHistory()}
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
        <form className={`floating-composer glass-panel ${uploadedFiles.length > 0 ? "has-attachments" : ""}`} onSubmit={sendMessage}>
          {renderUploadPanel()}
          {renderUploadedFiles()}
          <button
            className={`round-tool upload-trigger ${uploadPanelOpen ? "is-active" : ""} ${uploadBusy ? "is-busy" : ""}`}
            disabled={uploadBusy}
            onClick={toggleUploadPanel}
            type="button"
            aria-label="上传文件"
            title="上传文件"
          >
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
            <span className="eyebrow">{rightPanelTab === "chat" ? "Session" : "Task"}</span>
            <h2>{rightPanelTab === "chat" ? currentConversation?.title ?? "Kurisu" : "OpenCode 任务"}</h2>
          </div>
          <div className="right-panel-tabs" aria-label="右侧面板">
            <button
              className={rightPanelTab === "chat" ? "is-active" : ""}
              onClick={() => setRightPanelTab("chat")}
              type="button"
            >
              <MessageCircle size={14} />
              对话
            </button>
            <button
              className={rightPanelTab === "tasks" ? "is-active" : ""}
              onClick={() => setRightPanelTab("tasks")}
              type="button"
            >
              <Bell size={14} />
              任务
            </button>
          </div>
          <div className={`status-pill ${rightPanelTab === "chat" ? (isStreaming ? "is-live" : "") : codeTaskRunning ? "is-live" : ""}`}>
            {rightPanelTab === "chat" ? status : codeTaskStatus}
          </div>
          <button className="icon-button" onClick={() => setChatOpen(false)} type="button" aria-label="收起对话栏">
            <ChevronRight size={18} />
          </button>
        </div>

        {rightPanelTab === "chat" ? (
          <>
            <div className="message-list" ref={chatMessageRef}>
              {messages.length === 0 && (
                <div className="empty-state">
                  <MessageCircle size={24} />
                  <span>待机</span>
                </div>
              )}
              {messages.map((message) => renderMessageBubble(message, "panel"))}
            </div>

            <form className={`composer ${uploadedFiles.length > 0 ? "has-attachments" : ""}`} onSubmit={sendMessage}>
              {renderUploadPanel()}
              {renderUploadedFiles()}
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
                <div className="composer-left-actions">
                  <button
                    className={`round-tool composer-upload upload-trigger ${uploadPanelOpen ? "is-active" : ""} ${uploadBusy ? "is-busy" : ""}`}
                    disabled={uploadBusy}
                    onClick={toggleUploadPanel}
                    type="button"
                    aria-label="上传文件"
                    title="上传文件"
                  >
                    <Plus size={18} />
                  </button>
                  <button
                    className={`text-icon-button ${voiceSettings.autoPlay ? "is-active" : ""}`}
                    onClick={toggleAutoVoice}
                    type="button"
                  >
                    {voiceSettings.autoPlay ? <Volume2 size={16} /> : <VolumeX size={16} />}
                    语音
                  </button>
                </div>
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
          </>
        ) : (
          renderTaskPanel()
        )}
      </aside>

      {!enteredApp && <BootLoader ready={loadingReady} onEnter={() => setEnteredApp(true)} />}
    </main>
  );
}

