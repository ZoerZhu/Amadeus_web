/**
 * Centralized application configuration.
 *
 * All configurable constants that were previously scattered across
 * appSupport.tsx, api.ts, and components are now defined in one place.
 * Import from this module instead of duplicating magic values.
 */

/** Default persona used when none is specified */
export const PERSONA_ID = "kurisu_amadeus";

// ---- Storage keys ----
export const STORAGE_KEY = "amadeus-web-settings-v1";
export const LIVE2D_ACTIVE_STORAGE_KEY = "amadeus-live2d-active-model-v1";
export const LIVE2D_LOCK_STORAGE_KEY = "amadeus-live2d-transform-locked-v1";
export const LIVE2D_IMPORT_INPUT_ID = "amadeus-live2d-folder-input";

// ---- Layout constants ----
export const LEFT_SIDEBAR_WIDTH = 372;
export const SETTINGS_SIDEBAR_WIDTH = 388;
export const PANEL_GAP = 14;
export const EDGE_MARGIN = 18;
export const CHAT_MIN_WIDTH = 340;
export const CHAT_MAX_WIDTH = 620;
export const MIN_BOOT_DURATION_MS = 2000;

// ---- Code task constants ----
export const CODE_TASK_FLUSH_INTERVAL_MS = 260;
export const CODE_TASK_MAX_HISTORY = 48;
export const CODE_TASK_MAX_MESSAGES = 160;
export const CODE_TASK_MAX_ASSISTANT_TEXT = 80_000;
export const CODE_TASK_MAX_DETAIL_TEXT = 8_000;
export const CODE_TASK_SUMMARY_TEXT_LIMIT = 6_000;
export const DEFAULT_CODE_TASK_WORKSPACE = "";

// ---- Live2D constants ----
export const LIVE2D_MIN_MODEL_SCALE = 0.35;
export const LIVE2D_MAX_MODEL_SCALE = 2.6;
export const LIVE2D_MIN_MODEL_OFFSET = -0.8;
export const LIVE2D_MAX_MODEL_OFFSET = 0.8;

// ---- Default settings ----
import type {
  DesktopAssistantSettings,
  ModelSettings,
  SpeechInputSettings,
  VisionSettings,
  VoiceSettings,
} from "./types";

export const DEFAULT_MODEL_SETTINGS: ModelSettings = {
  providerName: "OpenAI",
  baseUrl: "https://api.openai.com/v1",
  model: "gpt-4.1-mini",
  apiKey: "",
  useRemote: true,
};

export const DEFAULT_VISION_SETTINGS: VisionSettings = {
  enabled: true,
  screenCaptureEnabled: false,
  providerName: "",
  baseUrl: "",
  model: "gpt-4.1-mini",
  apiKey: "",
  useRemote: true,
};

export const DEFAULT_SPEECH_INPUT_SETTINGS: SpeechInputSettings = {
  enabled: true,
  providerName: "小米 MiMo ASR",
  baseUrl: "https://api.xiaomimimo.com/v1",
  model: "mimo-v2.5-asr",
  apiKey: "",
  language: "auto",
  useRemote: true,
};

export const DEFAULT_VOICE_SETTINGS: VoiceSettings = {
  ttsBackend: "local",
  siliconFlowApiKey: "",
  clonedVoiceUri: "",
  autoPlay: false,
  syncTextOutput: true,
  speed: 1,
  gain: 0,
};

export const DEFAULT_DESKTOP_ASSISTANT_SETTINGS: DesktopAssistantSettings = {
  subtitleEnabled: true,
  voiceOutputEnabled: false,
  autoVoiceInputEnabled: true,
  autoScreenshotEnabled: false,
  screenshotIntervalSeconds: 15,
  cameraEnabled: false,
};
