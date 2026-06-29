import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  Database,
  FolderTree,
  Link2,
  Loader2,
  Plus,
  RefreshCcw,
  Save,
  Search
} from "lucide-react";
import {
  createMemoryProject,
  fetchMemoryProjects,
  fetchMemoryTree,
  ingestMemory,
  linkConversationProject,
  rebuildMemoryIndex,
  searchMemory
} from "../api";
import type {
  MemoryDomain,
  MemoryNode,
  MemoryProject,
  MemorySearchResult
} from "../types";

const MEMORY_DOMAINS: Array<{ value: MemoryDomain; label: string }> = [
  { value: "daily_chat", label: "日常聊天" },
  { value: "task", label: "任务相关" },
  { value: "code", label: "代码相关" },
  { value: "environment", label: "环境与工具" },
  { value: "creative", label: "创意与写作" },
  { value: "knowledge", label: "知识库" }
];

type MemoryPanelProps = {
  currentConversationId?: string | null;
};

type FeedbackKind = "info" | "success" | "error";

type Feedback = {
  kind: FeedbackKind;
  message: string;
};

function nodeLabel(node: MemoryNode): string {
  return node.label || node.path || node.id;
}

function formatCount(value: number | undefined): string {
  return String(value ?? 0);
}

function findNode(nodes: MemoryNode[], id: string): MemoryNode | null {
  for (const node of nodes) {
    if (node.id === id) {
      return node;
    }
    const child = findNode(node.children ?? [], id);
    if (child) {
      return child;
    }
  }
  return null;
}

function flattenTree(nodes: MemoryNode[]): MemoryNode[] {
  const result: MemoryNode[] = [];
  const visit = (node: MemoryNode) => {
    result.push(node);
    (node.children ?? []).forEach(visit);
  };
  nodes.forEach(visit);
  return result;
}

function joinKeywords(node: MemoryNode): string {
  return node.keywords.filter(Boolean).slice(0, 8).join(" / ");
}

function resultNodes(result: MemorySearchResult | null): MemoryNode[] {
  if (!result) {
    return [];
  }
  const seen = new Set<string>();
  const nodes: MemoryNode[] = [];
  [...result.topicNodes, ...result.leafNodes].forEach((node) => {
    if (!seen.has(node.id)) {
      seen.add(node.id);
      nodes.push(node);
    }
  });
  return nodes;
}

export function MemoryPanel({ currentConversationId }: MemoryPanelProps) {
  const [tree, setTree] = useState<MemoryNode[]>([]);
  const [allNodes, setAllNodes] = useState<MemoryNode[]>([]);
  const [projects, setProjects] = useState<MemoryProject[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [query, setQuery] = useState("");
  const [searchResult, setSearchResult] = useState<MemorySearchResult | null>(null);
  const [ingestTitle, setIngestTitle] = useState("");
  const [ingestText, setIngestText] = useState("");
  const [ingestDomain, setIngestDomain] = useState<MemoryDomain>("knowledge");
  const [projectName, setProjectName] = useState("");
  const [projectWorkspace, setProjectWorkspace] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const selectedNode = useMemo(() => {
    if (!selectedNodeId) {
      return allNodes[0] ?? null;
    }
    return findNode(tree, selectedNodeId) ?? allNodes.find((node) => node.id === selectedNodeId) ?? null;
  }, [allNodes, selectedNodeId, tree]);

  const visibleNodes = useMemo(() => flattenTree(tree), [tree]);
  const inactiveCount = allNodes.filter((node) => !node.isActive).length;
  const searchNodes = resultNodes(searchResult);

  const loadMemory = useCallback(async () => {
    setBusy("load");
    setFeedback(null);
    try {
      const [treeResponse, projectList] = await Promise.all([
        fetchMemoryTree({ depth: 4 }),
        fetchMemoryProjects()
      ]);
      setTree(treeResponse.tree);
      setAllNodes(treeResponse.nodes);
      setProjects(projectList);
      setSelectedNodeId((current) => current || treeResponse.tree[0]?.id || "");
      setFeedback({ kind: "success", message: `已加载 ${treeResponse.nodes.length} 个记忆节点` });
    } catch (error) {
      setFeedback({
        kind: "error",
        message: error instanceof Error ? error.message : "加载记忆失败"
      });
    } finally {
      setBusy(null);
    }
  }, []);

  useEffect(() => {
    void loadMemory();
  }, [loadMemory]);

  function handleSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = query.trim();
    if (!text) {
      setFeedback({ kind: "info", message: "请输入检索内容" });
      return;
    }
    void (async () => {
      setBusy("search");
      setFeedback(null);
      try {
        const result = await searchMemory({
          query: text,
          projectId: selectedProjectId || null,
          nodeTopK: 8,
          leafTopK: 10
        });
        setSearchResult(result);
        setFeedback({
          kind: "success",
          message: `检索完成，命中 ${result.topicNodes.length + result.leafNodes.length} 个节点`
        });
      } catch (error) {
        setFeedback({
          kind: "error",
          message: error instanceof Error ? error.message : "检索失败"
        });
      } finally {
        setBusy(null);
      }
    })();
  }

  function handleIngest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = ingestText.trim();
    if (!text) {
      setFeedback({ kind: "info", message: "请输入要写入的记忆文本" });
      return;
    }
    void (async () => {
      setBusy("ingest");
      setFeedback(null);
      try {
        const result = await ingestMemory({
          text,
          title: ingestTitle.trim(),
          domain: ingestDomain,
          projectId: selectedProjectId || null,
          conversationId: currentConversationId || null
        });
        setIngestText("");
        setIngestTitle("");
        setFeedback({ kind: "success", message: `记忆导入任务已入队 ${result.jobId}` });
      } catch (error) {
        setFeedback({
          kind: "error",
          message: error instanceof Error ? error.message : "导入失败"
        });
      } finally {
        setBusy(null);
      }
    })();
  }

  function handleCreateProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = projectName.trim();
    if (!name) {
      setFeedback({ kind: "info", message: "请输入项目名称" });
      return;
    }
    void (async () => {
      setBusy("project");
      setFeedback(null);
      try {
        const project = await createMemoryProject({
          name,
          workspacePath: projectWorkspace.trim()
        });
        setProjects((prev) => [project, ...prev]);
        setSelectedProjectId(project.id);
        setProjectName("");
        setProjectWorkspace("");
        setFeedback({ kind: "success", message: `已创建项目 ${project.name}` });
      } catch (error) {
        setFeedback({
          kind: "error",
          message: error instanceof Error ? error.message : "创建项目失败"
        });
      } finally {
        setBusy(null);
      }
    })();
  }

  function handleLinkConversation() {
    if (!currentConversationId) {
      setFeedback({ kind: "info", message: "当前没有打开的会话" });
      return;
    }
    if (!selectedProjectId) {
      setFeedback({ kind: "info", message: "请选择项目" });
      return;
    }
    void (async () => {
      setBusy("link");
      setFeedback(null);
      try {
        await linkConversationProject({
          conversationId: currentConversationId,
          projectId: selectedProjectId
        });
        setFeedback({ kind: "success", message: "当前会话已关联到项目" });
      } catch (error) {
        setFeedback({
          kind: "error",
          message: error instanceof Error ? error.message : "关联失败"
        });
      } finally {
        setBusy(null);
      }
    })();
  }

  function handleRebuildFts() {
    void (async () => {
      setBusy("rebuild");
      setFeedback(null);
      try {
        const result = await rebuildMemoryIndex({ rebuildFts: true, rebuildVectors: false });
        const count = Number(result.ftsNodes ?? 0);
        setFeedback({ kind: "success", message: `FTS 索引已重建 ${count} 个节点` });
      } catch (error) {
        setFeedback({
          kind: "error",
          message: error instanceof Error ? error.message : "索引重建失败"
        });
      } finally {
        setBusy(null);
      }
    })();
  }

  function renderTreeNode(node: MemoryNode) {
    const children = node.children ?? [];
    return (
      <li key={node.id} className={`memory-tree-node depth-${Math.min(node.depth, 4)}`}>
        <button
          className={`memory-tree-button ${node.id === selectedNode?.id ? "is-active" : ""} ${node.isActive ? "" : "is-inactive"}`}
          onClick={() => setSelectedNodeId(node.id)}
          type="button"
        >
          <span className={`memory-node-kind is-${node.nodeType}`}>{node.nodeType}</span>
          <span className="memory-node-label">{nodeLabel(node)}</span>
          <small>{formatCount(node.leafCount)}</small>
        </button>
        {children.length > 0 && <ul>{children.map(renderTreeNode)}</ul>}
      </li>
    );
  }

  return (
    <div className="memory-panel">
      <div className="memory-toolbar">
        <div className="memory-stat">
          <Database size={16} />
          <span>{allNodes.length} 节点</span>
          {inactiveCount > 0 && <small>{inactiveCount} 已归档</small>}
        </div>
        <div className="memory-actions">
          <button className="text-icon-button" onClick={() => void loadMemory()} disabled={busy === "load"} type="button">
            {busy === "load" ? <Loader2 size={16} className="spin" /> : <RefreshCcw size={16} />}
            刷新
          </button>
          <button className="text-icon-button" onClick={handleRebuildFts} disabled={busy === "rebuild"} type="button">
            {busy === "rebuild" ? <Loader2 size={16} className="spin" /> : <FolderTree size={16} />}
            重建索引
          </button>
        </div>
      </div>

      {feedback && <div className={`memory-feedback is-${feedback.kind}`}>{feedback.message}</div>}

      <div className="memory-layout">
        <section className="memory-tree-panel" aria-label="记忆树">
          <div className="memory-panel-head">
            <FolderTree size={16} />
            <strong>记忆树</strong>
            <small>{visibleNodes.length} 展开</small>
          </div>
          <div className="memory-tree-scroll">
            {tree.length > 0 ? (
              <ul className="memory-tree">{tree.map(renderTreeNode)}</ul>
            ) : (
              <div className="empty-hint">暂无记忆节点</div>
            )}
          </div>
        </section>

        <section className="memory-detail-panel" aria-label="记忆详情">
          {selectedNode ? (
            <div className="memory-node-detail">
              <div className="memory-node-detail-head">
                <div>
                  <span className={`memory-node-kind is-${selectedNode.nodeType}`}>{selectedNode.nodeType}</span>
                  <h4>{nodeLabel(selectedNode)}</h4>
                </div>
                <small>{selectedNode.path}</small>
              </div>
              <p>{selectedNode.summary || selectedNode.fullContent || "无摘要"}</p>
              {joinKeywords(selectedNode) && <div className="memory-keywords">{joinKeywords(selectedNode)}</div>}
              <dl className="memory-node-meta">
                <div>
                  <dt>领域</dt>
                  <dd>{selectedNode.domain || "-"}</dd>
                </div>
                <div>
                  <dt>分类</dt>
                  <dd>{selectedNode.category}</dd>
                </div>
                <div>
                  <dt>重要度</dt>
                  <dd>{selectedNode.importance.toFixed(2)}</dd>
                </div>
                <div>
                  <dt>置信度</dt>
                  <dd>{selectedNode.confidence.toFixed(2)}</dd>
                </div>
              </dl>
            </div>
          ) : (
            <div className="empty-hint">选择一个记忆节点</div>
          )}
        </section>
      </div>

      <form className="memory-search-form" onSubmit={handleSearch}>
        <label className="field">
          <span>检索 Memory</span>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="输入要召回的上下文"
          />
        </label>
        <button className="text-icon-button" disabled={busy === "search"} type="submit">
          {busy === "search" ? <Loader2 size={16} className="spin" /> : <Search size={16} />}
          检索
        </button>
      </form>

      {searchResult && (
        <div className="memory-search-results">
          {searchNodes.length > 0 ? (
            searchNodes.map((node) => (
              <button
                className="memory-result-row"
                key={node.id}
                onClick={() => setSelectedNodeId(node.id)}
                type="button"
              >
                <span>{nodeLabel(node)}</span>
                <small>{node.path}</small>
              </button>
            ))
          ) : (
            <div className="empty-hint">没有检索结果</div>
          )}
          {searchResult.contextText && <pre className="memory-context-preview">{searchResult.contextText}</pre>}
        </div>
      )}

      <div className="memory-projects">
        <label className="field">
          <span>项目</span>
          <select value={selectedProjectId} onChange={(event) => setSelectedProjectId(event.target.value)}>
            <option value="">不限定项目</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        </label>
        <button className="text-icon-button" onClick={handleLinkConversation} disabled={busy === "link"} type="button">
          {busy === "link" ? <Loader2 size={16} className="spin" /> : <Link2 size={16} />}
          关联会话
        </button>
      </div>

      <form className="memory-project-form" onSubmit={handleCreateProject}>
        <div className="inline-fields">
          <label className="field">
            <span>新项目</span>
            <input value={projectName} onChange={(event) => setProjectName(event.target.value)} />
          </label>
          <label className="field">
            <span>工作目录</span>
            <input value={projectWorkspace} onChange={(event) => setProjectWorkspace(event.target.value)} />
          </label>
        </div>
        <button className="text-icon-button" disabled={busy === "project"} type="submit">
          {busy === "project" ? <Loader2 size={16} className="spin" /> : <Plus size={16} />}
          创建项目
        </button>
      </form>

      <form className="memory-ingest-form" onSubmit={handleIngest}>
        <div className="inline-fields">
          <label className="field">
            <span>标题</span>
            <input value={ingestTitle} onChange={(event) => setIngestTitle(event.target.value)} />
          </label>
          <label className="field">
            <span>领域</span>
            <select value={ingestDomain} onChange={(event) => setIngestDomain(event.target.value as MemoryDomain)}>
              {MEMORY_DOMAINS.map((domain) => (
                <option key={domain.value} value={domain.value}>
                  {domain.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="field memory-text-field">
          <span>写入内容</span>
          <textarea
            value={ingestText}
            onChange={(event) => setIngestText(event.target.value)}
            placeholder="输入需要进入长期记忆的事实、偏好、任务状态或知识片段"
          />
        </label>
        <button className="text-icon-button" disabled={busy === "ingest"} type="submit">
          {busy === "ingest" ? <Loader2 size={16} className="spin" /> : <Save size={16} />}
          写入 Memory
        </button>
      </form>
    </div>
  );
}
