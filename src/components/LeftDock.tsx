import {
  Bell,
  BookOpenText,
  ChevronLeft,
  Clock3,
  Download,
  MessageCircle,
  Plus,
  Search,
  Settings,
  Trash2
} from "lucide-react";
import { formatHistoryTime } from "../app/appSupport";
import type { CodeTaskRecord } from "../app/appSupport";
import type { ConversationSummary } from "../types";

type LeftDockProps = {
  open: boolean;
  storageOnline: boolean;
  storageError: string;
  conversations: ConversationSummary[];
  filteredConversations: ConversationSummary[];
  currentConversationId: string | null;
  chatStatus: string;
  streaming: boolean;
  historySearch: string;
  visibleCodeTasks: CodeTaskRecord[];
  activeCodeTaskId: string | null;
  codeTaskRunning: boolean;
  codeTaskStatus: string;
  onCollapse: () => void;
  onNewConversation: () => void;
  onOpenTaskPanel: () => void;
  onOpenConfigPanel: () => void;
  onHistorySearchChange: (value: string) => void;
  onOpenConversation: (conversationId: string) => void | Promise<void>;
  onExportConversation: (conversation: ConversationSummary) => void | Promise<void>;
  onDeleteConversation: (conversation: ConversationSummary) => void | Promise<void>;
  onNewCodeTask: () => void;
  onOpenCodeTask: (task: CodeTaskRecord) => void;
  onExportCodeTask: (task: CodeTaskRecord) => void;
  onDeleteCodeTask: (task: CodeTaskRecord) => void;
};

export function LeftDock({
  open,
  storageOnline,
  storageError,
  conversations,
  filteredConversations,
  currentConversationId,
  chatStatus,
  streaming,
  historySearch,
  visibleCodeTasks,
  activeCodeTaskId,
  codeTaskRunning,
  codeTaskStatus,
  onCollapse,
  onNewConversation,
  onOpenTaskPanel,
  onOpenConfigPanel,
  onHistorySearchChange,
  onOpenConversation,
  onExportConversation,
  onDeleteConversation,
  onNewCodeTask,
  onOpenCodeTask,
  onExportCodeTask,
  onDeleteCodeTask
}: LeftDockProps) {
  return (
    <aside className={`left-dock glass-panel ${open ? "is-open" : ""}`} aria-hidden={!open}>
      <div className="dock-head">
        <div>
          <span className="eyebrow">Workspace</span>
          <h2>Amadeus</h2>
        </div>
        <button className="icon-button" onClick={onCollapse} type="button" aria-label="收起侧边栏">
          <ChevronLeft size={18} />
        </button>
      </div>

      <div className="dock-scroll">
        <button className="dock-action is-primary" onClick={onNewConversation} type="button">
          <Plus size={17} />
          新对话
        </button>

        <nav className="dock-nav" aria-label="main">
          <button className="dock-nav-item is-active" type="button">
            <MessageCircle size={17} />
            聊天记录
          </button>
          <button className="dock-nav-item" onClick={onOpenTaskPanel} type="button">
            <Bell size={17} />
            任务
          </button>
          <button className="dock-nav-item" onClick={onOpenConfigPanel} type="button">
            <Settings size={17} />
            设置
          </button>
        </nav>

        <section className="dock-section history-section">
          <label className="history-search">
            <Search size={15} />
            <input
              value={historySearch}
              onChange={(event) => onHistorySearchChange(event.target.value)}
              placeholder="搜索聊天记录"
              aria-label="搜索聊天记录"
            />
          </label>
          <div className="section-title">
            <Clock3 size={14} />
            最近
          </div>
          {!storageOnline && (
            <button className="history-item" type="button" disabled>
              <span>PostgreSQL 未连接</span>
              <small>{storageError || "offline"}</small>
            </button>
          )}
          {storageOnline && conversations.length === 0 && (
            <button className="history-item" type="button" disabled>
              <span>暂无历史</span>
              <small>new</small>
            </button>
          )}
          {storageOnline && conversations.length > 0 && filteredConversations.length === 0 && (
            <button className="history-item" type="button" disabled>
              <span>没有匹配记录</span>
              <small>search</small>
            </button>
          )}
          {filteredConversations.map((conversation) => (
            <div
              className={`history-row ${conversation.id === currentConversationId ? "is-current" : ""}`}
              key={conversation.id}
            >
              <button
                className="history-item"
                disabled={streaming}
                onClick={() => void onOpenConversation(conversation.id)}
                type="button"
              >
                <span>{conversation.title}</span>
                <small>{conversation.id === currentConversationId ? chatStatus : formatHistoryTime(conversation.updatedAt)}</small>
              </button>
              <div className="history-actions" aria-label={`${conversation.title} 操作`}>
                <button
                  className="history-icon-button"
                  onClick={() => void onExportConversation(conversation)}
                  title="导出"
                  type="button"
                  aria-label={`导出 ${conversation.title}`}
                >
                  <Download size={14} />
                </button>
                <button
                  className="history-icon-button is-danger"
                  disabled={streaming}
                  onClick={() => void onDeleteConversation(conversation)}
                  title="删除"
                  type="button"
                  aria-label={`删除 ${conversation.title}`}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </section>

        <section className="dock-section code-task-history-section">
          <div className="section-title">
            <BookOpenText size={14} />
            任务
          </div>
          <button className="dock-action" onClick={onNewCodeTask} type="button">
            <Plus size={16} />
            新建任务
          </button>
          {visibleCodeTasks.length === 0 && (
            <button className="task-item" onClick={onOpenTaskPanel} type="button">
              <span>暂无历史任务</span>
              <small>new</small>
            </button>
          )}
          {visibleCodeTasks.map((task) => (
            <div className={`history-row ${task.id === activeCodeTaskId ? "is-current" : ""}`} key={task.id}>
              <button
                className={`task-item ${task.id === activeCodeTaskId ? "is-current" : ""}`}
                disabled={codeTaskRunning && task.id !== activeCodeTaskId}
                onClick={() => onOpenCodeTask(task)}
                type="button"
              >
                <span>{task.title}</span>
                <small>{task.id === activeCodeTaskId ? codeTaskStatus : formatHistoryTime(task.updatedAt)}</small>
              </button>
              <div className="history-actions" aria-label={`${task.title} 操作`}>
                <button
                  className="history-icon-button"
                  onClick={() => onExportCodeTask(task)}
                  title="导出"
                  type="button"
                  aria-label={`导出 ${task.title}`}
                >
                  <Download size={14} />
                </button>
                <button
                  className="history-icon-button is-danger"
                  disabled={codeTaskRunning && task.id === activeCodeTaskId}
                  onClick={() => onDeleteCodeTask(task)}
                  title="删除"
                  type="button"
                  aria-label={`删除 ${task.title}`}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </section>
      </div>
    </aside>
  );
}
