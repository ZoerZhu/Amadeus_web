import type { ReactNode } from "react";
import { Fragment, useEffect, useState } from "react";
import {
  BookOpenText,
  Bot,
  CheckCircle2,
  CircleHelp,
  CircleDashed,
  Lock,
  Play,
  UserRound,
  Wrench,
  XCircle
} from "lucide-react";
import { buildCodeTaskPhases, formatMessageTime, limitCodeTaskText, renderMarkdownContent } from "../app/appSupport";
import type { CodeTaskMessage, CodeTaskPhase, CodeTaskQuestionData } from "../app/appSupport";

const CODE_TASK_SUMMARY_OUTPUT_LIMIT = 24000;

function codeTaskIcon(message: CodeTaskMessage) {
  if (message.kind === "user") {
    return <UserRound size={14} />;
  }
  if (message.kind === "assistant") {
    return <Bot size={14} />;
  }
  if (message.kind === "tool") {
    return <Wrench size={14} />;
  }
  if (message.kind === "command") {
    return <Play size={14} />;
  }
  if (message.kind === "file") {
    return <BookOpenText size={14} />;
  }
  if (message.kind === "permission") {
    return <Lock size={14} />;
  }
  if (message.kind === "question") {
    return <CircleHelp size={14} />;
  }
  if (message.kind === "error") {
    return <XCircle size={14} />;
  }
  if (message.kind === "done") {
    return <CheckCircle2 size={14} />;
  }
  return <CircleDashed size={14} />;
}

function codeTaskPhaseIcon(phase: CodeTaskPhase) {
  if (phase.status === "running") {
    return <CircleDashed className="phase-spinner" size={15} />;
  }
  if (phase.status === "error") {
    return <XCircle size={15} />;
  }
  return <CheckCircle2 size={15} />;
}

export function CodeTaskMessageItem({ message }: { message: CodeTaskMessage }) {
  return (
    <article className={`code-task-message is-${message.kind}`}>
      <span className="code-task-message-icon">{codeTaskIcon(message)}</span>
      <div className="code-task-message-body">
        <div className="code-task-message-head">
          <strong>{message.title || (message.kind === "assistant" ? "OpenCode" : "事件")}</strong>
          <time dateTime={message.createdAt}>{formatMessageTime(message.createdAt)}</time>
        </div>
        <p>{message.text}</p>
        {message.detail && <pre>{message.detail}</pre>}
      </div>
    </article>
  );
}

function CodeTaskPhaseMessage({ message }: { message: CodeTaskMessage }) {
  const textLimit = message.kind === "assistant" ? 4200 : 1400;
  return (
    <div className={`code-task-phase-event is-${message.kind}`}>
      <span className="code-task-phase-event-icon">{codeTaskIcon(message)}</span>
      <div className="code-task-phase-event-body">
        <div className="code-task-phase-event-head">
          <strong>{message.title || (message.kind === "assistant" ? "OpenCode" : "事件")}</strong>
          <time dateTime={message.createdAt}>{formatMessageTime(message.createdAt)}</time>
        </div>
        <p>{limitCodeTaskText(message.text, textLimit)}</p>
        {message.detail && <pre>{limitCodeTaskText(message.detail, 2200)}</pre>}
      </div>
    </div>
  );
}

export function CodeTaskPhaseItem({ phase }: { phase: CodeTaskPhase }) {
  const hiddenCount = Math.max(0, phase.messages.length - 10);
  const visiblePhaseMessages = phase.messages.slice(-10);
  return (
    <details
      className={`code-task-phase is-${phase.status}`}
      open={phase.status === "running" || phase.id === "result"}
    >
      <summary>
        <span className="code-task-phase-icon">{codeTaskPhaseIcon(phase)}</span>
        <span className="code-task-phase-title">{phase.title}</span>
        <small>{phase.status === "running" ? "进行中" : phase.status === "error" ? "异常" : "已完成"}</small>
      </summary>
      <div className="code-task-phase-body">
        {hiddenCount > 0 && <span className="code-task-phase-hidden">已折叠 {hiddenCount} 条较早事件</span>}
        {visiblePhaseMessages.map((message) => (
          <CodeTaskPhaseMessage key={message.id} message={message} />
        ))}
      </div>
    </details>
  );
}

function taskBubbleRole(message: CodeTaskMessage): "user" | "assistant" {
  return message.kind === "user" ? "user" : "assistant";
}

function taskBubbleText(message: CodeTaskMessage): string {
  if (message.kind === "error") {
    return `任务执行失败：${message.text}`;
  }
  return message.text;
}

function taskProcessPhases(phases: CodeTaskPhase[]): CodeTaskPhase[] {
  return phases
    .filter((phase) => phase.id !== "request")
    .map((phase) => ({
      ...phase,
      messages: phase.messages.filter((message) => !isFinalSummaryMessage(message))
    }))
    .filter((phase) => phase.messages.length > 0);
}

function taskPhaseStatusLabel(status: CodeTaskPhase["status"]): string {
  if (status === "running") {
    return "进行中";
  }
  if (status === "error") {
    return "异常";
  }
  return "完成";
}

function taskTraceClass(phase: CodeTaskPhase): string {
  if (phase.status === "running") {
    return "started";
  }
  if (phase.status === "error") {
    return "failed";
  }
  return "completed";
}

function phaseMessageTitle(message: CodeTaskMessage): string {
  return message.title || (message.kind === "status" ? "状态" : message.kind === "done" ? "结果" : "事件");
}

function isFinalSummaryMessage(message: CodeTaskMessage): boolean {
  return message.kind === "done" && message.title === "任务总结";
}

function isPendingQuestionMessage(message: CodeTaskMessage): boolean {
  return message.kind === "question" && Boolean(message.question) && !message.question?.answered && !message.question?.rejected;
}

type CodeTaskTurn = {
  id: string;
  messages: CodeTaskMessage[];
  running: boolean;
};

function buildTaskTurns(messages: CodeTaskMessage[], running: boolean): CodeTaskTurn[] {
  const turns: CodeTaskTurn[] = [];
  let current: CodeTaskTurn | null = null;

  for (const message of messages) {
    if (message.kind === "user" || current === null) {
      current = {
        id: message.kind === "user" ? message.id : `task-orphan-${message.id}`,
        messages: [message],
        running: false
      };
      turns.push(current);
      continue;
    }
    current.messages.push(message);
  }

  return turns.map((turn, index) => ({
    ...turn,
    running: running && index === turns.length - 1
  }));
}

function taskTurnResponseMessages(messages: CodeTaskMessage[], running: boolean): CodeTaskMessage[] {
  const responses = messages.filter(
    (message) => message.kind === "error" || isFinalSummaryMessage(message)
  );
  if (running || responses.length > 0) {
    return responses;
  }
  const fallback = [...messages].reverse().find((message) => message.kind === "done");
  return fallback ? [fallback] : responses;
}

function formatTaskDuration(messages: CodeTaskMessage[]): string {
  if (messages.length < 2) {
    return "";
  }
  const start = new Date(messages[0].createdAt).getTime();
  const end = new Date(messages[messages.length - 1].createdAt).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
    return "";
  }
  const totalSeconds = Math.max(1, Math.round((end - start) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}h ${minutes}m ${seconds}s`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds}s`;
  }
  return `${seconds}s`;
}

function answerSummary(question: CodeTaskQuestionData): string {
  if (question.rejected) {
    return "已拒绝该选择请求。";
  }
  if (!question.answered) {
    return "";
  }
  const answers = question.answers ?? [];
  const text = answers
    .map((items, index) => {
      const header = question.questions[index]?.header || `问题 ${index + 1}`;
      return `${header}: ${items.join(", ") || "无回答"}`;
    })
    .join("\n");
  return text ? `已提交：\n${text}` : "已提交回答。";
}

export type CodeTaskQuestionHandler = (
  message: CodeTaskMessage,
  answers: string[][],
  reject?: boolean
) => void | Promise<void>;

type CodeTaskQuestionComposerProps = {
  message: CodeTaskMessage;
  busy?: boolean;
  onAnswerQuestion?: CodeTaskQuestionHandler;
  surface?: "inline" | "composer";
};

export function CodeTaskQuestionComposer({
  message,
  busy,
  onAnswerQuestion,
  surface = "inline"
}: CodeTaskQuestionComposerProps) {
  const question = message.question;
  const [selected, setSelected] = useState<Record<number, string[]>>({});
  const [customAnswers, setCustomAnswers] = useState<Record<number, string>>({});
  useEffect(() => {
    setSelected({});
    setCustomAnswers({});
  }, [question?.requestId]);
  if (!question) {
    return null;
  }
  const locked = busy || question.answered || question.rejected || !onAnswerQuestion;
  const answers = question.questions.map((item, index) => {
    const chosen = selected[index] ?? [];
    const custom = customAnswers[index]?.trim();
    if (custom && !item.multiple) {
      return [custom];
    }
    return custom ? [...chosen, custom] : chosen;
  });
  const canSubmit = !locked && answers.length > 0 && answers.every((items) => items.length > 0);
  const submittedText = answerSummary(question);

  function toggleOption(index: number, label: string, multiple?: boolean) {
    if (locked) {
      return;
    }
    setSelected((prev) => {
      const current = prev[index] ?? [];
      if (!multiple) {
        return { ...prev, [index]: current.includes(label) ? [] : [label] };
      }
      return {
        ...prev,
        [index]: current.includes(label)
          ? current.filter((item) => item !== label)
          : [...current, label]
      };
    });
  }

  return (
    <section className={`code-task-question-card is-${surface}`}>
      {question.questions.map((item, index) => {
        const current = selected[index] ?? [];
        return (
          <div className="code-task-question-item" key={`${question.requestId}-${index}`}>
            <div className="code-task-question-head">
              <strong>{item.header || `问题 ${index + 1}`}</strong>
              {item.multiple && <small>可多选</small>}
            </div>
            <p>{item.question}</p>
            {item.options.length > 0 && (
              <div className="code-task-question-options">
                {item.options.map((option) => {
                  const active = current.includes(option.label);
                  return (
                    <button
                      className={active ? "is-selected" : ""}
                      disabled={locked}
                      key={option.label}
                      onClick={() => toggleOption(index, option.label, item.multiple)}
                      type="button"
                    >
                      <span>{option.label}</span>
                      {option.description && <small>{option.description}</small>}
                    </button>
                  );
                })}
              </div>
            )}
            <input
              className="code-task-question-custom"
              disabled={locked}
              onChange={(event) => setCustomAnswers((prev) => ({ ...prev, [index]: event.target.value }))}
              placeholder="自定义输入"
              value={customAnswers[index] ?? ""}
            />
          </div>
        );
      })}
      {submittedText && <pre className="code-task-question-submitted">{submittedText}</pre>}
      {!question.answered && !question.rejected && (
        <div className="code-task-question-actions">
          <button
            className="text-icon-button"
            disabled={!canSubmit}
            onClick={() => void onAnswerQuestion?.(message, answers, false)}
            type="button"
          >
            提交选择
          </button>
          <button
            className="text-icon-button is-muted"
            disabled={locked}
            onClick={() => void onAnswerQuestion?.(message, [], true)}
            type="button"
          >
            拒绝
          </button>
          {busy && <span>提交中...</span>}
        </div>
      )}
    </section>
  );
}

function CodeTaskProcessDetails({
  phases
}: {
  phases: CodeTaskPhase[];
}) {
  const processPhases = taskProcessPhases(phases);
  if (processPhases.length === 0) {
    return null;
  }
  const eventCount = processPhases.reduce((total, phase) => total + phase.messages.length, 0);
  return (
    <div className="code-task-processed-body">
      <p className="thinking-text">OpenCode 过程已归并为 {processPhases.length} 个阶段，记录 {eventCount} 条事件。</p>
      <div className="tool-trace-list code-task-tool-trace-list" aria-label="OpenCode 工具调用和任务过程">
        {processPhases.map((phase) => (
          <div className={`tool-trace-item is-${taskTraceClass(phase)}`} key={phase.id}>
            <span className="tool-trace-icon">{codeTaskPhaseIcon(phase)}</span>
            <div className="tool-trace-body">
              <div className="tool-trace-head">
                <span>{phase.title}</span>
                <small>{taskPhaseStatusLabel(phase.status)}</small>
              </div>
              {phase.messages.slice(-6).map((message) => (
                <div className="code-task-trace-event" key={message.id}>
                  <div className="code-task-trace-event-head">
                    <span>{phaseMessageTitle(message)}</span>
                    <time dateTime={message.createdAt}>{formatMessageTime(message.createdAt)}</time>
                  </div>
                  <p>{limitCodeTaskText(message.text, message.kind === "assistant" ? 1800 : 900)}</p>
                  {message.question && (
                    <p className="code-task-question-note">
                      {message.question.answered || message.question.rejected
                        ? answerSummary(message.question)
                        : "选择请求已发送到下方输入区。"}
                    </p>
                  )}
                  {message.detail && (
                    <details className="tool-result-details code-task-trace-details">
                      <summary>
                        <span>详情</span>
                        <small>展开</small>
                      </summary>
                      <pre className="tool-result-text">{limitCodeTaskText(message.detail, 2400)}</pre>
                    </details>
                  )}
                </div>
              ))}
              {phase.messages.length > 6 && (
                <p className="code-task-phase-hidden">已折叠 {phase.messages.length - 6} 条较早事件</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function CodeTaskProcessedBlock({
  messages,
  phases,
  running
}: {
  messages: CodeTaskMessage[];
  phases: CodeTaskPhase[];
  running: boolean;
}) {
  const processPhases = taskProcessPhases(phases);
  const pendingQuestion = messages.some(isPendingQuestionMessage);
  const hasError = processPhases.some((phase) => phase.status === "error");
  const shouldDefaultOpen = running || pendingQuestion || hasError;
  const [open, setOpen] = useState(shouldDefaultOpen);
  useEffect(() => {
    setOpen(shouldDefaultOpen);
  }, [shouldDefaultOpen]);
  if (processPhases.length === 0) {
    return null;
  }
  const duration = formatTaskDuration(messages);
  const eventCount = processPhases.reduce((total, phase) => total + phase.messages.length, 0);
  return (
    <section className={`code-task-processed ${running ? "is-running" : "is-complete"} ${open ? "is-open" : ""}`}>
      <button className="code-task-processed-toggle" onClick={() => setOpen((value) => !value)} type="button">
        <span className="code-task-processed-title">
          {running ? <CircleDashed className="phase-spinner" size={15} /> : <CheckCircle2 size={15} />}
          {running ? "处理中" : "已处理"}
          {duration && <small>{duration}</small>}
        </span>
        <span className="code-task-processed-meta">
          {eventCount} 条事件
          <span className="label-open">收起</span>
          <span className="label-closed">展开</span>
        </span>
      </button>
      {open && <CodeTaskProcessDetails phases={phases} />}
    </section>
  );
}

function CodeTaskBubble({
  message,
  children
}: {
  message: CodeTaskMessage;
  children?: ReactNode;
}) {
  const role = taskBubbleRole(message);
  const isAssistant = role === "assistant";
  const rawText = taskBubbleText(message);
  const visibleText = isAssistant ? limitCodeTaskText(rawText, CODE_TASK_SUMMARY_OUTPUT_LIMIT) : rawText;
  return (
    <article className={`dialog-message is-panel code-task-dialog is-${role}`} key={message.id}>
      {isAssistant && <div className="bubble-agent-name">{isFinalSummaryMessage(message) ? "任务总结" : "OpenCode"}</div>}
      <div className={`message-bubble ${isAssistant ? "is-assistant" : "is-user"}`}>
        {children}
        {renderMarkdownContent(visibleText)}
        {message.detail && <pre className="code-task-bubble-detail">{limitCodeTaskText(message.detail, 1800)}</pre>}
      </div>
      <div className="bubble-meta">
        <time dateTime={message.createdAt}>{formatMessageTime(message.createdAt)}</time>
      </div>
    </article>
  );
}

export function CodeTaskConversationTimeline({
  messages,
  phases: _phases,
  running
}: {
  messages: CodeTaskMessage[];
  phases: CodeTaskPhase[];
  running: boolean;
}) {
  const turns = buildTaskTurns(messages, running);

  return (
    <>
      {turns.map((turn) => {
        const turnPhases = buildCodeTaskPhases(turn.messages, turn.running);
        const requestMessages = turn.messages.filter((message) => message.kind === "user");
        const responseMessages = taskTurnResponseMessages(turn.messages, turn.running);
        return (
          <Fragment key={turn.id}>
            {requestMessages.map((message) => (
              <CodeTaskBubble key={message.id} message={message} />
            ))}
            <CodeTaskProcessedBlock
              messages={turn.messages}
              phases={turnPhases}
              running={turn.running}
            />
            {responseMessages.map((message) => (
              <CodeTaskBubble key={message.id} message={message} />
            ))}
          </Fragment>
        );
      })}
    </>
  );
}
