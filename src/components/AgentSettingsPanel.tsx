import { useRef, useState } from "react";
import {
  Boxes,
  CheckCircle2,
  Github,
  LayoutGrid,
  Loader2,
  Package,
  Plug,
  Plus,
  Power,
  Sparkles,
  Trash2,
  Upload,
  XCircle
} from "lucide-react";
import { ConfigGroup } from "./ConfigGroup";
import {
  connectMcpServer,
  deleteMcpServer,
  deleteSkill,
  disconnectMcpServer,
  fetchBuiltinSkills,
  fetchMcpPresets,
  importSkillGit,
  importSkillZip,
  installBuiltinSkills,
  testMcpServer,
  upsertMcpServer,
  upsertSkill
} from "../api";
import type {
  AgentSettings,
  BuiltinSkillInfo,
  McpAuthType,
  McpPreset,
  McpServerConfig,
  McpTransport,
  SkillPackageInfo
} from "../types";
import { normalizeAgentSettings } from "../agentDefaults";

type AgentSettingsPanelProps = {
  agent: AgentSettings;
  onAgentChange: (agent: AgentSettings) => void;
  mcpServers: McpServerConfig[];
  onMcpServersChange: (servers: McpServerConfig[]) => void;
  skills: SkillPackageInfo[];
  onSkillsChange: (skills: SkillPackageInfo[]) => void;
  section?: SectionKey;
};

type SectionKey = "agent" | "mcp" | "skills";
type McpFeedbackKind = "info" | "success" | "error";
type McpFeedback = {
  kind: McpFeedbackKind;
  message: string;
  serverId?: string;
};

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

function isDraftMcpServerId(id: string): boolean {
  return !id || id.startsWith("draft-");
}

function normalizedMcpText(value?: string): string {
  return (value ?? "").trim().toLowerCase();
}

function sameStringList(left: string[] = [], right: string[] = []): boolean {
  return left.map((item) => item.trim()).join("\u0000") === right.map((item) => item.trim()).join("\u0000");
}

function isSameMcpServerConfig(left: McpServerConfig, right: McpServerConfig): boolean {
  if (left.transport !== right.transport) {
    return false;
  }
  if (normalizedMcpText(left.name) && normalizedMcpText(left.name) === normalizedMcpText(right.name)) {
    return true;
  }
  if (left.transport === "http") {
    return Boolean(left.url && right.url && normalizedMcpText(left.url) === normalizedMcpText(right.url));
  }
  return Boolean(
    left.command &&
      right.command &&
      normalizedMcpText(left.command) === normalizedMcpText(right.command) &&
      sameStringList(left.args, right.args)
  );
}

export function AgentSettingsPanel({
  agent,
  onAgentChange,
  mcpServers,
  onMcpServersChange,
  skills,
  onSkillsChange,
  section
}: AgentSettingsPanelProps) {
  const [openSections, setOpenSections] = useState<Record<SectionKey, boolean>>({
    agent: false,
    mcp: false,
    skills: false
  });
  const [mcpBusyId, setMcpBusyId] = useState<string | null>(null);
  const [mcpStatus, setMcpStatus] = useState<string>("");
  const [mcpFeedback, setMcpFeedback] = useState<McpFeedback | null>(null);
  const [skillImportBusy, setSkillImportBusy] = useState(false);
  const [skillImportStatus, setSkillImportStatus] = useState<string>("");
  const [gitUrl, setGitUrl] = useState("");
  const [gitBranch, setGitBranch] = useState("main");
  const zipInputRef = useRef<HTMLInputElement | null>(null);
  const [presetOpen, setPresetOpen] = useState(false);
  const [presets, setPresets] = useState<McpPreset[]>([]);
  const [presetLoading, setPresetLoading] = useState(false);
  const [presetError, setPresetError] = useState("");
  const [builtinSkills, setBuiltinSkills] = useState<BuiltinSkillInfo[]>([]);
  const [builtinLoading, setBuiltinLoading] = useState(false);
  const [installBusy, setInstallBusy] = useState(false);
  const [installStatus, setInstallStatus] = useState<string>("");
  const [selectedMcpId, setSelectedMcpId] = useState<string>("");

  function toggleSection(section: SectionKey) {
    setOpenSections((prev) => ({ ...prev, [section]: !prev[section] }));
  }

  function setMcpNotice(message: string, kind: McpFeedbackKind = "info", serverId?: string) {
    setMcpStatus(message);
    setMcpFeedback({ kind, message, serverId });
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
    if (mcpServers.some((server) => isDraftMcpServerId(server.id))) {
      setMcpNotice("已有未保存 MCP 配置，请先保存或删除", "info");
      return;
    }
    const draft = emptyMcpServer();
    draft.id = `draft-${Date.now()}`;
    draft.name = "新 MCP 服务器";
    onMcpServersChange([...mcpServers, draft]);
    setSelectedMcpId(draft.id);
  }

  function removeServer(id: string) {
    void (async () => {
      try {
        if (!id.startsWith("draft-")) {
          await deleteMcpServer(id);
        }
        const nextServers = mcpServers.filter((server) => server.id !== id);
        onMcpServersChange(nextServers);
        if (selectedMcpId === id) {
          setSelectedMcpId(nextServers[0]?.id ?? "");
        }
        setMcpNotice(`已删除服务器`, "success");
      } catch (error) {
        setMcpNotice(error instanceof Error ? error.message : "删除失败", "error", id);
      }
    })();
  }

  function saveServer(server: McpServerConfig) {
    void (async () => {
      setMcpBusyId(server.id);
      try {
        const originalId = server.id;
        const saved = await upsertMcpServer(isDraftMcpServerId(server.id) ? { ...server, id: "" } : server);
        onMcpServersChange(
          mcpServers.some((item) => item.id === originalId)
            ? mcpServers.map((item) => (item.id === originalId ? saved : item))
            : [saved, ...mcpServers]
        );
        setSelectedMcpId(saved.id);
        setMcpNotice(`已保存 ${saved.name}`, "success", saved.id);
      } catch (error) {
        setMcpNotice(error instanceof Error ? error.message : "保存失败", "error", server.id);
      } finally {
        setMcpBusyId(null);
      }
    })();
  }

  function handleTestServer(server: McpServerConfig) {
    void (async () => {
      setMcpBusyId(server.id);
      setMcpNotice(`测试 ${server.name} 中…`, "info", server.id);
      try {
        const result = await testMcpServer(server);
        if (result.ok) {
          setMcpNotice(
            `测试成功，工具 ${result.toolCount ?? 0} 个，资源 ${result.resourceCount ?? 0} 个`,
            "success",
            server.id
          );
        } else {
          setMcpNotice(result.error || "测试失败", "error", server.id);
        }
      } catch (error) {
        setMcpNotice(error instanceof Error ? error.message : "测试失败", "error", server.id);
      } finally {
        setMcpBusyId(null);
      }
    })();
  }

  function handleConnect(server: McpServerConfig) {
    void (async () => {
      setMcpBusyId(server.id);
      setMcpNotice(`连接 ${server.name} 中…`, "info", server.id);
      try {
        const result = await connectMcpServer(server.id);
        updateServer(server.id, { connected: true });
        setMcpNotice(
          `已连接 ${server.name}，工具 ${result.toolCount} 个，资源 ${result.resourceCount} 个`,
          "success",
          server.id
        );
      } catch (error) {
        setMcpNotice(error instanceof Error ? error.message : "连接失败", "error", server.id);
      } finally {
        setMcpBusyId(null);
      }
    })();
  }

  function handleDisconnect(server: McpServerConfig) {
    void (async () => {
      setMcpBusyId(server.id);
      setMcpNotice(`断开 ${server.name} 中…`, "info", server.id);
      try {
        await disconnectMcpServer(server.id);
        updateServer(server.id, { connected: false });
        setMcpNotice(`已断开 ${server.name}`, "success", server.id);
      } catch (error) {
        setMcpNotice(error instanceof Error ? error.message : "断开失败", "error", server.id);
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

  function loadPresets() {
    if (presetLoading) {
      return;
    }
    void (async () => {
      setPresetLoading(true);
      setPresetError("");
      try {
        const list = await fetchMcpPresets();
        setPresets(list);
        if (list.length === 0) {
          setPresetError("暂无可用模板");
        }
      } catch (error) {
        setPresetError(error instanceof Error ? error.message : "加载模板失败");
      } finally {
        setPresetLoading(false);
      }
    })();
  }

  function handleTogglePresets() {
    const next = !presetOpen;
    setPresetOpen(next);
    if (next && presets.length === 0 && !presetLoading) {
      loadPresets();
    }
  }

  function presetToServer(preset: McpPreset): McpServerConfig {
    const transport: McpTransport = preset.transport === "http" ? "http" : "stdio";
    const authType = (preset.authType ?? "none") as McpAuthType;
    return {
      id: "",
      name: preset.name,
      enabled: preset.enabled,
      transport,
      command: preset.command ?? "",
      args: preset.args ?? [],
      cwd: preset.cwd ?? "",
      env: preset.env ?? {},
      url: preset.url ?? "",
      headers: preset.headers ?? {},
      authType,
      authToken: "",
      allowedTools: preset.allowedTools ?? [],
      allowedResources: preset.allowedResources ?? [],
      trusted: preset.trusted,
      timeoutSeconds: preset.timeoutSeconds ?? 30,
      connected: false
    };
  }

  function applyPreset(preset: McpPreset) {
    const draft = presetToServer(preset);
    const existing = mcpServers.find((server) => isSameMcpServerConfig(server, draft));
    if (existing) {
      setMcpNotice(`${existing.name} 已存在，未重复添加`, "info", existing.id);
      return;
    }
    void (async () => {
      setMcpBusyId(`preset-${preset.id}`);
      setMcpNotice(`添加模板 ${preset.name} 中…`, "info");
      try {
        draft.id = "";
        const saved = await upsertMcpServer(draft);
        onMcpServersChange([...mcpServers, saved]);
        setSelectedMcpId(saved.id);
        setMcpNotice(`已从模板添加 ${saved.name}`, "success", saved.id);
      } catch (error) {
        setMcpNotice(error instanceof Error ? error.message : "添加模板失败", "error");
      } finally {
        setMcpBusyId(null);
      }
    })();
  }

  function loadBuiltinSkills() {
    if (builtinLoading) {
      return;
    }
    void (async () => {
      setBuiltinLoading(true);
      try {
        const list = await fetchBuiltinSkills();
        setBuiltinSkills(list);
      } catch {
        // Silent fail — builtin skills are best-effort
      } finally {
        setBuiltinLoading(false);
      }
    })();
  }

  function handleInstallBuiltin() {
    if (installBusy) {
      return;
    }
    void (async () => {
      setInstallBusy(true);
      setInstallStatus("安装内置 Skills 中…");
      try {
        const result = await installBuiltinSkills();
        setInstallStatus(`已安装 ${result.installed.length} 个内置 Skill`);
        // Refresh skills list by reloading builtin skills (already installed skills
        // will appear in the regular skills list via parent component refresh).
        loadBuiltinSkills();
      } catch (error) {
        setInstallStatus(error instanceof Error ? error.message : "安装失败");
      } finally {
        setInstallBusy(false);
      }
    })();
  }

  function isSkillDependencyMet(skill: BuiltinSkillInfo): boolean {
    if (!skill.mcpServers || skill.mcpServers.length === 0) {
      return true;
    }
    const configuredNames = new Set(mcpServers.map((server) => server.name));
    return skill.mcpServers.every((name) => configuredNames.has(name));
  }

  const normalizedAgent = normalizeAgentSettings(agent);

  const isSingleSection = Boolean(section);
  const isAgentOpen = section ? section === "agent" : openSections.agent;
  const isMcpOpen = section ? section === "mcp" : openSections.mcp;
  const isSkillsOpen = section ? section === "skills" : openSections.skills;
  const selectedMcpServer =
    mcpServers.find((server) => server.id === selectedMcpId) ?? mcpServers[0] ?? null;
  const selectedMcpFeedback =
    mcpFeedback && (!mcpFeedback.serverId || mcpFeedback.serverId === selectedMcpServer?.id)
      ? mcpFeedback
      : null;

  return (
    <div className={`agent-settings-panel ${isSingleSection ? "is-single-section" : ""}`}>
      <ConfigGroup
        title="Agent"
        icon={<Boxes size={16} />}
        open={isAgentOpen}
        onToggle={() => {
          if (!section) {
            toggleSection("agent");
          }
        }}
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
        {normalizedAgent.opencodeEnabled && (
          <div className="opencode-routing-config">
            <label className="switch-row">
              <span>启用 OpenCode 路由（按复杂度按需调用）</span>
              <input
                checked={normalizedAgent.opencodeRouting.enabled}
                onChange={(event) => updateAgent({
                  opencodeRouting: {
                    ...normalizedAgent.opencodeRouting,
                    enabled: event.target.checked,
                  },
                })}
                type="checkbox"
              />
            </label>
            {normalizedAgent.opencodeRouting.enabled && (
              <>
                <div className="inline-fields">
                  <label className="field">
                    <span>允许阈值（分数 ≥ 此值 → 允许 OpenCode）</span>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={normalizedAgent.opencodeRouting.allowThreshold}
                      onChange={(event) => updateAgent({
                        opencodeRouting: {
                          ...normalizedAgent.opencodeRouting,
                          allowThreshold: Number(event.target.value) || 0,
                        },
                      })}
                    />
                  </label>
                  <label className="field">
                    <span>模糊阈值（分数 &lt; 此值 → 拒绝）</span>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={normalizedAgent.opencodeRouting.ambiguousThreshold}
                      onChange={(event) => updateAgent({
                        opencodeRouting: {
                          ...normalizedAgent.opencodeRouting,
                          ambiguousThreshold: Number(event.target.value) || 0,
                        },
                      })}
                    />
                  </label>
                </div>
                <label className="switch-row">
                  <span>允许 LLM 复判（模糊区间内用 LLM 二次判定）</span>
                  <input
                    checked={normalizedAgent.opencodeRouting.allowLlmRejudge}
                    onChange={(event) => updateAgent({
                      opencodeRouting: {
                        ...normalizedAgent.opencodeRouting,
                        allowLlmRejudge: event.target.checked,
                      },
                    })}
                    type="checkbox"
                  />
                </label>
                <label className="field">
                  <span>强制允许关键词（逗号分隔，命中即允许）</span>
                  <input
                    value={normalizedAgent.opencodeRouting.forceAllowKeywords.join(", ")}
                    onChange={(event) => updateAgent({
                      opencodeRouting: {
                        ...normalizedAgent.opencodeRouting,
                        forceAllowKeywords: event.target.value
                          .split(",").map((s) => s.trim()).filter(Boolean),
                      },
                    })}
                    placeholder="opencode, coding agent"
                  />
                </label>
                <label className="field">
                  <span>强制禁用关键词（逗号分隔，命中即拒绝，优先级最高）</span>
                  <input
                    value={normalizedAgent.opencodeRouting.forceDenyKeywords.join(", ")}
                    onChange={(event) => updateAgent({
                      opencodeRouting: {
                        ...normalizedAgent.opencodeRouting,
                        forceDenyKeywords: event.target.value
                          .split(",").map((s) => s.trim()).filter(Boolean),
                      },
                    })}
                    placeholder="留空则无强制禁用"
                  />
                </label>
                <div className="opencode-routing-rules">
                  <div className="opencode-routing-rules-head">
                    <span>路由规则（权重为正=允许信号，为负=拒绝信号）</span>
                  </div>
                  {normalizedAgent.opencodeRouting.rules.map((rule, index) => (
                    <div className="opencode-routing-rule-item" key={rule.id || index}>
                      <input
                        value={rule.name}
                        onChange={(event) => {
                          const rules = [...normalizedAgent.opencodeRouting.rules];
                          rules[index] = { ...rule, name: event.target.value };
                          updateAgent({
                            opencodeRouting: { ...normalizedAgent.opencodeRouting, rules },
                          });
                        }}
                        placeholder="规则名称"
                      />
                      <input
                        type="number"
                        value={rule.weight}
                        onChange={(event) => {
                          const rules = [...normalizedAgent.opencodeRouting.rules];
                          rules[index] = { ...rule, weight: Number(event.target.value) || 0 };
                          updateAgent({
                            opencodeRouting: { ...normalizedAgent.opencodeRouting, rules },
                          });
                        }}
                        placeholder="权重"
                      />
                      <select
                        value={rule.matchType}
                        onChange={(event) => {
                          const rules = [...normalizedAgent.opencodeRouting.rules];
                          rules[index] = { ...rule, matchType: event.target.value as "keyword" | "regex" };
                          updateAgent({
                            opencodeRouting: { ...normalizedAgent.opencodeRouting, rules },
                          });
                        }}
                      >
                        <option value="keyword">关键词</option>
                        <option value="regex">正则</option>
                      </select>
                      <input
                        value={rule.keywords.join(", ")}
                        onChange={(event) => {
                          const rules = [...normalizedAgent.opencodeRouting.rules];
                          rules[index] = {
                            ...rule,
                            keywords: event.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                          };
                          updateAgent({
                            opencodeRouting: { ...normalizedAgent.opencodeRouting, rules },
                          });
                        }}
                        placeholder={rule.matchType === "regex" ? "正则表达式（逗号分隔）" : "关键词（逗号分隔）"}
                      />
                      <input
                        value={rule.description}
                        onChange={(event) => {
                          const rules = [...normalizedAgent.opencodeRouting.rules];
                          rules[index] = { ...rule, description: event.target.value };
                          updateAgent({
                            opencodeRouting: { ...normalizedAgent.opencodeRouting, rules },
                          });
                        }}
                        placeholder="描述（可选）"
                      />
                      <button
                        type="button"
                        onClick={() => {
                          const rules = normalizedAgent.opencodeRouting.rules.filter((_, i) => i !== index);
                          updateAgent({
                            opencodeRouting: { ...normalizedAgent.opencodeRouting, rules },
                          });
                        }}
                      >
                        删除
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={() => {
                      const rules = [...normalizedAgent.opencodeRouting.rules, {
                        id: `rule-${Date.now()}`,
                        name: "新规则",
                        weight: 0,
                        keywords: [],
                        matchType: "keyword" as const,
                        description: "",
                      }];
                      updateAgent({
                        opencodeRouting: { ...normalizedAgent.opencodeRouting, rules },
                      });
                    }}
                  >
                    添加规则
                  </button>
                </div>
              </>
            )}
          </div>
        )}
      </ConfigGroup>

      <ConfigGroup
        title="MCP 服务器"
        icon={<Plug size={16} />}
        open={isMcpOpen}
        onToggle={() => {
          if (!section) {
            toggleSection("mcp");
          }
        }}
      >
        <div className="agent-section-actions">
          <button
            className="text-icon-button"
            onClick={addServer}
            type="button"
          >
            <Plus size={15} />
            添加服务器
          </button>
          <button
            className="text-icon-button"
            onClick={handleTogglePresets}
            type="button"
            aria-expanded={presetOpen}
          >
            <LayoutGrid size={15} />
            从模板添加
          </button>
          {mcpStatus && <span className="agent-status-text">{mcpStatus}</span>}
        </div>
        {presetOpen && (
          <div className="mcp-preset-panel">
            <div className="mcp-preset-panel-head">
              <span className="mcp-preset-panel-title">预设模板</span>
              <button
                className="text-icon-button is-muted"
                disabled={presetLoading}
                onClick={loadPresets}
                type="button"
              >
                {presetLoading ? <Loader2 className="phase-spinner" size={13} /> : <LayoutGrid size={13} />}
                刷新
              </button>
            </div>
            {presetError && <div className="agent-empty-hint">{presetError}</div>}
            {presets.length > 0 && (
              <div className="mcp-preset-grid">
                {presets.map((preset) => {
                  const category = preset._category ?? (preset.transport === "http" ? "远程" : "本地");
                  const busy = mcpBusyId === `preset-${preset.id}`;
                  const alreadyAdded = mcpServers.some((server) => isSameMcpServerConfig(server, presetToServer(preset)));
                  return (
                    <button
                      key={preset.id}
                      className={`mcp-preset-card ${alreadyAdded ? "is-added" : ""}`}
                      disabled={busy || alreadyAdded}
                      onClick={() => applyPreset(preset)}
                      type="button"
                      title={alreadyAdded ? "该 MCP 配置已添加" : undefined}
                    >
                      <div className="mcp-preset-card-head">
                        <strong>{preset.name}</strong>
                        <span className={`mcp-preset-category is-${preset.transport}`}>
                          {category}
                        </span>
                      </div>
                      {preset.description && (
                        <p className="mcp-preset-desc">{preset.description}</p>
                      )}
                      <div className="mcp-preset-card-foot">
                        <span className="mcp-preset-transport">{alreadyAdded ? "已添加" : preset.transport}</span>
                        {busy && <Loader2 className="phase-spinner" size={12} />}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}
        {mcpServers.length === 0 && (
          <div className="agent-empty-hint">尚未配置 MCP 服务器</div>
        )}
        {mcpServers.length > 0 && (
          <div className="mcp-configured-panel">
            <div className="mcp-preset-panel-head">
              <span className="mcp-preset-panel-title">已配置 MCP</span>
            </div>
            <div className="mcp-preset-grid">
              {mcpServers.map((server) => (
                <button
                  className={`mcp-preset-card mcp-config-card ${selectedMcpServer?.id === server.id ? "is-selected" : ""}`}
                  key={server.id}
                  onClick={() => setSelectedMcpId(server.id)}
                  type="button"
                >
                  <div className="mcp-preset-card-head">
                    <strong>{server.name || "未命名 MCP"}</strong>
                    <span className={`mcp-preset-category is-${server.transport}`}>
                      {server.transport === "http" ? "remote" : "local"}
                    </span>
                  </div>
                  <p className="mcp-preset-desc">
                    {server.transport === "http"
                      ? server.url || "未填写 URL"
                      : [server.command, ...server.args].filter(Boolean).join(" ") || "未填写命令"}
                  </p>
                  <div className="mcp-preset-card-foot">
                    <span className={`mcp-config-state ${server.connected ? "is-live" : ""}`}>
                      {server.connected ? "已连接" : server.enabled ? "已启用" : "已停用"}
                    </span>
                    {isDraftMcpServerId(server.id) && <span className="mcp-config-draft">未保存</span>}
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}
        {selectedMcpServer && (
          <div className="agent-mcp-card" key={selectedMcpServer.id}>
            <div className="agent-mcp-head">
              <input
                className="agent-mcp-name"
                value={selectedMcpServer.name}
                onChange={(event) => updateServer(selectedMcpServer.id, { name: event.target.value })}
                placeholder="服务器名称"
              />
              <label className="agent-mcp-toggle">
                <input
                  checked={selectedMcpServer.enabled}
                  onChange={(event) => updateServer(selectedMcpServer.id, { enabled: event.target.checked })}
                  type="checkbox"
                />
                <span>启用</span>
              </label>
              <span className={`agent-mcp-status ${selectedMcpServer.connected ? "is-live" : ""}`}>
                {selectedMcpServer.connected ? "已连接" : "未连接"}
              </span>
            </div>
            <label className="field">
              <span>传输方式</span>
              <select
                value={selectedMcpServer.transport}
                onChange={(event) => updateServer(selectedMcpServer.id, { transport: event.target.value as McpTransport })}
              >
                <option value="stdio">stdio</option>
                <option value="http">http</option>
              </select>
            </label>
            {selectedMcpServer.transport === "stdio" ? (
              <>
                <label className="field">
                  <span>命令</span>
                  <input
                    value={selectedMcpServer.command}
                    onChange={(event) => updateServer(selectedMcpServer.id, { command: event.target.value })}
                    placeholder="例如 npx"
                  />
                </label>
                <label className="field">
                  <span>参数（空格分隔）</span>
                  <input
                    value={selectedMcpServer.args.join(" ")}
                    onChange={(event) =>
                      updateServer(selectedMcpServer.id, {
                        args: event.target.value.split(/\s+/).filter(Boolean)
                      })
                    }
                    placeholder="例如 -y @modelcontextprotocol/server-memory"
                  />
                </label>
                <label className="field">
                  <span>工作目录</span>
                  <input
                    value={selectedMcpServer.cwd}
                    onChange={(event) => updateServer(selectedMcpServer.id, { cwd: event.target.value })}
                  />
                </label>
              </>
            ) : (
              <>
                <label className="field">
                  <span>URL</span>
                  <input
                    value={selectedMcpServer.url}
                    onChange={(event) => updateServer(selectedMcpServer.id, { url: event.target.value })}
                    placeholder="https://example.com/mcp"
                  />
                </label>
                <label className="field">
                  <span>认证方式</span>
                  <select
                    value={selectedMcpServer.authType}
                    onChange={(event) => updateServer(selectedMcpServer.id, { authType: event.target.value as McpAuthType })}
                  >
                    <option value="none">none</option>
                    <option value="bearer">bearer</option>
                    <option value="api_key">api_key</option>
                  </select>
                </label>
                {selectedMcpServer.authType !== "none" && (
                  <label className="field">
                    <span>认证令牌</span>
                    <input
                      type="password"
                      value={selectedMcpServer.authToken}
                      onChange={(event) => updateServer(selectedMcpServer.id, { authToken: event.target.value })}
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
                value={selectedMcpServer.timeoutSeconds}
                onChange={(event) => updateServer(selectedMcpServer.id, { timeoutSeconds: Number(event.target.value) || 30 })}
              />
            </label>
            <div className="agent-mcp-actions">
              <button
                className="text-icon-button"
                disabled={mcpBusyId === selectedMcpServer.id}
                onClick={() => saveServer(selectedMcpServer)}
                type="button"
              >
                <Power size={15} />
                保存
              </button>
              <button
                className="text-icon-button"
                disabled={mcpBusyId === selectedMcpServer.id}
                onClick={() => handleTestServer(selectedMcpServer)}
                type="button"
              >
                测试
              </button>
              {selectedMcpServer.connected ? (
                <button
                  className="text-icon-button"
                  disabled={mcpBusyId === selectedMcpServer.id}
                  onClick={() => handleDisconnect(selectedMcpServer)}
                  type="button"
                >
                  断开
                </button>
              ) : (
                <button
                  className="text-icon-button"
                  disabled={mcpBusyId === selectedMcpServer.id || isDraftMcpServerId(selectedMcpServer.id)}
                  onClick={() => handleConnect(selectedMcpServer)}
                  type="button"
                >
                  连接
                </button>
              )}
              <button
                className="text-icon-button is-muted"
                disabled={mcpBusyId === selectedMcpServer.id}
                onClick={() => removeServer(selectedMcpServer.id)}
                type="button"
                aria-label="删除服务器"
              >
                <Trash2 size={15} />
              </button>
              {mcpBusyId === selectedMcpServer.id && <Loader2 className="phase-spinner" size={14} />}
            </div>
            {selectedMcpFeedback && (
              <div className={`agent-mcp-feedback is-${selectedMcpFeedback.kind}`}>
                {selectedMcpFeedback.message}
              </div>
            )}
          </div>
        )}
      </ConfigGroup>

      <ConfigGroup
        title="技能包"
        icon={<Package size={16} />}
        open={isSkillsOpen}
        onToggle={() => {
          if (!section) {
            toggleSection("skills");
          }
        }}
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
            <button
              className="text-icon-button"
              disabled={installBusy}
              onClick={() => {
                handleInstallBuiltin();
                if (builtinSkills.length === 0 && !builtinLoading) {
                  loadBuiltinSkills();
                }
              }}
              type="button"
            >
              {installBusy ? <Loader2 className="phase-spinner" size={14} /> : <Sparkles size={15} />}
              安装内置 Skills
            </button>
            {skillImportBusy && <Loader2 className="phase-spinner" size={14} />}
            {skillImportStatus && <span className="agent-status-text">{skillImportStatus}</span>}
            {installStatus && <span className="agent-status-text">{installStatus}</span>}
          </div>
        </div>
        {builtinSkills.length > 0 && (
          <div className="agent-builtin-skills">
            <div className="agent-builtin-skills-head">
              <span>内置 Skills（{builtinSkills.length}）</span>
              <button
                className="text-icon-button is-muted"
                disabled={builtinLoading}
                onClick={loadBuiltinSkills}
                type="button"
              >
                {builtinLoading ? <Loader2 className="phase-spinner" size={13} /> : <Sparkles size={13} />}
                刷新
              </button>
            </div>
            {builtinSkills.map((skill) => {
              const met = isSkillDependencyMet(skill);
              return (
                <div className="agent-skill-card is-builtin" key={skill.id}>
                  <div className="agent-skill-head">
                    <div className="agent-skill-title">
                      <Sparkles size={15} />
                      <strong>{skill.name}</strong>
                      <small>v{skill.version}</small>
                      <span className="agent-skill-source">builtin</span>
                    </div>
                    <span className={`skill-dependency-status ${met ? "is-met" : "is-missing"}`}>
                      {met ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
                      {met ? "依赖已就绪" : "依赖缺失"}
                    </span>
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
                  {skill.mcpServers.length > 0 && (
                    <div className="agent-skill-deps">
                      <small>依赖 MCP：</small>
                      {skill.mcpServers.map((name) => {
                        const configured = mcpServers.some((server) => server.name === name);
                        return (
                          <span
                            className={`agent-skill-tag ${configured ? "is-met" : "is-missing"}`}
                            key={`${skill.id}-dep-${name}`}
                          >
                            {name}
                          </span>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
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
    </div>
  );
}
