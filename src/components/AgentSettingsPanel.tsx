import { useRef, useState } from "react";
import {
  Boxes,
  Github,
  Loader2,
  Package,
  Plug,
  Plus,
  Power,
  Trash2,
  Upload
} from "lucide-react";
import { ConfigGroup } from "./ConfigGroup";
import {
  connectMcpServer,
  deleteMcpServer,
  deleteSkill,
  disconnectMcpServer,
  importSkillGit,
  importSkillZip,
  testMcpServer,
  upsertMcpServer,
  upsertSkill
} from "../api";
import type {
  AgentSettings,
  McpAuthType,
  McpServerConfig,
  McpTransport,
  SkillPackageInfo
} from "../types";

type AgentSettingsPanelProps = {
  agent: AgentSettings;
  onAgentChange: (agent: AgentSettings) => void;
  mcpServers: McpServerConfig[];
  onMcpServersChange: (servers: McpServerConfig[]) => void;
  skills: SkillPackageInfo[];
  onSkillsChange: (skills: SkillPackageInfo[]) => void;
};

type SectionKey = "agent" | "mcp" | "skills";

const DEFAULT_AGENT_SETTINGS: AgentSettings = {
  enabled: true,
  trustMode: true,
  defaultWorkspace: "",
  maxRounds: 20,
  maxToolCalls: 80,
  maxRuntimeSeconds: 1800,
  maxSamplingDepth: 3,
  artifactRoot: "generated_docs/agent_artifacts",
  rollbackEnabled: true,
  browserEnabled: false,
  opencodeEnabled: true
};

function normalizeAgentSettings(value?: Partial<AgentSettings> | null): AgentSettings {
  return { ...DEFAULT_AGENT_SETTINGS, ...(value ?? {}) };
}

function emptyMcpServer(): McpServerConfig {
  return {
    id: "",
    name: "",
    enabled: true,
    transport: "stdio",
    command: "",
    args: [],
    cwd: "",
    env: {},
    url: "",
    headers: {},
    authType: "none",
    authToken: "",
    allowedTools: [],
    allowedResources: [],
    trusted: false,
    timeoutSeconds: 30,
    connected: false
  };
}

export function AgentSettingsPanel({
  agent,
  onAgentChange,
  mcpServers,
  onMcpServersChange,
  skills,
  onSkillsChange
}: AgentSettingsPanelProps) {
  const [openSections, setOpenSections] = useState<Record<SectionKey, boolean>>({
    agent: false,
    mcp: false,
    skills: false
  });
  const [mcpBusyId, setMcpBusyId] = useState<string | null>(null);
  const [mcpStatus, setMcpStatus] = useState<string>("");
  const [skillImportBusy, setSkillImportBusy] = useState(false);
  const [skillImportStatus, setSkillImportStatus] = useState<string>("");
  const [gitUrl, setGitUrl] = useState("");
  const [gitBranch, setGitBranch] = useState("main");
  const zipInputRef = useRef<HTMLInputElement | null>(null);

  function toggleSection(section: SectionKey) {
    setOpenSections((prev) => ({ ...prev, [section]: !prev[section] }));
  }

  function updateAgent(patch: Partial<AgentSettings>) {
    onAgentChange({ ...normalizeAgentSettings(agent), ...patch });
  }

  function updateServer(id: string, patch: Partial<McpServerConfig>) {
    onMcpServersChange(
      mcpServers.map((server) => (server.id === id ? { ...server, ...patch } : server))
    );
  }

  function addServer() {
    const draft = emptyMcpServer();
    draft.id = `draft-${Date.now()}`;
    draft.name = "新 MCP 服务器";
    onMcpServersChange([...mcpServers, draft]);
  }

  function removeServer(id: string) {
    void (async () => {
      try {
        if (!id.startsWith("draft-")) {
          await deleteMcpServer(id);
        }
        onMcpServersChange(mcpServers.filter((server) => server.id !== id));
        setMcpStatus(`已删除服务器`);
      } catch (error) {
        setMcpStatus(error instanceof Error ? error.message : "删除失败");
      }
    })();
  }

  function saveServer(server: McpServerConfig) {
    void (async () => {
      setMcpBusyId(server.id);
      try {
        const saved = await upsertMcpServer(server);
        onMcpServersChange(
          mcpServers.map((item) => (item.id === server.id ? saved : item))
        );
        setMcpStatus(`已保存 ${saved.name}`);
      } catch (error) {
        setMcpStatus(error instanceof Error ? error.message : "保存失败");
      } finally {
        setMcpBusyId(null);
      }
    })();
  }

  function handleTestServer(server: McpServerConfig) {
    void (async () => {
      setMcpBusyId(server.id);
      setMcpStatus(`测试 ${server.name} 中…`);
      try {
        const result = await testMcpServer(server);
        if (result.ok) {
          setMcpStatus(`连接成功，工具 ${result.toolCount ?? 0} 个，资源 ${result.resourceCount ?? 0} 个`);
        } else {
          setMcpStatus(result.error || "连接失败");
        }
      } catch (error) {
        setMcpStatus(error instanceof Error ? error.message : "测试失败");
      } finally {
        setMcpBusyId(null);
      }
    })();
  }

  function handleConnect(server: McpServerConfig) {
    void (async () => {
      setMcpBusyId(server.id);
      try {
        const result = await connectMcpServer(server.id);
        updateServer(server.id, { connected: true });
        setMcpStatus(`已连接 ${server.name}，工具 ${result.toolCount} 个`);
      } catch (error) {
        setMcpStatus(error instanceof Error ? error.message : "连接失败");
      } finally {
        setMcpBusyId(null);
      }
    })();
  }

  function handleDisconnect(server: McpServerConfig) {
    void (async () => {
      setMcpBusyId(server.id);
      try {
        await disconnectMcpServer(server.id);
        updateServer(server.id, { connected: false });
        setMcpStatus(`已断开 ${server.name}`);
      } catch (error) {
        setMcpStatus(error instanceof Error ? error.message : "断开失败");
      } finally {
        setMcpBusyId(null);
      }
    })();
  }

  function handleZipImport(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    void (async () => {
      setSkillImportBusy(true);
      setSkillImportStatus(`导入 ${file.name} 中…`);
      try {
        const saved = await importSkillZip(file);
        onSkillsChange([saved, ...skills]);
        setSkillImportStatus(`已导入 ${saved.name}`);
      } catch (error) {
        setSkillImportStatus(error instanceof Error ? error.message : "导入失败");
      } finally {
        setSkillImportBusy(false);
        if (zipInputRef.current) {
          zipInputRef.current.value = "";
        }
      }
    })();
  }

  function handleGitImport() {
    const url = gitUrl.trim();
    if (!url) {
      setSkillImportStatus("请填写 Git URL");
      return;
    }
    void (async () => {
      setSkillImportBusy(true);
      setSkillImportStatus(`从 Git 导入中…`);
      try {
        const saved = await importSkillGit(url, gitBranch);
        onSkillsChange([saved, ...skills]);
        setGitUrl("");
        setSkillImportStatus(`已导入 ${saved.name}`);
      } catch (error) {
        setSkillImportStatus(error instanceof Error ? error.message : "导入失败");
      } finally {
        setSkillImportBusy(false);
      }
    })();
  }

  function toggleSkillEnabled(skill: SkillPackageInfo) {
    void (async () => {
      const updated: SkillPackageInfo = { ...skill, enabled: !skill.enabled };
      try {
        const saved = await upsertSkill(skill.id, updated);
        onSkillsChange(skills.map((item) => (item.id === skill.id ? saved : item)));
      } catch (error) {
        setSkillImportStatus(error instanceof Error ? error.message : "更新失败");
      }
    })();
  }

  function removeSkill(skill: SkillPackageInfo) {
    void (async () => {
      try {
        await deleteSkill(skill.id);
        onSkillsChange(skills.filter((item) => item.id !== skill.id));
        setSkillImportStatus(`已删除 ${skill.name}`);
      } catch (error) {
        setSkillImportStatus(error instanceof Error ? error.message : "删除失败");
      }
    })();
  }

  const normalizedAgent = normalizeAgentSettings(agent);

  return (
    <>
      <ConfigGroup
        title="Agent"
        icon={<Boxes size={16} />}
        open={openSections.agent}
        onToggle={() => toggleSection("agent")}
      >
        <label className="switch-row">
          <span>启用复杂 Agent</span>
          <input
            checked={normalizedAgent.enabled}
            onChange={(event) => updateAgent({ enabled: event.target.checked })}
            type="checkbox"
          />
        </label>
        <label className="switch-row">
          <span>信任模式（自动批准已知确认类工具，高风险操作仍需确认）</span>
          <input
            checked={normalizedAgent.trustMode}
            onChange={(event) => updateAgent({ trustMode: event.target.checked })}
            type="checkbox"
          />
        </label>
        <label className="field">
          <span>默认工作目录</span>
          <input
            value={normalizedAgent.defaultWorkspace}
            onChange={(event) => updateAgent({ defaultWorkspace: event.target.value })}
            placeholder="留空则要求每次指定"
          />
        </label>
        <label className="field">
          <span>最大轮次</span>
          <input
            type="number"
            min={1}
            value={normalizedAgent.maxRounds}
            onChange={(event) => updateAgent({ maxRounds: Number(event.target.value) || 0 })}
          />
        </label>
        <label className="field">
          <span>最大工具调用数</span>
          <input
            type="number"
            min={1}
            value={normalizedAgent.maxToolCalls}
            onChange={(event) => updateAgent({ maxToolCalls: Number(event.target.value) || 0 })}
          />
        </label>
        <label className="field">
          <span>最大运行时长（秒）</span>
          <input
            type="number"
            min={1}
            value={normalizedAgent.maxRuntimeSeconds}
            onChange={(event) => updateAgent({ maxRuntimeSeconds: Number(event.target.value) || 0 })}
          />
        </label>
        <label className="field">
          <span>最大采样深度</span>
          <input
            type="number"
            min={0}
            value={normalizedAgent.maxSamplingDepth}
            onChange={(event) => updateAgent({ maxSamplingDepth: Number(event.target.value) || 0 })}
          />
        </label>
        <label className="switch-row">
          <span>允许回滚</span>
          <input
            checked={normalizedAgent.rollbackEnabled}
            onChange={(event) => updateAgent({ rollbackEnabled: event.target.checked })}
            type="checkbox"
          />
        </label>
        <label className="switch-row">
          <span>启用浏览器工具</span>
          <input
            checked={normalizedAgent.browserEnabled}
            onChange={(event) => updateAgent({ browserEnabled: event.target.checked })}
            type="checkbox"
          />
        </label>
        <label className="switch-row">
          <span>启用 OpenCode 集成</span>
          <input
            checked={normalizedAgent.opencodeEnabled}
            onChange={(event) => updateAgent({ opencodeEnabled: event.target.checked })}
            type="checkbox"
          />
        </label>
      </ConfigGroup>

      <ConfigGroup
        title="MCP 服务器"
        icon={<Plug size={16} />}
        open={openSections.mcp}
        onToggle={() => toggleSection("mcp")}
      >
        {mcpServers.length === 0 && (
          <div className="agent-empty-hint">尚未配置 MCP 服务器</div>
        )}
        {mcpServers.map((server) => (
          <div className="agent-mcp-card" key={server.id}>
            <div className="agent-mcp-head">
              <input
                className="agent-mcp-name"
                value={server.name}
                onChange={(event) => updateServer(server.id, { name: event.target.value })}
                placeholder="服务器名称"
              />
              <label className="agent-mcp-toggle">
                <input
                  checked={server.enabled}
                  onChange={(event) => updateServer(server.id, { enabled: event.target.checked })}
                  type="checkbox"
                />
                <span>启用</span>
              </label>
              <span className={`agent-mcp-status ${server.connected ? "is-live" : ""}`}>
                {server.connected ? "已连接" : "未连接"}
              </span>
            </div>
            <label className="field">
              <span>传输方式</span>
              <select
                value={server.transport}
                onChange={(event) => updateServer(server.id, { transport: event.target.value as McpTransport })}
              >
                <option value="stdio">stdio</option>
                <option value="http">http</option>
              </select>
            </label>
            {server.transport === "stdio" ? (
              <>
                <label className="field">
                  <span>命令</span>
                  <input
                    value={server.command}
                    onChange={(event) => updateServer(server.id, { command: event.target.value })}
                    placeholder="例如 npx"
                  />
                </label>
                <label className="field">
                  <span>参数（空格分隔）</span>
                  <input
                    value={server.args.join(" ")}
                    onChange={(event) =>
                      updateServer(server.id, {
                        args: event.target.value.split(/\s+/).filter(Boolean)
                      })
                    }
                    placeholder="例如 -y @modelcontextprotocol/server-memory"
                  />
                </label>
                <label className="field">
                  <span>工作目录</span>
                  <input
                    value={server.cwd}
                    onChange={(event) => updateServer(server.id, { cwd: event.target.value })}
                  />
                </label>
              </>
            ) : (
              <>
                <label className="field">
                  <span>URL</span>
                  <input
                    value={server.url}
                    onChange={(event) => updateServer(server.id, { url: event.target.value })}
                    placeholder="https://example.com/mcp"
                  />
                </label>
                <label className="field">
                  <span>认证方式</span>
                  <select
                    value={server.authType}
                    onChange={(event) => updateServer(server.id, { authType: event.target.value as McpAuthType })}
                  >
                    <option value="none">none</option>
                    <option value="bearer">bearer</option>
                    <option value="api_key">api_key</option>
                  </select>
                </label>
                {server.authType !== "none" && (
                  <label className="field">
                    <span>认证令牌</span>
                    <input
                      type="password"
                      value={server.authToken}
                      onChange={(event) => updateServer(server.id, { authToken: event.target.value })}
                      autoComplete="off"
                    />
                  </label>
                )}
              </>
            )}
            <label className="field">
              <span>超时（秒）</span>
              <input
                type="number"
                min={1}
                value={server.timeoutSeconds}
                onChange={(event) => updateServer(server.id, { timeoutSeconds: Number(event.target.value) || 30 })}
              />
            </label>
            <div className="agent-mcp-actions">
              <button
                className="text-icon-button"
                disabled={mcpBusyId === server.id}
                onClick={() => saveServer(server)}
                type="button"
              >
                <Power size={15} />
                保存
              </button>
              <button
                className="text-icon-button"
                disabled={mcpBusyId === server.id}
                onClick={() => handleTestServer(server)}
                type="button"
              >
                测试
              </button>
              {server.connected ? (
                <button
                  className="text-icon-button"
                  disabled={mcpBusyId === server.id}
                  onClick={() => handleDisconnect(server)}
                  type="button"
                >
                  断开
                </button>
              ) : (
                <button
                  className="text-icon-button"
                  disabled={mcpBusyId === server.id || !server.id || server.id.startsWith("draft-")}
                  onClick={() => handleConnect(server)}
                  type="button"
                >
                  连接
                </button>
              )}
              <button
                className="text-icon-button is-muted"
                disabled={mcpBusyId === server.id}
                onClick={() => removeServer(server.id)}
                type="button"
                aria-label="删除服务器"
              >
                <Trash2 size={15} />
              </button>
              {mcpBusyId === server.id && <Loader2 className="phase-spinner" size={14} />}
            </div>
          </div>
        ))}
        <div className="agent-section-actions">
          <button className="text-icon-button" onClick={addServer} type="button">
            <Plus size={15} />
            添加服务器
          </button>
          {mcpStatus && <span className="agent-status-text">{mcpStatus}</span>}
        </div>
      </ConfigGroup>

      <ConfigGroup
        title="技能包"
        icon={<Package size={16} />}
        open={openSections.skills}
        onToggle={() => toggleSection("skills")}
      >
        <div className="agent-skill-import">
          <div className="agent-skill-import-row">
            <input
              className="agent-skill-git-url"
              value={gitUrl}
              onChange={(event) => setGitUrl(event.target.value)}
              placeholder="Git URL"
            />
            <input
              className="agent-skill-git-branch"
              value={gitBranch}
              onChange={(event) => setGitBranch(event.target.value)}
              placeholder="分支"
            />
            <button
              className="text-icon-button"
              disabled={skillImportBusy}
              onClick={handleGitImport}
              type="button"
            >
              <Github size={15} />
              导入
            </button>
          </div>
          <div className="agent-skill-import-row">
            <input
              ref={zipInputRef}
              type="file"
              accept=".zip"
              onChange={handleZipImport}
              disabled={skillImportBusy}
              style={{ display: "none" }}
            />
            <button
              className="text-icon-button"
              disabled={skillImportBusy}
              onClick={() => zipInputRef.current?.click()}
              type="button"
            >
              <Upload size={15} />
              导入 ZIP
            </button>
            {skillImportBusy && <Loader2 className="phase-spinner" size={14} />}
            {skillImportStatus && <span className="agent-status-text">{skillImportStatus}</span>}
          </div>
        </div>
        {skills.length === 0 && (
          <div className="agent-empty-hint">尚未安装技能包</div>
        )}
        {skills.map((skill) => (
          <div className="agent-skill-card" key={skill.id}>
            <div className="agent-skill-head">
              <div className="agent-skill-title">
                <Package size={15} />
                <strong>{skill.name}</strong>
                <small>v{skill.version}</small>
                <span className="agent-skill-source">{skill.source}</span>
              </div>
              <label className="agent-mcp-toggle">
                <input
                  checked={skill.enabled}
                  onChange={() => toggleSkillEnabled(skill)}
                  type="checkbox"
                />
                <span>启用</span>
              </label>
            </div>
            {skill.description && <p className="agent-skill-desc">{skill.description}</p>}
            {skill.triggers.length > 0 && (
              <div className="agent-skill-triggers">
                {skill.triggers.map((trigger, index) => (
                  <span className="agent-skill-tag" key={`${skill.id}-trigger-${index}`}>
                    {trigger}
                  </span>
                ))}
              </div>
            )}
            <div className="agent-mcp-actions">
              <button
                className="text-icon-button is-muted"
                onClick={() => removeSkill(skill)}
                type="button"
                aria-label="删除技能"
              >
                <Trash2 size={15} />
                删除
              </button>
            </div>
          </div>
        ))}
      </ConfigGroup>
    </>
  );
}
