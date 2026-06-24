import type { AgentSettings, OpencodeRoutingRule } from "./types";

export const DEFAULT_OPENCODE_ROUTING_RULES: OpencodeRoutingRule[] = [
  {
    id: "multi_file_edit",
    name: "跨文件修改",
    weight: 30,
    keywords: ["跨文件", "多个文件", "multi-file", "multifile", "修改多个"],
    matchType: "keyword",
    description: "跨文件修改任务"
  },
  {
    id: "fix_bug",
    name: "修 Bug",
    weight: 30,
    keywords: ["修 bug", "修复 bug", "fix bug", "bug 修复", "解决 bug", "修bug"],
    matchType: "keyword",
    description: "修复 bug"
  },
  {
    id: "refactor",
    name: "重构",
    weight: 20,
    keywords: ["重构", "refactor", "重写代码"],
    matchType: "keyword",
    description: "代码重构"
  },
  {
    id: "new_module",
    name: "新模块",
    weight: 20,
    keywords: ["新模块", "新增模块", "new module", "创建模块", "新建模块"],
    matchType: "keyword",
    description: "新增模块/功能"
  },
  {
    id: "run_tests",
    name: "跑测试",
    weight: 30,
    keywords: [
      "跑测试",
      "运行测试",
      "run test",
      "run tests",
      "执行测试",
      "pytest",
      "jest",
      "vitest",
      "unittest",
      "跑一下测试",
      "跑下测试"
    ],
    matchType: "keyword",
    description: "运行测试"
  },
  {
    id: "build",
    name: "构建",
    weight: 15,
    keywords: ["构建", "build", "编译", "compile"],
    matchType: "keyword",
    description: "构建项目"
  },
  {
    id: "install_deps",
    name: "安装依赖",
    weight: 15,
    keywords: [
      "安装依赖",
      "install dependency",
      "install dependencies",
      "npm install",
      "pip install",
      "pnpm install",
      "yarn add"
    ],
    matchType: "keyword",
    description: "安装依赖"
  },
  {
    id: "build_config",
    name: "修改构建配置",
    weight: 15,
    keywords: ["构建配置", "build config", "webpack", "vite", "tsconfig", "pyproject", "package.json", "cargo.toml", "cmake"],
    matchType: "keyword",
    description: "修改构建配置"
  },
  {
    id: "implement_feature",
    name: "实现功能",
    weight: 15,
    keywords: ["实现功能", "实现", "implement", "开发功能", "编写代码", "写代码"],
    matchType: "keyword",
    description: "实现新功能"
  },
  {
    id: "search_code",
    name: "查代码",
    weight: -20,
    keywords: ["查代码", "搜索代码", "search code", "find code", "grep", "查找调用"],
    matchType: "keyword",
    description: "只读: 查代码"
  },
  {
    id: "find_file",
    name: "找文件",
    weight: -15,
    keywords: ["找文件", "查找文件", "find file", "locate file", "哪个文件"],
    matchType: "keyword",
    description: "只读: 找文件"
  },
  {
    id: "explain",
    name: "解释实现",
    weight: -25,
    keywords: ["解释", "explain", "说明实现", "怎么实现", "如何实现", "原理是什么"],
    matchType: "keyword",
    description: "只读: 解释实现"
  },
  {
    id: "read_only",
    name: "只读分析",
    weight: -25,
    keywords: ["只读", "read-only", "分析一下", "analyze", "检查一下", "inspect", "审查", "review code", "代码审查"],
    matchType: "keyword",
    description: "只读分析"
  },
  {
    id: "write_docs",
    name: "写文档",
    weight: -15,
    keywords: ["写文档", "写说明", "write doc", "write docs", "写 markdown", "写readme"],
    matchType: "keyword",
    description: "写文档"
  }
];

export function cloneOpencodeRoutingRules(rules = DEFAULT_OPENCODE_ROUTING_RULES): OpencodeRoutingRule[] {
  return rules.map((rule) => ({
    ...rule,
    keywords: Array.isArray(rule.keywords) ? [...rule.keywords] : [],
    matchType: rule.matchType === "regex" ? "regex" : "keyword",
    description: typeof rule.description === "string" ? rule.description : ""
  }));
}

export const DEFAULT_AGENT_SETTINGS: AgentSettings = {
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
  opencodeEnabled: true,
  opencodeRouting: {
    enabled: true,
    allowThreshold: 60,
    ambiguousThreshold: 40,
    allowLlmRejudge: true,
    forceAllowKeywords: ["opencode", "coding agent", "代码 agent", "强 coding", "用 opencode", "使用 opencode", "用 coding agent"],
    forceDenyKeywords: [],
    rules: cloneOpencodeRoutingRules()
  }
};

export function normalizeAgentSettings(value?: Partial<AgentSettings> | null): AgentSettings {
  const routing = {
    ...DEFAULT_AGENT_SETTINGS.opencodeRouting,
    ...(value?.opencodeRouting ?? {})
  };
  const rules = Array.isArray(routing.rules) && routing.rules.length
    ? cloneOpencodeRoutingRules(routing.rules)
    : cloneOpencodeRoutingRules();
  const forceAllowKeywords = Array.isArray(routing.forceAllowKeywords)
    ? [...routing.forceAllowKeywords]
    : [...DEFAULT_AGENT_SETTINGS.opencodeRouting.forceAllowKeywords];
  const forceDenyKeywords = Array.isArray(routing.forceDenyKeywords)
    ? [...routing.forceDenyKeywords]
    : [...DEFAULT_AGENT_SETTINGS.opencodeRouting.forceDenyKeywords];

  return {
    ...DEFAULT_AGENT_SETTINGS,
    ...(value ?? {}),
    opencodeRouting: {
      ...routing,
      forceAllowKeywords,
      forceDenyKeywords,
      rules
    }
  };
}
