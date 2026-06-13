import { useState } from "react";
import {
  BookOpenText,
  CheckCircle2,
  CircleDashed,
  Copy,
  ExternalLink,
  Volume1,
  Search,
  Volume2,
  Wrench,
  XCircle
} from "lucide-react";
import {
  extractToolEventsFromThinking,
  extractToolResultLinks,
  formatMessageTime,
  getToolResultText,
  mergeToolEvents,
  renderMarkdownContent,
  stripToolEventLines,
  stripToolResultTextFromThinking
} from "../app/appSupport";
import type { ChatAttachment, ChatMessage, ToolTraceEvent } from "../types";

type ChatMessageBubbleProps = {
  message: ChatMessage;
  surface: "floating" | "panel";
  voiceBusy: boolean;
  onCopy: (text: string) => void | Promise<void>;
  onPlay: (message: ChatMessage) => void | Promise<void>;
};

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

function renderMessageAttachments(message: ChatMessage, onPreview: (image: ChatAttachment) => void) {
  const images = (message.attachments ?? []).filter((item) => item.mediaType === "image" && item.path);
  if (images.length === 0) {
    return null;
  }
  return (
    <div className="message-attachment-preview" aria-label="图片附件">
      {images.map((image) => {
        const url = `/api/files/download?path=${encodeURIComponent(image.path)}`;
        const label = image.originalFilename || image.filename || "图片";
        return (
          <button
            className="message-image-thumb"
            key={image.path}
            onClick={() => onPreview(image)}
            type="button"
            title={`查看 ${label}`}
          >
            <img src={url} alt={label} loading="lazy" />
          </button>
        );
      })}
    </div>
  );
}

export function ChatMessageBubble({ message, surface, voiceBusy, onCopy, onPlay }: ChatMessageBubbleProps) {
  const [previewImage, setPreviewImage] = useState<ChatAttachment | null>(null);
  const [voicePlaying, setVoicePlaying] = useState(false);
  const isAssistant = message.role === "assistant";
  const content = message.content || (message.streaming ? "..." : "");
  const canUseText = message.content.trim().length > 0;
  const showTextContent = content.trim().length > 0 && !(message.role === "user" && message.voiceInput);
  const previewUrl = previewImage ? `/api/files/download?path=${encodeURIComponent(previewImage.path)}` : "";
  const previewLabel = previewImage?.originalFilename || previewImage?.filename || "图片预览";

  async function playUserVoiceInput() {
    if (!message.voiceInput?.audioUrl || voicePlaying) {
      return;
    }
    setVoicePlaying(true);
    const audio = new Audio(`${message.voiceInput.audioUrl}${message.voiceInput.audioUrl.includes("?") ? "&" : "?"}t=${Date.now()}`);
    const finish = () => setVoicePlaying(false);
    audio.onended = finish;
    audio.onerror = finish;
    try {
      await audio.play();
    } catch {
      finish();
    }
  }

  function renderVoiceInputBubble() {
    const voiceInput = message.voiceInput;
    if (!voiceInput || message.role !== "user") {
      return null;
    }
    const seconds = Math.max(1, Math.round(voiceInput.durationSeconds || 1));
    const waveform = voiceInput.waveform.length > 0 ? voiceInput.waveform : Array.from({ length: 18 }, () => 0.35);
    const width = Math.min(260, Math.max(116, 98 + seconds * 2.1));
    return (
      <button
        className={`voice-message-bubble ${voicePlaying ? "is-playing" : ""}`}
        onClick={() => void playUserVoiceInput()}
        style={{ width }}
        type="button"
        aria-label={`播放语音消息 ${seconds} 秒`}
        title={voiceInput.transcript || "播放语音消息"}
      >
        <Volume1 size={15} />
        <span className="voice-message-bars" aria-hidden="true">
          {waveform.slice(0, 18).map((level, index) => (
            <i key={index} style={{ height: `${Math.round(7 + level * 22)}px` }} />
          ))}
        </span>
        <span>{seconds}"</span>
      </button>
    );
  }

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
      {renderMessageAttachments(message, setPreviewImage)}
      <div className={`${surface === "floating" ? "float-bubble" : "message-bubble"} ${isAssistant ? "is-assistant" : "is-user"}`}>
        {isAssistant && renderThinkingPanel(message)}
        {renderVoiceInputBubble()}
        {showTextContent && renderMarkdownContent(content)}
      </div>
      <div className="bubble-meta">
        <time dateTime={message.createdAt}>{formatMessageTime(message.createdAt)}</time>
        <button
          className="bubble-tool"
          disabled={!canUseText}
          onClick={() => void onCopy(message.content)}
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
            onClick={() => void onPlay(message)}
            type="button"
            aria-label="播放回复"
            title="播放"
          >
            <Volume2 size={14} />
          </button>
        )}
      </div>
      {previewImage && (
        <div className="image-lightbox" role="dialog" aria-modal="true" aria-label={previewLabel} onClick={() => setPreviewImage(null)}>
          <div className="image-lightbox-body" onClick={(event) => event.stopPropagation()}>
            <button className="image-lightbox-close" onClick={() => setPreviewImage(null)} type="button" aria-label="关闭预览">
              <XCircle size={22} />
            </button>
            <img src={previewUrl} alt={previewLabel} />
            <div className="image-lightbox-caption">{previewLabel}</div>
          </div>
        </div>
      )}
    </article>
  );
}
