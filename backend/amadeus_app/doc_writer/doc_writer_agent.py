from __future__ import annotations

import csv
import json
import os
import re
from io import StringIO
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph

from ..search.web_search_agent import run_web_search_agent


DocFormat = Literal["md", "txt", "docx", "csv", "xlsx"]
WorkspacePath = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = Path("generated_docs")
DEFAULT_SECTIONS = ["背景与目标", "核心内容", "实施步骤", "注意事项", "后续工作"]
DOC_FORMAT_LABELS: dict[str, str] = {
    "md": "Markdown",
    "txt": "TXT",
    "docx": "DOCX",
    "csv": "CSV",
    "xlsx": "XLSX",
}
DOC_FORMAT_EXTENSIONS: dict[str, str] = {
    "md": ".md",
    "txt": ".txt",
    "docx": ".docx",
    "csv": ".csv",
    "xlsx": ".xlsx",
}
SUPPORTED_SUFFIX_FORMATS: dict[str, DocFormat] = {
    ".md": "md",
    ".markdown": "md",
    ".txt": "txt",
    ".docx": "docx",
    ".csv": "csv",
    ".xlsx": "xlsx",
}
TEXT_OUTPUT_FORMATS = {"md", "txt", "csv"}
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
    doc_format = str(plan.get("format") or "md")
    format_label = DOC_FORMAT_LABELS.get(doc_format, doc_format.upper())
    saved = output.get("saved", False)
    path_text = output.get("path", "")
    if saved:
        summary = f"已生成 {format_label} 文档《{title}》，保存到 {path_text}。"
    else:
        summary = f"已生成 {format_label} 文档《{title}》，未保存到文件。"
    data: dict[str, Any] = {
        "agent": "doc_writer_agent",
        "format": doc_format,
        "title": title,
        "markdown": markdown,
        "saved": saved,
        "outputPath": path_text,
        "absolutePath": output.get("absolutePath", ""),
        "byteCount": output.get("byteCount", len(markdown.encode("utf-8"))),
        "mimeType": output.get("mimeType", ""),
        "usedWebSearch": bool(plan.get("use_web_search")),
        "plan": plan,
        "outline": final_state.get("outline", []),
        "research": final_state.get("research", {}),
        "warnings": final_state.get("warnings", []),
    }
    if "content" in output:
        data["content"] = output.get("content", "")
    return {
        "summary": summary,
        "data": data,
    }


async def planner_agent(state: DocWriterState) -> dict[str, Any]:
    payload = state.get("payload", {})
    intent = state.get("intent", "")
    title = clean_title(str(payload.get("title") or payload.get("topic") or infer_title(intent) or "未命名文档"))
    warnings = list(state.get("warnings", []))
    raw_output_path = str(payload.get("outputPath") or payload.get("output_path") or "")
    raw_file_name = str(payload.get("fileName") or payload.get("file_name") or "")
    explicit_format = payload.get("format") or payload.get("type")
    if explicit_format is not None and str(explicit_format).strip():
        doc_format = normalize_requested_doc_format(explicit_format, warnings)
    else:
        doc_format = infer_doc_format_from_path(raw_file_name) or infer_doc_format_from_path(raw_output_path) or "md"
    sections = normalize_sections(payload.get("sections")) or DEFAULT_SECTIONS
    use_web_search = decide_web_search(payload, intent)
    search_query = str(payload.get("searchQuery") or payload.get("search_query") or payload.get("query") or title).strip()
    save = bool(payload.get("save", True))
    overwrite = bool(payload.get("overwrite", False))
    front_matter = bool(payload.get("frontMatter", payload.get("front_matter", False)))
    plan: DocWriterPlan = {
        "title": title,
        "format": doc_format,
        "audience": str(payload.get("audience") or "通用读者").strip(),
        "tone": str(payload.get("tone") or "清晰、结构化、偏工程说明").strip(),
        "sections": sections,
        "use_web_search": use_web_search,
        "search_query": search_query,
        "save": save,
        "overwrite": overwrite,
        "output_path": raw_output_path,
        "file_name": raw_file_name,
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

    lines.extend(["## 后续可扩展", "", "- 接入 LLM 写作节点，支持更自然的段落生成。", "- 增加 HTML/PDF 等更多导出格式。", "- 增加人工确认后写入任意项目路径。", ""])
    return {"markdown": "\n".join(lines)}


async def file_writer_agent(state: DocWriterState) -> dict[str, Any]:
    plan = state.get("plan", {})
    markdown = state.get("markdown", "")
    doc_format = str(plan.get("format") or "md")
    warnings = list(state.get("warnings", []))
    if not plan.get("save", True):
        output: dict[str, Any] = {
            "saved": False,
            "path": "",
            "byteCount": len(markdown.encode("utf-8")),
            "mimeType": mime_type_for_format(doc_format),
        }
        content = render_text_content(doc_format, state)
        if content is not None:
            output["content"] = content
            output["byteCount"] = len(content.encode("utf-8"))
        elif doc_format not in TEXT_OUTPUT_FORMATS:
            warnings.append(
                {
                    "type": "binary_output_not_returned",
                    "message": f"{DOC_FORMAT_LABELS.get(doc_format, doc_format)} 是二进制文档格式，save=false 时仅返回 Markdown 草稿。",
                }
            )
        return {
            "output": output,
            "warnings": warnings,
        }

    output_path = resolve_output_path(plan, markdown)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not plan.get("overwrite", False):
        output_path = unique_path(output_path)
    artifact = write_output_artifact(doc_format, state, output_path)
    return {
        "output": {
            "saved": True,
            "path": str(output_path.relative_to(WorkspacePath)).replace("\\", "/"),
            "absolutePath": str(output_path),
            "byteCount": artifact.get("byteCount", output_path.stat().st_size),
            "mimeType": artifact.get("mimeType", mime_type_for_format(doc_format)),
            **({"content": artifact["content"]} if "content" in artifact else {}),
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


def normalize_requested_doc_format(value: Any, warnings: list[dict[str, Any]]) -> DocFormat:
    doc_format = doc_format_from_value(value)
    if doc_format:
        return doc_format
    raw = str(value or "").strip() or "unknown"
    warnings.append(
        {
            "type": "unsupported_format",
            "message": f"不支持的文档格式 {raw}，已改用 Markdown。支持格式：md、txt、docx、csv、xlsx。",
        }
    )
    return "md"


def doc_format_from_value(value: Any) -> DocFormat | None:
    normalized = str(value or "").lower().strip().lstrip(".")
    if normalized == "markdown":
        return "md"
    if normalized in DOC_FORMAT_EXTENSIONS:
        return normalized  # type: ignore[return-value]
    return None


def infer_doc_format_from_path(path_text: str) -> DocFormat | None:
    suffix = Path(str(path_text or "").strip()).suffix.lower()
    return SUPPORTED_SUFFIX_FORMATS.get(suffix)


def mime_type_for_format(doc_format: str) -> str:
    return {
        "md": "text/markdown; charset=utf-8",
        "txt": "text/plain; charset=utf-8",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "csv": "text/csv; charset=utf-8",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(doc_format, "application/octet-stream")


def render_text_content(doc_format: str, state: DocWriterState) -> str | None:
    markdown = state.get("markdown", "")
    if doc_format == "md":
        return markdown
    if doc_format == "txt":
        return markdown_to_plain_text(markdown)
    if doc_format == "csv":
        return csv_content_from_state(state)
    return None


def write_output_artifact(doc_format: str, state: DocWriterState, output_path: Path) -> dict[str, Any]:
    text_content = render_text_content(doc_format, state)
    if text_content is not None:
        output_path.write_text(text_content, encoding="utf-8")
        return {
            "byteCount": len(text_content.encode("utf-8")),
            "mimeType": mime_type_for_format(doc_format),
            "content": text_content,
        }
    if doc_format == "docx":
        write_docx_document(state, output_path)
    elif doc_format == "xlsx":
        write_xlsx_workbook(state, output_path)
    else:
        raise ValueError(f"Unsupported doc_writer format: {doc_format}")
    return {
        "byteCount": output_path.stat().st_size,
        "mimeType": mime_type_for_format(doc_format),
    }


def markdown_to_plain_text(markdown: str) -> str:
    output: list[str] = []
    in_front_matter = False
    for index, line in enumerate(markdown.splitlines()):
        stripped = line.strip()
        if index == 0 and stripped == "---":
            in_front_matter = True
            continue
        if in_front_matter:
            if stripped == "---":
                in_front_matter = False
            continue
        if stripped.startswith("```"):
            continue
        text = re.sub(r"^#{1,6}\s+", "", line)
        text = re.sub(r"^\s*[-*+]\s+", "- ", text)
        text = re.sub(r"^\s*(\d+)\.\s+", r"\1. ", text)
        output.append(markdown_inline_to_text(text).rstrip())
    return "\n".join(output).strip() + "\n"


def markdown_inline_to_text(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda match: f"{match.group(1)} ({match.group(2)})", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    return text


def markdown_body_lines(markdown: str) -> list[str]:
    output: list[str] = []
    in_front_matter = False
    for index, line in enumerate(markdown.splitlines()):
        stripped = line.strip()
        if index == 0 and stripped == "---":
            in_front_matter = True
            continue
        if in_front_matter:
            if stripped == "---":
                in_front_matter = False
            continue
        output.append(line)
    return output


def write_docx_document(state: DocWriterState, output_path: Path) -> None:
    try:
        from docx import Document
    except ImportError as error:
        raise RuntimeError("生成 docx 需要安装 python-docx，请运行 pip install python-docx。") from error

    plan = state.get("plan", {})
    markdown = state.get("markdown", "")
    document = Document()
    document.core_properties.title = str(plan.get("title") or "Untitled Document")
    document.core_properties.author = "Amadeus doc_writer_agent"

    for line in markdown_body_lines(markdown):
        stripped = line.strip()
        if not stripped:
            continue
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading_match:
            level = min(len(heading_match.group(1)), 4)
            document.add_heading(markdown_inline_to_text(heading_match.group(2)), level=level)
            continue
        bullet_match = re.match(r"^[-*+]\s+(.+)$", stripped)
        if bullet_match:
            document.add_paragraph(markdown_inline_to_text(bullet_match.group(1)), style="List Bullet")
            continue
        numbered_match = re.match(r"^\d+\.\s+(.+)$", stripped)
        if numbered_match:
            document.add_paragraph(markdown_inline_to_text(numbered_match.group(1)), style="List Number")
            continue
        document.add_paragraph(markdown_inline_to_text(stripped))

    document.save(output_path)


def csv_content_from_state(state: DocWriterState) -> str:
    payload = state.get("payload", {})
    tabular = normalize_tabular_rows(payload.get("rows") or payload.get("table") or payload.get("dataset"))
    if tabular:
        headers, rows = tabular
        return csv_text([headers, *rows])

    plan = state.get("plan", {})
    research = state.get("research", {})
    source_refs = build_source_refs(research.get("results", []))
    rows: list[list[Any]] = [["type", "title", "content", "url"]]
    rows.append(["summary", plan.get("title", "未命名文档"), build_summary(state, source_refs), ""])
    for item in state.get("outline", []):
        heading = item.get("heading", "")
        for point in item.get("points", []):
            rows.append(["section", heading, point, ""])
    for source in source_refs:
        rows.append(["source", source.get("title", ""), source.get("snippet", ""), source.get("url", "")])
    for warning in [*state.get("warnings", []), *research.get("warnings", [])]:
        rows.append(["warning", warning.get("type", ""), warning.get("message", ""), ""])
    return csv_text(rows)


def csv_text(rows: list[list[Any]]) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in rows:
        writer.writerow([stringify_cell(cell) for cell in row])
    return buffer.getvalue()


def normalize_tabular_rows(value: Any) -> tuple[list[str], list[list[Any]]] | None:
    if not isinstance(value, list) or not value:
        return None
    if all(isinstance(item, dict) for item in value):
        headers: list[str] = []
        for item in value:
            for key in item.keys():
                key_text = str(key)
                if key_text not in headers:
                    headers.append(key_text)
        rows = [[item.get(header, "") for header in headers] for item in value]
        return headers, rows
    if all(isinstance(item, (list, tuple)) for item in value):
        rows = [list(item) for item in value]
        headers = [stringify_cell(cell) for cell in rows[0]]
        return headers, rows[1:]
    return None


def write_xlsx_workbook(state: DocWriterState, output_path: Path) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError as error:
        raise RuntimeError("生成 xlsx 需要安装 openpyxl，请运行 pip install openpyxl。") from error

    plan = state.get("plan", {})
    research = state.get("research", {})
    source_refs = build_source_refs(research.get("results", []))
    workbook = Workbook()
    document_sheet = workbook.active
    document_sheet.title = "Document"
    document_sheet.append(["Field", "Value"])
    document_sheet.append(["Title", plan.get("title", "未命名文档")])
    document_sheet.append(["Format", plan.get("format", "xlsx")])
    document_sheet.append(["Audience", plan.get("audience", "通用读者")])
    document_sheet.append(["Tone", plan.get("tone", "清晰、结构化")])
    document_sheet.append(["Used Web Search", "是" if plan.get("use_web_search") else "否"])
    document_sheet.append(["Generated At", current_time_text()])
    document_sheet.append([])
    document_sheet.append(["Section", "Point"])
    for item in state.get("outline", []):
        heading = item.get("heading", "")
        points = item.get("points", [])
        if not points:
            document_sheet.append([heading, ""])
        for point in points:
            document_sheet.append([heading, point])

    payload = state.get("payload", {})
    tabular = normalize_tabular_rows(payload.get("rows") or payload.get("table") or payload.get("dataset"))
    if tabular:
        headers, rows = tabular
        data_sheet = workbook.create_sheet("Data")
        data_sheet.append(headers)
        for row in rows:
            data_sheet.append([stringify_cell(cell) for cell in row])

    if source_refs:
        source_sheet = workbook.create_sheet("Sources")
        source_sheet.append(["Title", "URL", "Snippet"])
        for source in source_refs:
            source_sheet.append([source.get("title", ""), source.get("url", ""), source.get("snippet", "")])

    warnings = [*state.get("warnings", []), *research.get("warnings", [])]
    if warnings:
        warning_sheet = workbook.create_sheet("Warnings")
        warning_sheet.append(["Type", "Message"])
        for warning in warnings:
            warning_sheet.append([warning.get("type", ""), warning.get("message", "")])

    header_fill = PatternFill("solid", fgColor="E9EEF6")
    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.fill = header_fill
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        for column in sheet.columns:
            width = min(max(len(stringify_cell(cell.value)) for cell in column) + 2, 60)
            sheet.column_dimensions[column[0].column_letter].width = max(width, 12)

    workbook.save(output_path)


def resolve_output_path(plan: DocWriterPlan, markdown: str) -> Path:
    configured_dir = os.getenv("AMADEUS_DOC_WRITER_OUTPUT_DIR", "").strip()
    default_dir = Path(configured_dir) if configured_dir else DEFAULT_OUTPUT_DIR
    raw_output_path = str(plan.get("output_path") or "").strip()
    title = str(plan.get("title") or "document")
    raw_file_name = str(plan.get("file_name") or "").strip()
    doc_format = str(plan.get("format") or "md")
    extension = DOC_FORMAT_EXTENSIONS.get(doc_format, ".md")
    filename = sanitize_filename(raw_file_name) if raw_file_name else ""
    if not filename:
        filename = f"{slug_filename(title)}-{datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d-%H%M%S')}{extension}"
    elif Path(filename).suffix.lower() not in {extension, ".markdown" if doc_format == "md" else extension}:
        filename = str(Path(filename).with_suffix(extension))
    if infer_doc_format_from_path(raw_output_path):
        candidate = Path(raw_output_path)
    else:
        candidate = Path(raw_output_path) / filename if raw_output_path else default_dir / filename
    if candidate.suffix.lower() not in {extension, ".markdown" if doc_format == "md" else extension}:
        candidate = candidate.with_suffix(extension)
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
        return f"本文围绕“{title}”整理基础文档草稿，并结合 {len(sources)} 个来源形成可追溯内容。原始需求：{intent or '未提供'}。"
    return f"本文围绕“{title}”整理基础文档草稿。当前草稿主要依据用户输入生成，未使用外部检索。原始需求：{intent or '未提供'}。"


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


def stringify_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


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
