import { Bot, Lock, Play, Smile, Trash2, Unlock } from "lucide-react";
import type { Live2DControlOption, Live2DModelRecord } from "../agents/live2dImportAgent";

type Live2DControlTab = "expressions" | "motions";

type Live2DQuickControlsProps = {
  activeModel: Live2DModelRecord;
  currentEmotion: string;
  controlsOpen: boolean;
  transformLocked: boolean;
  tab: Live2DControlTab;
  onToggleControls: () => void;
  onToggleLock: () => void;
  onTabChange: (tab: Live2DControlTab) => void;
  onExpression: (option: Live2DControlOption) => void;
  onMotion: (option: Live2DControlOption) => void;
};

type Live2DModelHistoryProps = {
  models: Live2DModelRecord[];
  activeModelId: string;
  busy: boolean;
  onSwitch: (record: Live2DModelRecord) => void | Promise<void>;
  onRemove: (record: Live2DModelRecord) => void | Promise<void>;
};

function live2DRuntimeLabel(record: Live2DModelRecord): string {
  return record.runtime === "cubism3" ? "moc3" : "moc";
}

function Live2DOptionButtons({
  options,
  type,
  onSelect
}: {
  options: Live2DControlOption[];
  type: "expression" | "motion";
  onSelect: (option: Live2DControlOption) => void;
}) {
  if (options.length === 0) {
    return (
      <button className="live2d-option-button" type="button" disabled>
        {type === "expression" ? <Smile size={14} /> : <Play size={14} />}
        暂无
      </button>
    );
  }

  return (
    <>
      {options.map((option) => (
        <button
          className="live2d-option-button"
          key={option.id}
          onClick={() => onSelect(option)}
          title={option.file || option.name}
          type="button"
        >
          {type === "expression" ? <Smile size={14} /> : <Play size={14} />}
          <span>{option.label}</span>
        </button>
      ))}
    </>
  );
}

export function Live2DQuickControls({
  activeModel,
  currentEmotion,
  controlsOpen,
  transformLocked,
  tab,
  onToggleControls,
  onToggleLock,
  onTabChange,
  onExpression,
  onMotion
}: Live2DQuickControlsProps) {
  const options = tab === "expressions" ? activeModel.expressions : activeModel.motions;
  return (
    <div className={`live2d-quick-controls ${controlsOpen ? "is-open" : ""}`}>
      <div className="live2d-quick-actions">
        <button
          className="emotion-chip glass-panel"
          onClick={onToggleControls}
          type="button"
          aria-label="打开 Live2D 表情和动作"
          title="Live2D 表情和动作"
        >
          <Bot size={15} />
          <span>{currentEmotion}</span>
        </button>
        <button
          className={`live2d-lock-button glass-panel ${transformLocked ? "is-locked" : ""}`}
          onClick={onToggleLock}
          type="button"
          aria-label={transformLocked ? "解锁 Live2D 位置和缩放" : "锁定 Live2D 位置和缩放"}
          title={transformLocked ? "已锁定位置和缩放" : "可拖动和滚轮缩放"}
        >
          {transformLocked ? <Lock size={15} /> : <Unlock size={15} />}
        </button>
      </div>
      {controlsOpen && (
        <div className="live2d-control-panel glass-panel">
          <div className="live2d-control-head">
            <strong title={activeModel.name}>{activeModel.name}</strong>
            <small>
              {live2DRuntimeLabel(activeModel)} · {activeModel.expressions.length}/{activeModel.motions.length}
            </small>
          </div>
          <div className="live2d-tabs" role="tablist" aria-label="Live2D controls">
            <button
              className={tab === "expressions" ? "is-active" : ""}
              onClick={() => onTabChange("expressions")}
              type="button"
            >
              <Smile size={14} />
              表情
            </button>
            <button
              className={tab === "motions" ? "is-active" : ""}
              onClick={() => onTabChange("motions")}
              type="button"
            >
              <Play size={14} />
              动作
            </button>
          </div>
          <div className="live2d-option-grid">
            <Live2DOptionButtons
              options={options}
              type={tab === "expressions" ? "expression" : "motion"}
              onSelect={tab === "expressions" ? onExpression : onMotion}
            />
          </div>
        </div>
      )}
    </div>
  );
}

export function Live2DModelHistory({
  models,
  activeModelId,
  busy,
  onSwitch,
  onRemove
}: Live2DModelHistoryProps) {
  return (
    <div className="live2d-history-list">
      {models.map((record) => (
        <div className={`live2d-history-row ${record.id === activeModelId ? "is-current" : ""}`} key={record.id}>
          <button
            className="live2d-history-main"
            disabled={busy || record.id === activeModelId}
            onClick={() => void onSwitch(record)}
            type="button"
          >
            <span>{record.name}</span>
            <small>
              {live2DRuntimeLabel(record)} · {record.expressions.length} 表情 · {record.motions.length} 动作
            </small>
          </button>
          {record.source !== "default" && (
            <button
              className="history-icon-button is-danger"
              disabled={busy}
              onClick={() => void onRemove(record)}
              title="删除"
              type="button"
              aria-label={`删除 ${record.name}`}
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
