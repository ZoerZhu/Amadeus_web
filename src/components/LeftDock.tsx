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
import type { ConversationSummary, OrchestratorTaskSummary } from "../types";

type LeftDockProps = {
  open: boolean;
  storageOnline: boolean;
  conversations: ConversationSummary[];
  filteredConversations: ConversationSummary[];
  currentConversationId: string | null;
  chatStatus: string;
  streaming: boolean;
  historySearch: string;
  visibleTasks: OrchestratorTaskSummary[];
  activeTaskId: string | null;
  taskStatus: string;
  onCollapse: () => void;
  onNewConversation: () => void;
  onOpenTaskPanel: () => void;
  onOpenConfigPanel: () => void;
  onHistorySearchChange: (value: string) => void;
  onOpenConversation: (conversationId: string) => void | Promise<void>;
  onExportConversation: (conversation: ConversationSummary) => void | Promise<void>;
  onDeleteConversation: (conversation: ConversationSummary) => void | Promise<void>;
  onNewTask: () => void;
  onOpenTask: (task: OrchestratorTaskSummary) => void;
};

export function LeftDock({
  open,
  storageOnline,
  conversations,
  filteredConversations,
  currentConversationId,
  chatStatus,
  streaming,
  historySearch,
  visibleTasks,
  activeTaskId,
  taskStatus,
  onCollapse,
  onNewConversation,
  onOpenTaskPanel,
  onOpenConfigPanel,
  onHistorySearchChange,
  onOpenConversation,
  onExportConversation,
  onDeleteConversation,
  onNewTask,
  onOpenTask
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
          <button className="dock-action" onClick={onNewTask} type="button">
            <Plus size={16} />
            新建任务
          </button>
          {visibleTasks.length === 0 && (
            <button className="task-item" onClick={onOpenTaskPanel} type="button">
              <span>暂无历史任务</span>
              <small>new</small>
            </button>
          )}
          {visibleTasks.map((task) => (
            <div className={`history-row ${task.id === activeTaskId ? "is-current" : ""}`} key={task.id}>
              <button
                className={`task-item ${task.id === activeTaskId ? "is-current" : ""}`}
                onClick={() => onOpenTask(task)}
                type="button"
              >
                <span>{task.title}</span>
                <small>{task.id === activeTaskId ? taskStatus : formatHistoryTime(task.updatedAt)}</small>
              </button>
            </div>
          ))}
        </section>
      </div>
    </aside>
  );
}
