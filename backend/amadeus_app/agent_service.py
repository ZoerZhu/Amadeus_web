from __future__ import annotations

import ast
import operator
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .agent_registry import ToolDefinition, tool_registry
from .domain import AgentInvokeRequest
from .search.web_search_agent import run_web_search_agent


AGENTS = [
    {
        "name": "local_agent",
        "description": "处理 Web 前端发起的本机复杂任务。当前简单版仅启用安全工具。",
        "inputModes": ["text", "json"],
        "outputModes": ["summary", "data"],
    },
    {
        "name": "mobile_agent",
        "description": "接收 HarmonyOS 移动端请求，路由到后端安全工具或简单任务处理器。",
        "inputModes": ["text", "json"],
        "outputModes": ["summary", "data"],
    },
    {
        "name": "web_search_agent",
        "description": "LangGraph 搜索图：search_agent 规划和重排，reader_agent 读取网页，critic_agent 交叉验证，writer_agent 生成回答。",
        "inputModes": ["text", "json"],
        "outputModes": ["summary", "sources", "warnings"],
    },
]

CALCULATE_RE = re.compile(r"[0-9][0-9\s+\-*/().%]*[0-9)]")
TIME_KEYWORDS = ("几点", "时间", "当前时间", "现在", "today", "time", "date")
MATH_KEYWORDS = ("计算", "算", "加", "减", "乘", "除", "calculate", "math")
SEARCH_KEYWORDS = ("搜索", "查询", "查一下", "查找", "检索", "search", "web", "资料", "来源")


async def invoke_agent_request(request: AgentInvokeRequest) -> dict[str, Any]:
    try:
        if request.action == "query_capabilities":
            return capabilities_response()
        if request.action == "call_tool":
            return await call_tool(request)
        if request.action == "call_agent":
            return await call_agent(request)
        return error_response(f"Unsupported action: {request.action}")
    except Exception as error:
        return error_response(str(error) or error.__class__.__name__)


def capabilities_response() -> dict[str, Any]:
    return {
        "ok": True,
        "summary": "当前后端已启用 simple agent、移动端桥接协议和基础安全工具。",
        "data": {
            "agents": AGENTS,
            "tools": tool_registry.describe_tools(),
            "defaultEndpoint": "/api/agent/invoke",
            "protocol": {
                "actions": ["query_capabilities", "call_tool", "call_agent"],
                "targetTypes": ["tool", "agent", "auto"],
            },
            "limits": {
                "highRiskToolsEnabled": False,
                "shellEnabled": False,
                "browserAutomationEnabled": False,
            },
        },
    }


async def call_tool(request: AgentInvokeRequest) -> dict[str, Any]:
    target = request.target.strip() or infer_tool_name(request)
    tool = tool_registry.get(target)
    if tool is None or tool.handler is None:
        return error_response(f"Unknown tool: {target or '(empty)'}", data={"availableTools": tool_registry.describe_tools()})
    if tool.permission != "safe":
        return error_response(f"Tool requires confirmation and is not enabled in simple agent: {tool.name}")

    args = build_tool_args(request)
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


async def call_agent(request: AgentInvokeRequest) -> dict[str, Any]:
    target = (request.target or "auto").strip()
    if target not in {"", "auto", "local_agent", "mobile_agent", "simple_agent", "web_search_agent"}:
        return error_response(
            f"Unknown agent: {target}",
            data={"availableAgents": AGENTS},
        )

    inferred_tool = infer_tool_name(request)
    if inferred_tool:
        routed = AgentInvokeRequest(
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
        "summary": "simple agent 已收到任务；当前版本只执行能力查询、时间、计算和回显类安全任务。",
        "data": {
            "agent": resolve_agent_name(request),
            "intent": request.intent,
            "payload": request.payload,
            "rawArguments": request.raw_arguments,
            "client": request.client,
            "nextSuggestedAction": "把明确工具名放入 target，或将任务描述为时间/计算类请求。",
        },
    }


def resolve_agent_name(request: AgentInvokeRequest) -> str:
    if request.target == "web_search_agent":
        return "web_search_agent"
    if request.target in {"local_agent", "mobile_agent", "simple_agent"}:
        return request.target
    if "harmony" in request.client.lower() or "mobile" in request.client.lower():
        return "mobile_agent"
    return "local_agent"


def build_tool_args(request: AgentInvokeRequest) -> dict[str, Any]:
    args: dict[str, Any] = {}
    args.update(request.payload)
    args.update(request.raw_arguments)
    if request.intent and "intent" not in args:
        args["intent"] = request.intent
    if request.intent and "query" not in args and request.target in {"web_search", "web_search_agent"}:
        args["query"] = request.intent
    if "expression" not in args:
        expression = infer_expression(request.intent)
        if expression:
            args["expression"] = expression
    return args


def infer_tool_name(request: AgentInvokeRequest) -> str:
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
    if target == "web_search_agent" or any(keyword in text for keyword in SEARCH_KEYWORDS):
        return "web_search"
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
        "summary": "请求已由 simple agent 接收。",
        "data": {
            "intent": intent,
            "payload": {key: value for key, value in args.items() if key != "intent"},
        },
    }


async def describe_capabilities(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": "已返回 Agent 能力列表。",
        "data": capabilities_response()["data"],
    }


async def web_search(args: dict[str, Any]) -> dict[str, Any]:
    return await run_web_search_agent(args)


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
            description="回显请求内容，用于移动端桥接连通性测试。",
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


register_builtin_tools()
