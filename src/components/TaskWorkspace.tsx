import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleDashed,
  Download,
  FileText,
  FolderOpen,
  Loader2,
  Paperclip,
  Plus,
  Send,
  Shield,
  Square,
  Terminal,
  Trash2,
  Wrench,
  X
} from "lucide-react";
import {
  appendOrchestratorTaskMessage,
  approvePermission,
  controlOrchestratorTask,
  createOrchestratorTask,
  downloadArtifactUrl,
  fetchOrchestratorTaskDetail,
  fetchOrchestratorTasks,
  fetchPendingPermissions,
  rejectPermission,
  streamOrchestratorTaskEvents,
  uploadWorkspaceFile
} from "../api";
import { ACCEPTED_UPLOAD_TYPES, buildConversationTitle, formatHistoryTime, renderMarkdownContent } from "../app/appSupport";
import type {
  ChatAttachment,
  ChatMode,
  ModelSettings,
  OrchestratorArtifact,
  OrchestratorEventKind,
  OrchestratorPermissionRequest,
  OrchestratorSettings,
  OrchestratorTaskEvent,
  OrchestratorTaskSummary,
  UploadedFileInfo
} from "../types";

type TaskWorkspaceProps = {
  model: ModelSettings;
  mode: ChatMode;
  orchestratorSettings: OrchestratorSettings;
  selectedTaskId?: string | null;
  onSelectTask?: (taskId: string | null) => void;
  onTasksChange?: (tasks: OrchestratorTaskSummary[]) => void;
};

const CONTINUABLE_STATUS = new Set(["done", "failed"]);

const STATUS_LABELS: Record<string, string> = {
  created: "已创建",
  running: "运行中",
  paused: "等待确认",
  cancelled: "已取消",
  done: "已完成",
  failed: "失败",
  rolled_back: "已回滚"
};

const EVENT_LABELS: Record<OrchestratorEventKind, string> = {
  message: "消息",
  status: "状态",
  plan: "计划",
  step: "步骤",
  tool: "工具",
  mcp: "MCP",
  browser: "浏览器",
  artifact: "产物",
  question: "确认",
  sampling: "采样",
  opencode_routing: "OpenCode 路由",
  error: "错误",
  done: "完成",
  agent_thought_summary: "Agent 思考",
  tool_call: "工具调用",
  tool_result: "工具结果",
  command: "命令",
  working_set: "工作集"
};

function toChatAttachments(files: UploadedFileInfo[]): ChatAttachment[] {
  return files.map((file) => ({
    path: file.path,
    originalFilename: file.originalFilename,
    filename: file.filename,
    mediaType: file.mediaType,
    contentType: file.contentType
  }));
}

function attachmentLabel(attachment: ChatAttachment): string {
  return attachment.originalFilename || attachment.filename || attachment.path.split(/[\\/]/).pop() || "附件";
}

function eventIcon(kind: OrchestratorEventKind) {
  if (kind === "message") {
    return <BookOpen size={14} />;
  }
  if (kind === "plan" || kind === "step" || kind === "agent_thought_summary") {
    return <CheckCircle2 size={14} />;
  }
  if (kind === "question") {
    return <Shield size={14} />;
  }
  if (kind === "artifact") {
    return <FileText size={14} />;
  }
  if (kind === "error") {
    return <AlertTriangle size={14} />;
  }
  if (kind === "command") {
    return <Terminal size={14} />;
  }
  if (kind === "working_set") {
    return <FolderOpen size={14} />;
  }
  if (kind === "tool_call" || kind === "tool_result") {
    return <Wrench size={14} />;
  }
  return <Wrench size={14} />;
}

function eventDetail(event: OrchestratorTaskEvent): string {
  const payload = event.payload;
  if (!payload || Object.keys(payload).length === 0) {
    return "";
  }
  switch (event.kind) {
    case "agent_thought_summary":
      return JSON.stringify(
        { round: payload.round, preview: String(payload.preview || "").slice(0, 500) },
        null,
        2
      );
    case "tool_call":
      return JSON.stringify({ tool: event.name, arguments: payload.arguments }, null, 2);
    case "tool_result":
      return JSON.stringify({ ok: payload.ok, data: payload.data }, null, 2);
    case "command": {
      const stdout = String(payload.stdout || "");
      const stderr = String(payload.stderr || "");
      return JSON.stringify(
        {
          command: payload.command,
          exitCode: payload.exitCode,
          stdout: stdout.slice(0, 500),
          stderr: stderr.slice(0, 200)
        },
        null,
        2
      );
    }
    case "working_set":
      return JSON.stringify(
        {
          workspacePath: payload.workspacePath,
          toolCount: payload.toolCount,
          mcpAvailable: (payload.mcpSnapshot as Record<string, unknown>)?.available
        },
        null,
        2
      );
    default:
      try {
        return JSON.stringify(payload, null, 2);
      } catch {
        return "";
      }
  }
}

function taskMessageText(event: OrchestratorTaskEvent): string {
  const text = event.payload?.text;
  return typeof text === "string" && text.trim() ? text : event.summary;
}

function taskMessageAttachments(event: OrchestratorTaskEvent): ChatAttachment[] {
  const raw = event.payload?.attachments;
  return Array.isArray(raw)
    ? raw.filter((item): item is ChatAttachment => typeof item === "object" && item !== null && "path" in item)
    : [];
}

function finalAnswerPayload(event: OrchestratorTaskEvent): Record<string, unknown> {
  return event.payload && typeof event.payload === "object" ? event.payload : {};
}

export function TaskWorkspace({
  model,
  mode,
  orchestratorSettings,
  selectedTaskId,
  onSelectTask,
  onTasksChange
}: TaskWorkspaceProps) {
  const [tasks, setTasks] = useState<OrchestratorTaskSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(selectedTaskId ?? null);
  const [taskDetail, setTaskDetail] = useState<OrchestratorTaskSummary | null>(null);
  const [events, setEvents] = useState<OrchestratorTaskEvent[]>([]);
  const [artifacts, setArtifacts] = useState<OrchestratorArtifact[]>([]);
  const [permissions, setPermissions] = useState<OrchestratorPermissionRequest[]>([]);
  const [permBusy, setPermBusy] = useState<Record<string, boolean>>({});
  const [input, setInput] = useState("");
  const [workspace, setWorkspace] = useState(orchestratorSettings.defaultWorkspace || "");
  const [trustMode, setTrustMode] = useState(false);
  const [attachments, setAttachments] = useState<UploadedFileInfo[]>([]);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");
  const [taskMode, setTaskMode] = useState<ChatMode>(mode);
  const [taskModelName, setTaskModelName] = useState(model.model);
  const [streamVersion, setStreamVersion] = useState(0);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const eventsRef = useRef<HTMLDivElement | null>(null);
  const activeIdRef = useRef<string | null>(selectedTaskId ?? null);
  const lastSeqRef = useRef(0);

  const effectiveActiveId = selectedTaskId ?? activeId;
  const canSend = input.trim().length > 0 && !running && !uploadBusy;
  const activeRunning = taskDetail?.status === "running" || taskDetail?.status === "paused" || running;

  const setTaskList = useCallback(
    (next: OrchestratorTaskSummary[]) => {
      setTasks(next);
      onTasksChange?.(next);
    },
    [onTasksChange]
  );

  const handleSelect = useCallback(
    (taskId: string | null) => {
      activeIdRef.current = taskId;
      if (onSelectTask) {
        onSelectTask(taskId);
      } else {
        setActiveId(taskId);
      }
    },
    [onSelectTask]
  );

  const refreshTasks = useCallback(async () => {
    const list = await fetchOrchestratorTasks(undefined);
    setTaskList(list);
    if (!activeIdRef.current && list.length > 0) {
      handleSelect(list[0].id);
    }
  }, [handleSelect, setTaskList]);

  const refreshActiveDetail = useCallback(async () => {
    if (!activeIdRef.current) {
      return;
    }
    const detail = await fetchOrchestratorTaskDetail(activeIdRef.current);
    setTaskDetail(detail.task);
    setEvents(detail.events);
    setArtifacts(detail.artifacts);
    lastSeqRef.current = detail.events.reduce((max, event) => Math.max(max, event.seq), 0);
    setStatus(detail.task.status);
  }, []);

  const refreshPermissions = useCallback(async () => {
    if (!activeIdRef.current) {
      setPermissions([]);
      return;
    }
    setPermissions(await fetchPendingPermissions(activeIdRef.current));
  }, []);

  useEffect(() => {
    setWorkspace((current) => current || orchestratorSettings.defaultWorkspace || "");
  }, [orchestratorSettings.defaultWorkspace]);

  useEffect(() => {
    setTaskMode(mode);
  }, [mode]);

  useEffect(() => {
    setTaskModelName(model.model);
  }, [model.model]);

  useEffect(() => {
    activeIdRef.current = effectiveActiveId ?? null;
  }, [effectiveActiveId]);

  useEffect(() => {
    void refreshTasks().catch((err) => setError(err instanceof Error ? err.message : "加载任务失败"));
  }, [refreshTasks]);

  useEffect(() => {
    if (!effectiveActiveId) {
      setTaskDetail(null);
      setEvents([]);
      setArtifacts([]);
      setPermissions([]);
      lastSeqRef.current = 0;
      return;
    }
    let cancelled = false;
    setError("");
    fetchOrchestratorTaskDetail(effectiveActiveId)
      .then((detail) => {
        if (cancelled) {
          return;
        }
        setTaskDetail(detail.task);
        setEvents(detail.events);
        setArtifacts(detail.artifacts);
        setStatus(detail.task.status);
        setRunning(detail.task.status === "running");
        lastSeqRef.current = detail.events.reduce((max, event) => Math.max(max, event.seq), 0);
      })
      .then(() => refreshPermissions())
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "加载任务详情失败");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [effectiveActiveId, refreshPermissions]);

  useEffect(() => {
    if (!effectiveActiveId) {
      return;
    }
    const unsubscribe = streamOrchestratorTaskEvents({
      taskId: effectiveActiveId,
      afterSeq: lastSeqRef.current,
      onEvent: (event) => {
        lastSeqRef.current = Math.max(lastSeqRef.current, event.seq);
        setEvents((prev) => (prev.some((item) => item.seq === event.seq) ? prev : [...prev, event]));
        if (event.kind === "question" || event.kind === "error" || event.kind === "done") {
          void refreshPermissions().catch(() => undefined);
        }
        if (event.kind === "done" || event.kind === "error") {
          setRunning(false);
          void refreshTasks().catch(() => undefined);
          void refreshActiveDetail().catch(() => undefined);
        } else if (event.kind === "status") {
          setStatus(event.status || event.summary || "running");
        }
      },
      onError: (message) => setError(message)
    });
    return unsubscribe;
  }, [effectiveActiveId, refreshActiveDetail, refreshPermissions, refreshTasks, streamVersion]);

  useEffect(() => {
    eventsRef.current?.scrollTo({ top: eventsRef.current.scrollHeight, behavior: running ? "auto" : "smooth" });
  }, [events, running]);

  async function chooseWorkspace() {
    const selected = await window.amadeusDesktop?.selectFolder?.();
    if (selected) {
      setWorkspace(selected);
      return;
    }
    const manual = window.prompt("输入任务工作目录", workspace);
    if (manual !== null) {
      setWorkspace(manual.trim());
    }
  }

  async function uploadFiles(files: File[]) {
    if (files.length === 0 || uploadBusy) {
      return;
    }
    setUploadBusy(true);
    setError("");
    try {
      const uploaded: UploadedFileInfo[] = [];
      for (const file of files) {
        const response = await uploadWorkspaceFile({ file, device: "host", overwrite: false });
        uploaded.push(response.file);
      }
      setAttachments((prev) => {
        const byPath = new Map(prev.map((item) => [item.path, item]));
        uploaded.forEach((item) => byPath.set(item.path, item));
        return Array.from(byPath.values());
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploadBusy(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  }

  async function sendTask() {
    const prompt = input.trim();
    if (!prompt || running) {
      return;
    }
    const taskAttachments = toChatAttachments(attachments);
    setRunning(true);
    setStatus("connecting");
    setError("");
    try {
      let taskId = effectiveActiveId;
      const taskModel = {
        ...model,
        model: taskModelName.trim() || model.model
      };
      const payload = {
        prompt,
        attachments: taskAttachments,
        workspacePath: workspace.trim() || orchestratorSettings.defaultWorkspace,
        model: taskModel,
        mode: taskMode,
        trustMode
      };
      if (taskId && taskDetail && CONTINUABLE_STATUS.has(taskDetail.status)) {
        await appendOrchestratorTaskMessage(taskId, payload);
      } else {
        const result = await createOrchestratorTask({
          ...payload,
          title: buildConversationTitle(prompt)
        });
        taskId = result.taskId;
        handleSelect(taskId);
        lastSeqRef.current = 0;
      }
      setInput("");
      setAttachments([]);
      setStreamVersion((value) => value + 1);
      await refreshTasks();
    } catch (err) {
      setRunning(false);
      setStatus("error");
      setError(err instanceof Error ? err.message : "任务发送失败");
    }
  }

  async function stopTask() {
    if (!effectiveActiveId) {
      setRunning(false);
      return;
    }
    try {
      await controlOrchestratorTask(effectiveActiveId, "cancel");
      setRunning(false);
      setStatus("cancelled");
      await refreshActiveDetail();
      await refreshTasks();
    } catch (err) {
      setError(err instanceof Error ? err.message : "停止任务失败");
    }
  }

  function removeAttachment(path: string) {
    setAttachments((prev) => prev.filter((item) => item.path !== path));
  }

  function newTaskDraft() {
    handleSelect(null);
    setTaskDetail(null);
    setEvents([]);
    setArtifacts([]);
    setPermissions([]);
    setInput("");
    setAttachments([]);
    setStatus("idle");
    setError("");
    lastSeqRef.current = 0;
  }

  async function decidePermission(permissionId: string, decision: "approve" | "reject") {
    setPermBusy((prev) => ({ ...prev, [permissionId]: true }));
    try {
      if (decision === "approve") {
        await approvePermission(permissionId);
      } else {
        await rejectPermission(permissionId);
      }
      await refreshPermissions();
      setStreamVersion((value) => value + 1);
      await refreshActiveDetail();
    } catch (err) {
      setError(err instanceof Error ? err.message : "权限处理失败");
    } finally {
      setPermBusy((prev) => {
        const next = { ...prev };
        delete next[permissionId];
        return next;
      });
    }
  }

  const messageEvents = events.filter((event) => event.kind === "message");
  const processEvents = events.filter((event) => event.kind !== "message" && event.kind !== "done");
  const finalEvent = [...events].reverse().find((event) => event.kind === "message" && event.role === "assistant" && event.name === "final_answer");
  const doneEvent = [...events].reverse().find((event) => event.kind === "done");

  return (
    <div className="task-workspace">
      <div className="task-workspace-rail">
        <div className="task-workspace-rail-head">
          <strong>任务</strong>
          <button className="text-icon-button" onClick={newTaskDraft} type="button">
            <Plus size={14} />
            新建
          </button>
        </div>
        <div className="task-workspace-list">
          {tasks.length === 0 && <span className="task-workspace-empty-list">暂无历史任务</span>}
          {tasks.map((task) => (
            <button
              className={`task-workspace-task ${effectiveActiveId === task.id ? "is-active" : ""}`}
              key={task.id}
              onClick={() => handleSelect(task.id)}
              type="button"
            >
              <span>{task.title || task.prompt.slice(0, 40)}</span>
              <small>{STATUS_LABELS[task.status] ?? task.status} · {formatHistoryTime(task.updatedAt)}</small>
            </button>
          ))}
        </div>
      </div>

      <div className="task-workspace-main">
        <div className="task-thread" ref={eventsRef}>
          {!taskDetail && messageEvents.length === 0 && (
            <div className="task-workspace-hero">
              <BookOpen size={30} />
              <h3>Work with Amadeus</h3>
              <p>整理资料、分析文件、搜索信息、调用 MCP/Skills，复杂编码时再由 Orchestrator 委托 OpenCode。</p>
            </div>
          )}

          {taskDetail && (
            <div className="task-thread-title">
              <div>
                <strong>{taskDetail.title}</strong>
                <small>{taskDetail.workspacePath || "默认工作区"}</small>
              </div>
              <span className={`orchestrator-task-status-badge is-${taskDetail.status}`}>
                {STATUS_LABELS[taskDetail.status] ?? taskDetail.status}
              </span>
            </div>
          )}

          {messageEvents.map((event) => (
            <article className={`task-thread-message is-${event.role === "user" ? "user" : "assistant"}`} key={`${event.taskId}-${event.seq}`}>
              <div className="task-thread-message-body">
                <div className="task-thread-message-head">
                  <strong>{event.role === "user" ? "你" : "Amadeus Agent"}</strong>
                  <time>{formatHistoryTime(event.timestamp)}</time>
                </div>
                {event.name === "final_answer"
                  ? renderFinalAnswer(event)
                  : renderMarkdownContent(taskMessageText(event))}
                {taskMessageAttachments(event).length > 0 && (
                  <div className="task-attachment-list">
                    {taskMessageAttachments(event).map((attachment) => (
                      <a
                        href={`/api/files/download?path=${encodeURIComponent(attachment.path)}`}
                        key={attachment.path}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <Paperclip size={13} />
                        {attachmentLabel(attachment)}
                      </a>
                    ))}
                  </div>
                )}
              </div>
            </article>
          ))}

          {permissions.length > 0 && (
            <div className="task-permission-stack">
              {permissions.map((permission) => {
                const permPayload = permission.payload;
                return (
                <div className={`orchestrator-permission-card is-${permission.riskLevel}`} key={permission.id}>
                  <div className="orchestrator-permission-card-head">
                    <span className="orchestrator-permission-tool">{permission.toolName}</span>
                    <span className={`orchestrator-permission-risk is-${permission.riskLevel}`}>
                      {permission.riskLevel === "dangerous" ? "高风险" : "需确认"}
                    </span>
                    {permPayload?.autoApproved !== undefined && (
                      <small className="orchestrator-permission-auto">
                        {permPayload.autoApproved ? "自动批准" : "需手动确认"}
                      </small>
                    )}
                  </div>
                  {permPayload?.reason && (
                    <p className="orchestrator-permission-reason">{permPayload.reason}</p>
                  )}
                  {permPayload?.commandPreview && (
                    <code className="orchestrator-permission-command">{permPayload.commandPreview}</code>
                  )}
                  {permission.argumentsPreview && <pre className="orchestrator-permission-args">{permission.argumentsPreview}</pre>}
                  {permPayload?.workspacePath && (
                    <small className="orchestrator-permission-workspace">{permPayload.workspacePath}</small>
                  )}
                  <div className="orchestrator-permission-actions">
                    <button
                      className="text-icon-button is-primary"
                      disabled={permBusy[permission.id]}
                      onClick={() => void decidePermission(permission.id, "approve")}
                      type="button"
                    >
                      <Check size={14} />
                      批准
                    </button>
                    <button
                      className="text-icon-button is-danger"
                      disabled={permBusy[permission.id]}
                      onClick={() => void decidePermission(permission.id, "reject")}
                      type="button"
                    >
                      <X size={14} />
                      拒绝
                    </button>
                    {permBusy[permission.id] && <Loader2 className="phase-spinner" size={14} />}
                  </div>
                </div>
                );
              })}
            </div>
          )}

          {processEvents.length > 0 && (
            <details className="task-process-card" open={activeRunning && !finalEvent}>
              <summary>
                <span>
                  <CircleDashed className={activeRunning ? "phase-spinner" : ""} size={15} />
                  Agent 过程
                </span>
                <small>{processEvents.length} 条事件</small>
                <ChevronDown size={14} />
              </summary>
              <div className="task-process-list">
                {processEvents.slice(-40).map((event) => {
                  const detail = eventDetail(event);
                  const cmdPayload = event.kind === "command" ? event.payload : null;
                  return (
                  <div className={`task-process-event is-${event.kind}`} key={`${event.taskId}-${event.seq}`}>
                    <span className="task-process-icon">{eventIcon(event.kind)}</span>
                    <div>
                      <div className="task-process-head">
                        <strong>{event.name || EVENT_LABELS[event.kind]}</strong>
                        {Boolean(event.payload?.cached) && (
                          <span className="text-xs text-green-600 ml-1">cached</span>
                        )}
                        {Boolean(event.payload?.fromCache) && (
                          <span className="text-xs text-green-600 ml-1">from cache</span>
                        )}
                        <small>{EVENT_LABELS[event.kind]}</small>
                        {cmdPayload?.exitCode !== undefined && (
                          <span className={`command-exit-code ${Number(cmdPayload.exitCode) === 0 ? "ok" : "err"}`}>
                            exit {String(cmdPayload.exitCode)}
                          </span>
                        )}
                      </div>
                      {event.summary && <p>{event.summary}</p>}
                      {cmdPayload?.command != null && String(cmdPayload.command).length > 0 && (
                        <code className="command-preview">{String(cmdPayload.command)}</code>
                      )}
                      {detail && <pre>{detail}</pre>}
                    </div>
                  </div>
                  );
                })}
              </div>
            </details>
          )}

          {doneEvent && (
            <div className="task-done-card">
              <div className={`task-process-event is-${doneEvent.kind}`}>
                <span className="task-process-icon">{eventIcon(doneEvent.kind)}</span>
                <div>
                  <div className="task-process-head">
                    <strong>{doneEvent.name || EVENT_LABELS[doneEvent.kind]}</strong>
                    {Boolean(doneEvent.payload?.cacheStats) && (() => {
                      const cs = doneEvent.payload!.cacheStats as Record<string, number>;
                      return (
                        <span className="text-xs text-gray-500 ml-2">
                          缓存 {Number(cs.hits)} 命中 / {Number(cs.misses)} 未命中
                        </span>
                      );
                    })()}
                  </div>
                  {doneEvent.summary && <p>{doneEvent.summary}</p>}
                </div>
              </div>
            </div>
          )}

          {artifacts.length > 0 && (
            <div className="task-artifact-card">
              <div className="task-artifact-head">
                <FileText size={14} />
                <span>产物</span>
              </div>
              {artifacts.map((artifact) => (
                <a href={downloadArtifactUrl(artifact.id)} key={artifact.id} target="_blank" rel="noreferrer">
                  <Download size={14} />
                  <span>{artifact.name}</span>
                  <small>{artifact.kind}</small>
                </a>
              ))}
            </div>
          )}
        </div>

        {error && <div className="task-workspace-error">{error}</div>}

        <div className="task-composer">
          <input
            ref={fileInputRef}
            className="upload-native-input"
            type="file"
            multiple
            accept={ACCEPTED_UPLOAD_TYPES}
            onChange={(event) => void uploadFiles(Array.from(event.target.files ?? []))}
          />
          {attachments.length > 0 && (
            <div className="task-composer-attachments">
              {attachments.map((attachment) => (
                <span key={attachment.path}>
                  <Paperclip size={13} />
                  {attachment.originalFilename || attachment.filename}
                  <button onClick={() => removeAttachment(attachment.path)} type="button" aria-label="移除附件">
                    <Trash2 size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                void sendTask();
              }
            }}
            placeholder={effectiveActiveId ? "继续和 Agent 协作" : "帮你整理文档、查找代码、上网搜索、调用工具完成复杂任务。"}
            rows={4}
          />
          <div className="task-composer-footer">
            <div className="task-composer-controls">
              <button className="round-tool" disabled={uploadBusy} onClick={() => fileInputRef.current?.click()} type="button" title="上传附件">
                <Paperclip size={16} />
              </button>
              <button className="text-icon-button" onClick={() => void chooseWorkspace()} type="button">
                <FolderOpen size={15} />
                {workspace.trim() ? "工作目录" : "选择目录"}
              </button>
              <label className={`task-trust-toggle ${trustMode ? "is-active" : ""}`}>
                <input checked={trustMode} onChange={(event) => setTrustMode(event.target.checked)} type="checkbox" />
                信任本次任务
              </label>
              <label className="task-model-control">
                <span>模型</span>
                <input
                  value={taskModelName}
                  onChange={(event) => setTaskModelName(event.target.value)}
                  placeholder={model.model || "model"}
                />
              </label>
              <select
                className="task-mode-select"
                value={taskMode}
                onChange={(event) => setTaskMode(event.target.value as ChatMode)}
              >
                <option value="fast">快速</option>
                <option value="thinking">思考</option>
              </select>
            </div>
            {running ? (
              <button className="send-button" onClick={() => void stopTask()} type="button" aria-label="停止任务">
                <Square size={17} />
              </button>
            ) : (
              <button className="send-button" disabled={!canSend} onClick={() => void sendTask()} type="button" aria-label="发送任务">
                <Send size={17} />
              </button>
            )}
          </div>
          {workspace.trim() && <div className="task-workspace-path">{workspace.trim()}</div>}
          <div className={`code-task-status ${running ? "is-live" : ""}`}>{uploadBusy ? "upload" : status}</div>
        </div>
      </div>
    </div>
  );
}

function renderFinalAnswer(event: OrchestratorTaskEvent) {
  const payload = finalAnswerPayload(event);
  const text = typeof payload.text === "string" && payload.text.trim() ? payload.text : taskMessageText(event);
  return <div className="task-final-answer">{renderMarkdownContent(text)}</div>;
}
