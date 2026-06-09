import { Bot, Brain, Sparkles, Zap } from "lucide-react";
import { compactLabel } from "../app/appSupport";
import type { ChatMode, ModelSettings, ProviderPreset } from "../types";

type TopBarProps = {
  modelSettings: ModelSettings;
  providers: ProviderPreset[];
  selectedProvider?: ProviderPreset;
  mode: ChatMode;
  onProviderChange: (providerName: string) => void;
  onModeChange: (mode: ChatMode) => void;
};

export function TopBar({
  modelSettings,
  providers,
  selectedProvider,
  mode,
  onProviderChange,
  onModeChange
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
      </div>
    </header>
  );
}
