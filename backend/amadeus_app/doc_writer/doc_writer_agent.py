from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph

from ..search.web_search_agent import run_web_search_agent


DocFormat = Literal["md"]
WorkspacePath = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = Path("generated_docs")
DEFAULT_SECTIONS = ["背景与目标", "核心内容", "实施步骤", "注意事项", "后续工作"]
SEARCH_HINTS = (
    "最新",
    "当前",
    "实时",
    "搜索",
    "查询",
    "资料",
    "来源",
    "引用",
    "官方",
    "文档",
    "research",
    "search",
    "source",
    "reference",
    "latest",
)


class DocWriterPlan(TypedDict, total=False):
    title: str
    format: DocFormat
    audience: str
    tone: str
    sections: list[str]
    use_web_search: bool
    search_query: str
    save: bool
    overwrite: bool
    output_path: str
    file_name: str
    front_matter: bool


class DocWriterState(TypedDict, total=False):
    intent: str
    payload: dict[str, Any]
    raw_arguments: dict[str, Any]
    plan: DocWriterPlan
    research: dict[str, Any]
    outline: list[dict[str, Any]]
    markdown: str
    output: dict[str, Any]
    warnings: list[dict[str, Any]]


def build_doc_writer_graph():
    builder = StateGraph(DocWriterState)
    builder.add_node("planner_agent", planner_agent)
    builder.add_node("research_agent", research_agent)
    builder.add_node("outline_agent", outline_agent)
    builder.add_node("markdown_writer_agent", markdown_writer_agent)
    builder.add_node("file_writer_agent", file_writer_agent)
    builder.add_edge(START, "planner_agent")
    builder.add_edge("planner_agent", "research_agent")
    builder.add_edge("research_agent", "outline_agent")
    builder.add_edge("outline_agent", "markdown_writer_agent")
    builder.add_edge("markdown_writer_agent", "file_writer_agent")
    builder.add_edge("file_writer_agent", END)
    return builder.compile()


DOC_WRITER_GRAPH = None


def get_doc_writer_graph():
    global DOC_WRITER_GRAPH
    if DOC_WRITER_GRAPH is None:
        DOC_WRITER_GRAPH = build_doc_writer_graph()
    return DOC_WRITER_GRAPH


async def run_doc_writer_agent(args: dict[str, Any]) -> dict[str, Any]:
    intent = str(args.get("intent") or args.get("instruction") or args.get("topic") or "").strip()
    final_state = await get_doc_writer_graph().ainvoke(
        {
            "intent": intent,
            "payload": args,
            "raw_arguments": {},
            "warnings": [],
        }
    )
    output = final_state.get("output", {})
    plan = final_state.get("plan", {})
    markdown = final_state.get("markdown", "")
    title = plan.get("title", "Untitled Document")
    saved = output.get("saved", False)
    path_text = output.get("path", "")
    if saved:
        summary = f"已生成 Markdown 文档《{title}》，保存到 {path_text}。"
    else:
        summary = f"已生成 Markdown 文档《{title}》，未保存到文件。"
    return {
        "summary": summary,
        "data": {
            "agent": "doc_writer_agent",
            "format": "md",
            "title": title,
            "markdown": markdown,
            "saved": saved,
            "outputPath": path_text,
            "byteCount": output.get("byteCount", len(markdown.encode("utf-8"))),
            "usedWebSearch": bool(plan.get("use_web_search")),
            "plan": plan,
            "outline": final_state.get("outline", []),
            "research": final_state.get("research", {}),
            "warnings": final_state.get("warnings", []),
        },
    }


async def planner_agent(state: DocWriterState) -> dict[str, Any]:
    payload = state.get("payload", {})
    intent = state.get("intent", "")
    title = clean_title(str(payload.get("title") or payload.get("topic") or infer_title(intent) or "未命名文档"))
    doc_format = str(payload.get("format") or payload.get("type") or "md").lower().strip()
    warnings = list(state.get("warnings", []))
    if doc_format not in {"md", "markdown"}:
        warnings.append(
            {
                "type": "unsupported_format",
                "message": f"当前基础版只支持 Markdown，已将 {doc_format or 'unknown'} 转为 md。",
            }
        )
    sections = normalize_sections(payload.get("sections")) or DEFAULT_SECTIONS
    use_web_search = decide_web_search(payload, intent)
    search_query = str(payload.get("searchQuery") or payload.get("search_query") or payload.get("query") or title).strip()
    save = bool(payload.get("save", True))
    overwrite = bool(payload.get("overwrite", False))
    front_matter = bool(payload.get("frontMatter", payload.get("front_matter", False)))
    plan: DocWriterPlan = {
        "title": title,
        "format": "md",
        "audience": str(payload.get("audience") or "通用读者").strip(),
        "tone": str(payload.get("tone") or "清晰、结构化、偏工程说明").strip(),
        "sections": sections,
        "use_web_search": use_web_search,
        "search_query": search_query,
        "save": save,
        "overwrite": overwrite,
        "output_path": str(payload.get("outputPath") or payload.get("output_path") or ""),
        "file_name": str(payload.get("fileName") or payload.get("file_name") or ""),
        "front_matter": front_matter,
    }
    return {"plan": plan, "warnings": warnings}


async def research_agent(state: DocWriterState) -> dict[str, Any]:
    plan = state.get("plan", {})
    payload = state.get("payload", {})
    warnings = list(state.get("warnings", []))
    sources = normalize_sources(payload.get("sources"))
    if not plan.get("use_web_search"):
        return {
            "research": {
                "query": "",
                "answer": "",
                "results": sources,
                "warnings": [],
                "skipped": True,
            }
        }

    try:
        search_result = await run_web_search_agent(
            {
                "query": plan.get("search_query", ""),
                "intent": state.get("intent", ""),
                "domains": payload.get("domains", []),
                "excludeDomains": payload.get("excludeDomains", []),
                "maxResults": payload.get("maxResults", 5),
                "fetchContent": payload.get("fetchContent", True),
                "freshness": payload.get("freshness", "any"),
            }
        )
        data = search_result.get("data", {})
        merged_sources = [*sources, *data.get("results", [])]
        return {
            "research": {
                "query": data.get("query", plan.get("search_query", "")),
                "answer": data.get("answer", ""),
                "results": merged_sources,
                "warnings": data.get("warnings", []),
                "skipped": False,
            }
        }
    except Exception as error:
        warnings.append(
            {
                "type": "web_search_failed",
                "message": str(error) or error.__class__.__name__,
            }
        )
        return {
            "research": {
                "query": plan.get("search_query", ""),
                "answer": "",
                "results": sources,
                "warnings": [],
                "skipped": False,
            },
            "warnings": warnings,
        }


async def outline_agent(state: DocWriterState) -> dict[str, Any]:
    plan = state.get("plan", {})
    payload = state.get("payload", {})
    user_points = normalize_string_list(payload.get("keyPoints") or payload.get("key_points"))
    outline: list[dict[str, Any]] = []
    for section in plan.get("sections", DEFAULT_SECTIONS):
        outline.append(
            {
                "heading": section,
                "points": section_points(section, user_points),
            }
        )
    return {"outline": outline}


async def markdown_writer_agent(state: DocWriterState) -> dict[str, Any]:
    plan = state.get("plan", {})
    payload = state.get("payload", {})
    research = state.get("research", {})
    outline = state.get("outline", [])
    warnings = state.get("warnings", [])
    title = plan.get("title", "未命名文档")
    body = str(payload.get("content") or payload.get("body") or payload.get("draft") or "").strip()
    sources = research.get("results", [])
    source_refs = build_source_refs(sources)
    lines: list[str] = []

    if plan.get("front_matter"):
        lines.extend(
            [
                "---",
                f"title: {title}",
                f"format: {plan.get('format', 'md')}",
                f"createdAt: {current_time_iso()}",
                f"audience: {plan.get('audience', '通用读者')}",
                "---",
                "",
            ]
        )

    lines.extend(
        [
            f"# {title}",
            "",
            "## 摘要",
            "",
            build_summary(state, source_refs),
            "",
            "## 文档信息",
            "",
            f"- 目标读者：{plan.get('audience', '通用读者')}",
            f"- 写作语气：{plan.get('tone', '清晰、结构化')}",
            f"- 是否使用 Web Search：{'是' if plan.get('use_web_search') else '否'}",
            f"- 生成时间：{current_time_text()}",
            "",
            "## 目录",
            "",
        ]
    )
    for item in outline:
        lines.append(f"- [{item.get('heading', '')}](#{slug_anchor(item.get('heading', ''))})")
    lines.append("")

    if body:
        lines.extend(["## 输入草稿", "", body, ""])

    for item in outline:
        heading = item.get("heading", "")
        lines.extend([f"## {heading}", ""])
        for point in item.get("points", []):
            lines.append(f"- {point}")
        if heading == "核心内容" and source_refs:
            lines.append(f"- 已参考 {len(source_refs)} 个来源，关键事实应优先以来源列表为准。")
        if heading == "注意事项" and (warnings or research.get("warnings")):
            lines.append("- 检索和生成过程中存在风险提示，见文末“风险提示”。")
        lines.append("")

    if source_refs:
        lines.extend(["## 参考来源", ""])
        for index, source in enumerate(source_refs, start=1):
            title_text = source.get("title") or source.get("url") or f"来源 {index}"
            url = source.get("url", "")
            snippet = source.get("snippet") or source.get("contentSummary") or ""
            if url:
                lines.append(f"{index}. [{title_text}]({url})")
            else:
                lines.append(f"{index}. {title_text}")
            if snippet:
                lines.append(f"   - 摘要：{truncate(snippet, 180)}")
        lines.append("")

    all_warnings = [*warnings, *research.get("warnings", [])]
    if all_warnings:
        lines.extend(["## 风险提示", ""])
        for warning in all_warnings:
            lines.append(f"- {warning.get('message') or warning.get('type')}")
        lines.append("")

    lines.extend(["## 后续可扩展", "", "- 接入 LLM 写作节点，支持更自然的段落生成。", "- 增加 docx、txt、html 等格式导出。", "- 增加人工确认后写入任意项目路径。", ""])
    return {"markdown": "\n".join(lines)}


async def file_writer_agent(state: DocWriterState) -> dict[str, Any]:
    plan = state.get("plan", {})
    markdown = state.get("markdown", "")
    if not plan.get("save", True):
        return {
            "output": {
                "saved": False,
                "path": "",
                "byteCount": len(markdown.encode("utf-8")),
            }
        }

    output_path = resolve_output_path(plan, markdown)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not plan.get("overwrite", False):
        output_path = unique_path(output_path)
    output_path.write_text(markdown, encoding="utf-8")
    return {
        "output": {
            "saved": True,
            "path": str(output_path.relative_to(WorkspacePath)).replace("\\", "/"),
            "absolutePath": str(output_path),
            "byteCount": len(markdown.encode("utf-8")),
        }
    }


def decide_web_search(payload: dict[str, Any], intent: str) -> bool:
    value = payload.get("useWebSearch", payload.get("use_web_search", "auto"))
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "yes", "1", "on"}:
        return True
    if normalized in {"false", "no", "0", "off"}:
        return False
    if payload.get("sources") or payload.get("content") or payload.get("body"):
        return False
    text = " ".join([intent, str(payload.get("title", "")), str(payload.get("topic", ""))]).lower()
    return any(hint in text for hint in SEARCH_HINTS)


def resolve_output_path(plan: DocWriterPlan, markdown: str) -> Path:
    configured_dir = os.getenv("AMADEUS_DOC_WRITER_OUTPUT_DIR", "").strip()
    default_dir = Path(configured_dir) if configured_dir else DEFAULT_OUTPUT_DIR
    raw_output_path = str(plan.get("output_path") or "").strip()
    title = str(plan.get("title") or "document")
    raw_file_name = str(plan.get("file_name") or "").strip()
    filename = sanitize_filename(raw_file_name) if raw_file_name else ""
    if not filename:
        filename = f"{slug_filename(title)}-{datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')}.md"
    if raw_output_path.endswith((".md", ".markdown")):
        candidate = Path(raw_output_path)
    else:
        candidate = Path(raw_output_path) / filename if raw_output_path else default_dir / filename
    if candidate.suffix.lower() not in {".md", ".markdown"}:
        candidate = candidate.with_suffix(".md")
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (WorkspacePath / candidate).resolve()
    if not is_relative_to(resolved, WorkspacePath):
        raise ValueError("doc_writer outputPath must stay inside the workspace")
    return resolved


def unique_path(path: Path) -> Path:
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Unable to allocate a unique document path")


def infer_title(intent: str) -> str:
    clean = re.sub(r"^(写|编写|生成|创建|起草|write|create)\s*", "", intent.strip(), flags=re.IGNORECASE)
    clean = clean.strip(" ：:，,。.")
    if not clean:
        return "未命名文档"
    return truncate(clean, 80)


def clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    return truncate(title, 100) or "未命名文档"


def normalize_sections(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = re.split(r"[,，\n;；]+", value)
    elif isinstance(value, list):
        items = [str(item) for item in value]
    else:
        return []
    return [clean_title(item) for item in items if clean_title(item)]


def normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[,，\n;；]+", value)
    elif isinstance(value, list):
        parts = [str(item) for item in value]
    else:
        return []
    return [part.strip() for part in parts if part.strip()]


def normalize_sources(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    sources: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            sources.append(dict(item))
        elif str(item).strip():
            sources.append({"title": str(item).strip(), "url": ""})
    return sources


def section_points(section: str, user_points: list[str]) -> list[str]:
    if user_points and section == "核心内容":
        return user_points
    mapping = {
        "背景与目标": ["说明文档要解决的问题、使用场景和读者预期。", "明确本文档的边界，避免把未实现能力写成已完成能力。"],
        "核心内容": ["按照主题拆分关键概念、设计选择和主要结论。", "对需要事实支撑的内容保留来源或待验证标记。"],
        "实施步骤": ["列出可执行步骤，按依赖顺序组织。", "每一步应包含输入、动作和预期输出。"],
        "注意事项": ["标记风险、假设、限制条件和需要人工确认的环节。", "避免让文档读者误解 Agent 已执行未实际执行的操作。"],
        "后续工作": ["列出下一阶段可扩展能力。", "把未完成能力拆成可验证的小任务。"],
    }
    return mapping.get(section, [f"围绕“{section}”补充结构化内容。", "后续可接入 LLM 写作节点生成更丰富段落。"])


def build_summary(state: DocWriterState, sources: list[dict[str, Any]]) -> str:
    plan = state.get("plan", {})
    intent = state.get("intent", "")
    title = plan.get("title", "未命名文档")
    if sources:
        return f"本文围绕“{title}”整理基础 Markdown 文档，并结合 {len(sources)} 个来源形成可追溯草稿。原始需求：{intent or '未提供'}。"
    return f"本文围绕“{title}”整理基础 Markdown 文档。当前草稿主要依据用户输入生成，未使用外部检索。原始需求：{intent or '未提供'}。"


def build_source_refs(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        url = str(source.get("url") or "").strip()
        title = str(source.get("title") or url or "未命名来源").strip()
        key = url or title
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(
            {
                "title": title,
                "url": url,
                "snippet": source.get("snippet") or source.get("contentSummary") or "",
            }
        )
    return output[:10]


def sanitize_filename(value: str) -> str:
    name = Path(value).name
    name = re.sub(r"[^\w\u4e00-\u9fff.-]+", "-", name, flags=re.UNICODE).strip("-._")
    return name or "document.md"


def slug_filename(value: str) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", value.lower(), flags=re.UNICODE).strip("-")
    return truncate(slug, 60).strip("-") or "document"


def slug_anchor(value: str) -> str:
    return re.sub(r"\s+", "-", value.strip().lower())


def truncate(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def current_time_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def current_time_text() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S %Z")


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
