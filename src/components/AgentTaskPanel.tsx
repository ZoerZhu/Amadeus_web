import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  Boxes,
  Check,
  CheckCircle2,
  Circle,
  Download,
  FileText,
  Globe,
  HelpCircle,
  ListChecks,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  Shield,
  Square,
  TriangleAlert,
  Wrench,
  X
} from "lucide-react";
import {
  approvePermission,
  controlAgentTask,
  downloadArtifactUrl,
  fetchAgentTaskDetail,
  fetchAgentTasks,
  fetchPendingPermissions,
  rejectPermission,
  streamAgentTaskEvents
} from "../api";
import type {
  AgentArtifact,
  AgentEventKind,
  AgentTaskBudget,
  AgentTaskControlAction,
  AgentTaskEvent,
  AgentTaskStatus,
  AgentTaskSummary,
  PermissionRequest,
  TaskLedger
} from "../types";

type AgentTaskPanelProps = {
  conversationId?: string | null;
  selectedTaskId?: string | null;
  onSelectTask?: (taskId: string | null) => void;
};

const STATUS_LABELS: Record<AgentTaskStatus, string> = {
  created: "已创建",
  running: "运行中",
  paused: "已暂停",
  cancelled: "已取消",
  done: "已完成",
  failed: "失败",
  rolled_back: "已回滚"
};

const EVENT_KIND_LABELS: Record<AgentEventKind, string> = {
  status: "状态",
  plan: "计划",
  step: "步骤",
  tool: "工具",
  mcp: "MCP",
  browser: "浏览器",
  artifact: "产物",
  question: "提问",
  sampling: "采样",
  error: "错误",
  done: "完成"
};

const EVENT_GROUP_ORDER: AgentEventKind[] = ["plan", "step", "tool", "mcp", "browser", "artifact", "error"];

function isBudget(value: unknown): value is AgentTaskBudget {
  return typeof value === "object" && value !== null && "maxRounds" in value && "rounds" in value;
}

function normalizeBudget(value: unknown): AgentTaskBudget | null {
  if (!isBudget(value)) {
    return null;
  }
  return value as AgentTaskBudget;
}

function isTaskLedger(value: unknown): value is TaskLedger {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    "currentStepIndex" in record ||
    "completedSteps" in record ||
    "openQuestions" in record ||
    "risks" in record
  );
}

function extractLedger(event: AgentTaskEvent): TaskLedger | null {
  const payload = event.payload;
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const ledgerValue =
    (payload as Record<string, unknown>).ledger ??
    (payload as Record<string, unknown>).taskLedger;
  if (isTaskLedger(ledgerValue)) {
    return ledgerValue as TaskLedger;
  }
  // Some step events may carry ledger-like fields directly in the payload
  if (isTaskLedger(payload)) {
    return payload as TaskLedger;
  }
  return null;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string");
}

function asNumberArray(value: unknown): number[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => (typeof item === "number" ? item : Number(item)))
    .filter((item) => Number.isFinite(item));
}

function budgetPercent(used: number, max: number): number {
  if (max <= 0) {
    return 0;
  }
  return Math.min(100, Math.round((used / max) * 100));
}

function formatElapsed(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return "0s";
  }
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${secs}s`;
  }
  return `${secs}s`;
}

function formatTaskTime(value?: string | null): string {
  if (!value) {
    return "";
  }
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

function eventIcon(kind: AgentEventKind) {
  switch (kind) {
    case "plan":
      return <FileText size={14} />;
    case "step":
      return <Play size={14} />;
    case "tool":
      return <Wrench size={14} />;
    case "mcp":
      return <Boxes size={14} />;
    case "browser":
      return <Globe size={14} />;
    case "artifact":
      return <FileText size={14} />;
    case "error":
      return <AlertTriangle size={14} />;
    case "done":
      return <Play size={14} />;
    default:
      return <FileText size={14} />;
  }
}

export function AgentTaskPanel({
  conversationId,
  selectedTaskId,
  onSelectTask
}: AgentTaskPanelProps) {
  const [tasks, setTasks] = useState<AgentTaskSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(selectedTaskId ?? null);
  const [events, setEvents] = useState<AgentTaskEvent[]>([]);
  const [artifacts, setArtifacts] = useState<AgentArtifact[]>([]);
  const [taskDetail, setTaskDetail] = useState<AgentTaskSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [controlBusy, setControlBusy] = useState(false);
  const [error, setError] = useState("");
  const [permissions, setPermissions] = useState<PermissionRequest[]>([]);
  const [permBusy, setPermBusy] = useState<Record<string, boolean>>({});
  const eventsRef = useRef<HTMLDivElement | null>(null);
  const lastSeqRef = useRef(0);

  const effectiveActiveId = selectedTaskId ?? activeId;

  const handleSelect = useCallback(
    (id: string | null) => {
      if (onSelectTask) {
        onSelectTask(id);
      } else {
        setActiveId(id);
      }
    },
    [onSelectTask]
  );

  // Load task list
  const refreshTasks = useCallback(async () => {
    try {
      const list = await fetchAgentTasks(conversationId ?? undefined);
      setTasks(list);
      setError("");
      if (list.length > 0 && !effectiveActiveId) {
        handleSelect(list[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载任务失败");
    }
  }, [conversationId, effectiveActiveId, handleSelect]);

  useEffect(() => {
    void refreshTasks();
  }, [refreshTasks]);

  // Load task detail (events + artifacts) when selection changes
  useEffect(() => {
    if (!effectiveActiveId) {
      setEvents([]);
      setArtifacts([]);
      setTaskDetail(null);
      lastSeqRef.current = 0;
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    fetchAgentTaskDetail(effectiveActiveId)
      .then((detail) => {
        if (cancelled) {
          return;
        }
        setTaskDetail(detail.task);
        setEvents(detail.events);
        setArtifacts(detail.artifacts);
        lastSeqRef.current = detail.events.reduce((max, event) => Math.max(max, event.seq), 0);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "加载任务详情失败");
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [effectiveActiveId]);

  // Subscribe to live events for the selected task
  useEffect(() => {
    if (!effectiveActiveId) {
      return;
    }
    const unsubscribe = streamAgentTaskEvents({
      taskId: effectiveActiveId,
      afterSeq: lastSeqRef.current,
      onEvent: (event) => {
        lastSeqRef.current = Math.max(lastSeqRef.current, event.seq);
        setEvents((prev) => {
          if (prev.some((item) => item.seq === event.seq)) {
            return prev;
          }
          return [...prev, event];
        });
        if (event.kind === "status" || event.kind === "done" || event.kind === "error") {
          void refreshTasks();
          if (effectiveActiveId) {
            fetchAgentTaskDetail(effectiveActiveId)
              .then((detail) => {
                setTaskDetail(detail.task);
                setArtifacts(detail.artifacts);
              })
              .catch(() => undefined);
          }
        }
      },
      onError: (message) => {
        setError(message);
      }
    });
    return unsubscribe;
  }, [effectiveActiveId, refreshTasks]);

  // Auto-scroll events to bottom on new events
  useEffect(() => {
    if (eventsRef.current) {
      eventsRef.current.scrollTop = eventsRef.current.scrollHeight;
    }
  }, [events]);

  const groupedEvents = useMemo(() => {
    const groups = new Map<AgentEventKind, AgentTaskEvent[]>();
    for (const event of events) {
      const list = groups.get(event.kind) ?? [];
      list.push(event);
      groups.set(event.kind, list);
    }
    return EVENT_GROUP_ORDER.filter((kind) => groups.has(kind)).map((kind) => ({
      kind,
      label: EVENT_KIND_LABELS[kind],
      events: groups.get(kind) ?? []
    }));
  }, [events]);

  const budget = taskDetail ? normalizeBudget(taskDetail.budget) : null;
  const status = taskDetail?.status;

  // Derive the latest ledger snapshot from events (most recent wins)
  const ledger = useMemo<TaskLedger | null>(() => {
    let latest: TaskLedger | null = null;
    let latestSeq = -1;
    for (const event of events) {
      const extracted = extractLedger(event);
      if (extracted && event.seq > latestSeq) {
        latest = extracted;
        latestSeq = event.seq;
      }
    }
    return latest;
  }, [events]);

  const stepEvents = useMemo(() => events.filter((event) => event.kind === "step"), [events]);
  const totalStepCount = stepEvents.length;
  const completedStepCount = ledger?.completedSteps?.length ?? 0;
  const currentStepIndex = ledger?.currentStepIndex;

  function handleControl(action: AgentTaskControlAction) {
    if (!effectiveActiveId) {
      return;
    }
    void (async () => {
      setControlBusy(true);
      setError("");
      try {
        await controlAgentTask(effectiveActiveId, action);
        await refreshTasks();
        if (effectiveActiveId) {
          const detail = await fetchAgentTaskDetail(effectiveActiveId);
          setTaskDetail(detail.task);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : `${action} 失败`);
      } finally {
        setControlBusy(false);
      }
    })();
  }

  // Fetch pending permissions for the active task
  const refreshPermissions = useCallback(async () => {
    if (!effectiveActiveId) {
      setPermissions([]);
      return;
    }
    try {
      const perms = await fetchPendingPermissions(effectiveActiveId);
      setPermissions(perms);
    } catch {
      // Silent fail — permissions are best-effort
    }
  }, [effectiveActiveId]);

  // Fetch permissions when task changes
  useEffect(() => {
    void refreshPermissions();
  }, [refreshPermissions]);

  // Also refresh permissions when a question event arrives
  useEffect(() => {
    if (!effectiveActiveId) {
      return;
    }
    const unsubscribe = streamAgentTaskEvents({
      taskId: effectiveActiveId,
      afterSeq: lastSeqRef.current,
      onEvent: (event) => {
        if (event.kind === "question" && event.name === "tool_permission") {
          void refreshPermissions();
        }
      }
    });
    return unsubscribe;
  }, [effectiveActiveId, refreshPermissions]);

  function handlePermissionDecision(permissionId: string, decision: "approve" | "reject") {
    void (async () => {
      setPermBusy((prev) => ({ ...prev, [permissionId]: true }));
      try {
        if (decision === "approve") {
          await approvePermission(permissionId);
        } else {
          await rejectPermission(permissionId);
        }
        await refreshPermissions();
      } catch (err) {
        setError(err instanceof Error ? err.message : `${decision} 失败`);
      } finally {
        setPermBusy((prev) => {
          const next = { ...prev };
          delete next[permissionId];
          return next;
        });
      }
    })();
  }

  return (
    <div className="agent-task-panel">
      <div className="agent-task-list-head">
        <strong>Agent 任务</strong>
        <button className="text-icon-button" onClick={() => void refreshTasks()} type="button">
          刷新
        </button>
      </div>
      <div className="agent-task-selector">
        {tasks.length === 0 && !loading && (
          <div className="empty-state">
            <Boxes size={24} />
            <span>暂无 Agent 任务</span>
          </div>
        )}
        {tasks.map((task) => (
          <button
            key={task.id}
            className={`agent-task-item ${effectiveActiveId === task.id ? "is-active" : ""}`}
            onClick={() => handleSelect(task.id)}
            type="button"
          >
            <span className="agent-task-item-title">{task.title || task.prompt.slice(0, 40)}</span>
            <span className={`agent-task-status-badge is-${task.status}`}>
              {STATUS_LABELS[task.status] ?? task.status}
            </span>
          </button>
        ))}
      </div>

      {error && <div className="agent-task-error">{error}</div>}

      {taskDetail && (
        <div className="agent-task-detail">
          <div className="agent-task-detail-head">
            <div className="agent-task-detail-title">
              <strong>{taskDetail.title}</strong>
              <span className={`agent-task-status-badge is-${taskDetail.status}`}>
                {STATUS_LABELS[taskDetail.status] ?? taskDetail.status}
              </span>
            </div>
            <small>{formatTaskTime(taskDetail.createdAt)}</small>
          </div>

          {taskDetail.error && (
            <div className="agent-task-error-detail">{taskDetail.error}</div>
          )}

          {budget && (
            <div className="agent-budget-grid">
              <div className="agent-budget-item">
                <div className="agent-budget-label">
                  <span>轮次</span>
                  <small>
                    {budget.rounds}/{budget.maxRounds}
                  </small>
                </div>
                <div className="agent-budget-bar">
                  <div
                    className="agent-budget-fill"
                    style={{ width: `${budgetPercent(budget.rounds, budget.maxRounds)}%` }}
                  />
                </div>
              </div>
              <div className="agent-budget-item">
                <div className="agent-budget-label">
                  <span>工具调用</span>
                  <small>
                    {budget.toolCalls}/{budget.maxToolCalls}
                  </small>
                </div>
                <div className="agent-budget-bar">
                  <div
                    className="agent-budget-fill"
                    style={{ width: `${budgetPercent(budget.toolCalls, budget.maxToolCalls)}%` }}
                  />
                </div>
              </div>
              <div className="agent-budget-item">
                <div className="agent-budget-label">
                  <span>已用时</span>
                  <small>{formatElapsed(budget.elapsedSeconds)}</small>
                </div>
                <div className="agent-budget-bar">
                  <div
                    className="agent-budget-fill"
                    style={{ width: `${budgetPercent(budget.elapsedSeconds, budget.maxRuntimeSeconds)}%` }}
                  />
                </div>
              </div>
            </div>
          )}

          {ledger && (
            <div className="task-ledger">
              <div className="task-ledger-head">
                <ListChecks size={14} />
                <span>任务进度</span>
              </div>
              {(typeof currentStepIndex === "number" || totalStepCount > 0) && (
                <div className="task-step-progress">
                  <div className="task-step-progress-head">
                    <span>
                      当前步骤：
                      <strong>
                        {typeof currentStepIndex === "number"
                          ? currentStepIndex + 1
                          : "-"}
                      </strong>
                      {totalStepCount > 0 && (
                        <>
                          {" "}/ {totalStepCount}
                        </>
                      )}
                    </span>
                    <small>
                      已完成 {completedStepCount}
                      {totalStepCount > 0 ? ` / ${totalStepCount}` : ""}
                    </small>
                  </div>
                  <div className="agent-budget-bar">
                    <div
                      className="agent-budget-fill"
                      style={{
                        width: `${budgetPercent(
                          completedStepCount,
                          totalStepCount > 0 ? totalStepCount : 1
                        )}%`
                      }}
                    />
                  </div>
                  {totalStepCount > 0 && (
                    <div className="task-step-list">
                      {stepEvents.map((event, index) => {
                        const completedSet = new Set(
                          asNumberArray(ledger.completedSteps)
                        );
                        const isCompleted =
                          completedSet.has(index) ||
                          completedSet.has(event.seq) ||
                          event.status === "completed" ||
                          event.status === "done";
                        const isCurrent =
                          typeof currentStepIndex === "number" &&
                          currentStepIndex === index;
                        return (
                          <div
                            className={`task-step-item ${
                              isCompleted ? "is-completed" : ""
                            } ${isCurrent ? "is-current" : ""}`}
                            key={`${event.taskId}-${event.seq}`}
                          >
                            {isCompleted ? (
                              <CheckCircle2 size={13} />
                            ) : isCurrent ? (
                              <Circle size={13} />
                            ) : (
                              <Circle size={13} />
                            )}
                            <span>{event.name || event.summary || `步骤 ${index + 1}`}</span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
              {asStringArray(ledger.openQuestions).length > 0 && (
                <div className="task-ledger-block">
                  <div className="task-ledger-block-head">
                    <HelpCircle size={13} />
                    <span>未解决问题</span>
                  </div>
                  <ul className="task-ledger-list">
                    {asStringArray(ledger.openQuestions).map((question, index) => (
                      <li key={`q-${index}`}>{question}</li>
                    ))}
                  </ul>
                </div>
              )}
              {asStringArray(ledger.risks).length > 0 && (
                <div className="task-ledger-block">
                  <div className="task-ledger-block-head is-risk">
                    <TriangleAlert size={13} />
                    <span>风险</span>
                  </div>
                  <ul className="task-ledger-list">
                    {asStringArray(ledger.risks).map((risk, index) => (
                      <li key={`r-${index}`}>{risk}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          <div className="agent-task-controls">
            {status === "running" && (
              <button
                className="text-icon-button"
                disabled={controlBusy}
                onClick={() => handleControl("pause")}
                type="button"
              >
                <Pause size={15} />
                暂停
              </button>
            )}
            {status === "paused" && (
              <button
                className="text-icon-button"
                disabled={controlBusy}
                onClick={() => handleControl("resume")}
                type="button"
              >
                <Play size={15} />
                继续
              </button>
            )}
            {(status === "running" || status === "paused") && (
              <button
                className="text-icon-button"
                disabled={controlBusy}
                onClick={() => handleControl("cancel")}
                type="button"
              >
                <Square size={15} />
                取消
              </button>
            )}
            {(status === "done" || status === "failed") && taskDetail && (
              <button
                className="text-icon-button"
                disabled={controlBusy}
                onClick={() => handleControl("rollback")}
                type="button"
              >
                <RotateCcw size={15} />
                回滚
              </button>
            )}
            {controlBusy && <Loader2 className="phase-spinner" size={14} />}
          </div>

          {permissions.length > 0 && (
            <div className="agent-permissions">
              <div className="agent-permissions-head">
                <Shield size={14} />
                <span>待确认操作（{permissions.length}）</span>
              </div>
              {permissions.map((perm) => (
                <div className={`agent-permission-card is-${perm.riskLevel}`} key={perm.id}>
                  <div className="agent-permission-card-head">
                    <span className="agent-permission-tool">{perm.toolName}</span>
                    <span className={`agent-permission-risk is-${perm.riskLevel}`}>
                      {perm.riskLevel === "dangerous" ? "高风险" : "需确认"}
                    </span>
                  </div>
                  {perm.argumentsPreview && (
                    <pre className="agent-permission-args">{perm.argumentsPreview}</pre>
                  )}
                  {perm.reason && (
                    <div className="agent-permission-reason">
                      <AlertTriangle size={12} />
                      <span>{perm.reason}</span>
                    </div>
                  )}
                  <div className="agent-permission-actions">
                    <button
                      className="text-icon-button is-primary"
                      disabled={permBusy[perm.id]}
                      onClick={() => handlePermissionDecision(perm.id, "approve")}
                      type="button"
                    >
                      <Check size={14} />
                      批准
                    </button>
                    <button
                      className="text-icon-button is-danger"
                      disabled={permBusy[perm.id]}
                      onClick={() => handlePermissionDecision(perm.id, "reject")}
                      type="button"
                    >
                      <X size={14} />
                      拒绝
                    </button>
                    {permBusy[perm.id] && <Loader2 className="phase-spinner" size={14} />}
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="agent-events" ref={eventsRef}>
            {groupedEvents.length === 0 && !loading && (
              <div className="agent-events-empty">暂无事件</div>
            )}
            {groupedEvents.map((group) => (
              <details className="agent-event-group" key={group.kind} open>
                <summary>
                  <span className="agent-event-group-title">
                    {eventIcon(group.kind)}
                    {group.label}
                    <small>{group.events.length}</small>
                  </span>
                </summary>
                <div className="agent-event-group-body">
                  {group.events.map((event) => (
                    <div className={`agent-event is-${event.kind}`} key={`${event.taskId}-${event.seq}`}>
                      <div className="agent-event-head">
                        <span>{event.name || event.kind}</span>
                        <time>{formatTaskTime(event.timestamp)}</time>
                      </div>
                      {event.summary && <p>{event.summary}</p>}
                      {event.payload && Object.keys(event.payload).length > 0 && (
                        <pre>{JSON.stringify(event.payload, null, 2)}</pre>
                      )}
                    </div>
                  ))}
                </div>
              </details>
            ))}
          </div>

          {artifacts.length > 0 && (
            <div className="agent-artifacts">
              <div className="agent-artifacts-head">产物（{artifacts.length}）</div>
              {artifacts.map((artifact) => (
                <a
                  className="agent-artifact-item"
                  key={artifact.id}
                  href={downloadArtifactUrl(artifact.id)}
                  download={artifact.name}
                  target="_blank"
                  rel="noreferrer"
                >
                  <Download size={14} />
                  <span className="agent-artifact-name">{artifact.name}</span>
                  <small>{artifact.kind}</small>
                </a>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
