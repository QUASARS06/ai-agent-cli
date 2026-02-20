# agentcli/tools/web.py
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS

from agentcli.tools.base import ToolDef, int_schema, object_schema, str_schema
from agentcli.tools.registry import register_tool


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def web_search_tool(state: Any, args: Dict[str, Any]) -> Any:
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "Missing required arg: query"}

    max_results = int(args.get("max_results", 5))
    max_results = max(1, min(max_results, 10))

    results: List[Dict[str, str]] = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            # r typically includes: title, href, body
            results.append(
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
            )

    return {"query": query, "results": results}


def web_fetch_tool(state: Any, args: Dict[str, Any]) -> Any:
    url = str(args.get("url", "")).strip()
    if not url:
        return {"error": "Missing required arg: url"}

    # Safety: only allow http/https
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return {"error": "Only http/https URLs are allowed."}

    max_chars = int(args.get("max_chars", 8000))
    timeout_seconds = float(args.get("timeout_seconds", 20))

    headers = {
        "User-Agent": "agentcli/0.1 (educational coding agent)",
    }

    try:
        with httpx.Client(timeout=timeout_seconds, headers=headers, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        raw = resp.text or ""

        # If HTML, extract readable text
        if "text/html" in content_type.lower():
            soup = BeautifulSoup(raw, "html.parser")

            # remove junk
            for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "aside"]):
                tag.decompose()

            text = soup.get_text(separator="\n")
            text = _clean_text(text)
        else:
            # plain text / json etc.
            text = _clean_text(raw)

        if len(text) > max_chars:
            text = text[:max_chars] + "\n...[truncated]..."

        return {
            "url": url,
            "content_type": content_type,
            "text": text,
        }

    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


register_tool(
    ToolDef(
        name="web_search",
        description="Search the web (DuckDuckGo) and return top results with titles, URLs, and snippets.",
        input_schema=object_schema(
            properties={
                "query": str_schema("Search query."),
                "max_results": int_schema("Number of results (1-10).", default=5, minimum=1),
            },
            required=["query"],
        ),
        runner=web_search_tool,
    )
)

register_tool(
    ToolDef(
        name="web_fetch",
        description="Fetch a web page by URL and return extracted readable text (HTML cleaned).",
        input_schema=object_schema(
            properties={
                "url": str_schema("http/https URL to fetch."),
                "max_chars": int_schema("Max characters of text to return.", default=8000, minimum=200),
                "timeout_seconds": str_schema("Request timeout seconds (string ok).", default="20"),
            },
            required=["url"],
        ),
        runner=web_fetch_tool,
    )
)
