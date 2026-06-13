import { Bot, Brain, Maximize2, Minus, Sparkles, X, Zap } from "lucide-react";
import { compactLabel } from "../app/appSupport";
import type { ChatMode, ModelSettings, ProviderPreset } from "../types";

type TopBarProps = {
  modelSettings: ModelSettings;
  providers: ProviderPreset[];
  selectedProvider?: ProviderPreset;
  mode: ChatMode;
  desktopAssistantAvailable?: boolean;
  onProviderChange: (providerName: string) => void;
  onModeChange: (mode: ChatMode) => void;
  onOpenDesktopAssistant?: () => void;
  onWindowCommand?: (command: "minimize" | "toggle-maximize" | "close") => void;
};

export function TopBar({
  modelSettings,
  providers,
  selectedProvider,
  mode,
  desktopAssistantAvailable = false,
  onProviderChange,
  onModeChange,
  onOpenDesktopAssistant,
  onWindowCommand
}: TopBarProps) {
  return (
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
          <select value={modelSettings.providerName} onChange={(event) => onProviderChange(event.target.value)}>
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
            onClick={() => onModeChange("fast")}
            type="button"
          >
            <Zap size={14} />
            快速
          </button>
          <button
            className={mode === "thinking" ? "is-active" : ""}
            onClick={() => onModeChange("thinking")}
            type="button"
          >
            <Brain size={14} />
            思考
          </button>
        </div>
        {desktopAssistantAvailable && (
          <button className="text-icon-button desktop-assistant-entry" onClick={onOpenDesktopAssistant} type="button">
            <Bot size={15} />
            桌面助手
          </button>
        )}
        {desktopAssistantAvailable && onWindowCommand && (
          <div className="window-controls" aria-label="窗口控制">
            <button type="button" title="最小化" aria-label="最小化" onClick={() => onWindowCommand("minimize")}>
              <Minus size={14} />
            </button>
            <button type="button" title="最大化" aria-label="最大化" onClick={() => onWindowCommand("toggle-maximize")}>
              <Maximize2 size={13} />
            </button>
            <button type="button" title="关闭" aria-label="关闭" onClick={() => onWindowCommand("close")}>
              <X size={14} />
            </button>
          </div>
        )}
      </div>
    </header>
  );
}
