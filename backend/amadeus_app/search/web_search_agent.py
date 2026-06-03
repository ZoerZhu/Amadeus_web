from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from datetime import datetime
from typing import Any, Literal, TypedDict
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from langgraph.graph import END, START, StateGraph


SearchProviderName = Literal["direct_url", "searxng", "duckduckgo_html"]

DEFAULT_USER_AGENT = "AmadeusWebSearch/0.1"
DEFAULT_MAX_RESULTS = 8
DEFAULT_FETCH_TOP_N = 4
MAX_RESULTS_LIMIT = 20
MAX_FETCH_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 8.0
SEARCH_TIMEOUT_SECONDS = 10.0

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}

BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

SEARCH_KEYWORDS = ("搜索", "查询", "查一下", "查找", "检索", "search", "web", "资料", "来源")
OFFICIAL_HINTS = ("官方", "文档", "docs", "documentation", "reference", "api")
QUESTION_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "http",
    "https",
    "www",
    "com",
    "org",
    "net",
    "查询",
    "搜索",
    "一下",
    "资料",
    "官方",
    "文档",
}


class SearchResult(TypedDict, total=False):
    id: str
    title: str
    url: str
    displayUrl: str
    domain: str
    sourceType: str
    snippet: str
    contentSummary: str
    text: str
    publishedAt: str
    retrievedAt: str
    score: float
    scores: dict[str, float]
    evidence: list[dict[str, Any]]
    fetchStatus: str


class SearchWarning(TypedDict):
    type: str
    message: str
    sources: list[str]


class WebSearchState(TypedDict, total=False):
    query: str
    intent: str
    freshness: str
    domains: list[str]
    exclude_domains: list[str]
    max_results: int
    fetch_content: bool
    provider: SearchProviderName
    queries: list[str]
    raw_results: list[SearchResult]
    ranked_results: list[SearchResult]
    warnings: list[SearchWarning]
    answer: str
    retrieved_at: str
    debug: dict[str, Any]


def build_web_search_graph():
    builder = StateGraph(WebSearchState)
    builder.add_node("search_agent", search_agent)
    builder.add_node("reader_agent", reader_agent)
    builder.add_node("critic_agent", critic_agent)
    builder.add_node("writer_agent", writer_agent)
    builder.add_edge(START, "search_agent")
    builder.add_edge("search_agent", "reader_agent")
    builder.add_edge("reader_agent", "critic_agent")
    builder.add_edge("critic_agent", "writer_agent")
    builder.add_edge("writer_agent", END)
    return builder.compile()


WEB_SEARCH_GRAPH = None


def get_web_search_graph():
    global WEB_SEARCH_GRAPH
    if WEB_SEARCH_GRAPH is None:
        WEB_SEARCH_GRAPH = build_web_search_graph()
    return WEB_SEARCH_GRAPH


async def run_web_search_agent(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or args.get("intent") or "").strip()
    intent = str(args.get("intent") or query).strip()
    if not query and not intent:
        raise ValueError("web_search requires query or intent")

    domains = normalize_string_list(args.get("domains"))
    exclude_domains = normalize_string_list(args.get("excludeDomains") or args.get("exclude_domains"))
    max_results = clamp_int(args.get("maxResults") or args.get("max_results"), DEFAULT_MAX_RESULTS, 1, MAX_RESULTS_LIMIT)
    fetch_content = bool(args.get("fetchContent", args.get("fetch_content", True)))
    freshness = str(args.get("freshness") or "any").strip() or "any"

    final_state = await get_web_search_graph().ainvoke(
        {
            "query": query or intent,
            "intent": intent,
            "domains": domains,
            "exclude_domains": exclude_domains,
            "max_results": max_results,
            "fetch_content": fetch_content,
            "freshness": freshness,
            "warnings": [],
            "debug": {},
        }
    )
    results = final_state.get("ranked_results", [])[:max_results]
    return {
        "summary": final_state.get("answer") or f"找到 {len(results)} 个搜索结果。",
        "data": {
            "query": final_state.get("query", query),
            "queries": final_state.get("queries", []),
            "freshness": freshness,
            "answer": final_state.get("answer", ""),
            "results": strip_internal_result_fields(results),
            "warnings": final_state.get("warnings", []),
            "debug": final_state.get("debug", {}),
        },
    }


async def search_agent(state: WebSearchState) -> dict[str, Any]:
    query = state.get("query", "").strip()
    intent = state.get("intent", "").strip()
    domains = state.get("domains", [])
    max_results = state.get("max_results", DEFAULT_MAX_RESULTS)
    retrieved_at = current_time_iso()
    queries = plan_queries(query=query, intent=intent, domains=domains)
    provider = choose_provider(queries)
    warnings = list(state.get("warnings", []))

    raw_results: list[SearchResult] = []
    direct_urls = extract_urls(" ".join([query, intent]))
    if direct_urls:
        provider = "direct_url"
        raw_results = [
            make_result(
                title=url,
                url=url,
                snippet="Direct URL from user request.",
                retrieved_at=retrieved_at,
            )
            for url in direct_urls
        ]
    elif provider == "searxng":
        raw_results = await search_searxng(queries, max_results=max_results, retrieved_at=retrieved_at)
    else:
        raw_results = await search_duckduckgo_html(queries, max_results=max_results, retrieved_at=retrieved_at)

    if not raw_results:
        warnings.append(
            {
                "type": "no_results",
                "message": "搜索源没有返回结果；请配置 AMADEUS_SEARXNG_URL 或换一个查询词。",
                "sources": [],
            }
        )

    normalized = deduplicate_results(
        [
            result
            for result in raw_results
            if not is_excluded_domain(result.get("domain", ""), state.get("exclude_domains", []))
        ]
    )
    ranked = rank_results(normalized, query=query, intent=intent, domains=domains)
    return {
        "provider": provider,
        "queries": queries,
        "raw_results": normalized,
        "ranked_results": ranked[:max_results],
        "warnings": warnings,
        "retrieved_at": retrieved_at,
        "debug": {
            **state.get("debug", {}),
            "provider": provider,
            "rawResultCount": len(raw_results),
            "dedupedResultCount": len(normalized),
        },
    }


async def reader_agent(state: WebSearchState) -> dict[str, Any]:
    if not state.get("fetch_content", True):
        return {"ranked_results": state.get("ranked_results", [])}

    results = state.get("ranked_results", [])
    fetch_top_n = clamp_int(os.getenv("AMADEUS_SEARCH_FETCH_TOP_N"), DEFAULT_FETCH_TOP_N, 1, 8)
    top_results = results[: min(fetch_top_n, len(results))]
    warnings = list(state.get("warnings", []))

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(FETCH_TIMEOUT_SECONDS),
        follow_redirects=True,
        headers={"User-Agent": os.getenv("AMADEUS_SEARCH_USER_AGENT", DEFAULT_USER_AGENT)},
    ) as client:
        fetched = await asyncio.gather(
            *(fetch_and_extract(client, result, state.get("query", "")) for result in top_results),
            return_exceptions=True,
        )

    next_results = list(results)
    for index, item in enumerate(fetched):
        if isinstance(item, Exception):
            next_results[index]["fetchStatus"] = "error"
            warnings.append(
                {
                    "type": "fetch_error",
                    "message": str(item) or item.__class__.__name__,
                    "sources": [next_results[index].get("id", "")],
                }
            )
            continue
        next_results[index] = {**next_results[index], **item}

    ranked = rank_results(next_results, query=state.get("query", ""), intent=state.get("intent", ""), domains=state.get("domains", []))
    return {
        "ranked_results": ranked,
        "warnings": warnings,
        "debug": {
            **state.get("debug", {}),
            "fetchCount": len(top_results),
        },
    }


async def critic_agent(state: WebSearchState) -> dict[str, Any]:
    results = state.get("ranked_results", [])
    warnings = list(state.get("warnings", []))
    if not results:
        return {"warnings": warnings}

    domains = {result.get("domain", "") for result in results if result.get("domain")}
    source_types = {result.get("sourceType", "") for result in results if result.get("sourceType")}
    fetched_with_text = [result for result in results if result.get("text")]

    if len(domains) <= 1 and len(results) >= 3:
        warnings.append(
            {
                "type": "low_diversity",
                "message": "结果主要来自同一域名，交叉验证不足。",
                "sources": [result.get("id", "") for result in results[:3]],
            }
        )
    if not any(result.get("sourceType") in {"official_docs", "official_blog", "github", "paper"} for result in results):
        warnings.append(
            {
                "type": "weak_primary_sources",
                "message": "未找到明显的官方文档、论文或代码仓库来源。",
                "sources": [result.get("id", "") for result in results[:3]],
            }
        )
    if state.get("fetch_content", True) and len(fetched_with_text) == 0 and results:
        warnings.append(
            {
                "type": "content_not_fetched",
                "message": "未能读取网页正文，只能基于标题和摘要判断。",
                "sources": [result.get("id", "") for result in results[:3]],
            }
        )
    if "forum" in source_types or "social" in source_types:
        warnings.append(
            {
                "type": "informal_sources",
                "message": "部分结果来自论坛或社交平台，应优先核对官方来源。",
                "sources": [
                    result.get("id", "")
                    for result in results
                    if result.get("sourceType") in {"forum", "social"}
                ][:3],
            }
        )

    return {"warnings": warnings}


async def writer_agent(state: WebSearchState) -> dict[str, Any]:
    results = state.get("ranked_results", [])
    query = state.get("query", "")
    warnings = state.get("warnings", [])
    if not results:
        return {"answer": f"没有找到与“{query}”相关的可靠搜索结果。"}

    lines = [f"已检索“{query}”，找到 {len(results)} 个候选来源。"]
    top = results[: min(5, len(results))]
    for index, result in enumerate(top, start=1):
        title = result.get("title") or result.get("url", "")
        source_type = result.get("sourceType", "unknown")
        evidence = result.get("evidence", [])
        evidence_text = evidence[0]["quote"] if evidence else result.get("contentSummary") or result.get("snippet", "")
        if evidence_text:
            lines.append(f"{index}. {title}（{source_type}）：{truncate(evidence_text, 120)}")
        else:
            lines.append(f"{index}. {title}（{source_type}）")

    if warnings:
        lines.append(f"注意：存在 {len(warnings)} 条检索风险提示，建议在最终回答中保留不确定性。")

    return {"answer": "\n".join(lines)}


def plan_queries(*, query: str, intent: str, domains: list[str]) -> list[str]:
    base = query or intent
    queries = [base]
    if domains:
        for domain in domains[:3]:
            if domain and f"site:{domain}" not in base:
                queries.append(f"site:{domain} {base}")
    if any(hint in base.lower() for hint in OFFICIAL_HINTS) and not domains:
        queries.append(f"{base} official documentation")
    return dedupe_strings(queries)[:3]


def choose_provider(queries: list[str]) -> SearchProviderName:
    if extract_urls(" ".join(queries)):
        return "direct_url"
    if os.getenv("AMADEUS_SEARXNG_URL", "").strip():
        return "searxng"
    return "duckduckgo_html"


async def search_searxng(queries: list[str], *, max_results: int, retrieved_at: str) -> list[SearchResult]:
    base_url = os.getenv("AMADEUS_SEARXNG_URL", "").strip().rstrip("/")
    if not base_url:
        return []
    results: list[SearchResult] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(SEARCH_TIMEOUT_SECONDS)) as client:
        for query in queries:
            response = await client.get(
                f"{base_url}/search",
                params={"q": query, "format": "json", "language": "all"},
                headers={"User-Agent": os.getenv("AMADEUS_SEARCH_USER_AGENT", DEFAULT_USER_AGENT)},
            )
            if response.status_code >= 400:
                continue
            data = response.json()
            for item in data.get("results", [])[:max_results]:
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                results.append(
                    make_result(
                        title=str(item.get("title") or url),
                        url=url,
                        snippet=str(item.get("content") or ""),
                        retrieved_at=retrieved_at,
                    )
                )
            if len(results) >= max_results:
                break
    return results[:max_results]


async def search_duckduckgo_html(queries: list[str], *, max_results: int, retrieved_at: str) -> list[SearchResult]:
    results: list[SearchResult] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(SEARCH_TIMEOUT_SECONDS), follow_redirects=True) as client:
        for query in queries:
            response = await client.get(
                "https://duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": os.getenv("AMADEUS_SEARCH_USER_AGENT", DEFAULT_USER_AGENT)},
            )
            if response.status_code >= 400:
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            result_nodes = soup.select(".result")
            for node in result_nodes:
                link = node.select_one(".result__a")
                if link is None:
                    continue
                raw_url = str(link.get("href") or "").strip()
                url = unwrap_duckduckgo_url(raw_url)
                if not url:
                    continue
                snippet_node = node.select_one(".result__snippet")
                results.append(
                    make_result(
                        title=clean_text(link.get_text(" ")),
                        url=url,
                        snippet=clean_text(snippet_node.get_text(" ") if snippet_node else ""),
                        retrieved_at=retrieved_at,
                    )
                )
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
    return results[:max_results]


async def fetch_and_extract(client: httpx.AsyncClient, result: SearchResult, query: str) -> SearchResult:
    url = result.get("url", "")
    await ensure_public_url(url)
    response = await client.get(url)
    final_url = str(response.url)
    await ensure_public_url(final_url)
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        return {"fetchStatus": f"skipped:{content_type[:40]}"}
    content = response.content[:MAX_FETCH_BYTES]
    html = content.decode(response.encoding or "utf-8", errors="ignore")
    extracted = extract_page_content(html)
    text = extracted.get("text", "")
    evidence = extract_evidence(text, query)
    return {
        "url": normalize_url(final_url),
        "title": extracted.get("title") or result.get("title", ""),
        "snippet": result.get("snippet", "") or extracted.get("description", ""),
        "contentSummary": summarize_text(text),
        "text": text,
        "publishedAt": extracted.get("publishedAt", ""),
        "evidence": evidence,
        "fetchStatus": "ok" if text else "empty",
    }


def extract_page_content(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript", "svg", "form", "nav", "header", "footer", "aside"]):
        node.decompose()

    title = clean_text(soup.title.get_text(" ")) if soup.title else ""
    description = ""
    desc_node = soup.select_one('meta[name="description"], meta[property="og:description"]')
    if desc_node:
        description = clean_text(str(desc_node.get("content") or ""))
    published_at = ""
    date_node = soup.select_one(
        'meta[property="article:published_time"], meta[name="pubdate"], meta[name="date"], time[datetime]'
    )
    if date_node:
        published_at = clean_text(str(date_node.get("content") or date_node.get("datetime") or ""))

    main = soup.select_one("article") or soup.select_one("main") or soup.body or soup
    paragraphs = [clean_text(item.get_text(" ")) for item in main.find_all(["h1", "h2", "h3", "p", "li"])]
    text = clean_text("\n".join(item for item in paragraphs if item))
    return {
        "title": title,
        "description": description,
        "publishedAt": published_at,
        "text": text,
    }


def rank_results(results: list[SearchResult], *, query: str, intent: str, domains: list[str]) -> list[SearchResult]:
    terms = tokenize(" ".join([query, intent]))
    ranked: list[SearchResult] = []
    seen_domains: dict[str, int] = {}
    for result in results:
        source_type = classify_source(result)
        domain = result.get("domain", "")
        seen_domains[domain] = seen_domains.get(domain, 0) + 1
        relevance = relevance_score(result, terms)
        source = source_score(source_type)
        quality = content_quality_score(result)
        domain_boost = 0.1 if domain in domains else 0.0
        diversity = 1.0 / max(seen_domains[domain], 1)
        final_score = round(
            0.42 * relevance + 0.23 * source + 0.18 * quality + 0.12 * diversity + domain_boost,
            4,
        )
        ranked.append(
            {
                **result,
                "sourceType": source_type,
                "score": final_score,
                "scores": {
                    "relevance": round(relevance, 4),
                    "source": round(source, 4),
                    "contentQuality": round(quality, 4),
                    "diversity": round(diversity, 4),
                },
            }
        )
    return sorted(ranked, key=lambda item: item.get("score", 0), reverse=True)


def relevance_score(result: SearchResult, terms: list[str]) -> float:
    if not terms:
        return 0.5
    title = result.get("title", "").lower()
    snippet = result.get("snippet", "").lower()
    content = result.get("contentSummary", "").lower()
    hits = 0.0
    for term in terms:
        if term in title:
            hits += 2.0
        if term in snippet:
            hits += 1.0
        if term in content:
            hits += 0.7
    return min(hits / max(len(terms) * 2.0, 1), 1.0)


def source_score(source_type: str) -> float:
    return {
        "official_docs": 0.95,
        "official_blog": 0.85,
        "github": 0.8,
        "paper": 0.82,
        "news": 0.68,
        "wiki": 0.62,
        "forum": 0.5,
        "social": 0.42,
        "commercial": 0.45,
        "unknown": 0.52,
    }.get(source_type, 0.5)


def content_quality_score(result: SearchResult) -> float:
    text = result.get("text") or result.get("contentSummary") or result.get("snippet") or ""
    length = len(text)
    if length > 1000:
        return 0.95
    if length > 400:
        return 0.82
    if length > 120:
        return 0.65
    if length > 20:
        return 0.45
    return 0.25


def classify_source(result: SearchResult) -> str:
    domain = result.get("domain", "").lower()
    url = result.get("url", "").lower()
    if "docs." in domain or "/docs" in url or "documentation" in url or "developer." in domain:
        return "official_docs"
    if domain in {"github.com", "gitlab.com"}:
        return "github"
    if "arxiv.org" in domain or "doi.org" in domain or "acm.org" in domain or "ieee.org" in domain:
        return "paper"
    if "wikipedia.org" in domain:
        return "wiki"
    if any(item in domain for item in ("news", "reuters", "apnews", "bbc", "nytimes", "thepaper", "36kr")):
        return "news"
    if any(item in domain for item in ("reddit", "stackoverflow", "segmentfault", "zhihu", "v2ex")):
        return "forum"
    if any(item in domain for item in ("twitter", "x.com", "facebook", "weibo")):
        return "social"
    if any(item in domain for item in ("shop", "store", "pricing", "product")):
        return "commercial"
    return "unknown"


def make_result(*, title: str, url: str, snippet: str, retrieved_at: str) -> SearchResult:
    normalized_url = normalize_url(url)
    parsed = urlparse(normalized_url)
    return {
        "id": "",
        "title": title or normalized_url,
        "url": normalized_url,
        "displayUrl": parsed.netloc + parsed.path,
        "domain": parsed.netloc.lower().removeprefix("www."),
        "sourceType": "unknown",
        "snippet": snippet,
        "contentSummary": "",
        "publishedAt": "",
        "retrievedAt": retrieved_at,
        "evidence": [],
        "fetchStatus": "not_fetched",
    }


def deduplicate_results(results: list[SearchResult]) -> list[SearchResult]:
    seen: set[str] = set()
    output: list[SearchResult] = []
    for result in results:
        url = normalize_url(result.get("url", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        next_result = {**result, "url": url}
        next_result["id"] = f"src_{len(output) + 1:03d}"
        output.append(next_result)
    return output


def extract_evidence(text: str, query: str) -> list[dict[str, Any]]:
    if not text:
        return []
    terms = tokenize(query)
    sentences = re.split(r"(?<=[。！？.!?])\s+|\n+", text)
    scored: list[tuple[int, str]] = []
    cursor = 0
    for sentence in sentences:
        clean = clean_text(sentence)
        if len(clean) < 20:
            cursor += len(sentence) + 1
            continue
        score = sum(1 for term in terms if term in clean.lower())
        if score:
            scored.append((score, clean))
        cursor += len(sentence) + 1
    selected = [item[1] for item in sorted(scored, key=lambda item: item[0], reverse=True)[:2]]
    if not selected:
        selected = [truncate(clean_text(sentences[0]), 180)] if sentences else []
    return [{"quote": truncate(item, 220)} for item in selected if item]


def summarize_text(text: str) -> str:
    return truncate(clean_text(text), 500)


async def ensure_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
    host = parsed.hostname or ""
    if not host or host.lower() in {"localhost", "localhost.localdomain"}:
        raise ValueError("Localhost URLs are not allowed")
    if is_private_host_literal(host):
        raise ValueError("Private IP URLs are not allowed")
    infos = await asyncio.get_running_loop().getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    for info in infos:
        address = info[4][0]
        if is_private_host_literal(address):
            raise ValueError("Resolved private IP URLs are not allowed")


def is_private_host_literal(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.is_multicast:
        return True
    return any(ip in network for network in BLOCKED_IP_NETWORKS)


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = unquote(url.strip())
    parsed = urlparse(url)
    if not parsed.scheme and parsed.netloc == "":
        return ""
    query_pairs = parse_qs(parsed.query, keep_blank_values=True)
    clean_query_parts = []
    for key, values in query_pairs.items():
        if key.lower() in TRACKING_PARAMS:
            continue
        for value in values:
            clean_query_parts.append(f"{quote_plus(key)}={quote_plus(value)}")
    query = "&".join(clean_query_parts)
    normalized = parsed._replace(netloc=parsed.netloc.lower(), query=query, fragment="")
    return normalized.geturl()


def unwrap_duckduckgo_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.netloc.endswith("duckduckgo.com") or parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return normalize_url(target)
    return normalize_url(url)


def extract_urls(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s\]\[)）>\"']+", text)
    return [normalize_url(url.rstrip(".,;，。；")) for url in urls]


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
    return [token for token in tokens if len(token) > 1 and token not in QUESTION_WORDS]


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def truncate(text: str, limit: int) -> str:
    text = clean_text(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip().lower().removeprefix("www.") for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip().lower().removeprefix("www.") for item in value if str(item).strip()]
    return []


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        clean = value.strip()
        if clean and clean not in seen:
            seen.add(clean)
            output.append(clean)
    return output


def strip_internal_result_fields(results: list[SearchResult]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for result in results:
        item = dict(result)
        item.pop("text", None)
        output.append(item)
    return output


def is_excluded_domain(domain: str, exclude_domains: list[str]) -> bool:
    normalized = domain.lower().removeprefix("www.")
    return any(normalized == item or normalized.endswith(f".{item}") for item in exclude_domains)


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def current_time_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
