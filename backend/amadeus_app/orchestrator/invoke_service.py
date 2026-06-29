from __future__ import annotations

import ast
import operator
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..builtin_tool_registry import ToolDefinition, tool_registry
from ..domain import OrchestratorInvokeRequest
from .capability_adapters import CapabilityExecutionContext, default_capability_registry
from .capabilities import CapabilityGateway
from .domain import OrchestratorSettings


AGENTS = [
    {
        "name": "local_agent",
        "description": "兼容旧 Agent 调用协议；复杂任务会引导到 orchestrator capability 体系。",
        "inputModes": ["text", "json"],
        "outputModes": ["summary", "data"],
    },
    {
        "name": "web_search_agent",
        "description": "LangGraph 搜索图：search_agent 规划和重排，reader_agent 读取网页，critic_agent 交叉验证，writer_agent 生成回答。",
        "inputModes": ["text", "json"],
        "outputModes": ["summary", "sources", "warnings"],
    },
    {
        "name": "doc_writer_agent",
        "description": "LangGraph 文档写作图：规划文档、按需调用 web_search_agent，并导出 md、txt、docx、csv、xlsx 到受控目录。",
        "inputModes": ["text", "json"],
        "outputModes": ["markdown", "text", "docx", "csv", "xlsx", "file", "summary"],
    },
    {
        "name": "file_reader_agent",
        "description": "安全读取工作区内文本文件，支持 read/list/stat，默认屏蔽密钥、依赖目录和构建产物。",
        "inputModes": ["text", "json"],
        "outputModes": ["file_content", "directory_entries", "metadata"],
    },
    {
        "name": "image_understand_agent",
        "description": "视觉理解 Agent：读取工作区内已上传图片或截图，调用 OpenAI-compatible 视觉模型，返回中文图像理解摘要。",
        "inputModes": ["image", "text", "json"],
        "outputModes": ["summary", "vision_description", "metadata"],
    },
    {
        "name": "todo_task_agent",
        "description": "LangGraph 待办任务图：规划任务、拆解子任务、持久化 JSON 任务状态，并支持列表、更新、完成、阻塞和归档。",
        "inputModes": ["text", "json"],
        "outputModes": ["tasks", "summary", "metadata"],
    },
]

CALCULATE_RE = re.compile(r"[0-9][0-9\s+\-*/().%]*[0-9)]")
TIME_KEYWORDS = ("几点", "时间", "当前时间", "现在", "today", "time", "date")
MATH_KEYWORDS = ("计算", "算", "加", "减", "乘", "除", "calculate", "math")
SEARCH_KEYWORDS = ("搜索", "查询", "查一下", "查找", "检索", "search", "web", "资料", "来源")
DOC_WRITER_KEYWORDS = (
    "写文档",
    "编写文档",
    "生成文档",
    "起草",
    "markdown",
    "md",
    "docx",
    "txt",
    "xlsx",
    "csv",
    "表格",
    "document",
    "doc",
    "write",
)
FILE_READER_KEYWORDS = ("读文件", "读取文件", "查看文件", "列目录", "文件列表", "file", "read file", "list files", "stat file")
IMAGE_KEYWORDS = ("图片", "图像", "截图", "视觉", "image", "vision", "screenshot", "screen capture")
TODO_TASK_KEYWORDS = ("待办", "todo", "任务列表", "创建任务", "新增任务", "任务规划", "任务拆解", "task list", "todo list")
COMPAT_TOOL_CAPABILITIES = {
    "web_search": "web_search",
    "file_reader": "file_read",
    "image_understand": "vision",
    "doc_writer": "doc_writer",
    "todo_task": "todo_task",
}


async def invoke_orchestrator_request(request: OrchestratorInvokeRequest) -> dict[str, Any]:
    try:
        if request.action == "query_capabilities":
            return orchestrator_capabilities_response()
        if request.action == "call_tool":
            return await call_tool(request)
        if request.action == "call_agent":
            return await call_agent(request)
        return error_response(f"Unsupported action: {request.action}")
    except Exception as error:
        return error_response(str(error) or error.__class__.__name__)


def orchestrator_capabilities_response(settings: OrchestratorSettings | None = None) -> dict[str, Any]:
    capabilities = CapabilityGateway(trust_mode=False).list_capabilities()
    if settings is not None:
        enabled = set(settings.enabled_capabilities)
        if enabled:
            capabilities = [item for item in capabilities if item.get("name") in enabled]
        if not settings.browser_enabled:
            capabilities = [item for item in capabilities if item.get("name") != "browser"]

    data: dict[str, Any] = {
        "runtime": "orchestrator_v2",
        "agents": AGENTS,
        "tools": tool_registry.describe_tools(),
        "capabilities": capabilities,
        "defaultEndpoint": "/api/orchestrator/tasks",
        "invokeEndpoint": "/api/orchestrator/invoke",
        "compatInvokeEndpoint": "/api/agent/invoke",
        "protocol": {
            "actions": ["query_capabilities", "call_tool", "call_agent"],
            "targetTypes": ["tool", "agent", "auto"],
        },
        "fileUpload": {
            "endpoint": "/api/files/upload",
            "method": "POST",
            "contentType": "multipart/form-data",
            "fields": {
                "file": "required UploadFile",
                "device": "host",
                "overwrite": "boolean, default false",
            },
            "storagePattern": "agent_uploads/host/{yyyy-mm-dd}/{textType|image}/{originalFilename}",
            "nextStep": "Use returned file.path with file_reader for documents/text, or image_understand_agent for images.",
        },
        "limits": {
            "highRiskToolsEnabled": False,
            "shellEnabled": False,
            "browserAutomationEnabled": bool(settings.browser_enabled) if settings is not None else False,
        },
    }
    if settings is not None:
        data["settings"] = settings.model_dump(by_alias=True)
    return {
        "ok": True,
        "summary": "当前 Orchestrator invoke 入口已接入 capability gateway。",
        "data": data,
    }


async def call_tool(request: OrchestratorInvokeRequest) -> dict[str, Any]:
    target = request.target.strip() or infer_tool_name(request)
    tool = tool_registry.get(target)
    if tool is None or tool.handler is None:
        return error_response(f"Unknown tool: {target or '(empty)'}", data={"availableTools": tool_registry.describe_tools()})

    args = build_tool_args(request)
    if target in COMPAT_TOOL_CAPABILITIES:
        return await call_orchestrator_capability(target, args, request)
    if tool.permission != "safe":
        return error_response(f"Tool requires confirmation and is not enabled in the compatibility endpoint: {tool.name}")

    result = await tool.handler(args)
    return {
        "ok": True,
        "summary": result.get("summary") or f"{tool.name} 执行完成。",
        "data": {
            "tool": tool.name,
            "args": args,
            "result": result.get("data", {}),
        },
    }


async def call_agent(request: OrchestratorInvokeRequest) -> dict[str, Any]:
    target = (request.target or "auto").strip()
    if target not in {
        "",
        "auto",
        "local_agent",
        "simple_agent",
        "web_search_agent",
        "doc_writer_agent",
        "file_reader_agent",
        "image_understand_agent",
        "todo_task_agent",
    }:
        return error_response(
            f"Unknown agent: {target}",
            data={"availableAgents": AGENTS},
        )

    inferred_tool = infer_tool_name(request)
    if inferred_tool:
        routed = OrchestratorInvokeRequest(
            action="call_tool",
            targetType="tool",
            target=inferred_tool,
            intent=request.intent,
            payload=request.payload,
            rawArguments=request.raw_arguments,
            client=request.client,
            requestedAt=request.requested_at,
        )
        tool_response = await call_tool(routed)
        if tool_response.get("ok"):
            tool_response["summary"] = f"{resolve_agent_name(request)} 已完成路由：{tool_response['summary']}"
        return tool_response

    return {
        "ok": True,
        "summary": "兼容 Agent 入口已收到任务；复杂任务请使用 /api/orchestrator/tasks。",
        "data": {
            "agent": resolve_agent_name(request),
            "intent": request.intent,
            "payload": request.payload,
            "rawArguments": request.raw_arguments,
            "client": request.client,
            "nextSuggestedAction": "把明确工具名放入 target，或提交到 /api/orchestrator/tasks 执行多步骤任务。",
        },
    }


def capabilities_response() -> dict[str, Any]:
    return orchestrator_capabilities_response()


def resolve_agent_name(request: OrchestratorInvokeRequest) -> str:
    if request.target == "todo_task_agent":
        return "todo_task_agent"
    if request.target == "file_reader_agent":
        return "file_reader_agent"
    if request.target == "image_understand_agent":
        return "image_understand_agent"
    if request.target == "doc_writer_agent":
        return "doc_writer_agent"
    if request.target == "web_search_agent":
        return "web_search_agent"
    if request.target in {"local_agent", "simple_agent"}:
        return request.target
    return "local_agent"


def build_tool_args(request: OrchestratorInvokeRequest) -> dict[str, Any]:
    args: dict[str, Any] = {}
    args.update(request.payload)
    args.update(request.raw_arguments)
    if request.intent and "intent" not in args:
        args["intent"] = request.intent
    if request.intent and "query" not in args and request.target in {"web_search", "web_search_agent"}:
        args["query"] = request.intent
    if request.intent and "instruction" not in args and request.target in {"doc_writer", "doc_writer_agent"}:
        args["instruction"] = request.intent
    if request.intent and "path" not in args and request.target in {"file_reader", "file_reader_agent"}:
        path = infer_path_from_text(request.intent)
        if path:
            args["path"] = path
    if request.intent and "prompt" not in args and request.target in {"image_understand", "image_understand_agent"}:
        args["prompt"] = request.intent
    if "expression" not in args:
        expression = infer_expression(request.intent)
        if expression:
            args["expression"] = expression
    return args


async def call_orchestrator_capability(
    tool_name: str,
    args: dict[str, Any],
    request: OrchestratorInvokeRequest | None = None,
) -> dict[str, Any]:
    capability = COMPAT_TOOL_CAPABILITIES.get(tool_name, tool_name)
    gateway = CapabilityGateway(trust_mode=False)
    decision = gateway.decide(capability)
    if not decision.allowed:
        return error_response(
            f"{capability} requires orchestrator permission; use /api/orchestrator/tasks for this operation.",
            data={
                "tool": tool_name,
                "capability": capability,
                "risk": decision.risk,
                "reason": decision.reason,
            },
        )

    workspace_path = compat_workspace_from_args(args)
    context = CapabilityExecutionContext(
        task_id="orchestrator-invoke-compat",
        prompt=compat_prompt_from_request(request, args),
        workspace_path=workspace_path,
        settings=OrchestratorSettings(trustMode=False, defaultWorkspace=workspace_path),
        metadata={"source": "orchestrator_invoke_compat", "tool": tool_name},
    )
    result = await default_capability_registry.execute(capability, args, context)
    return {
        "ok": bool(result.get("ok", True)),
        "summary": str(result.get("summary") or f"{capability} completed."),
        "data": {
            "tool": tool_name,
            "capability": capability,
            "args": args,
            "result": result.get("data", {}),
            "artifactIds": result.get("artifactIds", []),
        },
    }


def compat_prompt_from_request(request: OrchestratorInvokeRequest | None, args: dict[str, Any]) -> str:
    if request is not None and request.intent.strip():
        return request.intent.strip()
    for key in ("query", "instruction", "prompt", "intent", "title", "topic", "task"):
        value = args.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def compat_workspace_from_args(args: dict[str, Any]) -> str:
    for key in ("workspacePath", "workspace", "root", "basePath"):
        value = args.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "."


def infer_tool_name(request: OrchestratorInvokeRequest) -> str:
    target = request.target.strip()
    if target and tool_registry.get(target) is not None:
        return target

    text = " ".join(
        [
            request.intent,
            str(request.payload),
            str(request.raw_arguments),
        ]
    ).lower()
    if any(keyword in text for keyword in ("capabilities", "能力", "工具列表")):
        return "describe_capabilities"
    if target == "file_reader_agent" or any(keyword in text for keyword in FILE_READER_KEYWORDS):
        return "file_reader"
    if request.payload.get("path") and str(request.payload.get("action", "")).lower() in {"read", "list", "stat"}:
        return "file_reader"
    if target == "image_understand_agent" or any(keyword in text for keyword in IMAGE_KEYWORDS):
        if request.payload.get("attachments") or request.payload.get("images") or request.payload.get("path"):
            return "image_understand"
    if target == "doc_writer_agent" or any(keyword in text for keyword in DOC_WRITER_KEYWORDS):
        return "doc_writer"
    if target == "web_search_agent" or any(keyword in text for keyword in SEARCH_KEYWORDS):
        return "web_search"
    if target == "todo_task_agent" or any(keyword in text for keyword in TODO_TASK_KEYWORDS):
        return "todo_task"
    if str(request.payload.get("action", "")).lower() in {
        "create",
        "plan",
        "list",
        "get",
        "update",
        "start",
        "complete",
        "block",
        "archive",
        "delete",
        "add_note",
        "summary",
    } and any(key in request.payload for key in {"tasks", "task", "taskId", "id", "status", "priority", "project"}):
        return "todo_task"
    if any(keyword in text for keyword in TIME_KEYWORDS):
        return "get_current_time"
    if any(keyword in text for keyword in MATH_KEYWORDS) or infer_expression(request.intent):
        return "calculate"
    if request.intent or request.payload or request.raw_arguments:
        return "echo"
    return ""


def infer_expression(intent: str) -> str:
    match = CALCULATE_RE.search(intent)
    if not match:
        return ""
    return match.group(0).strip()


def infer_path_from_text(text: str) -> str:
    match = re.search(r"([\w./\\-]+\.[A-Za-z0-9_]+)", text)
    if match:
        return match.group(1).strip()
    return ""


def error_response(message: str, *, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "summary": message,
        "data": data or {},
    }


async def get_current_time(args: dict[str, Any]) -> dict[str, Any]:
    timezone = str(args.get("timezone") or args.get("tz") or "Asia/Shanghai").strip()
    try:
        now = datetime.now(ZoneInfo(timezone))
    except Exception:
        timezone = "Asia/Shanghai"
        now = datetime.now(ZoneInfo(timezone))
    return {
        "summary": f"当前时间是 {now.strftime('%Y-%m-%d %H:%M:%S')} ({timezone})。",
        "data": {
            "timezone": timezone,
            "iso": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
        },
    }


async def calculate(args: dict[str, Any]) -> dict[str, Any]:
    expression = str(args.get("expression") or "").strip()
    if not expression:
        raise ValueError("Missing expression")
    value = safe_calculate(expression)
    return {
        "summary": f"{expression} = {value}",
        "data": {
            "expression": expression,
            "value": value,
        },
    }


async def echo(args: dict[str, Any]) -> dict[str, Any]:
    intent = str(args.get("intent") or "").strip()
    return {
        "summary": "请求已由兼容 Agent 入口接收。",
        "data": {
            "intent": intent,
            "payload": {key: value for key, value in args.items() if key != "intent"},
        },
    }


async def describe_capabilities(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": "已返回 Orchestrator 能力列表。",
        "data": orchestrator_capabilities_response()["data"],
    }


async def web_search(args: dict[str, Any]) -> dict[str, Any]:
    return await call_orchestrator_capability("web_search", args)


async def doc_writer(args: dict[str, Any]) -> dict[str, Any]:
    return await call_orchestrator_capability("doc_writer", args)


async def file_reader(args: dict[str, Any]) -> dict[str, Any]:
    return await call_orchestrator_capability("file_reader", args)


async def image_understand(args: dict[str, Any]) -> dict[str, Any]:
    if "attachments" not in args and "images" not in args and args.get("path"):
        args = {**args, "attachments": [{"path": args["path"], "mediaType": "image"}]}
    return await call_orchestrator_capability("image_understand", args)


async def todo_task(args: dict[str, Any]) -> dict[str, Any]:
    return await call_orchestrator_capability("todo_task", args)


BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def safe_calculate(expression: str) -> int | float:
    tree = ast.parse(expression, mode="eval")
    value = eval_math_node(tree)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def eval_math_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return eval_math_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY_OPS:
        return UNARY_OPS[type(node.op)](eval_math_node(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in BIN_OPS:
        left = eval_math_node(node.left)
        right = eval_math_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 12:
            raise ValueError("Exponent is too large")
        return BIN_OPS[type(node.op)](left, right)
    raise ValueError("Only basic arithmetic expressions are supported")


def register_builtin_tools() -> None:
    tool_registry.register(
        ToolDefinition(
            name="get_current_time",
            description="获取当前时间，默认使用 Asia/Shanghai 时区。",
            parameters={
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "description": "IANA timezone, e.g. Asia/Shanghai"},
                },
            },
            handler=get_current_time,
        )
    )
    tool_registry.register(
        ToolDefinition(
            name="calculate",
            description="计算基础四则运算表达式，支持括号、加减乘除、取模和小范围乘方。",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Arithmetic expression, e.g. 23 * (19 + 2)"},
                },
                "required": ["expression"],
            },
            handler=calculate,
        )
    )
    tool_registry.register(
        ToolDefinition(
            name="echo",
            description="回显请求内容，用于接口连通性测试。",
            parameters={
                "type": "object",
                "properties": {
                    "intent": {"type": "string"},
                },
            },
            handler=echo,
        )
    )
    tool_registry.register(
        ToolDefinition(
            name="describe_capabilities",
            description="返回当前后端 Agent 和工具能力列表。",
            parameters={"type": "object", "properties": {}},
            handler=describe_capabilities,
        )
    )
    tool_registry.register(
        ToolDefinition(
            name="web_search",
            description="调用 LangGraph web_search_agent，完成查询规划、网页读取、结果重排、风险提示和回答生成。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索查询词或用户任务"},
                    "freshness": {
                        "type": "string",
                        "enum": ["any", "day", "week", "month", "year"],
                        "description": "时效要求",
                    },
                    "domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "优先或限定的可信域名",
                    },
                    "excludeDomains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "排除域名",
                    },
                    "maxResults": {"type": "integer", "minimum": 1, "maximum": 20},
                    "fetchContent": {"type": "boolean", "description": "是否抓取前几条网页正文"},
                },
                "required": ["query"],
            },
            handler=web_search,
        )
    )
    tool_registry.register(
        ToolDefinition(
            name="doc_writer",
            description="调用 LangGraph doc_writer_agent，生成 md、txt、docx、csv、xlsx 文档，可按需调用 web_search_agent，并写入 generated_docs 等受控目录。",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "文档标题"},
                    "topic": {"type": "string", "description": "文档主题"},
                    "instruction": {"type": "string", "description": "写作需求或任务说明"},
                    "format": {
                        "type": "string",
                        "enum": ["md", "txt", "docx", "csv", "xlsx"],
                        "description": "输出格式；未填写时可从 fileName/outputPath 后缀推断，默认 md",
                    },
                    "audience": {"type": "string", "description": "目标读者"},
                    "tone": {"type": "string", "description": "写作语气"},
                    "sections": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "章节标题列表",
                    },
                    "keyPoints": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "核心要点",
                    },
                    "content": {"type": "string", "description": "已有草稿或素材"},
                    "rows": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "csv/xlsx 可选结构化行数据；对象键会作为表头",
                    },
                    "table": {
                        "type": "array",
                        "items": {"type": "array"},
                        "description": "csv/xlsx 可选二维表；第一行会作为表头",
                    },
                    "useWebSearch": {
                        "oneOf": [{"type": "boolean"}, {"type": "string", "enum": ["auto", "true", "false"]}],
                        "description": "是否调用 web_search_agent，可为 true/false/auto",
                    },
                    "searchQuery": {"type": "string", "description": "检索查询词"},
                    "sources": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "手动提供的来源列表",
                    },
                    "save": {"type": "boolean", "description": "是否保存到文件，默认 true"},
                    "outputPath": {"type": "string", "description": "工作区内相对输出目录，或 md/txt/docx/csv/xlsx 文件路径"},
                    "fileName": {"type": "string", "description": "输出文件名"},
                    "overwrite": {"type": "boolean", "description": "是否覆盖已存在文件，默认 false"},
                    "frontMatter": {"type": "boolean", "description": "是否生成 YAML front matter"},
                    "llmWrite": {"type": "boolean", "description": "是否启用 LLM 高级写作，默认 true；失败时自动回退到结构化写作"},
                    "writingMode": {
                        "type": "string",
                        "enum": ["auto", "llm", "advanced", "structured", "template"],
                        "description": "写作模式。llm/advanced 会让模型根据资料生成自然段；structured/template 使用规则结构化输出",
                    },
                },
            },
            handler=doc_writer,
            permission="confirm",
        )
    )
    tool_registry.register(
        ToolDefinition(
            name="file_reader",
            description="安全读取工作区内文本文件，支持读取文件、列目录和查看元信息；禁止读取 .env、.git、node_modules、.venv 等敏感路径。",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "list", "stat"],
                        "description": "读取动作：read 读取文本文件，list 列目录，stat 查看元信息",
                    },
                    "path": {"type": "string", "description": "工作区内相对路径，默认 ."},
                    "startLine": {"type": "integer", "minimum": 1, "description": "读取起始行，仅 read 有效"},
                    "endLine": {"type": "integer", "minimum": 1, "description": "读取结束行，仅 read 有效"},
                    "maxBytes": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1048576,
                        "description": "最多读取字节数，默认 131072",
                    },
                    "includeLineNumbers": {"type": "boolean", "description": "是否在 content 中加入行号"},
                    "recursive": {"type": "boolean", "description": "是否递归列目录，仅 list 有效"},
                    "includeHidden": {"type": "boolean", "description": "是否展示隐藏文件，受敏感路径限制约束"},
                    "maxEntries": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1000,
                        "description": "目录最多返回条目数，默认 200",
                    },
                },
            },
            handler=file_reader,
        )
    )
    tool_registry.register(
        ToolDefinition(
            name="image_understand",
            description="调用 image_understand_agent 理解工作区内已上传图片或截图，返回中文视觉摘要；最多一次 4 张图片。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "单张图片的工作区内相对路径"},
                    "attachments": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "originalFilename": {"type": "string"},
                                "mediaType": {"type": "string", "enum": ["image"]},
                                "contentType": {"type": "string"},
                            },
                            "required": ["path"],
                        },
                    },
                    "prompt": {"type": "string", "description": "用户问题或视觉理解重点"},
                    "vision": {"type": "object", "description": "可选视觉模型配置"},
                    "model": {"type": "object", "description": "可选基础模型配置，用于继承 Provider/Base URL/API Key"},
                },
            },
            handler=image_understand,
        )
    )
    tool_registry.register(
        ToolDefinition(
            name="todo_task",
            description="调用 LangGraph todo_task_agent，管理工作区内 JSON 待办任务；支持 create/plan/list/get/update/start/complete/block/archive/add_note/summary。",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "create",
                            "plan",
                            "list",
                            "get",
                            "update",
                            "start",
                            "complete",
                            "block",
                            "archive",
                            "delete",
                            "add_note",
                            "summary",
                        ],
                        "description": "待办任务动作；delete 需要 confirmDelete=true",
                    },
                    "title": {"type": "string", "description": "创建任务或计划父任务标题"},
                    "task": {"type": "string", "description": "单个任务标题"},
                    "tasks": {
                        "type": "array",
                        "items": {
                            "oneOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string"},
                                        "description": {"type": "string"},
                                        "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
                                        "status": {"type": "string"},
                                        "tags": {"type": "array", "items": {"type": "string"}},
                                        "project": {"type": "string"},
                                        "dueAt": {"type": "string"},
                                    },
                                },
                            ]
                        },
                        "description": "批量创建任务；plan 动作下可作为子任务列表",
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "plan 动作用于拆解子任务",
                    },
                    "id": {"type": "string", "description": "任务 ID"},
                    "taskId": {"type": "string", "description": "任务 ID，等同 id"},
                    "matchTitle": {"type": "string", "description": "没有 ID 时按标题片段匹配任务；多命中会拒绝"},
                    "description": {"type": "string", "description": "任务说明"},
                    "status": {
                        "type": "string",
                        "enum": ["todo", "in_progress", "blocked", "done", "archived"],
                        "description": "任务状态",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "urgent"],
                        "description": "任务优先级",
                    },
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
                    "project": {"type": "string", "description": "项目或分组"},
                    "dueAt": {"type": "string", "description": "截止时间，建议 ISO 8601"},
                    "note": {"type": "string", "description": "备注；block/add_note 时常用"},
                    "filters": {"type": "object", "description": "list 过滤条件"},
                    "query": {"type": "string", "description": "list 文本搜索条件"},
                    "includeArchived": {"type": "boolean", "description": "list 是否包含归档任务"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "storePath": {
                        "type": "string",
                        "description": "工作区内 .json 存储路径，默认 agent_state/todo_tasks.json",
                    },
                    "confirmDelete": {"type": "boolean", "description": "硬删除确认，delete 动作必填 true"},
                    "deleteSubtasks": {"type": "boolean", "description": "delete 时是否同时删除子任务"},
                },
            },
            handler=todo_task,
        )
    )


register_builtin_tools()
