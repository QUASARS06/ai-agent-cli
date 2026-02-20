# agentcli/tools/search.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from agentcli.tools.base import ToolDef, bool_schema, int_schema, object_schema, str_schema
from agentcli.tools.registry import register_tool


def _root(state: Any) -> Path:
    return Path(state.cwd).expanduser().resolve()


def _resolve_under_root(state: Any, user_path: str) -> Path:
    root = _root(state)
    p = Path(user_path).expanduser()
    if not p.is_absolute():
        p = root / p
    resolved = p.resolve()
    try:
        resolved.relative_to(root)
    except Exception:
        raise ValueError(f"Path escapes workspace root. Root={root}, path={user_path}")
    return resolved


def search_text_tool(state: Any, args: Dict[str, Any]) -> Any:
    """
    Simple grep-like search across text files under a directory.
    """
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "Missing required arg: query"}

    path = str(args.get("path", "."))
    case_sensitive = bool(args.get("case_sensitive", False))
    max_results = int(args.get("max_results", 50))
    max_file_bytes = int(args.get("max_file_bytes", 400_000))  # skip huge files
    include_hidden = bool(args.get("include_hidden", False))

    root_dir = _resolve_under_root(state, path)
    if not root_dir.exists():
        return {"error": f"Not found: {path}"}
    if not root_dir.is_dir():
        return {"error": f"Not a directory: {path}"}

    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(query), flags=flags)

    results: List[Dict[str, Any]] = []
    files_scanned = 0

    for fp in root_dir.rglob("*"):
        if len(results) >= max_results:
            break

        # skip hidden
        if not include_hidden and any(part.startswith(".") for part in fp.relative_to(root_dir).parts):
            continue

        if fp.is_dir():
            continue

        try:
            st = fp.stat()
            if st.st_size > max_file_bytes:
                continue
        except Exception:
            continue

        # try read as text
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        files_scanned += 1

        lines = text.splitlines()
        for i, line in enumerate(lines, start=1):
            if pattern.search(line):
                # Keep snippet short
                snippet = line.strip()
                if len(snippet) > 300:
                    snippet = snippet[:300] + "…"
                results.append(
                    {
                        "path": str(fp.relative_to(_root(state))),
                        "line": i,
                        "match": snippet,
                    }
                )
                if len(results) >= max_results:
                    break

    return {
        "query": query,
        "path": path,
        "case_sensitive": case_sensitive,
        "files_scanned": files_scanned,
        "results": results,
        "truncated": len(results) >= max_results,
    }


register_tool(
    ToolDef(
        name="search_text",
        description="Search for a text query in files under the workspace (grep-like). Returns matching file paths and line snippets.",
        input_schema=object_schema(
            properties={
                "query": str_schema("Text to search for."),
                "path": str_schema("Directory to search under (relative to workspace).", default="."),
                "case_sensitive": bool_schema("Case sensitive search.", default=False),
                "max_results": int_schema("Max matches to return.", default=50, minimum=1),
                "max_file_bytes": int_schema("Skip files larger than this (bytes).", default=400000, minimum=1),
                "include_hidden": bool_schema("Include hidden files/folders.", default=False),
            },
            required=["query"],
        ),
        runner=search_text_tool,
    )
)
