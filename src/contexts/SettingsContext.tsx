/**
 * Application-wide settings context.
 *
 * Provides a single source of truth for model, vision, voice,
 * speech-input, and desktop-assistant settings.  The existing
 * App.tsx still manages settings internally; this context is the
 * migration target for new code and future refactors.
 */

import { createContext, useContext, useMemo, useState, useCallback, type ReactNode } from "react";
import type {
  ChatMode,
  DesktopAssistantSettings,
  ModelSettings,
  SpeechInputSettings,
  VisionSettings,
  VoiceSettings,
} from "../types";
import {
  DEFAULT_DESKTOP_ASSISTANT_SETTINGS,
  DEFAULT_MODEL_SETTINGS,
  DEFAULT_SPEECH_INPUT_SETTINGS,
  DEFAULT_VISION_SETTINGS,
  DEFAULT_VOICE_SETTINGS,
} from "../config";

// ---------------------------------------------------------------------------
// Shape
// ---------------------------------------------------------------------------

export interface SettingsState {
  /** Chat mode: "fast" or "thinking" */
  mode: ChatMode;
  model: ModelSettings;
  vision: VisionSettings;
  speechInput: SpeechInputSettings;
  voice: VoiceSettings;
  desktopAssistant: DesktopAssistantSettings;
}

export interface SettingsActions {
  setMode: (mode: ChatMode) => void;
  setModel: (model: Partial<ModelSettings>) => void;
  setVision: (vision: Partial<VisionSettings>) => void;
  setSpeechInput: (speech: Partial<SpeechInputSettings>) => void;
  setVoice: (voice: Partial<VoiceSettings>) => void;
  setDesktopAssistant: (desktop: Partial<DesktopAssistantSettings>) => void;
  /** Bulk-apply a full settings payload (e.g. from server / localStorage) */
  applyAll: (settings: Partial<SettingsState>) => void;
}

export type SettingsContextValue = SettingsState & SettingsActions;

// ---------------------------------------------------------------------------
// Context
// ---------------------------------------------------------------------------

const SettingsContext = createContext<SettingsContextValue | null>(null);

// ---------------------------------------------------------------------------
// Provider
// ---------------------------------------------------------------------------

function defaults(): SettingsState {
  return {
    mode: "fast",
    model: { ...DEFAULT_MODEL_SETTINGS },
    vision: { ...DEFAULT_VISION_SETTINGS },
    speechInput: { ...DEFAULT_SPEECH_INPUT_SETTINGS },
    voice: { ...DEFAULT_VOICE_SETTINGS },
    desktopAssistant: { ...DEFAULT_DESKTOP_ASSISTANT_SETTINGS },
  };
}

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SettingsState>(defaults);

  const setMode = useCallback(
    (mode: ChatMode) => setState((prev) => ({ ...prev, mode })),
    [],
  );

  const setModel = useCallback(
    (model: Partial<ModelSettings>) =>
      setState((prev) => ({ ...prev, model: { ...prev.model, ...model } })),
    [],
  );

  const setVision = useCallback(
    (vision: Partial<VisionSettings>) =>
      setState((prev) => ({ ...prev, vision: { ...prev.vision, ...vision } })),
    [],
  );

  const setSpeechInput = useCallback(
    (speech: Partial<SpeechInputSettings>) =>
      setState((prev) => ({ ...prev, speechInput: { ...prev.speechInput, ...speech } })),
    [],
  );

  const setVoice = useCallback(
    (voice: Partial<VoiceSettings>) =>
      setState((prev) => ({ ...prev, voice: { ...prev.voice, ...voice } })),
    [],
  );

  const setDesktopAssistant = useCallback(
    (desktop: Partial<DesktopAssistantSettings>) =>
      setState((prev) => ({ ...prev, desktopAssistant: { ...prev.desktopAssistant, ...desktop } })),
    [],
  );

  const applyAll = useCallback(
    (settings: Partial<SettingsState>) =>
      setState((prev) => ({
        ...prev,
        ...settings,
        model: settings.model ? { ...prev.model, ...settings.model } : prev.model,
        vision: settings.vision ? { ...prev.vision, ...settings.vision } : prev.vision,
        speechInput: settings.speechInput ? { ...prev.speechInput, ...settings.speechInput } : prev.speechInput,
        voice: settings.voice ? { ...prev.voice, ...settings.voice } : prev.voice,
        desktopAssistant: settings.desktopAssistant
          ? { ...prev.desktopAssistant, ...settings.desktopAssistant }
          : prev.desktopAssistant,
      })),
    [],
  );

  const value: SettingsContextValue = useMemo(
    () => ({
      ...state,
      setMode,
      setModel,
      setVision,
      setSpeechInput,
      setVoice,
      setDesktopAssistant,
      applyAll,
    }),
    [state, setMode, setModel, setVision, setSpeechInput, setVoice, setDesktopAssistant, applyAll],
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useSettings(): SettingsContextValue {
  const ctx = useContext(SettingsContext);
  if (!ctx) {
    throw new Error("useSettings must be used inside <SettingsProvider>");
  }
  return ctx;
}
