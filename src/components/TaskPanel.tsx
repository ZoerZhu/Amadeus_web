import type { FormEvent, RefObject } from "react";
import { Bell, CircleStop, FolderOpen, Plus, Send } from "lucide-react";
import type { CodeTaskMessage, CodeTaskPhase, CodeTaskRecord } from "../app/appSupport";
import { CodeTaskConversationTimeline, CodeTaskQuestionComposer, type CodeTaskQuestionHandler } from "./CodeTaskTimeline";

type TaskPanelProps = {
  activeTask?: CodeTaskRecord;
  workspace: string;
  autoApprove: boolean;
  messagesCount: number;
  messages: CodeTaskMessage[];
  phases: CodeTaskPhase[];
  messageRef: RefObject<HTMLDivElement>;
  questionBusyIds: string[];
  input: string;
  running: boolean;
  status: string;
  canStart: boolean;
  onWorkspaceChange: (value: string) => void;
  onChooseFolder: () => void | Promise<void>;
  onAutoApproveChange: (value: boolean) => void;
  onAnswerQuestion: CodeTaskQuestionHandler;
  onInputChange: (value: string) => void;
  onSend: (event?: FormEvent) => void | Promise<void>;
  onNewDraft: () => void;
  onStop: () => void;
};

export function TaskPanel({
  activeTask,
  workspace,
  autoApprove,
  messagesCount,
  messages,
  phases,
  messageRef,
  questionBusyIds,
  input,
  running,
  status,
  canStart,
  onWorkspaceChange,
  onChooseFolder,
  onAutoApproveChange,
  onAnswerQuestion,
  onInputChange,
  onSend,
  onNewDraft,
  onStop
}: TaskPanelProps) {
  const pendingQuestionMessage = [...messages]
    .reverse()
    .find((message) => message.kind === "question" && message.question && !message.question.answered && !message.question.rejected);
  const questionBusy = pendingQuestionMessage?.question
    ? questionBusyIds.includes(pendingQuestionMessage.question.requestId)
    : false;
  const hasPendingQuestion = Boolean(pendingQuestionMessage);

  return (
    <>
      <div className="code-task-workspace">
        <div className="code-task-current">
          <strong>{activeTask?.title ?? "新 OpenCode 任务"}</strong>
          <small>
            {activeTask?.sessionId
              ? `session ${activeTask.sessionId}`
              : activeTask
                ? "等待 OpenCode 会话"
                : "输入任务后创建历史记录"}
          </small>
        </div>
        <label className="field">
          <span>工作目录</span>
          <div className="folder-field">
            <input
              value={workspace}
              onChange={(event) => onWorkspaceChange(event.target.value)}
              placeholder="留空则使用后端当前工作区"
            />
            <button className="icon-button" onClick={() => void onChooseFolder()} type="button" aria-label="选择文件夹">
              <FolderOpen size={16} />
            </button>
          </div>
        </label>
        <label className="code-task-approval">
          <input
            checked={autoApprove}
            onChange={(event) => onAutoApproveChange(event.target.checked)}
            type="checkbox"
          />
          <span>允许 OpenCode 修改文件和执行命令</span>
        </label>
      </div>

      <div className="code-task-list" ref={messageRef}>
        {messagesCount === 0 && (
          <div className="empty-state">
            <Bell size={24} />
            <span>暂无任务</span>
          </div>
        )}
        <CodeTaskConversationTimeline
          messages={messages}
          phases={phases}
          running={running}
        />
      </div>

      <form className={`code-task-composer ${hasPendingQuestion ? "has-question" : ""}`} onSubmit={hasPendingQuestion ? (event) => event.preventDefault() : onSend}>
        {pendingQuestionMessage ? (
          <CodeTaskQuestionComposer
            message={pendingQuestionMessage}
            busy={questionBusy}
            onAnswerQuestion={onAnswerQuestion}
            surface="composer"
          />
        ) : (
          <textarea
            value={input}
            onChange={(event) => onInputChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                void onSend();
              }
            }}
            placeholder="输入要交给 OpenCode 完成的任务"
            rows={4}
          />
        )}
        <div className="composer-actions">
          <div className="composer-left-actions">
            <button className="text-icon-button" onClick={onNewDraft} type="button">
              <Plus size={16} />
              新建任务
            </button>
            <span className={`code-task-status ${running ? "is-live" : ""}`}>{status}</span>
          </div>
          {running ? (
            <button className="send-button" onClick={onStop} type="button">
              <CircleStop size={18} />
            </button>
          ) : (
            <button className="send-button" disabled={!canStart} type="submit">
              <Send size={18} />
            </button>
          )}
        </div>
      </form>
    </>
  );
}
