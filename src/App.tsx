import { PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, CSSProperties, DragEvent as ReactDragEvent, FormEvent } from "react";
import {
  Bell,
  Camera,
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
  observeDesktop,
  publishCodeTaskMobileView,
  replyCodeTaskQuestion,
  saveSettings,
  subscribeAllCodeTaskEvents,
  subscribeCodeTaskEvents,
  streamCodeTask,
  streamChat,
  streamTaskSummaryVoice,
  syncCodeTaskHistory,
  synthesizeVoice,
  transcribeVoiceInput,
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
  DesktopAssistantSettings,
  ModelSettings,
  ProviderPreset,
  SpeechInputSettings,
  StoredSettings,
  StreamEvent,
  ChatAttachment,
  ToolTraceEvent,
  UploadedFileInfo,
  VoiceInputInfo,
  VisionSettings,
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
  normalizeAssistantVoice,
  normalizeDesktopAssistantSettings,
  normalizeLive2dEmotion,
  normalizeMessageContent,
  normalizeSpeechInputSettings,
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
  const [visionSettings, setVisionSettings] = useState<VisionSettings>(stored.vision);
  const [speechInputSettings, setSpeechInputSettings] = useState<SpeechInputSettings>(stored.speechInput);
  const [voiceSettings, setVoiceSettings] = useState<VoiceSettings>(stored.voice);
  const [desktopAssistantSettings, setDesktopAssistantSettings] = useState<DesktopAssistantSettings>(stored.desktopAssistant);
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
    vision: false,
    speechInput: false,
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
  const [screenCaptureBusy, setScreenCaptureBusy] = useState(false);
  const [voiceInputBusy, setVoiceInputBusy] = useState(false);
  const [voiceRecording, setVoiceRecording] = useState(false);
  const [voiceRecordingSeconds, setVoiceRecordingSeconds] = useState(0);
  const [voiceInputLevels, setVoiceInputLevels] = useState<number[]>(Array.from({ length: 18 }, () => 0.24));
  const [desktopAssistantMode, setDesktopAssistantMode] = useState(false);
  const [desktopAssistantStatus, setDesktopAssistantStatus] = useState("待机");
  const [desktopCameraAvailable, setDesktopCameraAvailable] = useState(false);
  const [desktopCameraReason, setDesktopCameraReason] = useState("尚未检测摄像头");
  const [desktopObserveBusy, setDesktopObserveBusy] = useState(false);
  const [live2dReady, setLive2dReady] = useState(false);
  const [live2dError, setLive2dError] = useState("");
  const [minimumBootElapsed, setMinimumBootElapsed] = useState(false);
  const [enteredApp, setEnteredApp] = useState(false);
  const live2dRef = useRef<Live2DStageHandle | null>(null);
  const activeLive2DModelRef = useRef<Live2DModelRecord>(DEFAULT_LIVE2D_MODEL);
  const abortRef = useRef<AbortController | null>(null);
  const isStreamingRef = useRef(false);
  const voiceInputBusyRef = useRef(false);
  const codeTaskAbortRef = useRef<AbortController | null>(null);
  const codeTaskAssistantMessageIdRef = useRef<string | null>(null);
  const codeTaskFlushTimerRef = useRef<number | null>(null);
  const pendingCodeTaskAssistantDeltaRef = useRef("");
  const pendingCodeTaskMessagesRef = useRef<Array<Omit<CodeTaskMessage, "id" | "createdAt">>>([]);
  const lastCodeTaskStatusMessageRef = useRef("");
  const codeTasksRef = useRef<CodeTaskRecord[]>(storedCodeTasks);
  const codeTaskMessagesRef = useRef<CodeTaskMessage[]>(storedCodeTasks[0]?.messages ?? []);
  const activeCodeTaskIdRef = useRef<string | null>(storedCodeTasks[0]?.id ?? null);
  const codeTaskStatusRef = useRef(storedCodeTasks[0]?.status ?? "idle");
  const codeTaskWorkspaceRef = useRef(storedCodeTasks[0]?.workspacePath ?? DEFAULT_CODE_TASK_WORKSPACE);
  const codeTaskRunningRef = useRef(false);
  const codeTaskCompletedRef = useRef(false);
  const codeTaskFinalStatusRef = useRef("");
  const codeTaskServerUrlRef = useRef("");
  const codeTaskPersistTimerRef = useRef<number | null>(null);
  const codeTaskHistorySyncTimerRef = useRef<number | null>(null);
  const mobileTaskViewTimerRef = useRef<number | null>(null);
  const mobileTaskViewFrameRef = useRef<number | null>(null);
  const pendingMobileTaskViewPublishRef = useRef(false);
  const lastMobileTaskViewHashRef = useRef("");
  const handledCodeTaskEventKeysRef = useRef<Set<string>>(new Set());
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioQueueRef = useRef<AudioQueueItem[]>([]);
  const audioPlayingRef = useRef(false);
  const floatingComposerInputRef = useRef<HTMLTextAreaElement | null>(null);
  const panelComposerInputRef = useRef<HTMLTextAreaElement | null>(null);
  const lipSyncAudioContextRef = useRef<AudioContext | null>(null);
  const lipSyncAnimationFrameRef = useRef<number | null>(null);
  const lipSyncTokenRef = useRef(0);
  const lipSyncLevelRef = useRef(0);
  const textRevealFrameRef = useRef<number | null>(null);
  const textRevealTokenRef = useRef(0);
  const floatingMessageRef = useRef<HTMLDivElement | null>(null);
  const chatMessageRef = useRef<HTMLDivElement | null>(null);
  const taskMessageRef = useRef<HTMLDivElement | null>(null);
  const saveSettingsTimerRef = useRef<number | null>(null);
  const voiceMediaRecorderRef = useRef<MediaRecorder | null>(null);
  const voiceMediaStreamRef = useRef<MediaStream | null>(null);
  const voiceAudioContextRef = useRef<AudioContext | null>(null);
  const voiceAnimationFrameRef = useRef<number | null>(null);
  const voiceDurationTimerRef = useRef<number | null>(null);
  const voiceChunksRef = useRef<Blob[]>([]);
  const voicePcmChunksRef = useRef<Float32Array[]>([]);
  const voicePcmProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const voicePcmSilentGainRef = useRef<GainNode | null>(null);
  const voicePcmSampleRateRef = useRef(48000);
  const voiceRecordingStartedAtRef = useRef(0);
  const voiceWaveformSamplesRef = useRef<number[]>([]);
  const desktopVadStreamRef = useRef<MediaStream | null>(null);
  const desktopVadAudioContextRef = useRef<AudioContext | null>(null);
  const desktopVadProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const desktopVadSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const desktopVadSilentGainRef = useRef<GainNode | null>(null);
  const desktopVadPreRollRef = useRef<Float32Array[]>([]);
  const desktopVadSpeechChunksRef = useRef<Float32Array[]>([]);
  const desktopVadWaveformRef = useRef<number[]>([]);
  const desktopVadRecordingRef = useRef(false);
  const desktopVadSpeechStartedAtRef = useRef(0);
  const desktopVadLastVoiceAtRef = useRef(0);
  const desktopVadNoiseFloorRef = useRef(0.012);
  const desktopVadCooldownUntilRef = useRef(0);
  const desktopAssistantModeRef = useRef(false);
  const desktopAutoScreenshotTimerRef = useRef<number | null>(null);
  const desktopDragRef = useRef<{ x: number; y: number } | null>(null);
  const desktopObservationBusyRef = useRef(false);

  const selectedProvider = providers.find((provider) => provider.name === modelSettings.providerName);
  const desktopAssistantAvailable = Boolean(window.amadeusDesktop?.setDesktopAssistantMode);
  const canSend = (input.trim().length > 0 || uploadedFiles.length > 0) && !isStreaming && !voiceRecording && !voiceInputBusy;
  const canStartCodeTask = codeTaskInput.trim().length > 0 && !codeTaskRunning;
  const visibleMessages = messages
    .filter((message) => message.content || message.streaming || (message.attachments?.length ?? 0) > 0)
    .slice(-8);
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

  function speechInputSettingsForPersistence(): SpeechInputSettings {
    if (speechInputSettings.providerName === DEFAULT_SPEECH_INPUT_SETTINGS.providerName && modelSettings.apiKey) {
      return { ...speechInputSettings, apiKey: "" };
    }
    return speechInputSettings;
  }

  function speechInputSettingsForRequest(): SpeechInputSettings {
    const settings = normalizeSpeechInputSettings(speechInputSettingsForPersistence());
    if (settings.providerName === DEFAULT_SPEECH_INPUT_SETTINGS.providerName) {
      return { ...settings, apiKey: modelSettings.apiKey || settings.apiKey };
    }
    return settings;
  }

  function resizeComposerTextarea(element: HTMLTextAreaElement | null) {
    if (!element) {
      return;
    }
    element.style.height = "auto";
    const style = window.getComputedStyle(element);
    const minHeight = Number.parseFloat(style.minHeight) || 0;
    const maxHeight = Number.parseFloat(style.maxHeight) || element.scrollHeight;
    const nextHeight = Math.max(minHeight, Math.min(element.scrollHeight, maxHeight));
    element.style.height = `${nextHeight}px`;
    element.style.overflowY = element.scrollHeight > maxHeight ? "auto" : "hidden";
  }

  function updateComposerInput(event: ChangeEvent<HTMLTextAreaElement>) {
    setInput(event.target.value);
    resizeComposerTextarea(event.currentTarget);
  }

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
          setVisionSettings((prev) => ({ ...prev, ...(remoteSettings.vision ?? {}) }));
          const normalizedSpeechInput = normalizeSpeechInputSettings(remoteSettings.speechInput);
          setSpeechInputSettings(() => ({
            ...normalizedSpeechInput,
            apiKey:
              normalizedSpeechInput.providerName === DEFAULT_SPEECH_INPUT_SETTINGS.providerName &&
              remoteSettings.model?.apiKey
                ? ""
                : normalizedSpeechInput.apiKey
          }));
          setVoiceSettings((prev) => ({ ...prev, ...remoteSettings.voice }));
          setDesktopAssistantSettings((prev) =>
            normalizeDesktopAssistantSettings({ ...prev, ...(remoteSettings.desktopAssistant ?? {}) })
          );
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
        vision: visionSettings,
        speechInput: speechInputSettingsForPersistence(),
        voice: voiceSettings,
        desktopAssistant: desktopAssistantSettings,
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
        vision: visionSettings,
        speechInput: speechInputSettingsForPersistence(),
        voice: voiceSettings,
        desktopAssistant: desktopAssistantSettings,
        mode
      }).catch((error) => {
        setStorageError(error instanceof Error ? error.message : "settings save failed");
      });
    }, 350);
  }, [modelSettings, visionSettings, speechInputSettings, voiceSettings, desktopAssistantSettings, mode, storageOnline]);

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
    isStreamingRef.current = isStreaming;
  }, [isStreaming]);

  useEffect(() => {
    voiceInputBusyRef.current = voiceInputBusy;
  }, [voiceInputBusy]);

  useEffect(() => {
    desktopAssistantModeRef.current = desktopAssistantMode;
  }, [desktopAssistantMode]);

  useEffect(() => {
    if (!desktopAssistantMode) {
      stopDesktopAssistantListening();
      return;
    }
    void startDesktopAssistantListening();
    return () => {
      stopDesktopAssistantListening();
    };
  }, [desktopAssistantMode, speechInputSettings.enabled]);

  useEffect(() => {
    if (desktopAutoScreenshotTimerRef.current !== null) {
      window.clearInterval(desktopAutoScreenshotTimerRef.current);
      desktopAutoScreenshotTimerRef.current = null;
    }
    if (!desktopAssistantMode || !desktopAssistantSettings.autoScreenshotEnabled) {
      return;
    }
    const intervalMs = clamp(desktopAssistantSettings.screenshotIntervalSeconds, 5, 120) * 1000;
    desktopAutoScreenshotTimerRef.current = window.setInterval(() => {
      void runDesktopAutoObservation();
    }, intervalMs);
    return () => {
      if (desktopAutoScreenshotTimerRef.current !== null) {
        window.clearInterval(desktopAutoScreenshotTimerRef.current);
        desktopAutoScreenshotTimerRef.current = null;
      }
    };
  }, [
    desktopAssistantMode,
    desktopAssistantSettings.autoScreenshotEnabled,
    desktopAssistantSettings.screenshotIntervalSeconds,
    currentConversationId,
    modelSettings,
    visionSettings,
    voiceSettings
  ]);

  useEffect(() => {
    if (
      speechInputSettings.providerName === DEFAULT_SPEECH_INPUT_SETTINGS.providerName &&
      modelSettings.apiKey &&
      speechInputSettings.apiKey
    ) {
      setSpeechInputSettings((prev) => ({ ...prev, apiKey: "" }));
    }
  }, [
    modelSettings.apiKey,
    speechInputSettings.apiKey,
    speechInputSettings.providerName
  ]);

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
    activeCodeTaskIdRef.current = activeCodeTaskId;
  }, [activeCodeTaskId]);

  useEffect(() => {
    codeTaskStatusRef.current = codeTaskStatus;
  }, [codeTaskStatus]);

  useEffect(() => {
    codeTaskWorkspaceRef.current = codeTaskWorkspace;
  }, [codeTaskWorkspace]);

  useEffect(() => {
    codeTaskRunningRef.current = codeTaskRunning;
  }, [codeTaskRunning]);

  useEffect(() => {
    return subscribeAllCodeTaskEvents({
      replay: false,
      onEvent: (event) => {
        const taskId = getCodeTaskEventTaskId(event);
        if (!taskId) {
          return;
        }
        if (!ensureIncomingCodeTaskVisible(taskId, event)) {
          return;
        }
        handleCodeTaskEventOnce(event, taskId);
      },
      onError: (message) => {
        if (rightPanelTab === "tasks") {
          setCodeTaskStatus(message);
        }
      }
    });
  }, [activeCodeTaskId, rightPanelTab, activeCodeTask?.sessionId]);

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
    if (!activeCodeTaskId) {
      return;
    }
    pendingMobileTaskViewPublishRef.current = true;
    if (!codeTaskRunning || codeTaskStatus === "waiting_input" || codeTaskStatus === "error") {
      scheduleMobileTaskViewPublish(120);
    }
  }, [activeCodeTaskId, codeTaskMessages, codeTaskStatus, codeTaskWorkspace, codeTaskRunning]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (pendingMobileTaskViewPublishRef.current) {
        publishMobileTaskViewSnapshot();
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

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
      if (mobileTaskViewTimerRef.current !== null) {
        window.clearTimeout(mobileTaskViewTimerRef.current);
      }
      if (mobileTaskViewFrameRef.current !== null) {
        window.cancelAnimationFrame(mobileTaskViewFrameRef.current);
      }
      stopAudioPlayback();
      cleanupVoiceRecordingResources();
      stopDesktopAssistantListening();
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
    resizeComposerTextarea(floatingComposerInputRef.current);
    resizeComposerTextarea(panelComposerInputRef.current);
  }, [input, chatOpen, voiceRecording]);

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

  function desktopAssistantConversationTitle(): string {
    const value = new Date().toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    });
    return `桌面助手 ${value}`;
  }

  async function createDesktopAssistantConversation(): Promise<string | null> {
    if (!storageOnline) {
      setMessages([]);
      setCurrentConversationId(null);
      return null;
    }
    try {
      const conversation = await createConversation({
        title: desktopAssistantConversationTitle(),
        personaId: PERSONA_ID,
        mode
      });
      setCurrentConversationId(conversation.id);
      setConversations((prev) => [
        conversation,
        ...prev.filter((item) => item.id !== conversation.id)
      ]);
      setMessages([]);
      return conversation.id;
    } catch (error) {
      setStorageError(error instanceof Error ? error.message : "desktop conversation create failed");
      return currentConversationId;
    }
  }

  async function refreshDesktopCameraAvailability() {
    const apiAvailable = await window.amadeusDesktop?.getCameraAvailability?.().catch((error) => ({
      available: false,
      reason: error instanceof Error ? error.message : "摄像头检测失败"
    }));
    if (apiAvailable && !apiAvailable.available) {
      setDesktopCameraAvailable(false);
      setDesktopCameraReason(apiAvailable.reason || "摄像头不可用");
      setDesktopAssistantSettings((prev) => ({ ...prev, cameraEnabled: false }));
      return;
    }
    if (!navigator.mediaDevices?.enumerateDevices) {
      setDesktopCameraAvailable(false);
      setDesktopCameraReason("当前环境不支持摄像头检测");
      setDesktopAssistantSettings((prev) => ({ ...prev, cameraEnabled: false }));
      return;
    }
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const hasCamera = devices.some((device) => device.kind === "videoinput");
      setDesktopCameraAvailable(hasCamera);
      setDesktopCameraReason(hasCamera ? "" : "未检测到摄像头");
      if (!hasCamera) {
        setDesktopAssistantSettings((prev) => ({ ...prev, cameraEnabled: false }));
      }
    } catch (error) {
      setDesktopCameraAvailable(false);
      setDesktopCameraReason(error instanceof Error ? error.message : "摄像头权限不可用");
      setDesktopAssistantSettings((prev) => ({ ...prev, cameraEnabled: false }));
    }
  }

  async function toggleDesktopCamera() {
    if (!desktopCameraAvailable) {
      return;
    }
    if (desktopAssistantSettings.cameraEnabled) {
      setDesktopAssistantSettings((prev) => ({ ...prev, cameraEnabled: false }));
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      stream.getTracks().forEach((track) => track.stop());
      setDesktopAssistantSettings((prev) => ({ ...prev, cameraEnabled: true }));
      setDesktopCameraAvailable(true);
      setDesktopCameraReason("");
    } catch (error) {
      setDesktopCameraAvailable(false);
      setDesktopCameraReason(error instanceof Error ? error.message : "摄像头权限被拒绝");
      setDesktopAssistantSettings((prev) => ({ ...prev, cameraEnabled: false }));
    }
  }

  async function enterDesktopAssistantMode() {
    if (!desktopAssistantAvailable) {
      setStatus("desktop only");
      return;
    }
    stopAudioPlayback();
    abortRef.current?.abort();
    setMenuOpen(false);
    setConfigOpen(false);
    setChatOpen(false);
    setUploadedFiles([]);
    setUploadPanelOpen(false);
    setDesktopAssistantSettings((prev) => ({
      ...prev,
      voiceOutputEnabled: prev.voiceOutputEnabled || voiceSettings.autoPlay
    }));
    const result = await window.amadeusDesktop?.setDesktopAssistantMode?.(true).catch((error) => ({
      ok: false,
      reason: error instanceof Error ? error.message : "桌面助手窗口切换失败"
    }));
    if (result && !result.ok) {
      setStatus(result.reason || "desktop assistant failed");
      return;
    }
    await createDesktopAssistantConversation();
    setDesktopAssistantMode(true);
    setDesktopAssistantStatus("监听中");
    void refreshDesktopCameraAvailability();
  }

  async function exitDesktopAssistantMode() {
    setDesktopAssistantMode(false);
    setDesktopAssistantStatus("待机");
    stopDesktopAssistantListening();
    if (desktopAutoScreenshotTimerRef.current !== null) {
      window.clearInterval(desktopAutoScreenshotTimerRef.current);
      desktopAutoScreenshotTimerRef.current = null;
    }
    await window.amadeusDesktop?.setDesktopAssistantMode?.(false).catch(() => undefined);
  }

  function startDesktopWindowDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (!desktopAssistantMode || !window.amadeusDesktop?.moveAssistantWindow) {
      return;
    }
    desktopDragRef.current = { x: event.clientX, y: event.clientY };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveDesktopWindowDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (!desktopAssistantMode || !desktopDragRef.current || !window.amadeusDesktop?.moveAssistantWindow) {
      return;
    }
    const dx = event.clientX - desktopDragRef.current.x;
    const dy = event.clientY - desktopDragRef.current.y;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) {
      return;
    }
    desktopDragRef.current = { x: event.clientX, y: event.clientY };
    void window.amadeusDesktop.moveAssistantWindow({ dx, dy });
  }

  function stopDesktopWindowDrag() {
    desktopDragRef.current = null;
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

  function assistantVoiceAudioUrls(message: ChatMessage): string[] {
    const voice = normalizeAssistantVoice(message.assistantVoice);
    return voice?.audioUrls ?? [];
  }

  function setAssistantVoice(id: string, value: unknown) {
    const voice = normalizeAssistantVoice(value);
    if (!voice) {
      return;
    }
    setMessages((prev) =>
      prev.map((message) => (message.id === id ? { ...message, assistantVoice: voice } : message))
    );
  }

  function appendAssistantVoiceAudio(id: string, url: string, index = 0) {
    if (!url) {
      return;
    }
    setMessages((prev) =>
      prev.map((message) => {
        if (message.id !== id) {
          return message;
        }
        const current = normalizeAssistantVoice(message.assistantVoice);
        const segmentMap = new Map<number, string>();
        (current?.segments ?? current?.audioUrls.map((item, itemIndex) => ({ index: itemIndex, url: item })) ?? [])
          .forEach((segment) => segmentMap.set(segment.index, segment.url));
        segmentMap.set(Number.isFinite(index) ? index : segmentMap.size, url);
        const segments = Array.from(segmentMap.entries())
          .map(([segmentIndex, segmentUrl]) => ({ index: segmentIndex, url: segmentUrl }))
          .filter((segment) => segment.url)
          .sort((a, b) => a.index - b.index);
        return {
          ...message,
          assistantVoice: {
            audioUrls: segments.map((segment) => segment.url),
            segments
          }
        };
      })
    );
  }

  function playAssistantVoiceUrls(urls: string[]) {
    const playableUrls = urls.map((url) => url.trim()).filter(Boolean);
    if (playableUrls.length === 0) {
      return;
    }
    stopAudioPlayback();
    playableUrls.forEach((url, index) => enqueueAudio({ url, index }));
    live2dRef.current?.playEmotion("smile1");
  }

  function stopLive2DLipSync() {
    lipSyncTokenRef.current += 1;
    lipSyncLevelRef.current = 0;
    if (lipSyncAnimationFrameRef.current !== null) {
      window.cancelAnimationFrame(lipSyncAnimationFrameRef.current);
      lipSyncAnimationFrameRef.current = null;
    }
    live2dRef.current?.resetMouth();
    const context = lipSyncAudioContextRef.current;
    lipSyncAudioContextRef.current = null;
    if (context && context.state !== "closed") {
      void context.close().catch(() => undefined);
    }
  }

  function startLive2DLipSync(audio: HTMLAudioElement): () => void {
    stopLive2DLipSync();
    const token = lipSyncTokenRef.current;
    let analyserStarted = false;

    const isCurrentAudio = () => token === lipSyncTokenRef.current && audioRef.current === audio && !audio.ended && !audio.paused;

    const startSyntheticLipSync = () => {
      if (!isCurrentAudio() || lipSyncAnimationFrameRef.current !== null) {
        return;
      }
      const startedAt = performance.now();
      const step = () => {
        if (!isCurrentAudio()) {
          lipSyncAnimationFrameRef.current = null;
          return;
        }
        const elapsed = (performance.now() - startedAt) / 1000;
        const duration = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0;
        const fadeIn = clamp((performance.now() - startedAt) / 180, 0, 1);
        const fadeOut = duration > 0 ? clamp((duration - audio.currentTime) / 0.22, 0, 1) : 1;
        const pulse = 0.38 + Math.sin(elapsed * 18) * 0.24 + Math.sin(elapsed * 31 + 0.8) * 0.18;
        const target = clamp(pulse * fadeIn * fadeOut, 0, 0.92);
        lipSyncLevelRef.current = lipSyncLevelRef.current * 0.5 + target * 0.5;
        live2dRef.current?.setMouthOpen(lipSyncLevelRef.current);
        lipSyncAnimationFrameRef.current = window.requestAnimationFrame(step);
      };
      step();
    };

    const startAnalyserLipSync = async () => {
      if (analyserStarted) {
        return;
      }
      analyserStarted = true;
      const AudioContextCtor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AudioContextCtor) {
        startSyntheticLipSync();
        return;
      }

      const context = new AudioContextCtor();
      lipSyncAudioContextRef.current = context;
      try {
        if (context.state !== "running") {
          await Promise.race([
            context.resume().catch(() => undefined),
            new Promise((resolve) => window.setTimeout(resolve, 140))
          ]);
        }
        if (token !== lipSyncTokenRef.current || audioRef.current !== audio) {
          if (context.state !== "closed") {
            void context.close().catch(() => undefined);
          }
          return;
        }
        if (context.state !== "running") {
          lipSyncAudioContextRef.current = null;
          if (context.state !== "closed") {
            void context.close().catch(() => undefined);
          }
          startSyntheticLipSync();
          return;
        }

        const source = context.createMediaElementSource(audio);
        const analyser = context.createAnalyser();
        analyser.fftSize = 256;
        source.connect(analyser);
        analyser.connect(context.destination);
        const data = new Uint8Array(analyser.fftSize);

        const step = () => {
          if (!isCurrentAudio()) {
            lipSyncAnimationFrameRef.current = null;
            return;
          }
          analyser.getByteTimeDomainData(data);
          let total = 0;
          for (let index = 0; index < data.length; index += 1) {
            const sample = (data[index] - 128) / 128;
            total += sample * sample;
          }
          const rms = Math.sqrt(total / data.length);
          const target = clamp((rms - 0.012) * 8.2, 0, 1);
          lipSyncLevelRef.current = lipSyncLevelRef.current * 0.58 + target * 0.42;
          live2dRef.current?.setMouthOpen(lipSyncLevelRef.current);
          lipSyncAnimationFrameRef.current = window.requestAnimationFrame(step);
        };
        step();
      } catch {
        if (lipSyncAudioContextRef.current === context) {
          lipSyncAudioContextRef.current = null;
        }
        if (context.state !== "closed") {
          void context.close().catch(() => undefined);
        }
        startSyntheticLipSync();
      }
    };

    const handlePlaying = () => {
      void startAnalyserLipSync();
    };
    audio.addEventListener("playing", handlePlaying, { once: true });
    if (!audio.paused && !audio.ended) {
      handlePlaying();
    }

    return () => {
      audio.removeEventListener("playing", handlePlaying);
      stopLive2DLipSync();
    };
  }

  function stopAudioPlayback() {
    audioQueueRef.current = [];
    audioPlayingRef.current = false;
    cancelSyncedTextReveal();
    stopLive2DLipSync();
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
    let finishLipSync: (() => void) | null = null;
    let finished = false;

    const finish = () => {
      if (finished) {
        return;
      }
      finished = true;
      finishSyncedText?.();
      finishLipSync?.();
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
    finishLipSync = startLive2DLipSync(audio);
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

  function toChatAttachments(files: UploadedFileInfo[]): ChatAttachment[] {
    return files.map((file) => ({
      path: file.path,
      originalFilename: file.originalFilename,
      filename: file.filename,
      mediaType: file.mediaType,
      contentType: file.contentType
    }));
  }

  function countImageAttachments(files: UploadedFileInfo[]): number {
    return files.filter((file) => file.mediaType === "image" || file.visionReadable).length;
  }

  function dataUrlToFile(dataUrl: string, filename: string): File {
    const [meta, payload = ""] = dataUrl.split(",", 2);
    const contentType = /data:([^;]+);base64/i.exec(meta)?.[1] || "image/png";
    const binary = atob(payload);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return new File([bytes], filename, { type: contentType });
  }

  async function captureBrowserScreenshotFile(): Promise<File> {
    if (!navigator.mediaDevices?.getDisplayMedia) {
      throw new Error("当前环境不支持屏幕截图");
    }
    const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
    try {
      const video = document.createElement("video");
      video.srcObject = stream;
      video.muted = true;
      await video.play();
      await new Promise<void>((resolve) => {
        if (video.videoWidth > 0 && video.videoHeight > 0) {
          resolve();
          return;
        }
        video.onloadedmetadata = () => resolve();
      });
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth || 1280;
      canvas.height = video.videoHeight || 720;
      const context = canvas.getContext("2d");
      if (!context) {
        throw new Error("无法创建截图画布");
      }
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
      if (!blob) {
        throw new Error("截图生成失败");
      }
      return new File([blob], `screen-${Date.now()}.png`, { type: "image/png" });
    } finally {
      stream.getTracks().forEach((track) => track.stop());
    }
  }

  async function captureScreenshotFile(): Promise<File> {
    const desktopCapture = window.amadeusDesktop?.captureScreen;
    if (desktopCapture) {
      const result = await desktopCapture();
      if (!result?.dataUrl) {
        throw new Error("截图已取消");
      }
      return dataUrlToFile(result.dataUrl, result.filename || `screen-${Date.now()}.png`);
    }
    return await captureBrowserScreenshotFile();
  }

  async function captureScreenAttachment() {
    if (!visionSettings.screenCaptureEnabled || uploadBusy || screenCaptureBusy) {
      return;
    }
    setScreenCaptureBusy(true);
    setStatus("screenshot");
    try {
      const file = await captureScreenshotFile();
      await uploadFiles([file]);
      setStatus("screenshot attached");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "screenshot failed");
    } finally {
      setScreenCaptureBusy(false);
    }
  }

  async function captureScreenUploadedAttachment(): Promise<UploadedFileInfo | null> {
    setScreenCaptureBusy(true);
    try {
      const file = await captureScreenshotFile();
      const uploaded = await uploadFilesForAttachments([file], false);
      return uploaded[0] ?? null;
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "screenshot failed");
      return null;
    } finally {
      setScreenCaptureBusy(false);
    }
  }

  function shouldCaptureScreenForPrompt(text: string): boolean {
    const value = text.trim().toLowerCase();
    if (!value) {
      return false;
    }
    return [
      "看屏幕",
      "看看屏幕",
      "查看屏幕",
      "看一下屏幕",
      "看下屏幕",
      "屏幕上",
      "我现在在干什么",
      "我在干什么",
      "这个报错",
      "这个错误",
      "当前画面",
      "帮我看看",
      "看一下这个",
      "看下这个",
      "what am i doing",
      "look at my screen",
      "see my screen"
    ].some((keyword) => value.includes(keyword));
  }

  function cleanupVoiceRecordingResources() {
    if (voiceAnimationFrameRef.current !== null) {
      window.cancelAnimationFrame(voiceAnimationFrameRef.current);
      voiceAnimationFrameRef.current = null;
    }
    if (voiceDurationTimerRef.current !== null) {
      window.clearInterval(voiceDurationTimerRef.current);
      voiceDurationTimerRef.current = null;
    }
    voiceMediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    voiceMediaStreamRef.current = null;
    voicePcmProcessorRef.current?.disconnect();
    voicePcmProcessorRef.current = null;
    voicePcmSilentGainRef.current?.disconnect();
    voicePcmSilentGainRef.current = null;
    void voiceAudioContextRef.current?.close().catch(() => undefined);
    voiceAudioContextRef.current = null;
  }

  function preferredVoiceInputMimeType(): string {
    const candidates = [
      "audio/webm;codecs=opus",
      "audio/webm",
      "audio/ogg;codecs=opus",
      "audio/mp4"
    ];
    return candidates.find((type) => MediaRecorder.isTypeSupported(type)) ?? "";
  }

  function voiceInputFileExtension(mimeType: string): string {
    if (mimeType.includes("wav") || mimeType.includes("wave")) {
      return "wav";
    }
    if (mimeType.includes("ogg")) {
      return "ogg";
    }
    if (mimeType.includes("mp4")) {
      return "m4a";
    }
    return "webm";
  }

  function encodePcmChunksToWav(chunks: Float32Array[], sampleRate: number): Blob {
    const samplesCount = chunks.reduce((total, chunk) => total + chunk.length, 0);
    if (samplesCount === 0) {
      return new Blob([], { type: "audio/wav" });
    }
    const bytesPerSample = 2;
    const dataBytes = samplesCount * bytesPerSample;
    const buffer = new ArrayBuffer(44 + dataBytes);
    const view = new DataView(buffer);
    let offset = 0;

    const writeString = (value: string) => {
      for (let index = 0; index < value.length; index += 1) {
        view.setUint8(offset, value.charCodeAt(index));
        offset += 1;
      }
    };

    writeString("RIFF");
    view.setUint32(offset, 36 + dataBytes, true);
    offset += 4;
    writeString("WAVE");
    writeString("fmt ");
    view.setUint32(offset, 16, true);
    offset += 4;
    view.setUint16(offset, 1, true);
    offset += 2;
    view.setUint16(offset, 1, true);
    offset += 2;
    view.setUint32(offset, sampleRate, true);
    offset += 4;
    view.setUint32(offset, sampleRate * bytesPerSample, true);
    offset += 4;
    view.setUint16(offset, bytesPerSample, true);
    offset += 2;
    view.setUint16(offset, 16, true);
    offset += 2;
    writeString("data");
    view.setUint32(offset, dataBytes, true);
    offset += 4;

    for (const chunk of chunks) {
      for (let index = 0; index < chunk.length; index += 1) {
        const sample = clamp(chunk[index], -1, 1);
        view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
        offset += 2;
      }
    }

    return new Blob([buffer], { type: "audio/wav" });
  }

  function normalizeWaveformSamples(samples: number[], count = 18): number[] {
    if (samples.length === 0) {
      return Array.from({ length: count }, () => 0.28);
    }
    const bucketSize = Math.max(1, Math.ceil(samples.length / count));
    const bars: number[] = [];
    for (let index = 0; index < count; index += 1) {
      const bucket = samples.slice(index * bucketSize, (index + 1) * bucketSize);
      const value = bucket.length > 0 ? bucket.reduce((total, item) => total + item, 0) / bucket.length : 0.22;
      bars.push(clamp(value, 0.18, 1));
    }
    return bars;
  }

  function startVoiceLevelMonitor(stream: MediaStream) {
    const AudioContextCtor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioContextCtor) {
      return;
    }
    const context = new AudioContextCtor();
    void context.resume().catch(() => undefined);
    const analyser = context.createAnalyser();
    analyser.fftSize = 256;
    const source = context.createMediaStreamSource(stream);
    source.connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);
    voiceAudioContextRef.current = context;
    voicePcmSampleRateRef.current = context.sampleRate || 48000;
    if (typeof context.createScriptProcessor === "function") {
      const processor = context.createScriptProcessor(4096, 1, 1);
      const silentGain = context.createGain();
      silentGain.gain.value = 0;
      processor.onaudioprocess = (event) => {
        const input = event.inputBuffer.getChannelData(0);
        voicePcmChunksRef.current.push(new Float32Array(input));
      };
      source.connect(processor);
      processor.connect(silentGain);
      silentGain.connect(context.destination);
      voicePcmProcessorRef.current = processor;
      voicePcmSilentGainRef.current = silentGain;
    }

    const tick = () => {
      analyser.getByteFrequencyData(data);
      const average = data.reduce((total, value) => total + value, 0) / Math.max(1, data.length);
      const level = clamp(average / 120, 0.16, 1);
      voiceWaveformSamplesRef.current.push(level);
      setVoiceInputLevels((prev) => [...prev.slice(1), level]);
      voiceAnimationFrameRef.current = window.requestAnimationFrame(tick);
    };
    tick();
  }

  async function startVoiceInputRecording() {
    if (isStreaming || voiceInputBusy || voiceRecording) {
      return;
    }
    if (!speechInputSettings.enabled) {
      setStatus("voice input off");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setStatus("mic unsupported");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = preferredVoiceInputMimeType();
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      voiceMediaRecorderRef.current = recorder;
      voiceMediaStreamRef.current = stream;
      voiceChunksRef.current = [];
      voicePcmChunksRef.current = [];
      voicePcmSampleRateRef.current = 48000;
      voiceWaveformSamplesRef.current = [];
      voiceRecordingStartedAtRef.current = Date.now();
      setVoiceInputLevels(Array.from({ length: 18 }, () => 0.24));
      setVoiceRecordingSeconds(0);

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          voiceChunksRef.current.push(event.data);
        }
      };
      recorder.onstop = () => {
        const durationSeconds = Math.max(0.4, (Date.now() - voiceRecordingStartedAtRef.current) / 1000);
        const chunks = voiceChunksRef.current;
        const audioType = recorder.mimeType || mimeType || "audio/webm";
        const wavBlob = encodePcmChunksToWav(voicePcmChunksRef.current, voicePcmSampleRateRef.current);
        const blob = wavBlob.size > 44 ? wavBlob : new Blob(chunks, { type: audioType });
        const uploadType = wavBlob.size > 44 ? "audio/wav" : audioType;
        const waveform = normalizeWaveformSamples(voiceWaveformSamplesRef.current);
        cleanupVoiceRecordingResources();
        setVoiceRecording(false);
        setVoiceRecordingSeconds(0);
        voiceMediaRecorderRef.current = null;
        void processVoiceInputBlob(blob, uploadType, durationSeconds, waveform);
      };

      recorder.start();
      startVoiceLevelMonitor(stream);
      voiceDurationTimerRef.current = window.setInterval(() => {
        setVoiceRecordingSeconds((Date.now() - voiceRecordingStartedAtRef.current) / 1000);
      }, 150);
      setVoiceRecording(true);
      setStatus("listening");
    } catch (error) {
      cleanupVoiceRecordingResources();
      setVoiceRecording(false);
      setStatus(error instanceof Error ? error.message : "mic failed");
    }
  }

  function stopVoiceInputRecording() {
    const recorder = voiceMediaRecorderRef.current;
    if (!recorder || recorder.state === "inactive") {
      return;
    }
    setStatus("transcribing");
    recorder.stop();
  }

  function toggleVoiceInputRecording() {
    if (voiceRecording) {
      stopVoiceInputRecording();
      return;
    }
    void startVoiceInputRecording();
  }

  function desktopVadRms(samples: Float32Array): number {
    let total = 0;
    for (let index = 0; index < samples.length; index += 1) {
      total += samples[index] * samples[index];
    }
    return Math.sqrt(total / Math.max(1, samples.length));
  }

  function desktopVadChunkDuration(chunks: Float32Array[], sampleRate: number): number {
    const samples = chunks.reduce((total, chunk) => total + chunk.length, 0);
    return samples / Math.max(1, sampleRate);
  }

  function keepDesktopVadPreRoll(chunk: Float32Array, sampleRate: number) {
    desktopVadPreRollRef.current.push(chunk);
    while (desktopVadChunkDuration(desktopVadPreRollRef.current, sampleRate) > 0.45) {
      desktopVadPreRollRef.current.shift();
    }
  }

  function resetDesktopVadSegment() {
    desktopVadRecordingRef.current = false;
    desktopVadSpeechChunksRef.current = [];
    desktopVadWaveformRef.current = [];
    desktopVadSpeechStartedAtRef.current = 0;
    desktopVadLastVoiceAtRef.current = 0;
  }

  function finishDesktopVadSegment(sampleRate: number) {
    const chunks = desktopVadSpeechChunksRef.current.slice();
    const waveform = normalizeWaveformSamples(desktopVadWaveformRef.current);
    const durationSeconds = Math.max(0, desktopVadChunkDuration(chunks, sampleRate));
    resetDesktopVadSegment();
    if (durationSeconds < 0.35 || chunks.length === 0) {
      return;
    }
    desktopVadCooldownUntilRef.current = performance.now() + 900;
    setDesktopAssistantStatus("识别中");
    const blob = encodePcmChunksToWav(chunks, sampleRate);
    void processVoiceInputBlob(blob, "audio/wav", durationSeconds, waveform);
  }

  async function startDesktopAssistantListening() {
    if (desktopVadStreamRef.current || !desktopAssistantModeRef.current) {
      return;
    }
    if (!speechInputSettings.enabled) {
      setDesktopAssistantStatus("语音输入未开启");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setDesktopAssistantStatus("麦克风不可用");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const AudioContextCtor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AudioContextCtor) {
        throw new Error("当前环境不支持 Web Audio");
      }
      const context = new AudioContextCtor();
      await context.resume().catch(() => undefined);
      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(4096, 1, 1);
      const silentGain = context.createGain();
      silentGain.gain.value = 0;
      desktopVadStreamRef.current = stream;
      desktopVadAudioContextRef.current = context;
      desktopVadSourceRef.current = source;
      desktopVadProcessorRef.current = processor;
      desktopVadSilentGainRef.current = silentGain;
      desktopVadPreRollRef.current = [];
      resetDesktopVadSegment();

      processor.onaudioprocess = (event) => {
        if (!desktopAssistantModeRef.current) {
          return;
        }
        const input = event.inputBuffer.getChannelData(0);
        const chunk = new Float32Array(input);
        const sampleRate = context.sampleRate || 48000;
        const rms = desktopVadRms(chunk);
        const now = performance.now();
        const floor = desktopVadNoiseFloorRef.current;
        const threshold = Math.max(0.018, floor * 3.0);
        const voiceDetected = rms > threshold;
        const level = clamp(rms * 18, 0.16, 1);
        setVoiceInputLevels((prev) => [...prev.slice(1), level]);

        if (!desktopVadRecordingRef.current && !voiceDetected) {
          desktopVadNoiseFloorRef.current = floor * 0.96 + Math.min(rms, 0.05) * 0.04;
        }
        keepDesktopVadPreRoll(chunk, sampleRate);

        if (isStreamingRef.current || voiceInputBusyRef.current || now < desktopVadCooldownUntilRef.current) {
          return;
        }

        if (!desktopVadRecordingRef.current) {
          if (!voiceDetected) {
            setDesktopAssistantStatus("监听中");
            return;
          }
          desktopVadRecordingRef.current = true;
          desktopVadSpeechStartedAtRef.current = now - desktopVadChunkDuration(desktopVadPreRollRef.current, sampleRate) * 1000;
          desktopVadLastVoiceAtRef.current = now;
          desktopVadSpeechChunksRef.current = [...desktopVadPreRollRef.current, chunk];
          desktopVadWaveformRef.current = [level];
          setDesktopAssistantStatus("听到了");
          return;
        }

        desktopVadSpeechChunksRef.current.push(chunk);
        desktopVadWaveformRef.current.push(level);
        if (voiceDetected) {
          desktopVadLastVoiceAtRef.current = now;
        }
        const durationMs = now - desktopVadSpeechStartedAtRef.current;
        const silenceMs = now - desktopVadLastVoiceAtRef.current;
        if (silenceMs >= 900 || durationMs >= 25000) {
          finishDesktopVadSegment(sampleRate);
        }
      };

      source.connect(processor);
      processor.connect(silentGain);
      silentGain.connect(context.destination);
      setDesktopAssistantStatus("监听中");
    } catch (error) {
      stopDesktopAssistantListening();
      setDesktopAssistantStatus(error instanceof Error ? error.message : "麦克风启动失败");
    }
  }

  function stopDesktopAssistantListening() {
    desktopVadProcessorRef.current?.disconnect();
    desktopVadSourceRef.current?.disconnect();
    desktopVadSilentGainRef.current?.disconnect();
    desktopVadStreamRef.current?.getTracks().forEach((track) => track.stop());
    void desktopVadAudioContextRef.current?.close().catch(() => undefined);
    desktopVadProcessorRef.current = null;
    desktopVadSourceRef.current = null;
    desktopVadSilentGainRef.current = null;
    desktopVadStreamRef.current = null;
    desktopVadAudioContextRef.current = null;
    desktopVadPreRollRef.current = [];
    desktopVadCooldownUntilRef.current = 0;
    resetDesktopVadSegment();
  }

  async function processVoiceInputBlob(blob: Blob, mimeType: string, durationSeconds: number, waveform: number[]) {
    if (blob.size === 0) {
      setStatus("empty voice");
      return;
    }
    setVoiceInputBusy(true);
    try {
      const extension = voiceInputFileExtension(mimeType);
      const file = new File([blob], `user-voice-${Date.now()}.${extension}`, { type: mimeType || "audio/webm" });
      const result = await transcribeVoiceInput({
        file,
        durationSeconds,
        model: modelSettings,
        speechInput: speechInputSettingsForRequest()
      });
      const transcript = result.text.trim();
      const voiceInput: VoiceInputInfo = {
        audioUrl: result.audio.url,
        path: result.audio.path,
        filename: result.audio.filename,
        contentType: result.audio.contentType,
        durationSeconds: result.audio.durationSeconds || durationSeconds,
        transcript,
        waveform
      };
      if (!result.ok || !transcript) {
        const errorText = result.error || "语音输入转写失败";
        setStatus(errorText);
        await sendVoiceInputMessage(`语音输入转写失败：${errorText}。请不要猜测语音内容，提示用户检查语音输入模型配置或重试。`, voiceInput);
        return;
      }
      const desktopVoice = desktopAssistantMode
        ? { ...voiceSettings, autoPlay: desktopAssistantSettings.voiceOutputEnabled }
        : voiceSettings;
      let attachments: ChatAttachment[] = [];
      if (desktopAssistantMode && shouldCaptureScreenForPrompt(transcript)) {
        setDesktopAssistantStatus("查看屏幕");
        const screenshot = await captureScreenUploadedAttachment();
        attachments = screenshot ? toChatAttachments([screenshot]) : [];
      }
      setDesktopAssistantStatus("思考中");
      await sendVoiceInputMessage(transcript, voiceInput, { attachments, voice: desktopVoice });
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "voice input failed");
      setDesktopAssistantStatus(error instanceof Error ? error.message : "语音失败");
    } finally {
      setVoiceInputBusy(false);
    }
  }

  async function runDesktopAutoObservation() {
    if (
      !desktopAssistantModeRef.current ||
      desktopObservationBusyRef.current ||
      isStreamingRef.current ||
      voiceInputBusyRef.current
    ) {
      return;
    }
    desktopObservationBusyRef.current = true;
    setDesktopObserveBusy(true);
    setDesktopAssistantStatus("观察中");
    try {
      const conversationId = currentConversationId || (await createDesktopAssistantConversation());
      if (!conversationId) {
        setDesktopAssistantStatus("历史不可用");
        return;
      }
      const screenshot = await captureScreenUploadedAttachment();
      if (!screenshot) {
        setDesktopAssistantStatus("截图失败");
        return;
      }
      const result = await observeDesktop({
        prompt: "桌面自动观察：除非画面确实需要提醒或适合简短回应，否则不要回应。",
        attachments: toChatAttachments([screenshot]),
        conversationId,
        saveResponse: true,
        model: modelSettings,
        vision: visionSettings
      });
      if (!result.shouldRespond || !result.assistantMessage) {
        setDesktopAssistantStatus(result.reason || "监听中");
        return;
      }
      const storedMessages = [result.userMessage, result.assistantMessage].filter(Boolean) as ConversationDetail["messages"];
      const chatItems = toChatMessages(storedMessages);
      setMessages((prev) => {
        const existing = new Set(prev.map((message) => message.id));
        return [...prev, ...chatItems.filter((message) => !existing.has(message.id))];
      });
      const assistant = chatItems.find((message) => message.role === "assistant");
      if (assistant) {
        setCurrentEmotion(normalizeDisplayEmotion(result.emotion));
        live2dRef.current?.playEmotion(normalizeLive2dEmotion(result.emotion, result.emotion));
        if (desktopAssistantSettings.voiceOutputEnabled) {
          const speech = await synthesizeVoice({
            text: assistant.content,
            model: modelSettings,
            voice: { ...voiceSettings, autoPlay: true },
            conversationId,
            messageId: assistant.id
          });
          const assistantVoice =
            normalizeAssistantVoice(speech.assistantVoice) ??
            normalizeAssistantVoice({ audioUrls: [speech.audioUrl], segments: [{ index: 0, url: speech.audioUrl }] });
          if (assistantVoice) {
            setAssistantVoice(assistant.id, assistantVoice);
            playAssistantVoiceUrls(assistantVoice.audioUrls);
          }
        }
      }
      setDesktopAssistantStatus("监听中");
      void refreshConversations();
    } catch (error) {
      setDesktopAssistantStatus(error instanceof Error ? error.message : "观察失败");
    } finally {
      desktopObservationBusyRef.current = false;
      setDesktopObserveBusy(false);
    }
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

  async function uploadFilesForAttachments(files: File[], addToComposer = true): Promise<UploadedFileInfo[]> {
    if (files.length === 0) {
      return [];
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
      if (addToComposer) {
        setUploadedFiles((prev) => {
          const byPath = new Map(prev.map((file) => [file.path, file]));
          uploaded.forEach((file) => byPath.set(file.path, file));
          return Array.from(byPath.values());
        });
        setUploadPanelOpen(false);
      }
      setStatus(files.length > 1 ? `uploaded ${files.length}` : "uploaded");
      return uploaded;
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "upload failed");
      return [];
    } finally {
      setUploadBusy(false);
    }
  }

  async function uploadFiles(files: File[]) {
    await uploadFilesForAttachments(files, true);
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

  function renderVoiceInputVisualizer(compact = false) {
    return (
      <div className={`voice-input-visualizer ${compact ? "is-compact" : ""}`} aria-label="正在录音">
        <div className="voice-input-bars" aria-hidden="true">
          {voiceInputLevels.map((level, index) => (
            <span
              key={`${index}-${voiceRecording ? "recording" : "idle"}`}
              style={{ height: `${Math.round(8 + level * 25)}px` }}
            />
          ))}
        </div>
        <span className="voice-input-time">{Math.max(1, Math.round(voiceRecordingSeconds))}"</span>
      </div>
    );
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
    const cachedUrls = assistantVoiceAudioUrls(message);
    if (cachedUrls.length > 0) {
      playAssistantVoiceUrls(cachedUrls);
      setStatus("done");
      return;
    }
    setVoiceBusy(true);
    setStatus("voice");
    try {
      const result = await synthesizeVoice({
        text,
        model: modelSettings,
        voice: voiceSettings,
        conversationId: currentConversationId,
        messageId: message.id
      });
      const assistantVoice =
        normalizeAssistantVoice(result.assistantVoice) ??
        normalizeAssistantVoice({
          audioUrls: [result.audioUrl],
          segments: [{ index: 0, url: result.audioUrl }]
        });
      if (assistantVoice) {
        setAssistantVoice(message.id, assistantVoice);
        playAssistantVoiceUrls(assistantVoice.audioUrls);
      }
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
      if (desktopAssistantModeRef.current) {
        setDesktopAssistantStatus(event.payload.phase);
      }
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
      appendAssistantVoiceAudio(assistantId, event.payload.url, event.payload.index ?? 0);
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
      if (desktopAssistantModeRef.current) {
        setDesktopAssistantStatus(event.payload.message);
      }
      return;
    }
    if (event.event === "done") {
      if (event.payload.assistantVoice) {
        setAssistantVoice(assistantId, event.payload.assistantVoice);
      }
      finalizeAssistant(assistantId);
      setStatus("done");
      if (desktopAssistantModeRef.current) {
        setDesktopAssistantStatus("监听中");
      }
    }
  }

  async function sendMessage(event?: FormEvent) {
    event?.preventDefault();
    const text = composeMessageText(input, uploadedFiles);
    if (!text || isStreaming) {
      return;
    }
    if (countImageAttachments(uploadedFiles) > 4) {
      setStatus("最多 4 张图片");
      return;
    }
    const attachments = toChatAttachments(uploadedFiles);

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
      attachments,
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
        attachments,
        mode,
        model: modelSettings,
        vision: visionSettings,
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

  async function sendVoiceInputMessage(
    text: string,
    voiceInput: VoiceInputInfo,
    options: { attachments?: ChatAttachment[]; voice?: VoiceSettings } = {}
  ) {
    const content = text.trim();
    if (!content || isStreaming) {
      return;
    }

    abortRef.current?.abort();
    stopAudioPlayback();
    const controller = new AbortController();
    abortRef.current = controller;
    setIsStreaming(true);
    setStatus("connecting");
    const conversationId = await ensureConversation(content);

    const userMessage: ChatMessage = {
      id: createId(),
      role: "user",
      content,
      attachments: options.attachments,
      voiceInput,
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
        attachments: options.attachments ?? [],
        mode,
        model: modelSettings,
        vision: visionSettings,
        voice: options.voice ?? voiceSettings,
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

  function getCodeTaskEventTaskId(event: CodeTaskEvent): string {
    const payload = event.payload as Record<string, unknown>;
    return typeof payload.taskId === "string" ? payload.taskId : "";
  }

  function ensureIncomingCodeTaskVisible(taskId: string, event: CodeTaskEvent): boolean {
    if (taskId === activeCodeTaskIdRef.current) {
      return true;
    }
    const payload = event.payload as Record<string, unknown>;
    const source = typeof payload.source === "string" ? payload.source : "";
    const existing = codeTasksRef.current.find((task) => task.id === taskId) ?? null;
    const shouldSwitch = !activeCodeTaskIdRef.current || !existing || source === "mobile";
    if (!shouldSwitch) {
      return false;
    }
    const now = new Date().toISOString();
    const prompt = typeof payload.text === "string" && payload.text.trim()
      ? payload.text.trim()
      : typeof payload.message === "string"
        ? payload.message.trim()
        : "OpenCode 任务";
    const workspacePath = typeof payload.workspacePath === "string" ? payload.workspacePath : existing?.workspacePath ?? "";
    const nextTask: CodeTaskRecord = existing ?? {
      id: taskId,
      title: buildConversationTitle(prompt),
      prompt,
      workspacePath,
      status: "running",
      messages: [],
      createdAt: now,
      updatedAt: now
    };
    const nextMessages = existing?.messages ?? [];
    activeCodeTaskIdRef.current = taskId;
    codeTaskMessagesRef.current = nextMessages;
    codeTaskStatusRef.current = nextTask.status;
    codeTaskWorkspaceRef.current = nextTask.workspacePath;
    codeTaskRunningRef.current = true;
    codeTaskAssistantMessageIdRef.current = null;
    codeTaskServerUrlRef.current = "";
    setActiveCodeTaskId(taskId);
    setCodeTaskMessages(nextMessages);
    setCodeTaskWorkspace(nextTask.workspacePath);
    setCodeTaskStatus(nextTask.status);
    setCodeTaskRunning(true);
    setCodeTaskQuestionBusyIds([]);
    if (!existing) {
      codeTasksRef.current = [nextTask, ...codeTasksRef.current].slice(0, CODE_TASK_MAX_HISTORY);
    }
    setCodeTasks((prev) => {
      if (prev.some((task) => task.id === taskId)) {
        return prev;
      }
      return [nextTask, ...prev].slice(0, CODE_TASK_MAX_HISTORY);
    });
    openTaskPanel();
    return true;
  }

  function scheduleMobileTaskViewPublish(delayMs: number) {
    if (mobileTaskViewTimerRef.current !== null) {
      window.clearTimeout(mobileTaskViewTimerRef.current);
    }
    mobileTaskViewTimerRef.current = window.setTimeout(() => {
      mobileTaskViewTimerRef.current = null;
      publishMobileTaskViewSnapshot();
    }, delayMs);
  }

  function publishMobileTaskViewSnapshot() {
    const taskId = activeCodeTaskIdRef.current;
    if (!taskId) {
      pendingMobileTaskViewPublishRef.current = false;
      return;
    }
    const baseTask = codeTasksRef.current.find((task) => task.id === taskId);
    if (!baseTask) {
      pendingMobileTaskViewPublishRef.current = false;
      return;
    }
    const messagesForSnapshot = codeTaskMessagesRef.current;
    const taskForSnapshot: CodeTaskRecord = {
      ...baseTask,
      workspacePath: codeTaskWorkspaceRef.current,
      status: codeTaskStatusRef.current,
      messages: messagesForSnapshot,
      updatedAt: new Date().toISOString()
    };
    const snapshot = buildCodeTaskMobileViewSnapshot(
      taskForSnapshot,
      messagesForSnapshot,
      codeTaskRunningRef.current
    );
    const snapshotHash = JSON.stringify({
      task: snapshot.task,
      status: snapshot.status,
      running: snapshot.running,
      view: snapshot.view,
      turns: snapshot.turns,
      pendingQuestion: snapshot.pendingQuestion,
      summary: snapshot.summary
    });
    if (snapshotHash === lastMobileTaskViewHashRef.current) {
      pendingMobileTaskViewPublishRef.current = false;
      return;
    }
    lastMobileTaskViewHashRef.current = snapshotHash;
    pendingMobileTaskViewPublishRef.current = false;
    if (mobileTaskViewFrameRef.current !== null) {
      window.cancelAnimationFrame(mobileTaskViewFrameRef.current);
    }
    mobileTaskViewFrameRef.current = window.requestAnimationFrame(() => {
      mobileTaskViewFrameRef.current = null;
      publishCodeTaskMobileView(snapshot).catch(() => undefined);
    });
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
    activeCodeTaskIdRef.current = task.id;
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
    activeCodeTaskIdRef.current = null;
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
      activeCodeTaskIdRef.current = nextTask?.id ?? null;
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
    if (taskId && taskId !== activeCodeTaskIdRef.current && !ensureIncomingCodeTaskVisible(taskId, event)) {
      return;
    }
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
    activeCodeTaskIdRef.current = taskId;
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
      const result = await synthesizeVoice({
        text: "これで音声のテストは完了。まったく、最初からそう言えばいいのに。",
        model: modelSettings,
        voice: voiceSettings
      });
      const assistantVoice = normalizeAssistantVoice(result.assistantVoice);
      playAssistantVoiceUrls(assistantVoice?.audioUrls ?? [result.audioUrl]);
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

  function desktopAssistantSubtitleText(): string {
    const latestAssistant = [...messages].reverse().find((message) => message.role === "assistant" && message.content.trim());
    if (latestAssistant?.content.trim()) {
      return latestAssistant.content.trim();
    }
    return desktopAssistantStatus;
  }

  function renderDesktopAssistantSubtitle() {
    if (!desktopAssistantMode || !desktopAssistantSettings.subtitleEnabled) {
      return null;
    }
    const text = desktopAssistantSubtitleText();
    if (!text) {
      return null;
    }
    return (
      <div className="desktop-assistant-subtitle" aria-live="polite">
        {text}
      </div>
    );
  }

  function renderDesktopAssistantControls() {
    if (!desktopAssistantMode) {
      return null;
    }
    const cameraTitle = desktopCameraAvailable
      ? desktopAssistantSettings.cameraEnabled
        ? "关闭摄像头检测"
        : "开启摄像头检测"
      : desktopCameraReason || "摄像头不可用";
    return (
      <div className="desktop-assistant-controls glass-panel">
        <button
          className={`round-tool ${desktopAssistantSettings.subtitleEnabled ? "is-active" : ""}`}
          onClick={() =>
            setDesktopAssistantSettings((prev) => ({ ...prev, subtitleEnabled: !prev.subtitleEnabled }))
          }
          type="button"
          title={desktopAssistantSettings.subtitleEnabled ? "关闭字幕" : "开启字幕"}
          aria-label={desktopAssistantSettings.subtitleEnabled ? "关闭字幕" : "开启字幕"}
        >
          <MessageCircle size={17} />
        </button>
        <button
          className={`round-tool ${desktopAssistantSettings.voiceOutputEnabled ? "is-active" : ""}`}
          onClick={() =>
            setDesktopAssistantSettings((prev) => ({ ...prev, voiceOutputEnabled: !prev.voiceOutputEnabled }))
          }
          type="button"
          title={desktopAssistantSettings.voiceOutputEnabled ? "关闭语音输出" : "开启语音输出"}
          aria-label={desktopAssistantSettings.voiceOutputEnabled ? "关闭语音输出" : "开启语音输出"}
        >
          {desktopAssistantSettings.voiceOutputEnabled ? <Volume2 size={17} /> : <VolumeX size={17} />}
        </button>
        <button
          className={`round-tool ${desktopAssistantSettings.cameraEnabled ? "is-active" : ""}`}
          disabled={!desktopCameraAvailable}
          onClick={() => void toggleDesktopCamera()}
          type="button"
          title={cameraTitle}
          aria-label={cameraTitle}
        >
          <Camera size={17} />
        </button>
        <button
          className={`round-tool ${desktopAssistantSettings.autoScreenshotEnabled ? "is-active" : ""} ${desktopObserveBusy ? "is-busy" : ""}`}
          onClick={() =>
            setDesktopAssistantSettings((prev) => ({ ...prev, autoScreenshotEnabled: !prev.autoScreenshotEnabled }))
          }
          type="button"
          title={desktopAssistantSettings.autoScreenshotEnabled ? "关闭自动截图" : "开启自动截图"}
          aria-label={desktopAssistantSettings.autoScreenshotEnabled ? "关闭自动截图" : "开启自动截图"}
        >
          <Camera size={17} />
        </button>
        <label className="desktop-assistant-frequency" title="自动截图频率">
          <input
            value={desktopAssistantSettings.screenshotIntervalSeconds}
            min={5}
            max={120}
            step={5}
            type="number"
            onChange={(event) =>
              setDesktopAssistantSettings((prev) => ({
                ...prev,
                screenshotIntervalSeconds: clamp(Number(event.target.value || 15), 5, 120)
              }))
            }
            aria-label="自动截图频率秒数"
          />
          <span>s</span>
        </label>
        <button
          className="round-tool"
          onClick={() => void exitDesktopAssistantMode()}
          type="button"
          title="返回窗口"
          aria-label="返回窗口"
        >
          <ChevronRight size={18} />
        </button>
      </div>
    );
  }

  return (
    <main
      className={[
        "app-shell",
        menuOpen ? "is-menu-open" : "",
        configOpen ? "is-config-open" : "",
        chatOpen ? "is-chat-open" : "",
        desktopAssistantMode ? "is-desktop-assistant" : ""
      ].join(" ")}
      style={
        {
          "--chat-panel-width": `${clampedChatWidth}px`,
          "--settings-left": `${settingsOffset}px`
        } as CSSProperties
      }
    >
      <section
        className="stage-panel"
        aria-label="Amadeus Live2D canvas"
        onPointerDown={startDesktopWindowDrag}
        onPointerMove={moveDesktopWindowDrag}
        onPointerUp={stopDesktopWindowDrag}
        onPointerCancel={stopDesktopWindowDrag}
      >
        <Live2DStage
          ref={live2dRef}
          model={activeLive2DModel}
          transformLocked={desktopAssistantMode ? true : live2dTransformLocked}
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
      {renderDesktopAssistantSubtitle()}
      {renderDesktopAssistantControls()}

      <TopBar
        modelSettings={modelSettings}
        providers={providers}
        selectedProvider={selectedProvider}
        mode={mode}
        desktopAssistantAvailable={desktopAssistantAvailable}
        onProviderChange={applyProvider}
        onModeChange={setMode}
        onOpenDesktopAssistant={() => void enterDesktopAssistantMode()}
        onWindowCommand={(command) => void window.amadeusDesktop?.windowCommand?.(command)}
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
            title="视觉"
            icon={<Camera size={16} />}
            open={openSections.vision}
            onToggle={() => toggleSection("vision")}
          >
            <label className="switch-row">
              <span>视觉理解</span>
              <input
                checked={visionSettings.enabled}
                onChange={(event) => setVisionSettings((prev) => ({ ...prev, enabled: event.target.checked }))}
                type="checkbox"
              />
            </label>
            <label className="switch-row">
              <span>截图入口</span>
              <input
                checked={visionSettings.screenCaptureEnabled}
                onChange={(event) =>
                  setVisionSettings((prev) => ({ ...prev, screenCaptureEnabled: event.target.checked }))
                }
                type="checkbox"
              />
            </label>
            <label className="switch-row">
              <span>远程视觉 API</span>
              <input
                checked={visionSettings.useRemote}
                onChange={(event) => setVisionSettings((prev) => ({ ...prev, useRemote: event.target.checked }))}
                type="checkbox"
              />
            </label>
            <label className="field">
              <span>Provider</span>
              <select
                value={visionSettings.providerName}
                onChange={(event) => {
                  const provider = providers.find((item) => item.name === event.target.value);
                  setVisionSettings((prev) => ({
                    ...prev,
                    providerName: event.target.value,
                    baseUrl: event.target.value ? provider?.baseUrl ?? prev.baseUrl : "",
                    useRemote: event.target.value ? event.target.value !== "演示模型" : prev.useRemote
                  }));
                }}
              >
                <option value="">继承基础模型</option>
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
                value={visionSettings.baseUrl}
                onChange={(event) => setVisionSettings((prev) => ({ ...prev, baseUrl: event.target.value }))}
                placeholder={modelSettings.baseUrl || DEFAULT_MODEL_SETTINGS.baseUrl}
              />
            </label>
            <label className="field">
              <span>Vision Model</span>
              <input
                value={visionSettings.model}
                onChange={(event) => setVisionSettings((prev) => ({ ...prev, model: event.target.value }))}
                placeholder={DEFAULT_VISION_SETTINGS.model}
              />
            </label>
            <label className="field">
              <span>API Key</span>
              <input
                value={visionSettings.apiKey}
                onChange={(event) => setVisionSettings((prev) => ({ ...prev, apiKey: event.target.value }))}
                placeholder={modelSettings.apiKey ? "继承基础模型 Key" : ""}
                type="password"
                autoComplete="off"
              />
            </label>
          </ConfigGroup>

          <ConfigGroup
            title="语音输入"
            icon={<Mic size={16} />}
            open={openSections.speechInput}
            onToggle={() => toggleSection("speechInput")}
          >
            <label className="switch-row">
              <span>语音输入</span>
              <input
                checked={speechInputSettings.enabled}
                onChange={(event) => setSpeechInputSettings((prev) => ({ ...prev, enabled: event.target.checked }))}
                type="checkbox"
              />
            </label>
            <label className="switch-row">
              <span>远程转写 API</span>
              <input
                checked={speechInputSettings.useRemote}
                onChange={(event) => setSpeechInputSettings((prev) => ({ ...prev, useRemote: event.target.checked }))}
                type="checkbox"
              />
            </label>
            <label className="field">
              <span>Provider</span>
              <select
                value={speechInputSettings.providerName}
                onChange={(event) => {
                  const provider = providers.find((item) => item.name === event.target.value);
                  if (event.target.value === DEFAULT_SPEECH_INPUT_SETTINGS.providerName) {
                    setSpeechInputSettings((prev) => ({
                      ...prev,
                      providerName: DEFAULT_SPEECH_INPUT_SETTINGS.providerName,
                      baseUrl: DEFAULT_SPEECH_INPUT_SETTINGS.baseUrl,
                      model: DEFAULT_SPEECH_INPUT_SETTINGS.model,
                      language: DEFAULT_SPEECH_INPUT_SETTINGS.language,
                      useRemote: true
                    }));
                    return;
                  }
                  setSpeechInputSettings((prev) => ({
                    ...prev,
                    providerName: event.target.value,
                    baseUrl: event.target.value ? provider?.baseUrl ?? prev.baseUrl : DEFAULT_SPEECH_INPUT_SETTINGS.baseUrl,
                    useRemote: event.target.value ? event.target.value !== "演示模型" : prev.useRemote
                  }));
                }}
              >
                <option value={DEFAULT_SPEECH_INPUT_SETTINGS.providerName}>小米 MiMo ASR</option>
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
                value={speechInputSettings.baseUrl}
                onChange={(event) => setSpeechInputSettings((prev) => ({ ...prev, baseUrl: event.target.value }))}
                placeholder={DEFAULT_SPEECH_INPUT_SETTINGS.baseUrl}
              />
            </label>
            <label className="field">
              <span>ASR Model</span>
              <input
                value={speechInputSettings.model}
                onChange={(event) => setSpeechInputSettings((prev) => ({ ...prev, model: event.target.value }))}
                placeholder={DEFAULT_SPEECH_INPUT_SETTINGS.model}
              />
            </label>
            <div className="inline-fields">
              <label className="field">
                <span>Language</span>
                <input
                  value={speechInputSettings.language}
                  onChange={(event) => setSpeechInputSettings((prev) => ({ ...prev, language: event.target.value }))}
                  placeholder="auto / zh / en"
                />
              </label>
              <label className="field">
                <span>API Key</span>
                <input
                  value={
                    speechInputSettings.providerName === DEFAULT_SPEECH_INPUT_SETTINGS.providerName &&
                    modelSettings.apiKey
                      ? ""
                      : speechInputSettings.apiKey
                  }
                  onChange={(event) => setSpeechInputSettings((prev) => ({ ...prev, apiKey: event.target.value }))}
                  placeholder={modelSettings.apiKey ? "默认复用基础模型 Key" : "填写 MiMo API Key"}
                  type="password"
                  autoComplete="off"
                />
              </label>
            </div>
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
        <form
          className={`floating-composer glass-panel ${uploadedFiles.length > 0 ? "has-attachments" : ""} ${visionSettings.screenCaptureEnabled ? "has-screen-capture" : ""}`}
          onSubmit={sendMessage}
        >
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
          {visionSettings.screenCaptureEnabled && (
            <button
              className={`round-tool screenshot-trigger ${screenCaptureBusy ? "is-busy" : ""}`}
              disabled={uploadBusy || screenCaptureBusy}
              onClick={() => void captureScreenAttachment()}
              type="button"
              aria-label="截图"
              title="截图并作为图片附件"
            >
              <Camera size={18} />
            </button>
          )}
          {voiceRecording ? (
            renderVoiceInputVisualizer(true)
          ) : (
            <textarea
              ref={floatingComposerInputRef}
              value={input}
              onChange={updateComposerInput}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void sendMessage();
                }
              }}
              placeholder="有问题，尽管问"
              rows={1}
            />
          )}
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
          <button
            className={`round-tool voice-input-trigger ${voiceRecording ? "is-recording" : ""} ${voiceInputBusy ? "is-busy" : ""}`}
            disabled={isStreaming || voiceInputBusy || !speechInputSettings.enabled}
            onClick={toggleVoiceInputRecording}
            type="button"
            aria-label={voiceRecording ? "停止语音输入" : "开始语音输入"}
            title={voiceRecording ? "停止语音输入" : "语音输入"}
          >
            {voiceRecording ? <CircleStop size={18} /> : <Mic2 size={18} />}
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
              {voiceRecording ? (
                renderVoiceInputVisualizer()
              ) : (
                <textarea
                  ref={panelComposerInputRef}
                  value={input}
                  onChange={updateComposerInput}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void sendMessage();
                    }
                  }}
                  placeholder="输入消息"
                  rows={3}
                />
              )}
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
                  {visionSettings.screenCaptureEnabled && (
                    <button
                      className={`round-tool composer-screenshot screenshot-trigger ${screenCaptureBusy ? "is-busy" : ""}`}
                      disabled={uploadBusy || screenCaptureBusy}
                      onClick={() => void captureScreenAttachment()}
                      type="button"
                      aria-label="截图"
                      title="截图并作为图片附件"
                    >
                      <Camera size={17} />
                    </button>
                  )}
                  <button
                    className={`text-icon-button ${voiceSettings.autoPlay ? "is-active" : ""}`}
                    onClick={toggleAutoVoice}
                    type="button"
                  >
                    {voiceSettings.autoPlay ? <Volume2 size={16} /> : <VolumeX size={16} />}
                    语音
                  </button>
                  <button
                    className={`round-tool voice-input-trigger ${voiceRecording ? "is-recording" : ""} ${voiceInputBusy ? "is-busy" : ""}`}
                    disabled={isStreaming || voiceInputBusy || !speechInputSettings.enabled}
                    onClick={toggleVoiceInputRecording}
                    type="button"
                    aria-label={voiceRecording ? "停止语音输入" : "开始语音输入"}
                    title={voiceRecording ? "停止语音输入" : "语音输入"}
                  >
                    {voiceRecording ? <CircleStop size={17} /> : <Mic2 size={17} />}
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

